"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ApiError,
  disconnectYoutube,
  fetchMe,
  saveDemoPortrait,
} from "@/lib/api";
import { clusterColor } from "@/lib/colors";
import { downloadBlob, renderShareCard } from "@/lib/share-card";
import {
  ClusterDiscovery,
  PointDiscovery,
  normalizeSavedDiscoveries,
  normalizeSavedPointDiscoveries,
} from "@/components/discover-section";
import GalaxyView, { hasGalaxyFeatures } from "@/components/galaxy-view";
import MiniPlayer from "@/components/mini-player";
import ObservatorySection, { useObservatory } from "@/components/observatory";
import PointAimPopover from "@/components/point-popover";
import {
  PreviewPlayButton,
  usePreviewResolver,
  type PreviewContext,
} from "@/components/preview-play";
import StoryView from "@/components/story-view";
import TasteConsole from "@/components/taste-console";
import TasteQuiz from "@/components/taste-quiz";
import TasteTimeline, { prepareBuckets } from "@/components/taste-timeline";
import { usePreviewPlayer } from "@/lib/use-audio";
import type {
  Cluster,
  DiscoveryResult,
  ExampleRich,
  Highlight,
  Me,
  Portrait,
  PortraitPoint,
  SavedPortrait,
} from "@/lib/types";

/* ---------- SVG-скаттер ---------- */

const W = 640;
const H = 460;
const PAD = { top: 20, right: 20, bottom: 44, left: 44 };

function sx(x: number): number {
  return PAD.left + (x / 100) * (W - PAD.left - PAD.right);
}

function sy(y: number): number {
  // y=0 внизу, y=100 вверху
  return H - PAD.bottom - (y / 100) * (H - PAD.top - PAD.bottom);
}

/* v0.9 (SPEC-v09-features §B): маркеры point-discovery на 2D-карте */

type PointMarkerStatus = "running" | "done" | "error";

interface PointMarker {
  key: string;
  /** Координаты в осях карты (0..100) */
  x: number;
  y: number;
  /** running — маркер пульсирует; done — бейдж с числом находок */
  status: PointMarkerStatus;
  count: number | null;
}

/** Квадрат радиуса (в px viewBox), в котором клик считается «по точке». */
const POINT_HIT_SQ = 81; // 9px: r точки 4 + хвост hover-обводки

function clamp01x100(v: number): number {
  return Math.min(100, Math.max(0, v));
}

function markerClass(status: PointMarkerStatus): string {
  if (status === "running") return "aim-marker aim-marker-running";
  if (status === "error") return "aim-marker aim-marker-error";
  return "aim-marker";
}

