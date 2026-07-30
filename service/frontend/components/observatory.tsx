"use client";

/**
 * v0.11 (SPEC-v11-observatory §B): секция «Обсерватория» — аналитический
 * этаж страницы портрета. Бэкенд (GET /api/p/{id}/observatory) отдаёт
 * ГОТОВЫЕ К ОТРИСОВКЕ модели, фронт только рисует: рукописный SVG,
 * палитра бренда, tabular-nums, ховер-тултипы. БЕЗ чарт-библиотек.
 *
 * Ленивая загрузка: fetch уходит только когда секция доскроллена
 * (IntersectionObserver по сентинелу у заголовка) — первый рендер портрета
 * не тормозится. Ошибки бэка: 404 (старый бэк без роута) — секция скрыта
 * молча; 409 (портрет старой версии без features) — подсказка «перестрой»;
 * прочее — человеческая ошибка с retry.
 *
 * Панели (каждая рендерится ТОЛЬКО если её данные валидны):
 *   1. колесо тональностей (круг квинт, мажор снаружи / минор внутри);
 *   2. риджлайны-joyplot KDE четырёх признаков (медиана, p25–p75);
 *   3. радар 7 осей — твои перцентили поверх пунктирного среднего;
 *   4. PCA-биплот — стрелки loadings + СВОИ точки, спроецированные
 *      на клиенте (lib/linalg: стандартизация × loadings);
 *   6. белые вороны — топ-аномалии IsolationForest с ▶ превью;
 *   7. часы вкуса — полярные 24-часовые лепестки + дни недели;
 *   8. тайл энтропии Шеннона.
 * (5-я панель — режим карты «Форма» — живёт в portrait-view: те же точки
 * ScatterMap на tsne-координатах из этого же payload.)
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ApiError, fetchObservatory } from "@/lib/api";
import { clusterColor } from "@/lib/colors";
import { projectOnLoadings, standardizeColumns } from "@/lib/linalg";
import {
  PreviewPlayButton,
  usePreviewResolver,
} from "@/components/preview-play";
import type { PreviewPlayer } from "@/lib/use-audio";
import type {
  Cluster,
  ObservatoryPayload,
  PortraitPoint,
} from "@/lib/types";

/* ================= Нормализация payload =================
 * Бэкенд может прислать неполный/чуть иной ответ — каждую панель
 * приводим к строгой внутренней форме или отбрасываем (null). */

function finiteArray(v: unknown, len?: number): number[] | null {
  if (!Array.isArray(v)) return null;
  if (len !== undefined && v.length !== len) return null;
  const out: number[] = [];
  for (const item of v) {
    if (typeof item !== "number" || !Number.isFinite(item)) return null;
    out.push(item);
  }
  return out;
}

function nullableArray(v: unknown, len: number): (number | null)[] | null {
  if (!Array.isArray(v) || v.length !== len) return null;
  return v.map((item) =>
    typeof item === "number" && Number.isFinite(item) ? item : null,
  );
}

