import type { Cluster } from "./types";

/** Резервные цвета кластеров — если бэк не прислал color. */
export const FALLBACK_COLORS = [
  "#7f77dd",
  "#5fb3a1",
  "#d98a6a",
  "#c76a9e",
  "#6a9ed9",
  "#b3ad5f",
];

export function clusterColor(clusters: Cluster[], index: number): string {
  return (
    clusters[index]?.color ?? FALLBACK_COLORS[index % FALLBACK_COLORS.length]
  );
}
