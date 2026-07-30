"use client";

/**
 * v0.9 (SPEC-v09-features §C): «Мои портреты» — сетка сохранённых портретов
 * (GET /api/portraits) + блок «Эволюция вкуса» при ≥2 портретах: SVG-график
 * руками (без библиотек) с линиями tempo_median / minor_share /
 * bittersweet_share по created_at и дельта-чипами первый-vs-последний.
 *
 * v0.10 (SPEC-v10-critique §A, «эволюция скучная»): главный блок теперь —
 * «Динамика вкуса за всё время» по timeline НОВЕЙШЕГО портрета с датами
 * лайков (обычно «лайки YouTube»): реальная ось времени, а не даты пересчёта.
 * Старый график «по портретам» ПОНИЖЕН: свёрнут в details, отфильтрован к
 * одному источнику (только лайки/Takeout, демо и плейлисты исключены) и
 * показывается только при ≥3 точках на ≥3 разных днях.
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, fetchPortraitsList, fetchSavedPortrait } from "@/lib/api";
import TasteTimeline, { prepareBuckets } from "@/components/taste-timeline";
import type { PortraitListItem, TimelineBucket } from "@/lib/types";

/* ---------- Форматирование ---------- */

function formatDate(iso: string | null | undefined): string | null {
  if (iso === undefined || iso === null || iso === "") return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function formatDateShort(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "short",
  });
}

/** Дельта со знаком: +7, −3, ±0 (для чипов «за период»). */
function formatDelta(value: number): string {
  const rounded = Math.round(value);
  if (rounded > 0) return `+${rounded}`;
  if (rounded < 0) return `−${Math.abs(rounded)}`;
  return "±0";
}

/* ---------- Эволюция вкуса: SVG-график без библиотек ---------- */

const CHART_W = 640;
const CHART_H = 240;
const CHART_PAD = { top: 22, right: 58, bottom: 30, left: 58 };

interface Series {
  name: string;
  unit: string;
  /** «пп» для долей в дельта-чипах, bpm для темпа */
  deltaUnit: string;
  color: string;
  value: (p: PortraitListItem) => number | null;
}

const SERIES: Series[] = [
  {
    name: "темп",
    unit: "bpm",
    deltaUnit: "bpm",
    color: "#7f77dd",
    value: (p) =>
      typeof p.fingerprint?.tempo_median === "number"
        ? p.fingerprint.tempo_median
        : null,
  },
  {
    name: "минор",
    unit: "%",
    deltaUnit: "пп",
    color: "#6a9ed9",
    value: (p) =>
      typeof p.fingerprint?.minor_share === "number"
        ? p.fingerprint.minor_share
        : null,
  },
  {
    name: "биттерсвит",
    unit: "%",
    deltaUnit: "пп",
    color: "#c76a9e",
    value: (p) =>
      typeof p.bittersweet_share === "number" ? p.bittersweet_share : null,
  },
];

/** Значения серии → 0..100 (min-max по серии; все равны → середина). */
function normalizeTo100(values: number[]): number[] {
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (!(max > min)) return values.map(() => 50);
  return values.map((v) => ((v - min) / (max - min)) * 100);
}

/**
 * Раздвигает y-координаты подписей на краях графика, чтобы они не
 * наслаивались: серии часто сходятся в одну точку после нормировки.
 */
function spreadLabelYs(ys: number[], minGap = 14): number[] {
  const order = ys
    .map((y, i) => [y, i] as const)
    .sort((a, b) => a[0] - b[0]);
  const spread = order.map(([y]) => y);
  for (let i = 1; i < spread.length; i++) {
    if (spread[i] - spread[i - 1] < minGap) {
      spread[i] = spread[i - 1] + minGap;
    }
  }
  // не вылезаем за низ графика — сдвигаем пачку обратно вверх
  const overflow = spread[spread.length - 1] - (CHART_H - CHART_PAD.bottom);
  if (overflow > 0) {
    for (let i = spread.length - 1; i >= 0; i--) {
      spread[i] -= overflow;
      if (i > 0 && spread[i] - spread[i - 1] >= minGap) break;
    }
  }
  const out = new Array<number>(ys.length).fill(0);
  order.forEach(([, sourceIndex], k) => {
    out[sourceIndex] = spread[k];
  });
  return out;
}