function str(v: unknown): string | null {
  return typeof v === "string" && v.trim() !== "" ? v.trim() : null;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

/** Кварто-квинтовый порядок из спеки §A1 — фолбэк, если бэк не прислал. */
const FIFTHS = [
  "C", "G", "D", "A", "E", "B", "F#", "C#", "G#", "D#", "A#", "F",
];

interface WheelData {
  order: string[];
  major: number[];
  minor: number[];
  topLabel: string | null;
  topCount: number | null;
}

function normalizeWheel(raw: ObservatoryPayload["key_wheel"]): WheelData | null {
  if (raw === null || raw === undefined || typeof raw !== "object") return null;
  const major = finiteArray(raw.major_counts, 12) ?? finiteArray(raw.major, 12);
  const minor = finiteArray(raw.minor_counts, 12) ?? finiteArray(raw.minor, 12);
  if (major === null || minor === null) return null;
  const orderRaw = Array.isArray(raw.order) ? raw.order : null;
  const order =
    orderRaw !== null &&
    orderRaw.length === 12 &&
    orderRaw.every((k) => typeof k === "string")
      ? orderRaw
      : FIFTHS;

  let topLabel: string | null = null;
  let topCount: number | null = null;
  const top = raw.top;
  if (typeof top === "string") {
    topLabel = str(top);
  } else if (top !== null && top !== undefined && typeof top === "object") {
    topLabel =
      str(top.label) ??
      (str(top.key) !== null
        ? `${str(top.key)} ${str(top.mode) ?? ""}`.trim()
        : null);
    topCount =
      typeof top.count === "number" && Number.isFinite(top.count)
        ? top.count
        : null;
  }
  if (topLabel === null) {
    // топ не пришёл — восстановим из counts
    let best = -1;
    for (let i = 0; i < 12; i++) {
      if (major[i] > best) {
        best = major[i];
        topLabel = `${order[i]} major`;
        topCount = major[i];
      }
      if (minor[i] > best) {
        best = minor[i];
        topLabel = `${order[i]} minor`;
        topCount = minor[i];
      }
    }
  }
  return { order, major, minor, topLabel, topCount };
}

interface RidgeData {
  feature: string;
  grid: number[];
  density: number[];
  median: number;
  p25: number;
  p75: number;
}

/** Порядок и человеческие подписи риджлайнов (§A2/§B2). */
const RIDGE_META: Record<
  string,
  { title: string; fmt: (v: number) => string; ends: [string, string] | null }
> = {
  tempo: {
    title: "темп",
    fmt: (v) => `${Math.round(v)} bpm`,
    ends: null,
  },
  brightness: {
    title: "яркость",
    fmt: (v) => `${Math.round(v)} Гц`,
    ends: ["темнее", "ярче"],
  },
  minor_score: {
    title: "минорность",
    fmt: (v) => v.toFixed(2),
    ends: ["мажор", "минор"],
  },
  bittersweet: {
    title: "биттерсвит",
    fmt: (v) => v.toFixed(2),
    ends: ["слабее", "сильнее"],
  },
};

const RIDGE_ORDER = ["tempo", "brightness", "minor_score", "bittersweet"];

function normalizeRidge(feature: string, raw: unknown): RidgeData | null {
  if (!isRecord(raw)) return null;
  const r = raw;
  const grid = finiteArray(r.grid);
  const density = finiteArray(r.density);
  if (
    grid === null ||
    density === null ||
    grid.length < 8 ||
    grid.length !== density.length
  ) {
    return null;
  }
  const median = typeof r.median === "number" ? r.median : NaN;
  const p25 = typeof r.p25 === "number" ? r.p25 : NaN;
  const p75 = typeof r.p75 === "number" ? r.p75 : NaN;
  if (![median, p25, p75].every(Number.isFinite)) return null;
  return { feature, grid, density, median, p25, p75 };
}

function normalizeRidgelines(
  raw: ObservatoryPayload["ridgelines"],
): RidgeData[] {
  if (raw === null || raw === undefined) return [];
  const out: RidgeData[] = [];
  if (Array.isArray(raw)) {
    for (const item of raw) {
      const feature = str((item as { feature?: unknown }).feature);
      if (feature === null || !(feature in RIDGE_META)) continue;
      const ridge = normalizeRidge(feature, item);
      if (ridge !== null) out.push(ridge);
    }
  } else if (typeof raw === "object") {
    for (const [feature, item] of Object.entries(raw)) {
      if (!(feature in RIDGE_META)) continue;
      const ridge = normalizeRidge(feature, item);
      if (ridge !== null) out.push(ridge);
    }
  }
  out.sort(
    (a, b) => RIDGE_ORDER.indexOf(a.feature) - RIDGE_ORDER.indexOf(b.feature),
  );
  return out;
}

interface RadarData {
  axes: { name: string; self: number; avg: number }[];
}

function clampPct(v: number): number {
  return Math.min(100, Math.max(0, v));
}

function normalizeRadar(raw: ObservatoryPayload["radar"]): RadarData | null {
  if (raw === null || raw === undefined || !Array.isArray(raw.axes)) {
    return null;
  }
  const axes: RadarData["axes"] = [];
  for (const axis of raw.axes) {
    const name = str(axis.name_ru);
    if (name === null) continue;
    if (typeof axis.self_pct !== "number" || !Number.isFinite(axis.self_pct)) {
      continue;
    }
    const avg =
      typeof axis.avg_pct === "number" && Number.isFinite(axis.avg_pct)
        ? axis.avg_pct
        : 50;
    axes.push({ name, self: clampPct(axis.self_pct), avg: clampPct(avg) });
  }
  return axes.length >= 3 ? { axes } : null;
}

interface PcaData {
  explained: [number, number];
  axes: [
    { loadings: Record<string, number>; labelPos: string; labelNeg: string },
    { loadings: Record<string, number>; labelPos: string; labelNeg: string },
  ];
}

function normalizeLoadings(v: unknown): Record<string, number> | null {
  if (v === null || typeof v !== "object" || Array.isArray(v)) return null;
  const out: Record<string, number> = {};
  for (const [key, w] of Object.entries(v)) {
    if (typeof w !== "number" || !Number.isFinite(w)) return null;
    out[key] = w;
  }
  return Object.keys(out).length >= 2 ? out : null;
}

function normalizePca(raw: ObservatoryPayload["pca"]): PcaData | null {
  if (raw === null || raw === undefined) return null;
  const explained = finiteArray(raw.explained);
  if (explained === null || explained.length < 2) return null;
  if (!Array.isArray(raw.axes) || raw.axes.length < 2) return null;
  const axes: PcaData["axes"][number][] = [];
  for (const axis of raw.axes.slice(0, 2)) {
    const loadings = normalizeLoadings(axis.loadings);
    if (loadings === null) return null;
    axes.push({
      loadings,
      labelPos: str(axis.label_pos) ?? "",
      labelNeg: str(axis.label_neg) ?? "",
    });
  }
  return {
    explained: [explained[0], explained[1]],
    axes: [axes[0], axes[1]],
  };
}

interface TsneData {
  x: number[];
  y: number[];
}

function normalizeTsne(
  raw: ObservatoryPayload["tsne"],
  nPoints: number,
): TsneData | null {
  if (raw === null || raw === undefined) return null;
  const x = finiteArray(raw.x, nPoints);
  const y = finiteArray(raw.y, nPoints);
  if (x === null || y === null || nPoints < 2) return null;
  return {
    x: x.map(clampPct),
    y: y.map(clampPct),
  };
}

interface OutlierData {
  label: string;
  videoId: string | null;
  score: number;
  why: string;
}

function normalizeOutliers(
  raw: ObservatoryPayload["outliers"],
): OutlierData[] {
  if (!Array.isArray(raw)) return [];
  const out: OutlierData[] = [];
  for (const item of raw) {
    if (item === null || typeof item !== "object") continue;
    const label = str(item.label);
    if (label === null) continue;
    const score =
      typeof item.score === "number" && Number.isFinite(item.score)
        ? Math.round(clampPct(item.score))
        : 0;
    const whyRaw = item.why;
    const why = Array.isArray(whyRaw)
      ? whyRaw.filter((w): w is string => typeof w === "string").join(" · ")
      : (str(whyRaw) ?? "");
    out.push({ label, videoId: str(item.videoId), score, why });
    if (out.length >= 8) break;
  }
  return out;
}

interface ClockData {
  hourCounts: number[];
  hourBittersweet: (number | null)[];
  weekdayCounts: number[] | null;
  weekdayBittersweet: (number | null)[];
  peakHour: number | null;
  insight: string | null;
}

function normalizeClock(raw: ObservatoryPayload["clock"]): ClockData | null {
  if (raw === null || raw === undefined || typeof raw !== "object") return null;
  const hourCounts = finiteArray(raw.hour_counts, 24);
  if (hourCounts === null) return null;
  const peak =
    typeof raw.peak_hour === "number" && Number.isFinite(raw.peak_hour)
      ? Math.round(raw.peak_hour)
      : null;
  return {
    hourCounts,
    hourBittersweet:
      nullableArray(raw.hour_bittersweet, 24) ?? new Array(24).fill(null),
    weekdayCounts: finiteArray(raw.weekday_counts, 7),
    weekdayBittersweet:
      nullableArray(raw.weekday_bittersweet, 7) ?? new Array(7).fill(null),
    peakHour: peak !== null && peak >= 0 && peak <= 23 ? peak : null,
    insight: str(raw.insight),
  };
}

interface EntropyData {
  bits: number;
  effectiveMoods: number;
  verdict: string;
}

function normalizeEntropy(
  raw: ObservatoryPayload["entropy"],
): EntropyData | null {
  if (raw === null || raw === undefined || typeof raw !== "object") return null;
  const candidates = [raw.h_bits, raw.bits, raw.h, raw.entropy];
  const bits = candidates.find(
    (v): v is number => typeof v === "number" && Number.isFinite(v),
  );
  if (bits === undefined || bits < 0) return null;
  const eff =
    typeof raw.effective_moods === "number" &&
    Number.isFinite(raw.effective_moods)
      ? raw.effective_moods
      : Math.round(2 ** bits * 10) / 10;
  // пороги вердикта — из спеки §A8 (фолбэк, если бэк не прислал фразу)
  const verdict =
    str(raw.verdict) ??
    (bits < 1.5
      ? "монолитный вкус"
      : bits < 2.2
        ? "сфокусированный"
        : "многоликий");
  return { bits, effectiveMoods: eff, verdict };
}

export interface ObservatoryData {
  wheel: WheelData | null;
  ridgelines: RidgeData[];
  radar: RadarData | null;
  pca: PcaData | null;
  tsne: TsneData | null;
  outliers: OutlierData[];
  clock: ClockData | null;
  entropy: EntropyData | null;
}

export function normalizeObservatory(
  raw: ObservatoryPayload,
  nPoints: number,
): ObservatoryData {
  return {
    wheel: normalizeWheel(raw.key_wheel),
    ridgelines: normalizeRidgelines(raw.ridgelines),
    radar: normalizeRadar(raw.radar),
    pca: normalizePca(raw.pca),
    tsne: normalizeTsne(raw.tsne, nPoints),
    outliers: normalizeOutliers(raw.outliers),
    clock: normalizeClock(raw.clock),
    entropy: normalizeEntropy(raw.entropy),
  };
}

/* ================= Ленивая загрузка ================= */

export type ObservatoryStatus =
  | "idle" // ещё не доскроллили
  | "loading"
  | "ready"
  | "hidden" // 404 старого бэка — секции нет
  | "stale" // 409 — портрет без features, нужна пересборка
  | "error";

export interface ObservatoryState {
  status: ObservatoryStatus;
  data: ObservatoryData | null;
  error: string;
  /** Запуск загрузки (идемпотентен; работает из idle и error) */
  load: () => void;
}

/**
 * Хук живёт в PortraitView (не в секции): tsne-координаты нужны
 * сегмент-контролу карты «Карта | Галактика | Форма» НАД секцией.
 */
export function useObservatory(
  portraitId: string | null,
  nPoints: number,
): ObservatoryState {
  const [status, setStatus] = useState<ObservatoryStatus>("idle");
  const [data, setData] = useState<ObservatoryData | null>(null);
  const [error, setError] = useState("");
  // ref-дубль: load зовётся из IntersectionObserver-колбэка
  const statusRef = useRef<ObservatoryStatus>(status);

  const load = useCallback(() => {
    if (portraitId === null || portraitId === "") return;
    if (statusRef.current !== "idle" && statusRef.current !== "error") return;
    statusRef.current = "loading";
    setStatus("loading");
    fetchObservatory(portraitId)
      .then((raw) => {
        setData(normalizeObservatory(raw, nPoints));
        statusRef.current = "ready";
        setStatus("ready");
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 404) {
          // старый бэк без роута — секция скрыта молча (§B)
          statusRef.current = "hidden";
          setStatus("hidden");
          return;
        }
        if (err instanceof ApiError && err.status === 409) {
          statusRef.current = "stale";
          setStatus("stale");
          return;
        }
        setError(
          err instanceof Error
            ? err.message
            : "Не получилось посчитать модели вкуса.",
        );
        statusRef.current = "error";
        setStatus("error");
      });
  }, [portraitId, nPoints]);

  return { status, data, error, load };
}

