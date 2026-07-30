/**
 * v0.11 (SPEC-v11-observatory §B4): мини-линейная-алгебра для PCA-биплота.
 *
 * Бэкенд отдаёт ТОЛЬКО loadings компонент (координаты точек не гоняем по
 * сети) — фронт проецирует существующие points сам: стандартизует фичи
 * по своим точкам (z-оценки, ddof=0 — как sklearn StandardScaler) и
 * умножает на loadings. Без библиотек; инварианты покрыты консольной
 * проверкой на синтетике (см. §C — прогонялась при сборке волны).
 */

/** Среднее и стандартное отклонение (популяционное, ddof=0). */
export function meanStd(values: readonly number[]): {
  mean: number;
  std: number;
} {
  const n = values.length;
  if (n === 0) return { mean: 0, std: 0 };
  let sum = 0;
  for (const v of values) sum += v;
  const mean = sum / n;
  let varSum = 0;
  for (const v of values) varSum += (v - mean) ** 2;
  return { mean, std: Math.sqrt(varSum / n) };
}

/**
 * Матрица n×f → z-оценки по колонкам. Вырожденная колонка (std=0,
 * все значения равны) даёт нули — точки не разъезжаются по мусорной оси.
 */
export function standardizeColumns(
  rows: readonly (readonly number[])[],
): number[][] {
  if (rows.length === 0) return [];
  const f = rows[0].length;
  const stats = Array.from({ length: f }, (_, j) =>
    meanStd(rows.map((row) => row[j])),
  );
  return rows.map((row) =>
    row.map((v, j) =>
      stats[j].std > 0 ? (v - stats[j].mean) / stats[j].std : 0,
    ),
  );
}

/**
 * Проекция z-оценок (n×f) на k компонент (loadings k×f) → scores n×k:
 * score[i][a] = Σ_j z[i][j] · w[a][j]. Недостающие веса считаются нулём.
 */
export function projectOnLoadings(
  z: readonly (readonly number[])[],
  loadings: readonly (readonly number[])[],
): number[][] {
  return z.map((row) =>
    loadings.map((w) => {
      let s = 0;
      for (let j = 0; j < row.length; j++) s += row[j] * (w[j] ?? 0);
      return s;
    }),
  );
}