function ScatterMap({
  points,
  clusters,
  markers = [],
  onAim,
  coords = null,
}: {
  points: PortraitPoint[];
  clusters: Cluster[];
  /** v0.9: активные/завершённые point-discovery — 🎯 с пульсом/бейджем */
  markers?: PointMarker[];
  /** v0.9: клик по ПУСТОМУ месту карты → прицел; undefined — клики выключены */
  onAim?: (x: number, y: number) => void;
  /**
   * v0.11 (SPEC-v11-observatory §B5), режим «Форма»: альтернативные
   * координаты точек 0..100 ПО ИНДЕКСАМ points (t-SNE из обсерватории).
   * Компонент точек тот же — смена координат анимируется CSS-переходом;
   * оси настроения в этом режиме смысла не имеют, подписи меняются,
   * прицел и маркеры discovery выключает вызывающий код.
   */
  coords?: { x: number[]; y: number[] } | null;
}) {
  const shape = coords !== null;
  // прицел до подтверждения: клик по пустому месту → 🎯 + поповер
  const [aim, setAim] = useState<{ x: number; y: number } | null>(null);

  const handleClick = (e: React.MouseEvent<SVGSVGElement>) => {
    if (onAim === undefined) return;
    const rect = e.currentTarget.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    const svgX = ((e.clientX - rect.left) / rect.width) * W;
    const svgY = ((e.clientY - rect.top) / rect.height) * H;
    // клики за пределами поля графика (в отступах-осях) — просто закрыть
    if (
      svgX < PAD.left ||
      svgX > W - PAD.right ||
      svgY < PAD.top ||
      svgY > H - PAD.bottom
    ) {
      setAim(null);
      return;
    }
    // клик по точке трека — прежнее тултип-поведение, прицел НЕ ставим
    for (const p of points) {
      if ((sx(p.x) - svgX) ** 2 + (sy(p.y) - svgY) ** 2 < POINT_HIT_SQ) return;
    }
    setAim({
      x: clamp01x100(((svgX - PAD.left) / (W - PAD.left - PAD.right)) * 100),
      y: clamp01x100(((H - PAD.bottom - svgY) / (H - PAD.top - PAD.bottom)) * 100),
    });
  };

  // позиция поповера в % от сцены (svg тянется на всю ширину)
  const popLeft = aim !== null ? Math.min(86, Math.max(14, (sx(aim.x) / W) * 100)) : 0;
  const popAbove = aim !== null && sy(aim.y) > H * 0.55;

  return (
    <div className="scatter-wrap">
      <div className="scatter-stage">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={
          shape
            ? "Форма вкуса: t-SNE-проекция, похожие по звуку треки рядом"
            : "Карта настроений: valence по горизонтали, энергия по вертикали"
        }
        onClick={onAim !== undefined ? handleClick : undefined}
        style={onAim !== undefined ? { cursor: "crosshair" } : undefined}
      >
        {/* сетка по квартилям */}
        {[25, 50, 75].map((v) => (
          <g key={v}>
            <line
              className="grid-line"
              x1={sx(v)}
              y1={sy(0)}
              x2={sx(v)}
              y2={sy(100)}
            />
            <line
              className="grid-line"
              x1={sx(0)}
              y1={sy(v)}
              x2={sx(100)}
              y2={sy(v)}
            />
          </g>
        ))}
        {/* оси */}
        <line
          x1={sx(0)}
          y1={sy(0)}
          x2={sx(100)}
          y2={sy(0)}
          stroke="var(--border)"
        />
        <line
          x1={sx(0)}
          y1={sy(0)}
          x2={sx(0)}
          y2={sy(100)}
          stroke="var(--border)"
        />
        {/* подписи осей: в «Форме» оси настроения смысла не имеют */}
        {shape ? (
          <text className="axis-label" x={sx(0)} y={H - 14}>
            форма вкуса · t-SNE: похожие по звуку треки рядом
          </text>
        ) : (
          <>
            <text className="axis-label" x={sx(0)} y={H - 14}>
              грустнее
            </text>
            <text
              className="axis-label"
              x={sx(100)}
              y={H - 14}
              textAnchor="end"
            >
              радостнее → valence
            </text>
            <text
              className="axis-label"
              x={14}
              y={sy(100)}
              transform={`rotate(-90 14 ${sy(100)})`}
              textAnchor="end"
            >
              энергичнее → energy
            </text>
            <text
              className="axis-label"
              x={14}
              y={sy(0)}
              transform={`rotate(-90 14 ${sy(0)})`}
            >
              спокойнее
            </text>
          </>
        )}
        {/* точки: те же элементы в обоих режимах — переход Карта↔Форма
            анимируется CSS-переходом cx/cy */}
        {points.map((p, i) => (
          <circle
            key={i}
            className="scatter-point"
            cx={sx(coords !== null ? (coords.x[i] ?? p.x) : p.x)}
            cy={sy(coords !== null ? (coords.y[i] ?? p.y) : p.y)}
            r={4}
            fill={clusterColor(clusters, p.cluster)}
            fillOpacity={0.82}
          >
            <title>{`${p.label}\n${p.meta}`}</title>
          </circle>
        ))}
        {/* v0.9: маркеры point-discovery — пульс при поиске, бейдж по done */}
        {markers.map((m) => (
          <g key={m.key} className="aim-marker-group" aria-hidden>
            <text
              className={markerClass(m.status)}
              x={sx(m.x)}
              y={sy(m.y)}
              textAnchor="middle"
              dominantBaseline="central"
            >
              🎯
            </text>
            {m.status === "done" && typeof m.count === "number" && (
              <g
                className="aim-badge"
                transform={`translate(${sx(m.x) + 13}, ${sy(m.y) - 13})`}
              >
                <circle r={9} />
                <text textAnchor="middle" dominantBaseline="central">
                  {m.count}
                </text>
              </g>
            )}
          </g>
        ))}
        {/* прицел до подтверждения */}
        {aim !== null && (
          <text
            className="aim-marker"
            x={sx(aim.x)}
            y={sy(aim.y)}
            textAnchor="middle"
            dominantBaseline="central"
            aria-hidden
          >
            🎯
          </text>
        )}
      </svg>
      {aim !== null && onAim !== undefined && (
        <PointAimPopover
          x={aim.x}
          y={aim.y}
          className={popAbove ? "point-popover-above" : "point-popover-below"}
          style={{
            left: `${popLeft}%`,
            top: `${(sy(aim.y) / H) * 100}%`,
          }}
          onConfirm={() => {
            onAim(aim.x, aim.y);
            setAim(null);
          }}
          onCancel={() => setAim(null)}
        />
      )}
      </div>
    </div>
  );
}

/* ---------- v0.6: превью на хайлайтах и примерах кластеров (B3) ----------
 * Кнопка ▶ и ленивый батч-резолвер жили здесь; в v0.11 вынесены в
 * components/preview-play.tsx — их переиспользует обсерватория. */