/* ================= Общая мелочь ================= */

function tracksWord(n: number): string {
  const tail = Math.abs(n) % 10;
  const teen = Math.abs(n) % 100;
  if (tail === 1 && teen !== 11) return "трек";
  if (tail >= 2 && tail <= 4 && (teen < 12 || teen > 14)) return "трека";
  return "треков";
}

/** Полярная точка: угол в градусах, 0° = вверх, по часовой. */
function polarPoint(
  cx: number,
  cy: number,
  r: number,
  deg: number,
): [number, number] {
  const rad = (deg * Math.PI) / 180;
  return [cx + r * Math.sin(rad), cy - r * Math.cos(rad)];
}

/** Кольцевой сектор (annular sector) — путь для колеса и часов. */
function ringSector(
  cx: number,
  cy: number,
  r0: number,
  r1: number,
  a0: number,
  a1: number,
): string {
  const [x0, y0] = polarPoint(cx, cy, r1, a0);
  const [x1, y1] = polarPoint(cx, cy, r1, a1);
  const [x2, y2] = polarPoint(cx, cy, r0, a1);
  const [x3, y3] = polarPoint(cx, cy, r0, a0);
  return [
    `M${x0.toFixed(2)} ${y0.toFixed(2)}`,
    `A${r1} ${r1} 0 0 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`,
    `L${x2.toFixed(2)} ${y2.toFixed(2)}`,
    `A${r0} ${r0} 0 0 0 ${x3.toFixed(2)} ${y3.toFixed(2)}`,
    "Z",
  ].join(" ");
}

/** Линейная смесь двух hex-цветов (для шкалы «синий → розовый»). */
function lerpColor(a: string, b: string, t: number): string {
  const pa = parseInt(a.slice(1), 16);
  const pb = parseInt(b.slice(1), 16);
  const k = Math.min(1, Math.max(0, t));
  const mix = (sa: number, sb: number): number => Math.round(sa + (sb - sa) * k);
  const r = mix((pa >> 16) & 255, (pb >> 16) & 255);
  const g = mix((pa >> 8) & 255, (pb >> 8) & 255);
  const bl = mix(pa & 255, pb & 255);
  return `rgb(${r}, ${g}, ${bl})`;
}

const COLOR_MAJOR = "#d98a6a";
const COLOR_MINOR = "#7f77dd";
const COLOR_CLOCK_LOW = "#6a9ed9";
const COLOR_CLOCK_HIGH = "#c76a9e";
const RIDGE_COLORS: Record<string, string> = {
  tempo: "#7f77dd",
  brightness: "#d98a6a",
  minor_score: "#6a9ed9",
  bittersweet: "#c76a9e",
};

interface TipState {
  /** Позиция в % от сцены панели */
  left: number;
  top: number;
  lines: ReactNode;
}

/** Ховер-тултип панели — стиль как у карты/таймлайна. */
function PanelTip({ tip }: { tip: TipState | null }) {
  if (tip === null) return null;
  return (
    <div
      className="obs-tip"
      role="status"
      style={{
        left: `${Math.min(88, Math.max(12, tip.left))}%`,
        top: `${tip.top}%`,
      }}
    >
      {tip.lines}
    </div>
  );
}

/* ================= Панель 1: колесо тональностей ================= */

const WHEEL_S = 360;
const WHEEL_C = WHEEL_S / 2;
const WHEEL_R_LABEL = 163;
const WHEEL_R_OUT = 146;
const WHEEL_R_BASE = 78;
const WHEEL_R_IN = 26;

