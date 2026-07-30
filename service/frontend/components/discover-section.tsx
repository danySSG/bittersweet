"use client";

/**
 * v0.4 (SPEC-v04-discover, раздел B): секция «найти ещё такого».
 *
 * ClusterDiscovery — панель находок одного кластера: стартует discovery-джобу,
 * поллит её общим pollJob и рендерит список кандидатов. Аудио-хук
 * usePreviewPlayer с v0.6 живёт в lib/use-audio.ts (общий для discovery,
 * хайлайтов и примеров кластеров); плеер передаётся в панели пропсом.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, startDiscovery, startPointDiscovery } from "@/lib/api";
import { JobTimeoutError, pollJob } from "@/lib/job-polling";
import type { PreviewPlayer, QueueTrack } from "@/lib/use-audio";
import type {
  Cluster,
  Discovery,
  DiscoveryResult,
  Job,
  SavedPortrait,
} from "@/lib/types";

/* ---------- Нормализация результата ---------- */

/** Результат discovery-джобы: поля либо прямо в джобе, либо в result. */
function extractResult(job: Job): DiscoveryResult {
  const nested = job.result ?? null;
  return {
    discoveries: job.discoveries ?? nested?.discoveries ?? [],
    cluster_label: job.cluster_label ?? nested?.cluster_label,
    archetype: job.archetype ?? nested?.archetype,
    listen_all_url: job.listen_all_url ?? nested?.listen_all_url,
    // v0.9: цель поиска (point-режим); старый бэк поля не отдаёт
    target: job.target ?? nested?.target ?? null,
  };
}

/**
 * discoveries из payload сохранённого портрета (/p/{id}) → Map по кластерам.
 * Значение может быть и полным результатом, и голым списком находок.
 */
export function normalizeSavedDiscoveries(
  raw: SavedPortrait["discoveries"],
): Map<number, DiscoveryResult> {
  const map = new Map<number, DiscoveryResult>();
  if (raw === undefined || raw === null || typeof raw !== "object") return map;
  for (const [key, value] of Object.entries(raw)) {
    const index = Number(key);
    if (!Number.isInteger(index) || index < 0) continue;
    if (Array.isArray(value)) {
      map.set(index, { discoveries: value });
    } else if (value !== null && typeof value === "object") {
      map.set(index, {
        ...value,
        discoveries: Array.isArray(value.discoveries) ? value.discoveries : [],
      });
    }
  }
  return map;
}

/**
 * v0.9 (SPEC-v09-features §B): discoveries по точкам карты хранятся в payload
 * под ключами `point:{round(x)}:{round(y)}` — вытаскиваем их отдельной картой
 * (normalizeSavedDiscoveries такие ключи молча пропускает: Number("point:…")
 * даёт NaN). Точные x/y берём из target результата, если бэк их сохранил,
 * иначе — округлённые из ключа.
 */
export interface SavedPointDiscovery {
  x: number;
  y: number;
  result: DiscoveryResult;
}

const POINT_KEY_RE = /^point:(-?\d+):(-?\d+)$/;

export function normalizeSavedPointDiscoveries(
  raw: SavedPortrait["discoveries"],
): Map<string, SavedPointDiscovery> {
  const map = new Map<string, SavedPointDiscovery>();
  if (raw === undefined || raw === null || typeof raw !== "object") return map;
  for (const [key, value] of Object.entries(raw)) {
    const match = POINT_KEY_RE.exec(key);
    if (match === null) continue;
    let result: DiscoveryResult;
    if (Array.isArray(value)) {
      result = { discoveries: value };
    } else if (value !== null && typeof value === "object") {
      result = {
        ...value,
        discoveries: Array.isArray(value.discoveries) ? value.discoveries : [],
      };
    } else {
      continue;
    }
    const target = result.target ?? null;
    const x = typeof target?.x === "number" ? target.x : Number(match[1]);
    const y = typeof target?.y === "number" ? target.y : Number(match[2]);
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    map.set(key, { x, y, result });
  }
  return map;
}

/** Фолбэк «слушать всё», если бэк не прислал listen_all_url. */
function buildListenAllUrl(discoveries: Discovery[]): string | null {
  const ids = discoveries
    .map((d) => d.videoId)
    .filter((id) => typeof id === "string" && id !== "")
    .slice(0, 25);
  if (ids.length === 0) return null;
  return `https://www.youtube.com/watch_videos?video_ids=${ids.join(",")}`;
}

