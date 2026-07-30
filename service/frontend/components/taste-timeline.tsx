"use client";

/**
 * v0.10 (SPEC-v10-critique §A): «Динамика вкуса за всё время» — SVG-график
 * по timeline портрета (бакеты по датам ЛАЙКОВ, строит бэк из liked_at).
 *
 * Реальная ось времени (месяцы/годы, x — середина периода бакета);
 * линии minor_share и bittersweet_share (%, левая ось), пунктиром tempo
 * (нормированный, правая ось в bpm); над бакетами — эмодзи топ-архетипа
 * периода; ховер бакета — тултип с цифрами. Дельта-чипы: первый период
 * vs последний («за N лет: минор −8 пп, темп +15 bpm…»).
 *
 * variant="compact" — сжатая версия под картой на странице портрета.
 * Без библиотек. Старый бэк без timeline — компонент просто не рендерится
 * (решает вызывающий код).
 */

import { useMemo, useState } from "react";
import type { TimelineBucket } from "@/lib/types";

/* ---------- Геометрия ---------- */

const W = 680;
const PAD_LEFT = 46;
const PAD_RIGHT = 52;
const PAD_BOTTOM = 30;

interface Geometry {
  h: number;
  padTop: number;
}

const GEOMETRY: Record<"full" | "compact", Geometry> = {
  full: { h: 260, padTop: 40 },
  compact: { h: 190, padTop: 20 },
};

/* ---------- Подготовка данных ---------- */

/** Бакет с распарсенными числами — мусор бэка отфильтрован заранее. */
interface Bucket {
  startMs: number;
  endMs: number;
  midMs: number;
  nTracks: number | null;
  tempo: number;
  minor: number;
  bittersweet: number;
  emoji: string | null;
  archetypeName: string | null;
  periodLabel: string;
}

const MONTH_FMT = new Intl.DateTimeFormat("ru-RU", {
  month: "short",
  year: "numeric",
});

function formatMonth(ms: number): string {
  // «июл. 2023 г.» → «июл 2023»
  return MONTH_FMT.format(new Date(ms)).replace(" г.", "").replace(".", "");
}