function KeyWheelPanel({ wheel }: { wheel: WheelData }) {
  const [tip, setTip] = useState<TipState | null>(null);
  const maxCount = Math.max(1, ...wheel.major, ...wheel.minor);
  const total =
    wheel.major.reduce((s, v) => s + v, 0) +
    wheel.minor.reduce((s, v) => s + v, 0);

  const hoverSector = (i: number, mode: "major" | "minor"): void => {
    const count = mode === "major" ? wheel.major[i] : wheel.minor[i];
    const share = total > 0 ? Math.round((count / total) * 100) : 0;
    const midR =
      mode === "major"
        ? (WHEEL_R_BASE + WHEEL_R_OUT) / 2
        : (WHEEL_R_IN + WHEEL_R_BASE) / 2;
    const [px, py] = polarPoint(WHEEL_C, WHEEL_C, midR, i * 30);
    setTip({
      left: (px / WHEEL_S) * 100,
      top: (py / WHEEL_S) * 100,
      lines: (
        <>
          <b>
            {wheel.order[i]} {mode}
          </b>
          <span className="mono">
            {count} {tracksWord(count)} · {share}%
          </span>
        </>
      ),
    });
  };

  const sectors: ReactNode[] = [];
  for (let i = 0; i < 12; i++) {
    const a0 = i * 30 - 13;
    const a1 = i * 30 + 13;
    const majorH = (wheel.major[i] / maxCount) * (WHEEL_R_OUT - WHEEL_R_BASE - 3);
    const minorH = (wheel.minor[i] / maxCount) * (WHEEL_R_BASE - 3 - WHEEL_R_IN);
    if (wheel.major[i] > 0) {
      sectors.push(
        <path
          key={`M${i}`}
          d={ringSector(
            WHEEL_C,
            WHEEL_C,
            WHEEL_R_BASE + 3,
            WHEEL_R_BASE + 3 + Math.max(3, majorH),
            a0,
            a1,
          )}
          fill={COLOR_MAJOR}
          fillOpacity={0.85}
          className="obs-wheel-sector"
        />,
      );
    }
    if (wheel.minor[i] > 0) {
      sectors.push(
        <path
          key={`m${i}`}
          d={ringSector(
            WHEEL_C,
            WHEEL_C,
            Math.max(WHEEL_R_IN, WHEEL_R_BASE - 3 - Math.max(3, minorH)),
            WHEEL_R_BASE - 3,
            a0,
            a1,
          )}
          fill={COLOR_MINOR}
          fillOpacity={0.9}
          className="obs-wheel-sector"
        />,
      );
    }
    // невидимые зоны ховера: внешняя (мажор) и внутренняя (минор)
    sectors.push(
      <path
        key={`hM${i}`}
        d={ringSector(WHEEL_C, WHEEL_C, WHEEL_R_BASE, WHEEL_R_OUT, a0 - 2, a1 + 2)}
        fill="transparent"
        onMouseEnter={() => hoverSector(i, "major")}
      />,
      <path
        key={`hm${i}`}
        d={ringSector(WHEEL_C, WHEEL_C, WHEEL_R_IN, WHEEL_R_BASE, a0 - 2, a1 + 2)}
        fill="transparent"
        onMouseEnter={() => hoverSector(i, "minor")}
      />,
    );
  }

  const topIsAt = (i: number): boolean =>
    wheel.topLabel !== null && wheel.topLabel.startsWith(`${wheel.order[i]} `);

  return (
    <article className="obs-panel">
      <h3 className="obs-panel-title">Колесо тональностей</h3>
      <div className="timeline-legend" aria-hidden>
        <span className="timeline-legend-item">
          <span className="cluster-dot" style={{ background: COLOR_MAJOR }} />
          мажор (снаружи)
        </span>
        <span className="timeline-legend-item">
          <span className="cluster-dot" style={{ background: COLOR_MINOR }} />
          минор (внутри)
        </span>
      </div>
      <div className="obs-stage" onMouseLeave={() => setTip(null)}>
        <svg
          viewBox={`0 0 ${WHEEL_S} ${WHEEL_S}`}
          role="img"
          aria-label="Колесо тональностей по кругу квинт: мажор наружу, минор внутрь"
        >
          <circle
            cx={WHEEL_C}
            cy={WHEEL_C}
            r={WHEEL_R_BASE}
            fill="none"
            className="grid-line"
          />
          {sectors}
          {wheel.order.map((key, i) => {
            const [lx, ly] = polarPoint(WHEEL_C, WHEEL_C, WHEEL_R_LABEL, i * 30);
            return (
              <text
                key={key}
                className="chart-num"
                x={lx}
                y={ly}
                textAnchor="middle"
                dominantBaseline="central"
                fill={topIsAt(i) ? "var(--accent)" : "var(--text-secondary)"}
                fontWeight={topIsAt(i) ? 700 : 400}
              >
                {key}
              </text>
            );
          })}
        </svg>
        <PanelTip tip={tip} />
      </div>
      {wheel.topLabel !== null && (
        <p className="obs-caption">
          топ-тональность: <b>{wheel.topLabel}</b>
          {wheel.topCount !== null && (
            <>
              {" "}
              · <span className="mono">{wheel.topCount}</span>{" "}
              {tracksWord(wheel.topCount)}
            </>
          )}
        </p>
      )}
    </article>
  );
}

/* ================= Панель 2: риджлайны (joyplot) ================= */

const RIDGE_W = 620;
const RIDGE_PAD_L = 118;
const RIDGE_PAD_R = 18;
const RIDGE_H = 56; // высота горба
const RIDGE_STEP = 55; // сдвиг между базовыми линиями
const RIDGE_TOP = 14;

