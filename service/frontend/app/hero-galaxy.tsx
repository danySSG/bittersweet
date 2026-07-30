"use client";

/**
 * v0.9 (SPEC-v09-features §D): живая галактика за hero-текстом лендинга.
 * Клиентский фетч /api/demo/portrait → GalaxyView variant="hero" в фоновом
 * слое (opacity ~0.5, pointer-events: none, автовращение медленнее).
 * Бэк недоступен или портрет без features → тихий фолбэк на прежние
 * CSS-точки. prefers-reduced-motion обрабатывает сам GalaxyView
 * (статичный кадр без вращения). Canvas рендерится сразу (до данных) —
 * SSR-разметка лендинга содержит галактику, дублирующих CSS-точек нет.
 */

import { useEffect, useState } from "react";
import { fetchPortrait } from "@/lib/api";
import GalaxyView, { hasGalaxyFeatures } from "@/components/galaxy-view";
import type { Portrait } from "@/lib/types";

/** «Живые точки»-кластеры: 12 элементов, анимация — чистый CSS (фолбэк). */
function HeroDots() {
  return (
    <div className="hero-dots" aria-hidden>
      {Array.from({ length: 12 }, (_, i) => (
        <span key={i} />
      ))}
    </div>
  );
}

export default function HeroGalaxy() {
  const [portrait, setPortrait] = useState<Portrait | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchPortrait(true)
      .then((data) => {
        if (!cancelled) setPortrait(data);
      })
      .catch(() => {
        // бэк не запущен / сеть — молча остаёмся на CSS-точках
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // фолбэк: бэк недоступен ИЛИ демо-портрет без points[].features (старый бэк)
  if (failed || (portrait !== null && !hasGalaxyFeatures(portrait.points))) {
    return <HeroDots />;
  }

  return (
    <div className="hero-galaxy" aria-hidden>
      <GalaxyView
        points={portrait?.points ?? []}
        clusters={portrait?.clusters ?? []}
        variant="hero"
      />
    </div>
  );
}