/** Видимые примеры кластера: examples_rich (v0.6) или строки старого бэка. */
function exampleRows(cluster: Cluster): ExampleRich[] {
  if (cluster.examples_rich !== undefined && cluster.examples_rich.length > 0) {
    return cluster.examples_rich.slice(0, 3);
  }
  return cluster.examples.slice(0, 3).map((label) => ({ label }));
}

/** Русское склонение для «N настроений» в шапке (жаргон «кластер» — только в коде). */
function ruMoodWord(n: number): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return "настроение вкуса";
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "настроения вкуса";
  return "настроений вкуса";
}

/* ---------- Карточка кластера ---------- */

function ClusterCard({
  cluster,
  onDiscover,
  preview,
}: {
  cluster: Cluster;
  /** v0.4: клик «Найти ещё такого»; undefined — портрет без id, кнопки нет */
  onDiscover?: () => void;
  /** v0.6: контекст превью для кнопок ▶ на примерах */
  preview: PreviewContext;
}) {
  // v0.3: сверху крупно emoji + archetype.name, техническое label — подписью.
  // Старый бэк без archetype — label остаётся заголовком.
  const archetype = cluster.archetype;
  return (
    <article className="cluster-card">
      {archetype ? (
        <>
          <div className="cluster-archetype">
            <span className="cluster-archetype-emoji" aria-hidden>
              {archetype.emoji}
            </span>
            <span className="cluster-archetype-name">{archetype.name}</span>
          </div>
          <div className="cluster-card-head cluster-card-head-sub">
            <span
              className="cluster-dot"
              style={{ background: cluster.color }}
              aria-hidden
            />
            <span className="cluster-tech-label">{cluster.label}</span>
          </div>
        </>
      ) : (
        <div className="cluster-card-head">
          <span
            className="cluster-dot"
            style={{ background: cluster.color }}
            aria-hidden
          />
          <span className="cluster-label">{cluster.label}</span>
        </div>
      )}
      <div className="cluster-share">
        <span className="mono">{cluster.size}</span> треков ·{" "}
        <span className="mono">{cluster.share}%</span> библиотеки
      </div>
      <div className="cluster-medians">
        <div className="median">
          <span className="median-value">{cluster.medians.tempo}</span>
          <span className="median-key">bpm</span>
        </div>
        <div className="median">
          <span className="median-value">{cluster.medians.minor_share}%</span>
          <span className="median-key">минор</span>
        </div>
        <div className="median">
          <span className="median-value">{cluster.medians.brightness}</span>
          <span className="median-key">яркость</span>
        </div>
      </div>
      <ul className="cluster-examples">
        {exampleRows(cluster).map((example) => (
          <li
            key={example.label}
            title={example.label}
            className="cluster-example-row"
          >
            <PreviewPlayButton
              label={example.label}
              state={preview.stateFor(example.label, example.preview_url)}
              player={preview.player}
              onLazy={preview.onLazy}
              videoId={example.videoId}
            />
            <span className="cluster-example-label">{example.label}</span>
          </li>
        ))}
      </ul>
      {onDiscover !== undefined && (
        <button
          type="button"
          className="btn btn-ghost btn-small cluster-discover-btn"
          onClick={onDiscover}
        >
          ⚡ Найти ещё такого
        </button>
      )}
    </article>
  );
}

/* ---------- Хайлайты «Самые-самые» (v0.3) ---------- */

const HIGHLIGHT_ICONS: Record<string, string> = {
  fastest: "⚡",
  darkest: "🌑",
  most_bittersweet: "🍬",
  most_energetic: "🔋",
  most_yours: "🎯",
};

function HighlightChip({
  highlight,
  preview,
}: {
  highlight: Highlight;
  /** v0.6: контекст превью — кнопка ▶ на чипе при наличии preview_url */
  preview: PreviewContext;
}) {
  return (
    <div className="highlight-chip">
      <span className="highlight-icon" aria-hidden>
        {HIGHLIGHT_ICONS[highlight.kind] ?? "🎵"}
      </span>
      <PreviewPlayButton
        label={highlight.track}
        state={preview.stateFor(highlight.track, highlight.preview_url)}
        player={preview.player}
        onLazy={preview.onLazy}
        videoId={highlight.videoId}
      />
      <span className="highlight-body">
        <span className="highlight-title">{highlight.title}</span>
        <span className="highlight-track" title={highlight.track}>
          {highlight.track}
        </span>
      </span>
      <span className="highlight-value mono">{highlight.value}</span>
    </div>
  );
}

/* ---------- Чип YouTube-аккаунта в шапке (v0.5) ---------- */

/**
 * «YouTube: {channel_title}» + «Отключить» (confirm → POST disconnect →
 * редирект на лендинг). Рендерится ТОЛЬКО когда GET /api/me ответил
 * connected; старый или ненастроенный бэк (404/503/сеть) — чипа просто нет.
 */