function RidgelinesPanel({ ridgelines }: { ridgelines: RidgeData[] }) {
  const [tip, setTip] = useState<TipState | null>(null);
  const H = RIDGE_TOP + RIDGE_H + RIDGE_STEP * (ridgelines.length - 1) + 26;

  /** Плотность в произвольной точке сетки — линейная интерполяция. */
  const densityAt = (ridge: RidgeData, v: number): number => {
    const { grid, density } = ridge;
    if (v <= grid[0]) return density[0];
    for (let j = 1; j < grid.length; j++) {
      if (v <= grid[j]) {
        const t = (v - grid[j - 1]) / (grid[j] - grid[j - 1] || 1);
        return density[j - 1] + (density[j] - density[j - 1]) * t;
      }
    }
    return density[density.length - 1];
  };

  return (
    <article className="obs-panel">
      <h3 className="obs-panel-title">Профили признаков</h3>
      <p className="obs-panel-sub">
        распределение треков; точка — медиана, подсветка — середина p25–p75
      </p>
      <div className="obs-stage" onMouseLeave={() => setTip(null)}>
        <svg
          viewBox={`0 0 ${RIDGE_W} ${H}`}
          role="img"
          aria-label="Риджлайны распределений темпа, яркости, минорности и биттерсвита"
        >
          {ridgelines.map((ridge, i) => {
            const meta = RIDGE_META[ridge.feature];
            const color = RIDGE_COLORS[ridge.feature] ?? "#7f77dd";
            const base = RIDGE_TOP + RIDGE_H + i * RIDGE_STEP;
            const g0 = ridge.grid[0];
            const g1 = ridge.grid[ridge.grid.length - 1];
            const x = (v: number): number =>
              RIDGE_PAD_L +
              ((v - g0) / (g1 - g0 || 1)) * (RIDGE_W - RIDGE_PAD_L - RIDGE_PAD_R);
            const y = (d: number): number => base - d * RIDGE_H;

            const curve = ridge.grid
              .map((v, j) => `${x(v).toFixed(1)},${y(ridge.density[j]).toFixed(1)}`)
              .join(" L");
            const area = `M${RIDGE_PAD_L} ${base} L${curve} L${RIDGE_W - RIDGE_PAD_R} ${base} Z`;

            // сегмент p25–p75: точки сетки внутри + интерполяция на краях
            const inner = ridge.grid
              .map((v, j) => ({ v, d: ridge.density[j] }))
              .filter(({ v }) => v >= ridge.p25 && v <= ridge.p75);
            const band = [
              { v: ridge.p25, d: densityAt(ridge, ridge.p25) },
              ...inner,
              { v: ridge.p75, d: densityAt(ridge, ridge.p75) },
            ];
            const bandPath =
              `M${x(ridge.p25).toFixed(1)} ${base} L` +
              band.map(({ v, d }) => `${x(v).toFixed(1)},${y(d).toFixed(1)}`).join(" L") +
              ` L${x(ridge.p75).toFixed(1)} ${base} Z`;

            const medianY = y(densityAt(ridge, ridge.median));
            const [endL, endR] =
              meta.ends ?? ([meta.fmt(g0), meta.fmt(g1)] as [string, string]);

            return (
              <g key={ridge.feature}>
                {/* базовая линия */}
                <line
                  x1={RIDGE_PAD_L}
                  y1={base}
                  x2={RIDGE_W - RIDGE_PAD_R}
                  y2={base}
                  stroke="var(--border)"
                />
                <path d={area} fill={color} fillOpacity={0.2} />
                <path d={bandPath} fill={color} fillOpacity={0.38} />
                <path
                  d={`M${curve}`}
                  fill="none"
                  stroke={color}
                  strokeWidth={1.6}
                  strokeLinejoin="round"
                />
                {/* медиана: штрих + точка */}
                <line
                  x1={x(ridge.median)}
                  y1={base}
                  x2={x(ridge.median)}
                  y2={medianY}
                  stroke={color}
                  strokeWidth={1}
                  opacity={0.8}
                />
                <circle cx={x(ridge.median)} cy={medianY} r={3.2} fill={color} />
                {/* подписи: название + медиана слева, края диапазона по углам */}
                <text
                  className="axis-label"
                  x={10}
                  y={base - 16}
                  fill="var(--text)"
                >
                  {meta.title}
                </text>
                <text className="chart-num" x={10} y={base - 2} fill={color}>
                  {meta.fmt(ridge.median)}
                </text>
                <text
                  className="chart-date"
                  x={RIDGE_PAD_L}
                  y={base + 13}
                  textAnchor="start"
                >
                  {endL}
                </text>
                <text
                  className="chart-date"
                  x={RIDGE_W - RIDGE_PAD_R}
                  y={base + 13}
                  textAnchor="end"
                >
                  {endR}
                </text>
                {/* зона ховера строки */}
                <rect
                  x={0}
                  y={base - RIDGE_H - 6}
                  width={RIDGE_W}
                  height={RIDGE_STEP}
                  fill="transparent"
                  onMouseEnter={() =>
                    setTip({
                      left: ((x(ridge.median) / RIDGE_W) * 100),
                      top: ((base - RIDGE_H) / H) * 100,
                      lines: (
                        <>
                          <b>{meta.title}</b>
                          <span className="mono">
                            медиана {meta.fmt(ridge.median)} · p25{" "}
                            {meta.fmt(ridge.p25)} · p75 {meta.fmt(ridge.p75)}
                          </span>
                        </>
                      ),
                    })
                  }
                />
              </g>
            );
          })}
        </svg>
        <PanelTip tip={tip} />
      </div>
    </article>
  );
}

/* ================= Панель 3: радар ================= */

// ширина с запасом: подписи осей («биттерсвит») не должны резаться краем
const RADAR_W = 420;
const RADAR_H = 312;
const RADAR_CX = RADAR_W / 2;
const RADAR_CY = 162;
const RADAR_R = 108;

function RadarPanel({ radar }: { radar: RadarData }) {
  const [tip, setTip] = useState<TipState | null>(null);
  const n = radar.axes.length;
  const vertex = (i: number, pct: number): [number, number] =>
    polarPoint(RADAR_CX, RADAR_CY, (pct / 100) * RADAR_R, (i * 360) / n);
  const polygon = (value: (a: RadarData["axes"][number]) => number): string =>
    radar.axes
      .map((axis, i) => {
        const [px, py] = vertex(i, value(axis));
        return `${px.toFixed(1)},${py.toFixed(1)}`;
      })
      .join(" ");

  return (
    <article className="obs-panel">
      <h3 className="obs-panel-title">Радар вкуса</h3>
      <p className="obs-panel-sub">
        перцентили относительно других портретов; пунктир — средний портрет
      </p>
      <div className="obs-stage" onMouseLeave={() => setTip(null)}>
        <svg
          viewBox={`0 0 ${RADAR_W} ${RADAR_H}`}
          role="img"
          aria-label="Радар признаков вкуса против среднего портрета"
        >
          {/* кольца-сетка */}
          {[25, 50, 75, 100].map((pct) => (
            <polygon
              key={pct}
              points={polygon(() => pct)}
              fill="none"
              className="grid-line"
            />
          ))}
          {/* спицы */}
          {radar.axes.map((axis, i) => {
            const [px, py] = vertex(i, 100);
            return (
              <line
                key={axis.name}
                x1={RADAR_CX}
                y1={RADAR_CY}
                x2={px}
                y2={py}
                className="grid-line"
              />
            );
          })}
          {/* средний портрет — пунктир */}
          <polygon
            points={polygon((a) => a.avg)}
            fill="none"
            stroke="var(--text-secondary)"
            strokeWidth={1.2}
            strokeDasharray="5 4"
            opacity={0.8}
          />
          {/* твой контур */}
          <polygon
            points={polygon((a) => a.self)}
            fill="rgba(127, 119, 221, 0.3)"
            stroke="var(--accent)"
            strokeWidth={1.8}
            strokeLinejoin="round"
          />
          {radar.axes.map((axis, i) => {
            const [px, py] = vertex(i, axis.self);
            return (
              <circle key={axis.name} cx={px} cy={py} r={2.8} fill="var(--accent)" />
            );
          })}
          {/* подписи осей */}
          {radar.axes.map((axis, i) => {
            const deg = (i * 360) / n;
            const [lx, ly] = polarPoint(RADAR_CX, RADAR_CY, RADAR_R + 16, deg);
            const rad = (deg * Math.PI) / 180;
            const anchor =
              Math.sin(rad) > 0.35
                ? "start"
                : Math.sin(rad) < -0.35
                  ? "end"
                  : "middle";
            return (
              <text
                key={axis.name}
                className="axis-label"
                x={lx}
                y={ly}
                textAnchor={anchor}
                dominantBaseline="central"
              >
                {axis.name}
              </text>
            );
          })}
          {/* зоны ховера вершин */}
          {radar.axes.map((axis, i) => {
            const [px, py] = vertex(i, Math.max(axis.self, 24));
            return (
              <circle
                key={axis.name}
                cx={px}
                cy={py}
                r={13}
                fill="transparent"
                onMouseEnter={() =>
                  setTip({
                    left: (px / RADAR_W) * 100,
                    top: (py / RADAR_H) * 100,
                    lines: (
                      <>
                        <b>{axis.name}</b>
                        <span className="mono">
                          твой перцентиль {Math.round(axis.self)} · среднее{" "}
                          {Math.round(axis.avg)}
                        </span>
                      </>
                    ),
                  })
                }
              />
            );
          })}
        </svg>
        <PanelTip tip={tip} />
      </div>
    </article>
  );
}