/* ---------- Ошибки ---------- */

interface DiscoverError {
  message: string;
  /** Показывать ли кнопку «Попробовать снова» */
  retriable: boolean;
}

function describeDiscoverError(err: unknown): DiscoverError {
  if (err instanceof JobTimeoutError) {
    return {
      message: "Поиск занял слишком много времени. Попробуй ещё раз.",
      retriable: true,
    };
  }
  if (err instanceof ApiError) {
    if (err.status === 409) {
      // портрет без videoId в points — сохранён до v0.4
      return {
        message:
          "Этот портрет сохранён старой версией — построй заново, и поиск похожего заработает.",
        retriable: false,
      };
    }
    if (err.status === 404) {
      return {
        message:
          "Бэкенд не нашёл портрет или джобу — возможно, его перезапустили. Попробуй ещё раз.",
        retriable: true,
      };
    }
    // 422 и прочее: detail уже человеческий
    return { message: err.message, retriable: true };
  }
  return {
    message: err instanceof Error ? err.message : "Что-то пошло не так.",
    retriable: true,
  };
}

/* ---------- Строка находки ---------- */

/** Прозрачная подложка цвета кластера для строк с match >= 70. */
function tint(color: string): string {
  return /^#[0-9a-fA-F]{6}$/.test(color) ? `${color}1f` : "var(--accent-dim)";
}

function formatMeta(d: Discovery): string {
  const parts: string[] = [];
  if (typeof d.tempo === "number" && Number.isFinite(d.tempo)) {
    parts.push(`${Math.round(d.tempo)} bpm`);
  }
  const tonality = [d.key, d.mode].filter(
    (v): v is string => typeof v === "string" && v !== "",
  );
  if (tonality.length > 0) parts.push(tonality.join(" "));
  return parts.join(" · ");
}

function DiscoveryRow({
  discovery,
  color,
  player,
}: {
  discovery: Discovery;
  color: string;
  player: PreviewPlayer;
}) {
  const url = discovery.preview_url ?? null;
  const playing = url !== null && player.playingUrl === url;
  const meta = formatMeta(discovery);
  const hot = discovery.match >= 70;
  const label = `${discovery.artist} — ${discovery.title}`;
  return (
    <li
      className="discover-row"
      style={hot ? { background: tint(color) } : undefined}
    >
      <button
        type="button"
        className="play-btn"
        disabled={url === null}
        title={url === null ? "превью не нашлось" : undefined}
        aria-label={
          playing
            ? "Пауза"
            : `Слушать превью: ${discovery.artist} — ${discovery.title}`
        }
        onClick={
          url === null
            ? undefined
            : // v0.10 §D: videoId уходит в toggle — кнопка ▶ уважает
              // режим «Полностью» (одиночная очередь с видимым YT-плеером)
              () => player.toggle(url, { videoId: discovery.videoId, label })
        }
      >
        {playing ? "⏸︎" : "▶︎"}
      </button>
      <span
        className="discover-match mono"
        style={{ background: color }}
        title={`совпадение по звуку: ${discovery.match} из 100`}
      >
        {discovery.match}
      </span>
      <span className="discover-track">
        <span
          className="discover-track-title"
          title={`${discovery.artist} — ${discovery.title}`}
        >
          {discovery.artist} — {discovery.title}
        </span>
        {meta !== "" && (
          <span className="discover-track-meta mono">{meta}</span>
        )}
      </span>
      <a
        className="discover-open-link"
        href={`https://music.youtube.com/watch?v=${encodeURIComponent(discovery.videoId)}`}
        target="_blank"
        rel="noopener noreferrer"
      >
        открыть в YT Music ↗
      </a>
    </li>
  );
}

/* ---------- Тело панели: прогресс / ошибка / список ---------- */

