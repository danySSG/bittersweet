/**
 * v0.10 (SPEC-v10-critique §B): математика «Галактики 2.0» — без зависимостей.
 *
 * probit() — обратная CDF стандартной нормали (рациональная аппроксимация
 * Питера Аклама, |относительная ошибка| < 1.15e-9 на всём (0, 1)).
 * Перцентиль каждой оси прогоняется через probit, клипится в ±2.5σ и
 * нормируется — куб перцентилей превращается в гауссово облако-сферу.
 *
 * mulberry32() + hashSeed() — детерминированный PRNG для «пылинок» атмосферы:
 * сид фиксирован от id портрета, Math.random в рендер-цикле запрещён.
 */

/* Коэффициенты Аклама (https://web.archive.org/web/20151030215612/
   http://home.online.no/~pjacklam/notes/invnorm/) */
const A = [
  -3.969683028665376e1, 2.209460984245205e2, -2.759285104469687e2,
  1.38357751867269e2, -3.066479806614716e1, 2.506628277459239,
] as const;
const B = [
  -5.447609879822406e1, 1.615858368580409e2, -1.556989798598866e2,
  6.680131188771972e1, -1.328068155288572e1,
] as const;
const C = [
  -7.784894002430293e-3, -3.223964580411365e-1, -2.400758277161838,
  -2.549732539343734, 4.374664141464968, 2.938163982698783,
] as const;
const D = [
  7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996,
  3.754408661907416,
] as const;

/** Граница между центральной и хвостовой ветками аппроксимации. */
const P_LOW = 0.02425;

/**
 * Обратная CDF стандартной нормали: p ∈ (0, 1) → z.
 * p <= 0 → -Infinity, p >= 1 → +Infinity (вызывающий код клипит сам).
 */
export function probit(p: number): number {
  if (Number.isNaN(p)) return NaN;
  if (p <= 0) return -Infinity;
  if (p >= 1) return Infinity;

  if (p < P_LOW) {
    // нижний хвост
    const q = Math.sqrt(-2 * Math.log(p));
    return (
      (((((C[0] * q + C[1]) * q + C[2]) * q + C[3]) * q + C[4]) * q + C[5]) /
      ((((D[0] * q + D[1]) * q + D[2]) * q + D[3]) * q + 1)
    );
  }
  if (p > 1 - P_LOW) {
    // верхний хвост — зеркало нижнего
    const q = Math.sqrt(-2 * Math.log(1 - p));
    return (
      -(((((C[0] * q + C[1]) * q + C[2]) * q + C[3]) * q + C[4]) * q + C[5]) /
      ((((D[0] * q + D[1]) * q + D[2]) * q + D[3]) * q + 1)
    );
  }
  // центральная область
  const q = p - 0.5;
  const r = q * q;
  return (
    ((((((A[0] * r + A[1]) * r + A[2]) * r + A[3]) * r + A[4]) * r + A[5]) *
      q) /
    (((((B[0] * r + B[1]) * r + B[2]) * r + B[3]) * r + B[4]) * r + 1)
  );
}

/** Клип в ±sigma и нормировка в [-1, 1] — координата оси галактики. */
export function gaussianAxis(p: number, sigma = 2.5): number {
  const z = probit(Math.min(1 - 1e-6, Math.max(1e-6, p)));
  return Math.max(-sigma, Math.min(sigma, z)) / sigma;
}

/** mulberry32 — быстрый детерминированный PRNG (0..1) от 32-битного сида. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Строка (id портрета) → 32-битный сид, FNV-1a. */
export function hashSeed(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}