/* ================= Панель 4: PCA-биплот ================= */

const PCA_W = 620;
const PCA_H = 430;
const PCA_CX = PCA_W / 2;
const PCA_CY = PCA_H / 2;
const PCA_RX = PCA_CX - 44;
const PCA_RY = PCA_CY - 40;

/** feat → рус для подписей стрелок (§A4). */
const FEAT_RU: Record<string, string> = {
  tempo: "темп",
  minor_score: "минорность",
  brightness: "яркость",
  bittersweet: "биттерсвит",
  percussive: "ритмичность",
  energy_rms: "плотность",
  onset_rate: "дробность",
};

function PcaPanel({
  pca,
  points,
  clusters,
}: {
  pca: PcaData;
  points: PortraitPoint[];
  clusters: Cluster[];
}) {
  // проекция СВОИХ точек на компоненты бэка: z-оценки × loadings (§B4)
  const projected = useMemo(() => {
    const feats = Object.keys(pca.axes[0].loadings);
    const usable: { point: PortraitPoint; row: number[] }[] = [];
    for (const point of points) {
      const f = point.features;
      if (f === undefined || f === null) continue;
      const row: number[] = [];
      let ok = true;
      for (const name of feats) {
        const v = f[name];
        if (typeof v !== "number" || !Number.isFinite(v)) {
          ok = false;
          break;
        }
        row.push(v);
      }
      if (ok) usable.push({ point, row });
    }
    if (usable.length < 3) return null;
    const z = standardizeColumns(usable.map((u) => u.row));
    const loadings = [
      feats.map((name) => pca.axes[0].loadings[name]),
      feats.map((name) => pca.axes[1].loadings[name] ?? 0),
    ];
    const scores = projectOnLoadings(z, loadings);
    const max0 = Math.max(1e-9, ...scores.map((s) => Math.abs(s[0])));
    const max1 = Math.max(1e-9, ...scores.map((s) => Math.abs(s[1])));
    return usable.map((u, i) => ({
      point: u.point,
      x: PCA_CX + (scores[i][0] / max0) * PCA_RX * 0.94,
      y: PCA_CY - (scores[i][1] / max1) * PCA_RY * 0.94,
    }));
  }, [pca, points]);

  if (projected === null) return null;

  // стрелки признаков по loadings; масштаб — самая длинная на ~80% полуоси
  const feats = Object.keys(pca.axes[0].loadings);
  const maxNorm = Math.max(
    1e-9,
    ...feats.map((name) =>
      Math.hypot(pca.axes[0].loadings[name], pca.axes[1].loadings[name] ?? 0),
    ),
  );
  const arrowScale = (Math.min(PCA_RX, PCA_RY) * 0.82) / maxNorm;

  // подписи стрелок: близкие по направлению стрелки (у нас 3–4 признака
  // часто смотрят в одну сторону) наслаивают текст — раздвигаем по y
  // жадным проходом с минимальным зазором 13px на каждой стороне
  const arrowLabels = feats.map((name) => {
    const wx = pca.axes[0].loadings[name] * arrowScale;
    const wy = (pca.axes[1].loadings[name] ?? 0) * arrowScale;
    return {
      name,
      tx: PCA_CX + wx,
      ty: PCA_CY - wy,
      lx: PCA_CX + wx + (wx >= 0 ? 6 : -6),
      ly: PCA_CY - wy + (wy >= 0 ? -6 : 12),
      right: wx >= 0,
    };
  });
  for (const side of [true, false]) {
    const group = arrowLabels
      .filter((l) => l.right === side)
      .sort((a, b) => a.ly - b.ly);
    for (let i = 1; i < group.length; i++) {
      if (group[i].ly - group[i - 1].ly < 13) {
        group[i].ly = group[i - 1].ly + 13;
      }
    }
  }

  const e1 = Math.round(pca.explained[0]);
  const e2 = Math.round(pca.explained[1]);

  return (
    <article className="obs-panel">
      <h3 className="obs-panel-title">Компоненты вкуса · PCA</h3>
      <p className="obs-panel-sub">
        два главных направления, объясняющие {e1 + e2}% различий твоих треков
      </p>
      <div className="obs-stage">
        <svg
          viewBox={`0 0 ${PCA_W} ${PCA_H}`}
          role="img"
          aria-label="PCA-биплот: точки треков и стрелки вклада признаков"
        >
          <defs>
            <marker
              id="obs-arrow"
              viewBox="0 0 8 8"
              refX="7"
              refY="4"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M0 0 L8 4 L0 8 Z" fill="var(--text)" opacity="0.75" />
            </marker>
          </defs>
          {/* оси */}
          <line
            x1={PCA_CX - PCA_RX}
            y1={PCA_CY}
            x2={PCA_CX + PCA_RX}
            y2={PCA_CY}
            stroke="var(--border)"
          />
          <line
            x1={PCA_CX}
            y1={PCA_CY - PCA_RY}
            x2={PCA_CX}
            y2={PCA_CY + PCA_RY}
            stroke="var(--border)"
          />
          {/* точки — те же треки, что на карте */}
          {projected.map(({ point, x, y }, i) => (
            <circle
              key={i}
              className="scatter-point"
              cx={x}
              cy={y}
              r={3.2}
              fill={clusterColor(clusters, point.cluster)}
              fillOpacity={0.72}
            >
              <title>{`${point.label}\n${point.meta}`}</title>
            </circle>
          ))}
          {/* стрелки признаков */}
          {arrowLabels.map((arrow) => (
            <g key={arrow.name}>
              <line
                x1={PCA_CX}
                y1={PCA_CY}
                x2={arrow.tx}
                y2={arrow.ty}
                stroke="var(--text)"
                strokeWidth={1.3}
                opacity={0.7}
                markerEnd="url(#obs-arrow)"
              />
              <text
                className="obs-arrow-label"
                x={arrow.lx}
                y={arrow.ly}
                textAnchor={arrow.right ? "start" : "end"}
              >
                {FEAT_RU[arrow.name] ?? arrow.name}
              </text>
            </g>
          ))}
          {/* подписи концов осей: человеческие интерпретации loadings */}
          <text className="axis-label" x={PCA_CX + PCA_RX} y={PCA_CY - 8} textAnchor="end">
            {pca.axes[0].labelPos}
          </text>
          <text className="axis-label" x={PCA_CX - PCA_RX} y={PCA_CY - 8} textAnchor="start">
            {pca.axes[0].labelNeg}
          </text>
          <text className="axis-label" x={PCA_CX + 8} y={PCA_CY - PCA_RY + 10} textAnchor="start">
            {pca.axes[1].labelPos}
          </text>
          <text className="axis-label" x={PCA_CX + 8} y={PCA_CY + PCA_RY - 4} textAnchor="start">
            {pca.axes[1].labelNeg}
          </text>
          {/* доли объяснённой дисперсии */}
          <text
            className="chart-num obs-axis-cap"
            x={PCA_CX + PCA_RX}
            y={PCA_CY + 16}
            textAnchor="end"
          >
            ось 1 · {e1}% вкуса
          </text>
          <text
            className="chart-num obs-axis-cap"
            x={PCA_CX - 8}
            y={PCA_CY - PCA_RY + 10}
            textAnchor="end"
          >
            ось 2 · {e2}%
          </text>
        </svg>
      </div>
    </article>
  );
}