function chartX(i: number, positions: number[]): number {
  return (
    CHART_PAD.left +
    positions[i] * (CHART_W - CHART_PAD.left - CHART_PAD.right)
  );
}

function chartY(norm: number): number {
  return (
    CHART_H -
    CHART_PAD.bottom -
    (norm / 100) * (CHART_H - CHART_PAD.top - CHART_PAD.bottom)
  );
}

function EvolutionChart({ items }: { items: PortraitListItem[] }) {
  // хронология: старые слева, новые справа
  const chrono = useMemo(() => {
    const withDates = items.filter((p) => {
      const t = new Date(p.created_at ?? "").getTime();
      if (Number.isNaN(t)) return false;
      return SERIES.every((s) => s.value(p) !== null);
    });
    return [...withDates].sort(
      (a, b) =>
        new Date(a.created_at ?? "").getTime() -
        new Date(b.created_at ?? "").getTime(),
    );
  }, [items]);

  // x-позиции 0..1: по времени; вырожденный интервал → равномерно
  const positions = useMemo(() => {
    const times = chrono.map((p) => new Date(p.created_at ?? "").getTime());
    const span = times[times.length - 1] - times[0];
    if (!(span > 0)) return chrono.map((_, i) => i / Math.max(1, chrono.length - 1));
    return times.map((t) => (t - times[0]) / span);
  }, [chrono]);

  if (chrono.length < 2) return null;

  const first = chrono[0];
  const last = chrono[chrono.length - 1];

  // серии считаем заранее: подписи на краях раздвигаются сообща
  const seriesData = SERIES.map((s) => {
    const raw = chrono.map((p) => s.value(p) as number);
    return { s, raw, norm: normalizeTo100(raw) };
  });
  const firstLabelYs = spreadLabelYs(
    seriesData.map((d) => chartY(d.norm[0])),
  );
  const lastLabelYs = spreadLabelYs(
    seriesData.map((d) => chartY(d.norm[d.norm.length - 1])),
  );

  return (
    <section className="evolution-card">
      <h2 className="section-title evolution-title">Эволюция вкуса</h2>
      <div className="evolution-legend">
        {SERIES.map((s) => (
          <span key={s.name} className="evolution-legend-item">
            <span className="cluster-dot" style={{ background: s.color }} aria-hidden />
            {s.name}
          </span>
        ))}
      </div>
      <svg
        className="evolution-svg"
        viewBox={`0 0 ${CHART_W} ${CHART_H}`}
        role="img"
        aria-label="Эволюция вкуса: темп, доля минора и биттерсвита по датам портретов"
      >
        {/* сетка (нормированная шкала 0..100) */}
        {[0, 50, 100].map((v) => (
          <line
            key={v}
            className="grid-line"
            x1={CHART_PAD.left}
            y1={chartY(v)}
            x2={CHART_W - CHART_PAD.right}
            y2={chartY(v)}
          />
        ))}
        {/* линии серий: каждая нормирована в 0..100 отдельно */}
        {seriesData.map(({ s, raw, norm }, si) => {
          const pts = norm
            .map((v, i) => `${chartX(i, positions).toFixed(1)},${chartY(v).toFixed(1)}`)
            .join(" ");
          return (
            <g key={s.name}>
              <polyline
                points={pts}
                fill="none"
                stroke={s.color}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
              {norm.map((v, i) => (
                <circle
                  key={i}
                  cx={chartX(i, positions)}
                  cy={chartY(v)}
                  r={3.2}
                  fill={s.color}
                >
                  <title>{`${formatDate(chrono[i].created_at) ?? ""}: ${s.name} ${Math.round(raw[i])} ${s.unit}`}</title>
                </circle>
              ))}
              {/* подписи значений на концах линии — tabular-nums,
                  y раздвинуты, чтобы серии не наслаивались */}
              <text
                className="chart-num"
                x={chartX(0, positions) - 8}
                y={firstLabelYs[si]}
                textAnchor="end"
                dominantBaseline="central"
                fill={s.color}
              >
                {Math.round(raw[0])}
              </text>
              <text
                className="chart-num"
                x={chartX(norm.length - 1, positions) + 8}
                y={lastLabelYs[si]}
                textAnchor="start"
                dominantBaseline="central"
                fill={s.color}
              >
                {Math.round(raw[raw.length - 1])} {s.unit}
              </text>
            </g>
          );
        })}
        {/* даты первого и последнего портрета */}
        <text
          className="chart-date"
          x={CHART_PAD.left}
          y={CHART_H - 8}
          textAnchor="start"
        >
          {formatDateShort(first.created_at ?? "")}
        </text>
        <text
          className="chart-date"
          x={CHART_W - CHART_PAD.right}
          y={CHART_H - 8}
          textAnchor="end"
        >
          {formatDateShort(last.created_at ?? "")}
        </text>
      </svg>
      {/* дельта-чипы: первый портрет vs последний */}
      <div className="evolution-deltas">
        <span>за период:</span>
        {SERIES.map((s) => {
          const delta =
            (s.value(last) as number) - (s.value(first) as number);
          return (
            <span key={s.name} className="delta-chip">
              {s.name} {formatDelta(delta)} {s.deltaUnit}
            </span>
          );
        })}
      </div>
    </section>
  );
}