function periodLabel(startMs: number, endMs: number): string {
  const a = formatMonth(startMs);
  const b = formatMonth(endMs);
  return a === b ? a : `${a} – ${b}`;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** timeline бэка → валидные бакеты по возрастанию времени. */
export function prepareBuckets(
  timeline: TimelineBucket[] | null | undefined,
): Bucket[] {
  if (!Array.isArray(timeline)) return [];
  const out: Bucket[] = [];
  for (const raw of timeline) {
    const startMs = new Date(raw.period_start ?? "").getTime();
    const endMs = new Date(raw.period_end ?? "").getTime();
    const tempo = num(raw.tempo_median);
    const minor = num(raw.minor_share);
    const bittersweet = num(raw.bittersweet_share);
    if (
      Number.isNaN(startMs) ||
      Number.isNaN(endMs) ||
      tempo === null ||
      minor === null ||
      bittersweet === null
    ) {
      continue;
    }
    out.push({
      startMs,
      endMs,
      midMs: (startMs + endMs) / 2,
      nTracks: num(raw.n_tracks),
      tempo,
      minor,
      bittersweet,
      emoji: raw.top_archetype?.emoji ?? null,
      archetypeName: raw.top_archetype?.name ?? null,
      periodLabel: periodLabel(startMs, endMs),
    });
  }
  out.sort((a, b) => a.midMs - b.midMs);
  return out;
}

/* ---------- Шкалы ---------- */

/** min..max серии → [lo, hi], округлённые к десяткам с воздухом. */
function niceDomain(values: number[], clampLo?: number, clampHi?: number): [number, number] {
  let lo = Math.floor((Math.min(...values) - 6) / 10) * 10;
  let hi = Math.ceil((Math.max(...values) + 6) / 10) * 10;
  if (clampLo !== undefined) lo = Math.max(clampLo, lo);
  if (clampHi !== undefined) hi = Math.min(clampHi, hi);
  if (hi <= lo) {
    lo -= 10;
    hi += 10;
  }
  return [lo, hi];
}

/** Дельта со знаком: +7, −3, ±0. */
function formatDelta(value: number): string {
  const rounded = Math.round(value);
  if (rounded > 0) return `+${rounded}`;
  if (rounded < 0) return `−${Math.abs(rounded)}`;
  return "±0";
}

/** Человеческий охват периода: «за 3 года», «за год», «за 8 мес.». */
function spanLabel(startMs: number, endMs: number): string {
  const months = Math.max(1, Math.round((endMs - startMs) / (30.44 * 86400e3)));
  if (months >= 21) {
    const years = Math.round(months / 12);
    const tail = years % 10;
    const teen = years % 100;
    const word =
      tail === 1 && teen !== 11
        ? "год"
        : tail >= 2 && tail <= 4 && (teen < 12 || teen > 14)
          ? "года"
          : "лет";
    return `за ${years} ${word}`;
  }
  if (months >= 11) return "за год";
  return `за ${months} мес.`;
}

/* ---------- Компонент ---------- */

/** Цвета серий — те же, что в «эволюции по портретам» (/portraits). */
const COLOR_TEMPO = "#7f77dd";
const COLOR_MINOR = "#6a9ed9";
const COLOR_BITTERSWEET = "#c76a9e";

export default function TasteTimeline({
  timeline,
  variant = "full",
}: {
  timeline: TimelineBucket[] | null | undefined;
  /** compact — сжатая версия под картой портрета */
  variant?: "full" | "compact";
}) {
  const buckets = useMemo(() => prepareBuckets(timeline), [timeline]);
  const [hover, setHover] = useState<number | null>(null);

  if (buckets.length < 2) return null;

  const { h: H, padTop } = GEOMETRY[variant];
  const compact = variant === "compact";

  const t0 = buckets[0].midMs;
  const t1 = buckets[buckets.length - 1].midMs;
  const span = t1 - t0;
  const x = (ms: number): number =>
    PAD_LEFT +
    (span > 0 ? (ms - t0) / span : 0.5) * (W - PAD_LEFT - PAD_RIGHT);

  // левая ось — проценты (минор и биттерсвит делят шкалу)
  const [pctLo, pctHi] = niceDomain(
    buckets.flatMap((b) => [b.minor, b.bittersweet]),
    0,
    100,
  );
  const yPct = (v: number): number =>
    H - PAD_BOTTOM - ((v - pctLo) / (pctHi - pctLo)) * (H - padTop - PAD_BOTTOM);

  // правая ось — темп (нормированный в свой диапазон)
  const [bpmLo, bpmHi] = niceDomain(buckets.map((b) => b.tempo));
  const yBpm = (v: number): number =>
    H - PAD_BOTTOM - ((v - bpmLo) / (bpmHi - bpmLo)) * (H - padTop - PAD_BOTTOM);

  const line = (value: (b: Bucket) => number, y: (v: number) => number): string =>
    buckets
      .map((b) => `${x(b.midMs).toFixed(1)},${y(value(b)).toFixed(1)}`)
      .join(" ");

  // годовые засечки между первым и последним бакетом; слишком близкие
  // к краевым подписям дат — прячем, чтобы подписи не наслаивались
  const yearTicks: number[] = [];
  const firstYear = new Date(t0).getFullYear() + 1;
  const lastYear = new Date(t1).getFullYear();
  for (let year = firstYear; year <= lastYear; year++) {
    const ms = Date.UTC(year, 0, 1);
    if (ms <= t0 || ms >= t1) continue;
    const tickX = x(ms);
    if (tickX - PAD_LEFT < 64 || W - PAD_RIGHT - tickX < 64) continue;
    yearTicks.push(ms);
  }

  // эмодзи топ-архетипа над бакетами; при тесноте — только смены
  const emojiEvery = buckets.length <= 12;
  const emojiMarks = buckets
    .map((b, i) => ({ b, i }))
    .filter(({ b, i }) => {
      if (b.emoji === null) return false;
      if (emojiEvery) return true;
      return i === 0 || buckets[i - 1].emoji !== b.emoji;
    });

  // зоны ховера: границы — середины между соседними бакетами
  const zones = buckets.map((b, i) => {
    const left = i === 0 ? PAD_LEFT : (x(buckets[i - 1].midMs) + x(b.midMs)) / 2;
    const right =
      i === buckets.length - 1
        ? W - PAD_RIGHT
        : (x(b.midMs) + x(buckets[i + 1].midMs)) / 2;
    return { left, right };
  });

  const first = buckets[0];
  const last = buckets[buckets.length - 1];
  const hovered = hover !== null ? buckets[hover] : null;

  // позиция тултипа в % ширины сцены (svg тянется на всю ширину)
  const tipLeft =
    hovered !== null
      ? Math.min(84, Math.max(16, (x(hovered.midMs) / W) * 100))
      : 0;

  return (
    <div className={compact ? "timeline-block timeline-compact" : "timeline-block"}>
      <div className="timeline-legend" aria-hidden>
        <span className="timeline-legend-item">
          <span className="cluster-dot" style={{ background: COLOR_MINOR }} />
          минор %
        </span>
        <span className="timeline-legend-item">
          <span className="cluster-dot" style={{ background: COLOR_BITTERSWEET }} />
          биттерсвит %
        </span>
        <span className="timeline-legend-item">
          <span
            className="cluster-dot timeline-dot-dashed"
            style={{ borderColor: COLOR_TEMPO }}
          />
          темп bpm (пунктир, правая ось)
        </span>
      </div>
      <div
        className="timeline-stage"
        onMouseLeave={() => setHover(null)}
      >
        <svg
          className="timeline-svg"
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label="Динамика вкуса по датам лайков: доля минора, биттерсвита и темп по реальной оси времени"
        >
          {/* сетка процентов слева + подписи */}
          {[pctLo, (pctLo + pctHi) / 2, pctHi].map((v) => (
            <g key={`pct-${v}`}>
              <line
                className="grid-line"
                x1={PAD_LEFT}
                y1={yPct(v)}
                x2={W - PAD_RIGHT}
                y2={yPct(v)}
              />
              <text
                className="chart-num timeline-axis-num"
                x={PAD_LEFT - 8}
                y={yPct(v)}
                textAnchor="end"
                dominantBaseline="central"
              >
                {Math.round(v)}%
              </text>
            </g>
          ))}
          {/* подписи темпа справа */}
          {[bpmLo, bpmHi].map((v) => (
            <text
              key={`bpm-${v}`}
              className="chart-num timeline-axis-num"
              x={W - PAD_RIGHT + 8}
              y={yBpm(v)}
              textAnchor="start"
              dominantBaseline="central"
              fill={COLOR_TEMPO}
            >
              {Math.round(v)}
            </text>
          ))}
          {/* годовые засечки реальной оси времени */}
          {yearTicks.map((ms) => (
            <g key={`year-${ms}`}>
              <line
                className="grid-line timeline-year-line"
                x1={x(ms)}
                y1={padTop}
                x2={x(ms)}
                y2={H - PAD_BOTTOM}
              />
              <text
                className="chart-date"
                x={x(ms)}
                y={H - 10}
                textAnchor="middle"
              >
                {new Date(ms).getFullYear()}
              </text>
            </g>
          ))}
          {/* даты краёв */}
          <text className="chart-date" x={PAD_LEFT} y={H - 10} textAnchor="start">
            {formatMonth(first.startMs)}
          </text>
          <text
            className="chart-date"
            x={W - PAD_RIGHT}
            y={H - 10}
            textAnchor="end"
          >
            {formatMonth(last.endMs)}
          </text>
          {/* подсветка бакета под курсором */}
          {hover !== null && (
            <rect
              className="timeline-hover-band"
              x={zones[hover].left}
              y={padTop}
              width={Math.max(2, zones[hover].right - zones[hover].left)}
              height={H - padTop - PAD_BOTTOM}
            />
          )}
          {/* темп — пунктир по правой оси */}
          <polyline
            points={line((b) => b.tempo, yBpm)}
            fill="none"
            stroke={COLOR_TEMPO}
            strokeWidth={1.6}
            strokeDasharray="5 4"
            strokeLinejoin="round"
            strokeLinecap="round"
            opacity={0.85}
          />
          {/* минор и биттерсвит — сплошные по левой оси */}
          <polyline
            points={line((b) => b.minor, yPct)}
            fill="none"
            stroke={COLOR_MINOR}
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
          <polyline
            points={line((b) => b.bittersweet, yPct)}
            fill="none"
            stroke={COLOR_BITTERSWEET}
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
          {/* точки бакетов */}
          {buckets.map((b, i) => (
            <g key={`pt-${b.midMs}`}>
              <circle cx={x(b.midMs)} cy={yPct(b.minor)} r={hover === i ? 4 : 2.8} fill={COLOR_MINOR} />
              <circle
                cx={x(b.midMs)}
                cy={yPct(b.bittersweet)}
                r={hover === i ? 4 : 2.8}
                fill={COLOR_BITTERSWEET}
              />
              <circle cx={x(b.midMs)} cy={yBpm(b.tempo)} r={hover === i ? 3.4 : 2.2} fill={COLOR_TEMPO} />
            </g>
          ))}
          {/* эмодзи топ-архетипа периода — над бакетами */}
          {!compact &&
            emojiMarks.map(({ b, i }) => (
              <text
                key={`emoji-${i}`}
                className="timeline-emoji"
                x={x(b.midMs)}
                y={padTop - 14}
                textAnchor="middle"
              >
                {b.emoji}
              </text>
            ))}
          {/* невидимые зоны ховера по бакетам */}
          {zones.map((z, i) => (
            <rect
              key={`zone-${i}`}
              x={z.left}
              y={0}
              width={Math.max(1, z.right - z.left)}
              height={H}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
            />
          ))}
        </svg>
        {/* тултип бакета */}
        {hovered !== null && (
          <div
            className="timeline-tip"
            role="status"
            style={{ left: `${tipLeft}%` }}
          >
            <b>
              {hovered.emoji !== null ? `${hovered.emoji} ` : ""}
              {hovered.periodLabel}
            </b>
            {hovered.archetypeName !== null && (
              <span className="timeline-tip-arch">{hovered.archetypeName}</span>
            )}
            <span className="mono">
              минор {Math.round(hovered.minor)}% · биттерсвит{" "}
              {Math.round(hovered.bittersweet)}% · {Math.round(hovered.tempo)}{" "}
              bpm
              {hovered.nTracks !== null ? ` · ${hovered.nTracks} тр.` : ""}
            </span>
          </div>
        )}
      </div>
      {/* дельта-чипы: первый период vs последний */}
      <div className="evolution-deltas timeline-deltas">
        <span>{spanLabel(first.startMs, last.endMs)}:</span>
        <span className="delta-chip">
          минор {formatDelta(last.minor - first.minor)} пп
        </span>
        <span className="delta-chip">
          биттерсвит {formatDelta(last.bittersweet - first.bittersweet)} пп
        </span>
        <span className="delta-chip">
          темп {formatDelta(last.tempo - first.tempo)} bpm
        </span>
      </div>
    </div>
  );
}