/* ================= Панель 6: белые вороны ================= */

function OutliersPanel({
  outliers,
  player,
}: {
  outliers: OutlierData[];
  player: PreviewPlayer;
}) {
  // свой ленивый резолвер превью: до 8 лейблов, один батч по первому клику
  const labels = useMemo(() => outliers.map((o) => o.label), [outliers]);
  const { stateFor, onLazy } = usePreviewResolver(labels, player);

  return (
    <article className="obs-panel">
      <h3 className="obs-panel-title">Белые вороны</h3>
      <p className="obs-panel-sub">
        треки, меньше всего похожие на остальную библиотеку (IsolationForest)
      </p>
      <ul className="obs-crow-list">
        {outliers.map((crow) => (
          <li key={crow.label} className="obs-crow-row">
            <span
              className="obs-crow-score mono"
              title={`аномальность ${crow.score} из 100`}
            >
              {crow.score}
            </span>
            <PreviewPlayButton
              label={crow.label}
              state={stateFor(crow.label)}
              player={player}
              onLazy={onLazy}
              videoId={crow.videoId}
            />
            <span className="obs-crow-body">
              <span className="obs-crow-label" title={crow.label}>
                {crow.label}
              </span>
              {crow.why !== "" && (
                <span className="obs-crow-why">{crow.why}</span>
              )}
            </span>
          </li>
        ))}
      </ul>
    </article>
  );
}

/* ================= Панель 7: часы вкуса ================= */

const CLOCK_S = 300;
const CLOCK_C = CLOCK_S / 2;
const CLOCK_R0 = 36;
const CLOCK_R1 = 124;

const WEEKDAY_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
const WEEKDAY_FULL = [
  "понедельник",
  "вторник",
  "среда",
  "четверг",
  "пятница",
  "суббота",
  "воскресенье",
];

const WD_W = 300;
const WD_H = 300;

function clockColor(share: number | null, maxShare: number): string {
  if (share === null) return "#3c3c50";
  return lerpColor(COLOR_CLOCK_LOW, COLOR_CLOCK_HIGH, share / Math.max(1e-9, maxShare));
}

function ClockPanel({ clock }: { clock: ClockData }) {
  const [tip, setTip] = useState<TipState | null>(null);
  const [tipWd, setTipWd] = useState<TipState | null>(null);
  const maxHour = Math.max(1, ...clock.hourCounts);
  const maxShare = Math.max(
    0.01,
    ...clock.hourBittersweet.filter((v): v is number => v !== null),
    ...clock.weekdayBittersweet.filter((v): v is number => v !== null),
  );

  const weekdays = clock.weekdayCounts;
  const maxWd = weekdays !== null ? Math.max(1, ...weekdays) : 1;

  return (
    <article className="obs-panel obs-panel-wide">
      <h3 className="obs-panel-title">Часы вкуса</h3>
      <p className="obs-panel-sub">
        когда ты лайкаешь музыку: высота — количество, цвет — доля биттерсвита
      </p>
      <div className="timeline-legend" aria-hidden>
        <span className="timeline-legend-item">
          <span className="cluster-dot" style={{ background: COLOR_CLOCK_LOW }} />
          меньше биттерсвита
        </span>
        <span className="timeline-legend-item">
          <span className="cluster-dot" style={{ background: COLOR_CLOCK_HIGH }} />
          больше
        </span>
        <span className="timeline-legend-item">
          <span className="cluster-dot" style={{ background: "#3c3c50" }} />
          мало данных
        </span>
      </div>
      <div className="obs-clock-row">
        <div className="obs-stage obs-clock-stage" onMouseLeave={() => setTip(null)}>
          <svg
            viewBox={`0 0 ${CLOCK_S} ${CLOCK_S}`}
            role="img"
            aria-label="Полярные часы лайков: 24 часовых лепестка"
          >
            <circle
              cx={CLOCK_C}
              cy={CLOCK_C}
              r={CLOCK_R0 - 4}
              fill="none"
              className="grid-line"
            />
            {clock.hourCounts.map((count, hour) => {
              const a0 = hour * 15 - 6.4;
              const a1 = hour * 15 + 6.4;
              const h = (count / maxHour) * (CLOCK_R1 - CLOCK_R0);
              const share = clock.hourBittersweet[hour];
              const isPeak = clock.peakHour === hour;
              return (
                <g key={hour}>
                  {count > 0 && (
                    <path
                      d={ringSector(
                        CLOCK_C,
                        CLOCK_C,
                        CLOCK_R0,
                        CLOCK_R0 + Math.max(2.5, h),
                        a0,
                        a1,
                      )}
                      fill={clockColor(share, maxShare)}
                      fillOpacity={0.9}
                      stroke={isPeak ? "var(--text)" : "none"}
                      strokeWidth={isPeak ? 1 : 0}
                    />
                  )}
                  <path
                    d={ringSector(CLOCK_C, CLOCK_C, CLOCK_R0, CLOCK_R1 + 6, a0 - 1, a1 + 1)}
                    fill="transparent"
                    onMouseEnter={() => {
                      const [px, py] = polarPoint(
                        CLOCK_C,
                        CLOCK_C,
                        CLOCK_R0 + Math.max(14, h),
                        hour * 15,
                      );
                      setTip({
                        left: (px / CLOCK_S) * 100,
                        top: (py / CLOCK_S) * 100,
                        lines: (
                          <>
                            <b>
                              {String(hour).padStart(2, "0")}:00–
                              {String((hour + 1) % 24).padStart(2, "0")}:00
                            </b>
                            <span className="mono">
                              {count} {tracksWord(count)}
                              {share !== null
                                ? ` · биттерсвит ${Math.round(share * 100)}%`
                                : " · мало данных о биттерсвите"}
                            </span>
                          </>
                        ),
                      });
                    }}
                  />
                </g>
              );
            })}
            {[0, 6, 12, 18].map((hour) => {
              const [lx, ly] = polarPoint(CLOCK_C, CLOCK_C, CLOCK_R1 + 14, hour * 15);
              return (
                <text
                  key={hour}
                  className="chart-num"
                  x={lx}
                  y={ly}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fill="var(--text-secondary)"
                >
                  {String(hour).padStart(2, "0")}
                </text>
              );
            })}
          </svg>
          <PanelTip tip={tip} />
        </div>
        {weekdays !== null && (
          <div className="obs-stage obs-clock-stage" onMouseLeave={() => setTipWd(null)}>
            <svg
              viewBox={`0 0 ${WD_W} ${WD_H}`}
              role="img"
              aria-label="Лайки по дням недели"
            >
              {weekdays.map((count, wd) => {
                const barW = 26;
                const gap = (WD_W - 40 - barW * 7) / 6;
                const bx = 20 + wd * (barW + gap);
                const bh = (count / maxWd) * 210;
                const by = 252 - Math.max(2.5, bh);
                const share = clock.weekdayBittersweet[wd];
                return (
                  <g key={wd}>
                    {count > 0 && (
                      <rect
                        x={bx}
                        y={by}
                        width={barW}
                        height={Math.max(2.5, bh)}
                        rx={4}
                        fill={clockColor(share, maxShare)}
                        fillOpacity={0.9}
                      />
                    )}
                    <text
                      className="chart-date"
                      x={bx + barW / 2}
                      y={270}
                      textAnchor="middle"
                    >
                      {WEEKDAY_SHORT[wd]}
                    </text>
                    <rect
                      x={bx - gap / 2}
                      y={20}
                      width={barW + gap}
                      height={WD_H - 40}
                      fill="transparent"
                      onMouseEnter={() =>
                        setTipWd({
                          left: ((bx + barW / 2) / WD_W) * 100,
                          top: ((by - 6) / WD_H) * 100,
                          lines: (
                            <>
                              <b>{WEEKDAY_FULL[wd]}</b>
                              <span className="mono">
                                {count} {tracksWord(count)}
                                {share !== null
                                  ? ` · биттерсвит ${Math.round(share * 100)}%`
                                  : ""}
                              </span>
                            </>
                          ),
                        })
                      }
                    />
                  </g>
                );
              })}
            </svg>
            <PanelTip tip={tipWd} />
          </div>
        )}
      </div>
      {clock.insight !== null && (
        <p className="obs-insight">💡 {clock.insight}</p>
      )}
    </article>
  );
}