function YoutubeAccountChip() {
  const [me, setMe] = useState<Me | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchMe()
      .then((data) => {
        if (!cancelled && data.connected) setMe(data);
      })
      .catch(() => {
        // /api/me недоступен (старый бэк, 503, сеть) — молча без чипа
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const disconnect = useCallback(async () => {
    const ok = window.confirm(
      "Отключить YouTube? Мы отзовём доступ у Google и сразу удалим твои youtube-портреты.",
    );
    if (!ok) return;
    setBusy(true);
    try {
      await disconnectYoutube();
      // cookie снята, аккаунт удалён — возвращаемся на лендинг
      window.location.href = "/";
    } catch (err) {
      setBusy(false);
      window.alert(
        err instanceof Error
          ? err.message
          : "Не получилось отключить аккаунт. Попробуй ещё раз.",
      );
    }
  }, []);

  if (me === null) return null;

  return (
    <span className="account-chip">
      <span
        className="account-chip-name"
        title={me.channel_title ?? undefined}
      >
        YouTube: {me.channel_title ?? "аккаунт"}
      </span>
      <button
        type="button"
        className="account-chip-disconnect"
        onClick={() => void disconnect()}
        disabled={busy}
      >
        {busy ? "Отключаем…" : "Отключить"}
      </button>
    </span>
  );
}

/* ---------- Рендер портрета (общий: demo / плейлист / /p/[id]) ---------- */

/** v0.12 (SPEC-v12-human §B): ключ localStorage выбора вида «История|Портрет». */
const VIEW_STORAGE_KEY = "bittersweet:view";

type ViewMode = "story" | "portrait";

export default function PortraitView({
  portrait,
  modeNote = "",
  portraitId = null,
  isDemo = false,
  title = "Твой акустический портрет",
  headerExtra,
  savedDiscoveries,
  defaultView = "portrait",
}: {
  portrait: Portrait;
  /** Хвост мета-строки, напр. " · демо-режим" */
  modeNote?: string;
  /** Постоянный id портрета (из джобы или /p/[id]), если известен */
  portraitId?: string | null;
  /** Демо-поток: перед шарингом сохраняем через POST /api/demo/portrait/save */
  isDemo?: boolean;
  title?: string;
  /** Дополнительные кнопки в шапке (напр. CTA «Построить свой» на /p/[id]) */
  headerExtra?: ReactNode;
  /** v0.4: сохранённые находки из payload /p/{id} — рендерим без пересчёта */
  savedDiscoveries?: SavedPortrait["discoveries"];
  /**
   * v0.12 (SPEC-v12-human §B): стартовый вид. Пермалинк /p/[id] передаёт
   * "story" (новому зрителю — рассказ), владелец после анализа остаётся
   * в "portrait". Явный выбор пользователя запоминается в localStorage
   * и перекрывает дефолт.
   */
  defaultView?: ViewMode;
}) {
  const [permanentId, setPermanentId] = useState<string | null>(
    portraitId ?? null,
  );
  const [copied, setCopied] = useState(false);
  const [copiedOnce, setCopiedOnce] = useState(false);
  const [rendering, setRendering] = useState(false);
  const [cardError, setCardError] = useState<string | null>(null);

  /* ---- v0.4: discovery ---- */

  // счётчики кликов «Найти ещё такого» по индексу кластера
  const [discoverRuns, setDiscoverRuns] = useState<Record<number, number>>({});
  const player = usePreviewPlayer();

  // ref, чтобы resolvePortraitId был стабильным и не перезапускал джобы
  const permanentIdRef = useRef<string | null>(permanentId);
  useEffect(() => {
    permanentIdRef.current = permanentId;
  }, [permanentId]);

  /**
   * portrait_id для discovery: сохранённый — как есть; несохранённое демо —
   * молча сохраняем (POST /api/demo/portrait/save) и запоминаем id.
   */
  const resolvePortraitId = useCallback(async (): Promise<string> => {
    const existing = permanentIdRef.current;
    if (existing !== null && existing !== "") return existing;
    if (!isDemo) {
      throw new ApiError(
        "У портрета нет постоянного id — построй его заново и попробуй ещё раз.",
      );
    }
    const saved = await saveDemoPortrait();
    permanentIdRef.current = saved.portrait_id;
    setPermanentId(saved.portrait_id);
    return saved.portrait_id;
  }, [isDemo]);

  const savedMap = useMemo(
    () => normalizeSavedDiscoveries(savedDiscoveries),
    [savedDiscoveries],
  );

  // кнопка есть только у сохранённых портретов; демо сохраняем молча по клику
  const canDiscover =
    isDemo || (permanentId !== null && permanentId !== "");

  /* ---- v0.9, §B: discovery по точке карты ---- */

  // сохранённые point-результаты из payload (/p/{id}): ключ point:x:y
  const savedPointMap = useMemo(
    () => normalizeSavedPointDiscoveries(savedDiscoveries),
    [savedDiscoveries],
  );
  // запуски по точкам за эту сессию: ключ → координаты + счётчик запусков
  const [pointRuns, setPointRuns] = useState<
    Record<string, { x: number; y: number; runToken: number }>
  >({});
  // статусы панелей — для маркеров 🎯 (пульс/бейдж числа находок)
  const [pointStatuses, setPointStatuses] = useState<
    Record<string, { status: "running" | "done" | "error"; count: number | null }>
  >({});

  // подтверждённый прицел с карты или галактики → панель «Ещё такого»;
  // повтор той же округлённой точки перезапускает поиск (бэк перезапишет)
  const discoverPoint = useCallback((x: number, y: number) => {
    const key = `point:${Math.round(x)}:${Math.round(y)}`;
    setPointRuns((prev) => ({
      ...prev,
      [key]: { x, y, runToken: (prev[key]?.runToken ?? 0) + 1 },
    }));
  }, []);

  const handlePointStatus = useCallback(
    (key: string, status: "running" | "done" | "error", count: number | null) => {
      setPointStatuses((prev) => ({ ...prev, [key]: { status, count } }));
    },
    [],
  );

  // панели по точкам: сохранённые из payload + запущенные в этой сессии
  const pointPanels = useMemo(() => {
    const map = new Map<
      string,
      { x: number; y: number; runToken: number; initial?: DiscoveryResult }
    >();
    for (const [key, saved] of savedPointMap) {
      map.set(key, { x: saved.x, y: saved.y, runToken: 0, initial: saved.result });
    }
    for (const [key, run] of Object.entries(pointRuns)) {
      map.set(key, {
        x: run.x,
        y: run.y,
        runToken: run.runToken,
        initial: map.get(key)?.initial,
      });
    }
    return [...map.entries()];
  }, [savedPointMap, pointRuns]);

  // маркеры 🎯 для 2D-карты
  const pointMarkers = useMemo(
    () =>
      pointPanels.map(([key, panel]) => {
        const live = pointStatuses[key];
        const status =
          live?.status ?? (panel.initial !== undefined ? "done" : "running");
        const count =
          live !== undefined
            ? live.count
            : (panel.initial?.discoveries.length ?? null);
        return { key, x: panel.x, y: panel.y, status, count };
      }),
    [pointPanels, pointStatuses],
  );

  // панели: кластеры с сохранёнными находками + те, где нажали кнопку
  const panelIndices = useMemo(() => {
    const indices = new Set<number>();
    for (const index of savedMap.keys()) {
      if (index < portrait.clusters.length) indices.add(index);
    }
    for (const [key, runs] of Object.entries(discoverRuns)) {
      const index = Number(key);
      if (runs > 0 && index < portrait.clusters.length) indices.add(index);
    }
    return [...indices].sort((a, b) => a - b);
  }, [savedMap, discoverRuns, portrait.clusters.length]);

  const discover = useCallback((index: number) => {
    setDiscoverRuns((runs) => ({ ...runs, [index]: (runs[index] ?? 0) + 1 }));
  }, []);

  const share = useCallback(async () => {
    let id = permanentId;
    if (id === null && isDemo) {
      // демо: сначала сохраняем портрет — получаем постоянную ссылку
      try {
        const saved = await saveDemoPortrait();
        id = saved.portrait_id;
        setPermanentId(id);
      } catch {
        // бэк без save-эндпоинта — падаем на текущий URL
        id = null;
      }
    }
    const link =
      id !== null && id !== ""
        ? `${window.location.origin}/p/${id}`
        : window.location.href;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setCopiedOnce(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard может быть недоступен — молча игнорируем
    }
  }, [permanentId, isDemo]);

  const downloadCard = useCallback(async () => {
    setRendering(true);
    setCardError(null);
    try {
      const blob = await renderShareCard(portrait, permanentId);
      downloadBlob(
        blob,
        `bittersweet-${permanentId ?? "portrait"}.png`,
      );
    } catch (err) {
      setCardError(
        err instanceof Error ? err.message : "Не удалось нарисовать карточку.",
      );
    } finally {
      setRendering(false);
    }
  }, [portrait, permanentId]);

  const percentile = portrait.bittersweet.percentile;
  const highlights = portrait.highlights ?? [];

  /* ---- v0.13 (SPEC-v13-pulse §B): честное покрытие в шапке ----
   * Показываем «· проанализировано N из M» ТОЛЬКО когда кандидатов было
   * больше, чем проанализировано; старые payload'ы без coverage (или с
   * кривым coverage) — приписки просто нет. */
  const coverage = portrait.coverage;
  const showCoverage =
    coverage != null &&
    typeof coverage.candidates === "number" &&
    typeof coverage.analyzed === "number" &&
    Number.isFinite(coverage.candidates) &&
    Number.isFinite(coverage.analyzed) &&
    coverage.candidates > coverage.analyzed;

  /* ---- v0.6, B2: переключатель «Карта | Галактика» ---- */

  const [scene, setScene] = useState<"map" | "galaxy" | "shape">("map");
  // старые портреты без points[].features — переключатель скрыт
  const galaxyAvailable = useMemo(
    () => hasGalaxyFeatures(portrait.points),
    [portrait.points],
  );

  /* ---- v0.11 (SPEC-v11-observatory §B): обсерватория ---- */

  // хук живёт здесь (не в секции): tsne-координаты режима «Форма» нужны
  // сегмент-контролу карты выше секции; fetch всё равно ленивый — его
  // запускает IntersectionObserver внутри ObservatorySection
  const observatory = useObservatory(permanentId, portrait.points.length);
  // «Форма» доступна, когда обсерватория загрузилась и tsne валиден
  const shapeCoords = observatory.data?.tsne ?? null;
  const effectiveScene =
    scene === "shape" && shapeCoords === null ? "map" : scene;

  // v0.10 §A: компактный график динамики — только если timeline пригоден
  const hasTimeline = useMemo(
    () => prepareBuckets(portrait.timeline).length >= 2,
    [portrait.timeline],
  );

  /* ---- v0.12 (SPEC-v12-human §B/§D): «История | Портрет» и квиз ---- */

  // история и квиз требуют постоянный id (роуты /story и PRNG квиза)
  const storyAvailable = permanentId !== null && permanentId !== "";
  const [view, setView] = useState<ViewMode>(
    defaultView === "story" ? "story" : "portrait",
  );
  // явный выбор пользователя (localStorage) перекрывает дефолт страницы;
  // читаем в эффекте — SSR-разметка не должна зависеть от браузера
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(VIEW_STORAGE_KEY);
      if (saved === "story" || saved === "portrait") setView(saved);
    } catch {
      // приватный режим — живём с дефолтом
    }
  }, []);
  const switchView = useCallback((next: ViewMode) => {
    setView(next);
    try {
      window.localStorage.setItem(VIEW_STORAGE_KEY, next);
    } catch {
      // приватный режим — просто не запомним
    }
  }, []);
  const effectiveView: ViewMode =
    storyAvailable && view === "story" ? "story" : "portrait";

  const [quizOpen, setQuizOpen] = useState(false);
  const observatoryLoad = observatory.load;
  const openQuiz = useCallback(() => {
    // вопросы про ворон и тональность питаются обсерваторией — дожимаем
    observatoryLoad();
    setQuizOpen(true);
  }, [observatoryLoad]);

  // CTA «пульт» из финала истории: в «Портрет» и плавно к пульту
  const goConsole = useCallback(() => {
    switchView("portrait");
    const reduced =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.setTimeout(() => {
      document.getElementById("taste-console")?.scrollIntoView({
        behavior: reduced ? "auto" : "smooth",
        block: "start",
      });
    }, 150);
  }, [switchView]);

  /* ---- v0.6, B3: превью на хайлайтах и примерах кластеров ---- */

  // кандидаты на ленивый resolve: видимые строки без известного preview_url
  const resolveCandidates = useMemo(() => {
    const seen = new Set<string>();
    const missing: string[] = [];
    const push = (label: string) => {
      if (label !== "" && !seen.has(label)) {
        seen.add(label);
        missing.push(label);
      }
    };
    for (const highlight of highlights) {
      if (highlight.preview_url == null) push(highlight.track);
    }
    for (const cluster of portrait.clusters) {
      for (const example of exampleRows(cluster)) {
        if (example.preview_url == null) push(example.label);
      }
    }
    return missing.slice(0, 24); // лимит батча /api/preview/resolve
    // highlights — производное от portrait, отдельная зависимость не нужна
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [portrait]);

  const { stateFor, onLazy } = usePreviewResolver(resolveCandidates, player);
  const preview: PreviewContext = useMemo(
    () => ({ player, stateFor, onLazy }),
    [player, stateFor, onLazy],
  );

  return (
    <>
      <header className="portrait-header">
        <h1>{title}</h1>
        <div className="portrait-actions">
          <YoutubeAccountChip />
          {/* v0.9, §C: шапка портрета ведёт на «Мои портреты» */}
          <Link href="/portraits" className="btn btn-ghost btn-small">
            Мои портреты
          </Link>
          <span className="share-wrap">
            <button
              type="button"
              className="btn btn-ghost btn-small"
              onClick={() => void share()}
            >
              Поделиться
            </button>
            {copied && (
              <span className="share-tooltip" role="status">
                ссылка скопирована
              </span>
            )}
          </span>
          <button
            type="button"
            className="btn btn-ghost btn-small"
            onClick={() => void downloadCard()}
            disabled={rendering}
          >
            {rendering && <span className="btn-spinner" aria-hidden />}
            {rendering ? "Рисуем…" : "Скачать карточку"}
          </button>
          {headerExtra}
        </div>
      </header>
      <p className="portrait-meta">
        <span className="mono">{portrait.n_tracks}</span> треков
        {showCoverage && (
          <>
            {" "}
            ·{" "}
            <span
              className="coverage-note"
              title="для остальных не нашлось 30-сек превью в открытых каталогах — чаще всего это редкие или региональные треки"
            >
              проанализировано{" "}
              <span className="mono">{coverage.analyzed}</span> из{" "}
              <span className="mono">{coverage.candidates}</span>
            </span>
          </>
        )}{" "}
        · {portrait.clusters.length} {ruMoodWord(portrait.clusters.length)}
        {modeNote}
      </p>
      {copiedOnce && (
        <p className="share-hint">
          Отправь другу и сравните вкус →{" "}
          <Link href="/compare">/compare</Link>
        </p>
      )}
      {cardError !== null && (
        <p className="share-hint share-hint-error" role="alert">
          {cardError}
        </p>
      )}

      {/* v0.12 §B: переключатель вида + кнопка квиза — только у портретов
          с постоянным id (история и квиз живут на /story и PRNG от id) */}
      {storyAvailable && (
        <div className="view-toggle-row">
          <div
            className="scene-toggle view-toggle"
            role="group"
            aria-label="Режим просмотра портрета"
          >
            <button
              type="button"
              className={
                effectiveView === "story"
                  ? "scene-tab scene-tab-active"
                  : "scene-tab"
              }
              aria-pressed={effectiveView === "story"}
              onClick={() => switchView("story")}
            >
              История
            </button>
            <button
              type="button"
              className={
                effectiveView === "portrait"
                  ? "scene-tab scene-tab-active"
                  : "scene-tab"
              }
              aria-pressed={effectiveView === "portrait"}
              onClick={() => switchView("portrait")}
            >
              Портрет
            </button>
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-small quiz-open-btn"
            onClick={openQuiz}
          >
            🎯 Угадай себя
          </button>
        </div>
      )}

      {effectiveView === "story" && permanentId !== null ? (
        <StoryView
          portraitId={permanentId}
          portrait={portrait}
          player={player}
          onOpenQuiz={openQuiz}
          onDig={goConsole}
          onShowPortrait={() => switchView("portrait")}
        />
      ) : (
        <>
      {(galaxyAvailable || shapeCoords !== null) && (
        <div className="scene-toggle" role="group" aria-label="Вид сцены">
          <button
            type="button"
            className={
              effectiveScene === "map"
                ? "scene-tab scene-tab-active"
                : "scene-tab"
            }
            aria-pressed={effectiveScene === "map"}
            onClick={() => setScene("map")}
          >
            Карта
          </button>
          {galaxyAvailable && (
            <button
              type="button"
              className={
                effectiveScene === "galaxy"
                  ? "scene-tab scene-tab-active"
                  : "scene-tab"
              }
              aria-pressed={effectiveScene === "galaxy"}
              onClick={() => setScene("galaxy")}
            >
              Галактика
            </button>
          )}
          {/* v0.11 §B5: «Форма» появляется после загрузки обсерватории */}
          {shapeCoords !== null && (
            <button
              type="button"
              className={
                effectiveScene === "shape"
                  ? "scene-tab scene-tab-active"
                  : "scene-tab"
              }
              aria-pressed={effectiveScene === "shape"}
              onClick={() => setScene("shape")}
            >
              Форма
            </button>
          )}
        </div>
      )}
      {effectiveScene === "galaxy" && galaxyAvailable ? (
        <GalaxyView
          points={portrait.points}
          clusters={portrait.clusters}
          onAim={canDiscover ? discoverPoint : undefined}
          // v0.10 §B: сид пылинок атмосферы — от id портрета
          seed={permanentId ?? undefined}
        />
      ) : effectiveScene === "shape" && shapeCoords !== null ? (
        // v0.11 §B5: та же карта точек на t-SNE-координатах; только
        // просмотр — прицел discovery и маркеры 🎯 в этом режиме выключены
        <ScatterMap
          points={portrait.points}
          clusters={portrait.clusters}
          coords={shapeCoords}
        />
      ) : (
        <ScatterMap
          points={portrait.points}
          clusters={portrait.clusters}
          markers={pointMarkers}
          onAim={canDiscover ? discoverPoint : undefined}
        />
      )}

      {/* v0.10 §A: компактная динамика вкуса по датам лайков — под картой */}
      {hasTimeline && (
        <section className="timeline-under-map">
          <h2 className="section-title">Динамика вкуса</h2>
          <TasteTimeline timeline={portrait.timeline} variant="compact" />
        </section>
      )}

      <h2 className="section-title">Настроения вкуса</h2>
      <div className="cluster-grid">
        {portrait.clusters.map((cluster, i) => (
          <ClusterCard
            key={i}
            cluster={cluster}
            onDiscover={canDiscover ? () => discover(i) : undefined}
            preview={preview}
          />
        ))}
      </div>

      {/* v0.12 §C: «Пульт вкуса» — секция над discovery */}
      <TasteConsole
        points={portrait.points}
        clusters={portrait.clusters}
        player={player}
        onDig={canDiscover ? discoverPoint : undefined}
      />

      {(panelIndices.length > 0 || pointPanels.length > 0) && (
        <>
          <h2 className="section-title">Ещё такого</h2>
          <div className="discover-panels">
            {panelIndices.map((index) => (
              <ClusterDiscovery
                key={index}
                cluster={portrait.clusters[index]}
                clusterIndex={index}
                runToken={discoverRuns[index] ?? 0}
                initial={savedMap.get(index)}
                resolvePortraitId={resolvePortraitId}
                player={player}
              />
            ))}
            {/* v0.9: панели discovery по точкам карты/галактики */}
            {pointPanels.map(([key, panel]) => (
              <PointDiscovery
                key={key}
                x={panel.x}
                y={panel.y}
                runToken={panel.runToken}
                initial={panel.initial}
                resolvePortraitId={resolvePortraitId}
                player={player}
                onStatus={(status, count) =>
                  handlePointStatus(key, status, count)
                }
              />
            ))}
          </div>
        </>
      )}

      {highlights.length > 0 && (
        <>
          <h2 className="section-title">Самые-самые</h2>
          <div className="highlight-row">
            {highlights.map((highlight, i) => (
              <HighlightChip
                key={`${highlight.kind}-${i}`}
                highlight={highlight}
                preview={preview}
              />
            ))}
          </div>
        </>
      )}

      <h2 className="section-title">Биттерсвит</h2>
      <div className="bittersweet-card">
        <div className="bittersweet-head">
          <span className="bittersweet-count">
            {portrait.bittersweet.share}%
          </span>
          <span>
            <span className="mono">{portrait.bittersweet.count}</span> треков —
            грустные по тональности, но энергичные по звуку
          </span>
        </div>
        {typeof percentile === "number" && (
          <p className="bittersweet-percentile">
            биттерсвитнее, чем у <span className="mono">{percentile}%</span>{" "}
            портретов
          </p>
        )}
        <p className="bittersweet-desc">
          Биттерсвит — фирменная смесь минора и драйва: музыка, под которую
          одновременно грустно и хочется двигаться.
        </p>
        <ul className="bittersweet-top">
          {portrait.bittersweet.top.map((track) => (
            <li key={track}>{track}</li>
          ))}
        </ul>
      </div>

      {/* v0.11 (SPEC-v11-observatory §B): аналитический этаж — после
          биттерсвита; грузится лениво по доскроллу, старый бэк без роута
          прячет секцию молча. Только у сохранённых портретов (есть id). */}
      {permanentId !== null && permanentId !== "" && (
        <ObservatorySection
          observatory={observatory}
          points={portrait.points}
          clusters={portrait.clusters}
          player={player}
        />
      )}

      <h2 className="section-title">Сигнатура</h2>
      <div className="fingerprint-row">
        <div className="fingerprint-stat">
          <span className="median-value">
            {portrait.fingerprint.tempo_median}
          </span>{" "}
          <div className="median-key">медианный bpm</div>
        </div>
        <div className="fingerprint-stat">
          <span className="median-value">
            {portrait.fingerprint.minor_share}%
          </span>
          <div className="median-key">доля минора</div>
        </div>
        <div className="fingerprint-stat">
          <span className="median-value">
            {portrait.fingerprint.brightness_mean}
          </span>
          <div className="median-key">средняя яркость, Гц</div>
        </div>
      </div>

      {/* v0.6, B3: атрибуция превью — один раз, в футере портрета */}
      <p className="portrait-attribution">превью: iTunes/Deezer</p>
        </>
      )}

      {/* v0.12 §D: квиз «Угадай себя» — модалка поверх любого вида */}
      {quizOpen && permanentId !== null && permanentId !== "" && (
        <TasteQuiz
          portraitId={permanentId}
          portrait={portrait}
          observatory={observatory}
          player={player}
          onClose={() => setQuizOpen(false)}
        />
      )}

      {/* v0.9, §A: мини-плеер радио — ОДИН на приложение, живёт тут,
          в верхнем клиентском компоненте портрета; виден только при
          активной очереди (player.queue) */}
      <MiniPlayer player={player} />
    </>
  );
}
