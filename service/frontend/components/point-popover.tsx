"use client";

/**
 * v0.9 (SPEC-v09-features §B): поповер прицела 🎯 — общий для 2D-карты
 * (клик по пустому месту) и галактики (клик по звезде). Показывает
 * координаты точки в осях карты и явные кнопки — discovery не стартует
 * без подтверждения (принцип явного контроля).
 */

import type { CSSProperties } from "react";

export default function PointAimPopover({
  x,
  y,
  style,
  className,
  onConfirm,
  onCancel,
}: {
  /** Координаты в осях карты: valence/energy-перцентили 0..100 */
  x: number;
  y: number;
  /** Позиционирование внутри relative-контейнера сцены */
  style?: CSSProperties;
  className?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div
      className={className !== undefined ? `point-popover ${className}` : "point-popover"}
      style={style}
      role="dialog"
      aria-label="Поиск музыки по точке карты"
    >
      <div className="point-popover-label mono">
        настроение {Math.round(x)} · энергия {Math.round(y)}
      </div>
      <div className="point-popover-actions">
        <button
          type="button"
          className="btn btn-primary btn-small"
          onClick={onConfirm}
        >
          Найти музыку здесь
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-small"
          onClick={onCancel}
        >
          Отмена
        </button>
      </div>
    </div>
  );
}