/* ================= Панель 8: энтропия ================= */

function moodsWord(n: number): string {
  if (!Number.isInteger(n)) return "настроения";
  const tail = n % 10;
  const teen = n % 100;
  if (tail === 1 && teen !== 11) return "настроение";
  if (tail >= 2 && tail <= 4 && (teen < 12 || teen > 14)) return "настроения";
  return "настроений";
}

function EntropyPanel({ entropy }: { entropy: EntropyData }) {
  const eff = Math.round(entropy.effectiveMoods * 10) / 10;
  return (
    <article className="obs-panel obs-entropy">
      <h3 className="obs-panel-title">Разнообразие вкуса</h3>
      <div className="obs-entropy-value mono">
        {entropy.bits.toFixed(1)} <span>бит</span>
      </div>
      <p className="obs-entropy-moods">
        ≈ <span className="mono">{eff}</span> {moodsWord(eff)} в постоянной
        ротации
      </p>
      <p className="obs-entropy-verdict">{entropy.verdict}</p>
      <p className="obs-caption">энтропия Шеннона по кластерам вкуса</p>
    </article>
  );
}

/* ================= Секция ================= */

export default function ObservatorySection({
  observatory,
  points,
  clusters,
  player,
}: {
  observatory: ObservatoryState;
  points: PortraitPoint[];
  clusters: Cluster[];
  player: PreviewPlayer;
}) {
  const { status, data, error, load } = observatory;
  const loadRef = useRef(load);
  useEffect(() => {
    loadRef.current = load;
  });

  /* v0.12 (SPEC-v12-human §B): секция переименована в «Под капотом» и
   * свёрнута по умолчанию (details) — математика для любопытных, а не
   * первый экран. Ленивый fetch теперь стартует по РАСКРЫТИЮ (onToggle),
   * а не по доскроллу: закрытый капот ничего не тянет. Квиз при
   * необходимости дожимает load() сам через тот же хук. */

  // 404 старого бэка — секции просто нет (молча)
  if (status === "hidden") return null;

  let body: ReactNode;
  if (status === "idle" || status === "loading") {
    body = (
      <div className="obs-placeholder">
        <span className="btn-spinner" aria-hidden />
        считаем модели вкуса…
      </div>
    );
  } else if (status === "stale") {
    body = (
      <div className="obs-note">
        Портрет сохранён старой версией — в нём нет признаков для моделей.
        Перестрой портрет, и обсерватория откроется.
      </div>
    );
  } else if (status === "error" || data === null) {
    body = (
      <div className="obs-note">
        <span>{error !== "" ? error : "Не получилось посчитать модели."}</span>
        <button type="button" className="btn btn-ghost btn-small" onClick={load}>
          Попробовать снова
        </button>
      </div>
    );
  } else {
    body = (
      <div className="obs-grid">
        {data.wheel !== null && <KeyWheelPanel wheel={data.wheel} />}
        {data.ridgelines.length > 0 && (
          <RidgelinesPanel ridgelines={data.ridgelines} />
        )}
        {data.radar !== null && <RadarPanel radar={data.radar} />}
        {data.pca !== null && (
          <PcaPanel pca={data.pca} points={points} clusters={clusters} />
        )}
        {data.outliers.length > 0 && (
          <OutliersPanel outliers={data.outliers} player={player} />
        )}
        {/* clock: null → панель скрыта (§B7) */}
        {data.clock !== null && <ClockPanel clock={data.clock} />}
        {data.entropy !== null && <EntropyPanel entropy={data.entropy} />}
      </div>
    );
  }

  return (
    <details
      className="obs-section obs-details"
      onToggle={(e) => {
        if ((e.currentTarget as HTMLDetailsElement).open) loadRef.current();
      }}
    >
      <summary className="obs-summary">
        <h2 className="section-title">Под капотом</h2>
        <span className="obs-summary-hint">
          математика портрета — для любопытных
        </span>
      </summary>
      <p className="obs-sub">
        математические модели поверх твоего портрета: тональности,
        распределения, компоненты, аномалии
      </p>
      {body}
    </details>
  );
}
