"use client";

/**
 * v0.12 (SPEC-v12-human §B/§C): мини-карта вкуса — компактный SVG-скаттер
 * точек библиотеки. Переиспользуется в двух местах:
 *   - глава «Кто ты» истории: клик по чипу архетипа подсвечивает его точки
 *     (highlightCluster — остальные гаснут);
 *   - «Пульт вкуса»: большой курсор-«пятно» (cursor) едет за слайдерами,
 *     а сыгранная точка пульсирует (pulse; смена pulse.key перезапускает
 *     CSS-анимацию через remount круга).
 * Только просмотр: без тултипов, кликов и discovery — это витрина, не пульт
 * управления картой.
 */

import { clusterColor } from "@/lib/colors";
import type { Cluster, PortraitPoint } from "@/lib/types";

const W = 480;
const H = 300;
const PAD = 16;

function px(x: number): number {
  return PAD + (x / 100) * (W - PAD * 2);
}

function py(y: number): number {
  // y=0 внизу (спокойнее), y=100 вверху (мощнее) — как на большой карте
  return H - PAD - (y / 100) * (H - PAD * 2);
}

export default function MiniMap({
  points,
  clusters,
  highlightCluster = null,
  cursor = null,
  pulse = null,
  ariaLabel = "Мини-карта вкуса: настроение по горизонтали, энергия по вертикали",
}: {
  points: PortraitPoint[];
  clusters: Cluster[];
  /** Индекс кластера для подсветки: его точки ярче, остальные гаснут */
  highlightCluster?: number | null;
  /** Курсор пульта в координатах карты 0..100 */
  cursor?: { x: number; y: number } | null;
  /** Пульс сыгранной точки; смена key перезапускает анимацию */
  pulse?: { x: number; y: number; key: number } | null;
  ariaLabel?: string;
}) {
  return (
    <div className="mini-map">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={ariaLabel}>
        {/* лёгкая сетка по половинам — только намёк на оси */}
        <line
          className="grid-line"
          x1={px(50)}
          y1={py(0)}
          x2={px(50)}
          y2={py(100)}
        />
        <line
          className="grid-line"
          x1={px(0)}
          y1={py(50)}
          x2={px(100)}
          y2={py(50)}
        />
        {points.map((p, i) => {
          const dim = highlightCluster !== null && p.cluster !== highlightCluster;
          return (
            <circle
              key={i}
              className="mini-map-point"
              cx={px(p.x)}
              cy={py(p.y)}
              r={dim ? 2 : 2.7}
              fill={clusterColor(clusters, p.cluster)}
              fillOpacity={dim ? 0.13 : 0.85}
            />
          );
        })}
        {pulse !== null && (
          <circle
            key={pulse.key}
            className="mini-map-pulse"
            cx={px(pulse.x)}
            cy={py(pulse.y)}
            r={7}
            aria-hidden
          />
        )}
        {cursor !== null && (
          <g className="console-cursor" aria-hidden>
            <circle
              className="console-cursor-glow"
              cx={px(cursor.x)}
              cy={py(cursor.y)}
              r={30}
            />
            <circle
              className="console-cursor-dot"
              cx={px(cursor.x)}
              cy={py(cursor.y)}
              r={7}
            />
          </g>
        )}
      </svg>
    </div>
  );
}