/* ---------- v0.10 §A: главный блок «Динамика вкуса за всё время» ---------- */

/** Найденный носитель timeline: сводка из списка + сами бакеты. */
interface DynamicsSource {
  item: PortraitListItem;
  timeline: TimelineBucket[];
}

/** Сколько новейших портретов-кандидатов опрашиваем за полным payload. */
const TIMELINE_PROBE_LIMIT = 6;

/** У timeline есть смысл от двух валидных бакетов. */
function usableTimeline(
  timeline: TimelineBucket[] | null | undefined,
): timeline is TimelineBucket[] {
  return prepareBuckets(timeline).length >= 2;
}

/**
 * Ищет НОВЕЙШИЙ портрет с timeline. Бэк v0.10 отдаёт в сводке флаг
 * has_timeline — тогда тянем полный payload только для отмеченных
 * (список уже отсортирован «новые сверху»). Старый бэк без флага —
 * пробуем кандидатов сами: сперва источники с датами лайков (лайки
 * YouTube / Takeout), демо и плейлисты — в хвосте. Ничего не нашли
 * или бэк старый — вместо блока честный empty-state (см. вызывающий код).
 */
async function findDynamicsSource(
  items: PortraitListItem[],
  isCancelled: () => boolean,
): Promise<DynamicsSource | null> {
  // сводка уже содержит сам timeline — без лишних запросов
  for (const item of items) {
    if (usableTimeline(item.timeline)) {
      return { item, timeline: item.timeline };
    }
  }

  const flagKnown = items.some((item) => typeof item.has_timeline === "boolean");
  let candidates: PortraitListItem[];
  if (flagKnown) {
    // бэк v0.10: точный список носителей timeline (может быть пуст)
    candidates = items.filter((item) => item.has_timeline === true);
  } else {
    // старый бэк: датами лайков живут лайки YouTube и Takeout
    const likely: PortraitListItem[] = [];
    const rest: PortraitListItem[] = [];
    for (const item of items) {
      if (/лайк|takeout/i.test(item.source_label ?? "")) likely.push(item);
      else rest.push(item);
    }
    candidates = [...likely, ...rest];
  }

  for (const item of candidates.slice(0, TIMELINE_PROBE_LIMIT)) {
    if (isCancelled()) return null;
    try {
      const portrait = await fetchSavedPortrait(item.id);
      if (usableTimeline(portrait.timeline)) {
        return { item, timeline: portrait.timeline };
      }
    } catch {
      // портрет мог исчезнуть / старый бэк — пробуем следующего кандидата
    }
  }
  return null;
}