function DiscoveryProgress({ job }: { job: Job | null }) {
  const stage = job?.progress.stage;
  const total = job?.progress.total ?? 0;
  const done = job?.progress.done ?? 0;
  if (stage === "analyzing") {
    const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
    return (
      <div className="discover-progress">
        <span className="btn-spinner" aria-hidden />
        <span role="status">
          Слушаем кандидатов…{" "}
          {total > 0 && (
            <>
              <span className="mono">{done}</span> из{" "}
              <span className="mono">{total}</span>
            </>
          )}
        </span>
        {total > 0 && (
          <div
            className="progress-track"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={total}
            aria-valuenow={done}
            aria-label="Прогресс анализа кандидатов"
          >
            <div className="progress-fill" style={{ width: `${pct}%` }} />
          </div>
        )}
      </div>
    );
  }
  // seeding | radio | queued | старт джобы
  return (
    <div className="discover-progress">
      <span className="btn-spinner" aria-hidden />
      <span role="status">Собираем радио по трекам-эталонам…</span>
    </div>
  );
}

function DiscoveryList({
  result,
  color,
  player,
  queueMeta,
}: {
  result: DiscoveryResult;
  color: string;
  player: PreviewPlayer;
  /** v0.9: эмодзи источника для мини-плеера радио (архетип / 🎯) */
  queueMeta?: string;
}) {
  // бэк сортирует по match; на всякий случай не доверяем порядку слепо
  const discoveries = useMemo(
    () => [...result.discoveries].sort((a, b) => b.match - a.match),
    [result.discoveries],
  );

  /**
   * v0.9 §A: очередь радио. Мемо держит ссылку стабильной: player.toggle
   * по ней понимает, что клик по строке — прыжок именно этой очереди.
   * v0.10 §D: в очередь попадают и треки без превью, но с videoId —
   * в режиме «Полностью» они играют целиком; в «Превью» хук их пропустит.
   */
  const radioTracks = useMemo<QueueTrack[]>(
    () =>
      discoveries
        .filter(
          (d) =>
            (typeof d.preview_url === "string" && d.preview_url !== "") ||
            (typeof d.videoId === "string" && d.videoId !== ""),
        )
        .map((d) => ({
          url:
            typeof d.preview_url === "string" && d.preview_url !== ""
              ? d.preview_url
              : null,
          videoId: d.videoId,
          label: `${d.artist} — ${d.title}`,
          meta: queueMeta,
        })),
    [discoveries, queueMeta],
  );

  if (discoveries.length === 0) {
    return (
      <p className="discover-empty">
        Радио не дало близких кандидатов — попробуй другой архетип.
      </p>
    );
  }
  const listenAll =
    result.listen_all_url ?? buildListenAllUrl(discoveries) ?? null;
  return (
    <>
      {(radioTracks.length > 0 || listenAll !== null) && (
        <div className="discover-actions">
          {radioTracks.length > 0 && (
            <button
              type="button"
              className="btn btn-ghost btn-small discover-radio-btn"
              title={`играть превью подряд: ${radioTracks.length} шт.`}
              onClick={() => player.playQueue(radioTracks, 0)}
            >
              📻 Радио
            </button>
          )}
          {listenAll !== null && (
            <a
              className="discover-listen-all"
              href={listenAll}
              target="_blank"
              rel="noopener noreferrer"
            >
              Слушать всё в YouTube Music ↗
            </a>
          )}
        </div>
      )}
      <ol className="discover-list">
        {discoveries.map((d, i) => (
          <DiscoveryRow
            key={`${d.videoId}-${i}`}
            discovery={d}
            color={color}
            player={player}
          />
        ))}
      </ol>
      {/* v0.6: атрибуция превью — один раз в футере портрета, не тут */}
    </>
  );
}

/* ---------- Панель кластера ---------- */

type Phase =
  | { kind: "idle"; result: DiscoveryResult } // сохранённые находки, без джобы
  | { kind: "starting" } // save-демо / POST /api/discover
  | { kind: "running"; job: Job | null }
  | { kind: "done"; result: DiscoveryResult }
  | { kind: "error"; error: DiscoverError };

export function ClusterDiscovery({
  cluster,
  clusterIndex,
  runToken,
  initial,
  resolvePortraitId,
  player,
}: {
  cluster: Cluster;
  clusterIndex: number;
  /** Счётчик кликов «Найти ещё такого» на карточке; 0 — только initial */
  runToken: number;
  /** Сохранённые находки из payload /p/{id} — рендерим без пересчёта */
  initial?: DiscoveryResult;
  /** Отдаёт portrait_id (для демо — молча сохраняет портрет). Стабильный. */
  resolvePortraitId: () => Promise<string>;
  player: PreviewPlayer;
}) {
  const [phase, setPhase] = useState<Phase>(() =>
    initial !== undefined
      ? { kind: "idle", result: initial }
      : { kind: "starting" },
  );
  // локальный триггер «обновить подборку» / «попробовать снова»
  const [localRun, setLocalRun] = useState(0);
  const rootRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (runToken === 0 && localRun === 0) return; // показываем только initial
    let cancelled = false;
    setPhase({ kind: "starting" });

    const go = async () => {
      const portraitId = await resolvePortraitId();
      const { job_id } = await startDiscovery(portraitId, clusterIndex);
      if (cancelled) return;
      setPhase({ kind: "running", job: null });
      const state = await pollJob(job_id, {
        onUpdate: (job) => {
          if (!cancelled) setPhase({ kind: "running", job });
        },
        isCancelled: () => cancelled,
      });
      if (state === null || cancelled) return;
      if (state.status === "error") {
        setPhase({
          kind: "error",
          error: {
            message:
              state.error !== null && state.error !== ""
                ? state.error
                : "Поиск похожего не удался. Попробуй ещё раз.",
            retriable: true,
          },
        });
        return;
      }
      setPhase({ kind: "done", result: extractResult(state) });
    };

    go().catch((err: unknown) => {
      if (!cancelled) {
        setPhase({ kind: "error", error: describeDiscoverError(err) });
      }
    });

    return () => {
      cancelled = true;
    };
  }, [runToken, localRun, clusterIndex, resolvePortraitId]);

  // клик по карточке — доскроллить до панели
  useEffect(() => {
    if (runToken > 0) {
      rootRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [runToken]);

  const refresh = useCallback(() => setLocalRun((n) => n + 1), []);

  const archetype = cluster.archetype;
  const showRefresh = phase.kind === "idle" || phase.kind === "done";

  let body: React.ReactNode;
  if (phase.kind === "starting") {
    body = <DiscoveryProgress job={null} />;
  } else if (phase.kind === "running") {
    body = <DiscoveryProgress job={phase.job} />;
  } else if (phase.kind === "error") {
    body = (
      <div className="discover-error-box">
        <p className="discover-error" role="alert">
          {phase.error.message}
        </p>
        {phase.error.retriable && (
          <button
            type="button"
            className="btn btn-ghost btn-small"
            onClick={refresh}
          >
            Попробовать снова
          </button>
        )}
      </div>
    );
  } else {
    body = (
      <DiscoveryList
        result={phase.result}
        color={cluster.color}
        player={player}
        queueMeta={archetype?.emoji ?? "📻"}
      />
    );
  }

  return (
    <section className="discover-panel" ref={rootRef}>
      <header className="discover-head">
        <span
          className="cluster-dot"
          style={{ background: cluster.color }}
          aria-hidden
        />
        <span className="discover-head-title">
          {archetype ? `${archetype.emoji} ${archetype.name}` : cluster.label}
        </span>
        {showRefresh && (
          <button
            type="button"
            className="btn btn-ghost btn-small"
            onClick={refresh}
          >
            Обновить подборку
          </button>
        )}
      </header>
      {body}
    </section>
  );
}

/* ---------- v0.9 (SPEC-v09-features §B): панель discovery по точке карты ---------- */

/**
 * Ошибки point-режима: 409 здесь — «рядом с точкой мало треков с превью»
 * (бэк присылает человеческое сообщение), а не «портрет старой версии»,
 * поэтому describeDiscoverError не годится — он подменяет detail.
 */
function describePointError(err: unknown): DiscoverError {
  if (err instanceof ApiError && err.status === 409) {
    return {
      message:
        err.message !== ""
          ? err.message
          : "Рядом с этой точкой слишком мало твоих треков — кликни ближе к скоплению.",
      retriable: false,
    };
  }
  return describeDiscoverError(err);
}

/** Цвет строк point-панели: у точки нет кластера — берём акцент продукта. */
const POINT_COLOR = "#7f77dd";

/**
 * Панель «Ещё такого» для точки карты (заголовок «🎯 точка карты»).
 * Устроена как ClusterDiscovery, но стартует POST /api/discover
 * {point: {x, y}} и показывает near-треки цели. Статус наружу через
 * onStatus — по нему PortraitView пульсирует маркером 🎯 на карте
 * и вешает бейдж с числом находок.
 */
export function PointDiscovery({
  x,
  y,
  runToken,
  initial,
  resolvePortraitId,
  player,
  onStatus,
}: {
  /** Координаты в осях карты (valence/energy-перцентили 0..100) */
  x: number;
  y: number;
  /** Счётчик запусков по этой точке; 0 — только initial из payload */
  runToken: number;
  /** Сохранённый результат из payload /p/{id} — рендерим без пересчёта */
  initial?: DiscoveryResult;
  resolvePortraitId: () => Promise<string>;
  player: PreviewPlayer;
  /** Статус для маркера на карте: пульс при running, бейдж по done */
  onStatus?: (status: "running" | "done" | "error", count: number | null) => void;
}) {
  const [phase, setPhase] = useState<Phase>(() =>
    initial !== undefined
      ? { kind: "idle", result: initial }
      : { kind: "starting" },
  );
  const [localRun, setLocalRun] = useState(0);
  const rootRef = useRef<HTMLElement | null>(null);
  // ref, чтобы смена колбэка не перезапускала джобу
  const onStatusRef = useRef(onStatus);
  useEffect(() => {
    onStatusRef.current = onStatus;
  });

  useEffect(() => {
    if (runToken === 0 && localRun === 0) return; // показываем только initial
    let cancelled = false;
    setPhase({ kind: "starting" });
    onStatusRef.current?.("running", null);

    const go = async () => {
      const portraitId = await resolvePortraitId();
      const { job_id } = await startPointDiscovery(portraitId, x, y);
      if (cancelled) return;
      setPhase({ kind: "running", job: null });
      const state = await pollJob(job_id, {
        onUpdate: (job) => {
          if (!cancelled) setPhase({ kind: "running", job });
        },
        isCancelled: () => cancelled,
      });
      if (state === null || cancelled) return;
      if (state.status === "error") {
        setPhase({
          kind: "error",
          error: {
            message:
              state.error !== null && state.error !== ""
                ? state.error
                : "Поиск по точке не удался. Попробуй ещё раз.",
            retriable: true,
          },
        });
        onStatusRef.current?.("error", null);
        return;
      }
      const result = extractResult(state);
      setPhase({ kind: "done", result });
      onStatusRef.current?.("done", result.discoveries.length);
    };

    go().catch((err: unknown) => {
      if (!cancelled) {
        setPhase({ kind: "error", error: describePointError(err) });
        onStatusRef.current?.("error", null);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [runToken, localRun, x, y, resolvePortraitId]);

  // запуск с карты/галактики — доскроллить до панели
  useEffect(() => {
    if (runToken > 0) {
      rootRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [runToken]);

  const refresh = useCallback(() => setLocalRun((n) => n + 1), []);

  const showRefresh = phase.kind === "idle" || phase.kind === "done";
  const result =
    phase.kind === "idle" || phase.kind === "done" ? phase.result : null;
  const near = result?.target?.near ?? [];

  let body: React.ReactNode;
  if (phase.kind === "starting") {
    body = <DiscoveryProgress job={null} />;
  } else if (phase.kind === "running") {
    body = <DiscoveryProgress job={phase.job} />;
  } else if (phase.kind === "error") {
    body = (
      <div className="discover-error-box">
        <p className="discover-error" role="alert">
          {phase.error.message}
        </p>
        {phase.error.retriable && (
          <button
            type="button"
            className="btn btn-ghost btn-small"
            onClick={refresh}
          >
            Попробовать снова
          </button>
        )}
      </div>
    );
  } else {
    body = (
      <DiscoveryList
        result={phase.result}
        color={POINT_COLOR}
        player={player}
        queueMeta="🎯"
      />
    );
  }

  return (
    <section className="discover-panel" ref={rootRef}>
      <header className="discover-head">
        <span className="discover-head-title">🎯 точка карты</span>
        <span className="discover-point-label mono">
          настроение {Math.round(x)} · энергия {Math.round(y)}
        </span>
        {showRefresh && (
          <button
            type="button"
            className="btn btn-ghost btn-small"
            onClick={refresh}
          >
            Обновить подборку
          </button>
        )}
      </header>
      {near.length > 0 && (
        <p className="discover-point-near">
          рядом из твоего: {near.slice(0, 3).join(" · ")}
        </p>
      )}
      {body}
    </section>
  );
}