function TasteDynamicsCard({ source }: { source: DynamicsSource }) {
  const { item, timeline } = source;
  const date = formatDate(item.created_at);
  return (
    <section className="evolution-card timeline-card">
      <h2 className="section-title evolution-title">
        Динамика вкуса за всё время
      </h2>
      <p className="timeline-source">
        по датам лайков · {item.source_label ?? "портрет"}
        {typeof item.n_tracks === "number" ? (
          <>
            {" · "}
            <span className="mono">{item.n_tracks}</span> треков
          </>
        ) : null}
        {date !== null ? ` · портрет от ${date}` : ""}
      </p>
      <TasteTimeline timeline={timeline} />
    </section>
  );
}

/**
 * Честный empty-state блока динамики (DoD v0.10 §A: график ИЛИ объяснение).
 * Ни у одного сохранённого портрета нет пригодного timeline — обычно потому,
 * что портреты построены до v0.10 (даты лайков тогда не сохранялись) или из
 * источников без дат (демо, часть плейлистов). Говорим прямо и подсказываем,
 * как включить динамику — без скрытых лимитов.
 */
function TasteDynamicsEmpty() {
  return (
    <section className="evolution-card timeline-card">
      <h2 className="section-title evolution-title">
        Динамика вкуса за всё время
      </h2>
      <p className="timeline-empty-text">
        У сохранённых портретов пока нет дат лайков — они построены до того,
        как мы начали их запоминать, или из источников без дат (демо,
        плейлисты). Дат должно быть у большинства треков, и покрывать они
        должны хотя бы три месяца.
      </p>
      <p className="timeline-empty-text">
        <Link href="/">Пересобери портрет по лайкам YouTube или Takeout</Link>{" "}
        — и здесь появится график: как менялись минор, биттерсвит и темп по
        реальным датам твоих лайков.
      </p>
    </section>
  );
}

/* ---------- Карточка портрета ---------- */

function PortraitCard({ item }: { item: PortraitListItem }) {
  const date = formatDate(item.created_at);
  const stats: string[] = [];
  if (typeof item.n_tracks === "number") stats.push(`${item.n_tracks} треков`);
  if (typeof item.bittersweet_share === "number") {
    stats.push(`биттерсвит ${item.bittersweet_share}%`);
  }
  return (
    <Link href={`/p/${encodeURIComponent(item.id)}`} className="portrait-card">
      <span className="portrait-card-emoji" aria-hidden>
        {item.top_archetype?.emoji ?? "🎵"}
      </span>
      <span className="portrait-card-name">
        {item.top_archetype?.name ?? "портрет"}
      </span>
      <span className="portrait-card-meta">
        {item.source_label ?? "портрет"}
        {date !== null ? ` · ${date}` : ""}
      </span>
      {stats.length > 0 && (
        <span className="portrait-card-stats mono">{stats.join(" · ")}</span>
      )}
    </Link>
  );
}

/* ---------- Страница ---------- */

type PageStatus = "loading" | "ready" | "error";

export default function PortraitsPage() {
  const [items, setItems] = useState<PortraitListItem[]>([]);
  const [status, setStatus] = useState<PageStatus>("loading");
  const [message, setMessage] = useState("");
  const [attempt, setAttempt] = useState(0);
  // v0.10 §A: носитель «динамики за всё время» (новейший портрет с
  // timeline); null — поиск ещё идёт, "none" — закончился впустую
  const [dynamics, setDynamics] = useState<DynamicsSource | "none" | null>(
    null,
  );

  useEffect(() => {
    void attempt;
    let cancelled = false;
    setStatus("loading");
    fetchPortraitsList()
      .then((list) => {
        if (cancelled) return;
        setItems(list.filter((p) => typeof p.id === "string" && p.id !== ""));
        setStatus("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // 404 — старый бэк без роута /api/portraits (v0.9): объясняем человечески
        if (err instanceof ApiError && err.status === 404) {
          setMessage(
            "Запущена старая версия бэкенда — у неё нет списка портретов. Обнови и перезапусти бэкенд.",
          );
        } else {
          setMessage(
            err instanceof Error ? err.message : "Неизвестная ошибка.",
          );
        }
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  // v0.10 §A: после списка молча ищем новейший портрет с timeline
  useEffect(() => {
    if (status !== "ready" || items.length === 0) {
      setDynamics(null);
      return;
    }
    let cancelled = false;
    void findDynamicsSource(items, () => cancelled).then((found) => {
      // не нашли ни одного носителя timeline — честный empty-state
      if (!cancelled) setDynamics(found ?? "none");
    });
    return () => {
      cancelled = true;
    };
  }, [status, items]);

  /**
   * v0.10 §A: данные пониженного графика «по портретам» — только один
   * источник с датами лайков (лайки/Takeout; демо и плейлисты исключены,
   * у них created_at — дата пересчёта, а не история вкуса), и только если
   * ≥3 портретов на ≥3 разных днях. Иначе график скрыт целиком.
   */
  const evolutionItems = useMemo(() => {
    const allowed = items.filter(
      (p) =>
        typeof p.source_label === "string" &&
        /лайк|takeout/i.test(p.source_label),
    );
    const groups = new Map<string, PortraitListItem[]>();
    for (const p of allowed) {
      const key = p.source_label as string;
      const group = groups.get(key) ?? [];
      group.push(p);
      groups.set(key, group);
    }
    let best: PortraitListItem[] = [];
    for (const group of groups.values()) {
      if (group.length > best.length) best = group;
    }
    const days = new Set(
      best
        .map((p) => (p.created_at ?? "").slice(0, 10))
        .filter((day) => day !== ""),
    );
    if (best.length < 3 || days.size < 3) return [];
    return best;
  }, [items]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  let content: React.ReactNode;
  if (status === "loading") {
    content = (
      <div className="state-box">
        <div className="spinner" aria-hidden />
        <h2>Открываем твои портреты…</h2>
        <p>Собираем сохранённые портреты и считаем эволюцию вкуса.</p>
      </div>
    );
  } else if (status === "error") {
    content = (
      <div className="state-box">
        <h2>Не получилось загрузить портреты</h2>
        <p>{message !== "" ? message : "Пустой ответ бэкенда."}</p>
        <div className="state-actions">
          <button type="button" className="btn btn-primary" onClick={retry}>
            Попробовать снова
          </button>
          <Link href="/" className="btn btn-ghost">
            На главную
          </Link>
        </div>
      </div>
    );
  } else if (items.length === 0) {
    // пустое состояние — зовём построить первый портрет
    content = (
      <div className="state-box">
        <h2>Пока ни одного портрета</h2>
        <p>
          Построй первый — по лайкам YouTube, плейлисту или демо-набору — и
          он появится здесь. С двух портретов начнёт рисоваться эволюция
          вкуса.
        </p>
        <div className="state-actions">
          <Link href="/" className="btn btn-primary">
            Построить первый портрет
          </Link>
        </div>
      </div>
    );
  } else {
    content = (
      <>
        <p className="portrait-meta">
          <span className="mono">{items.length}</span> сохранённых портретов ·
          новые сверху
        </p>
        {/* v0.10 §A: главный блок — динамика по датам ЛАЙКОВ;
            носителя нет — честный empty-state (пока ищем — ничего) */}
        {dynamics === "none" ? (
          <TasteDynamicsEmpty />
        ) : (
          dynamics !== null && <TasteDynamicsCard source={dynamics} />
        )}
        {/* старый график «по портретам» понижен: details + фильтр источника */}
        {evolutionItems.length >= 3 && (
          <details className="evolution-details">
            <summary>динамика по портретам</summary>
            <EvolutionChart items={evolutionItems} />
          </details>
        )}
        <div className="portraits-grid">
          {items.map((item) => (
            <PortraitCard key={item.id} item={item} />
          ))}
        </div>
      </>
    );
  }

  return (
    <main className="portrait-page container">
      <Link href="/" className="back-link">
        ← Bittersweet
      </Link>
      <header className="portrait-header">
        <h1>Мои портреты</h1>
      </header>
      {content}
    </main>
  );
}
