"use client";

/**
 * v0.6 (SPEC-v06-ux, раздел B2): 3D-«галактика вкуса» на canvas.
 *
 * Перенос выверенного прототипа (scratchpad/taste-galaxy-3d.html):
 * вращение «схватил объект» (rotY -= dx, rotX -= dy — знаки выверены),
 * автовращение с паузой при drag и возвратом через 2.5 c, тултип по ближней
 * спроецированной точке, DPR ≤ 2. prefers-reduced-motion: reduce —
 * автовращение выключено совсем, рисуем только по взаимодействию.
 *
 * Оси считаются на клиенте из points[].features (перцентили по портрету):
 *   X = 1 − perc(minor_score)  — настроение (грусть ↔ радость)
 *   Y = perc(композит tempo, energy_rms, percussive, onset_rate) — энергия
 *   Z = perc(brightness)       — тембр (глубина)
 *
 * v0.9 (SPEC-v09-features §B/D):
 * - onAim: клик по звезде (не drag) → поповер «Найти музыку здесь» с x/y
 *   этой звезды В ОСЯХ 2D-КАРТЫ (valence/energy-перцентили бэка — их ест
 *   POST /api/discover point-режим). Клик мимо звёзд — ничего: в 3D пустота
 *   неоднозначна. Hover-тултип не тронут — клик и hover не конфликтуют.
 * - variant="hero": фоновая галактика лендинга — автовращение медленнее,
 *   без HUD/тултипа/подписи осей (контейнер сам глушит pointer-events).
 *
 * v0.10 (SPEC-v10-critique §B, «галактика кубическая»):
 * - сферизация: перцентиль каждой оси → probit (обратная CDF нормали,
 *   рациональная аппроксимация Аклама, lib/probit.ts), клип ±2.5σ,
 *   нормировка — гауссово облако вместо куба;
 * - атмосфера: слабое фиолетовое свечение в центре сцены + усиленный
 *   глубинный туман (дальние точки заметно бледнее и меньше) + пылинки
 *   с сидированным дрейфом (mulberry32 от hash(portrait_id) — БЕЗ
 *   Math.random в рендер-цикле);
 * - §C: hero-вариант вдвое медленнее, центр смещён вправо от текста.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { clusterColor } from "@/lib/colors";
import { gaussianAxis, hashSeed, mulberry32 } from "@/lib/probit";
import PointAimPopover from "@/components/point-popover";
import type { Cluster, PortraitPoint } from "@/lib/types";

/** Признаки, без которых точку не спроецировать в 3D. */
const AXIS_FEATURES = [
  "minor_score",
  "brightness",
  "tempo",
  "energy_rms",
  "percussive",
  "onset_rate",
] as const;

/** Композит энергии для оси Y. */
const ENERGY_FEATURES = [
  "tempo",
  "energy_rms",
  "percussive",
  "onset_rate",
] as const;

/** Скорость автовращения: hero-фон лендинга крутится заметно медленнее
 *  (v0.10 §C: вдвое медленнее прежнего — вращение не должно мешать читать). */
const AUTOROT_SPEED = 0.0035;
const AUTOROT_SPEED_HERO = 0.0006;

/** Дальше этого (px²) клик/hover звезду не находит. */
const HIT_RADIUS_SQ = 144;

/** v0.10 §C: центр hero-галактики смещён вправо от текстовой колонки. */
const HERO_CENTER_X = 0.68;

/** v0.10 §B: пылинки атмосферы — количество и границы облака. */
const DUST_COUNT = 28;

function pointUsable(p: PortraitPoint): boolean {
  const f = p.features;
  if (f === undefined || f === null) return false;
  return AXIS_FEATURES.every(
    (name) => typeof f[name] === "number" && Number.isFinite(f[name]),
  );
}

/**
 * Есть ли у портрета топливо для галактики? Старые портреты без
 * points[].features → false, переключатель «Карта | Галактика» скрыт.
 */
export function hasGalaxyFeatures(points: PortraitPoint[]): boolean {
  // одна точка — не галактика: перцентили вырождаются в центр сцены
  return points.filter(pointUsable).length >= 2;
}

/** Перцентильные ранги 0..1 (по рангу; n ≤ 1 — все в середине). */
function percentileRanks(values: number[]): number[] {
  const n = values.length;
  if (n <= 1) return values.map(() => 0.5);
  const order = values
    .map((v, i) => [v, i] as const)
    .sort((a, b) => a[0] - b[0]);
  const out = new Array<number>(n).fill(0.5);
  for (let rank = 0; rank < n; rank++) {
    out[order[rank][1]] = rank / (n - 1);
  }
  return out;
}

interface Star {
  x: number;
  y: number;
  z: number;
  color: string;
  label: string;
  meta: string;
  /** Координаты исходной точки в осях 2D-карты (0..100) — для point-discovery */
  mapX: number;
  mapY: number;
}

/**
 * Точки портрета → звёзды гауссова облака в [-1, 1]³.
 * v0.10 §B: перцентиль оси — не координата напрямую (это давало куб),
 * а вход probit: обратная CDF нормали растягивает середину и поджимает
 * края → сферичное облако с плотным ядром. Клип ±2.5σ + нормировка.
 */
function buildStars(points: PortraitPoint[], clusters: Cluster[]): Star[] {
  const usable = points.filter(pointUsable);
  const column = (name: string): number[] =>
    usable.map((p) => (p.features as Record<string, number>)[name]);

  const minor = percentileRanks(column("minor_score"));
  const bright = percentileRanks(column("brightness"));
  // энергия — среднее перцентилей четырёх признаков, снова в перцентили,
  // чтобы облако не сжималось к центру оси
  const parts = ENERGY_FEATURES.map((name) => percentileRanks(column(name)));
  const energy = percentileRanks(
    usable.map((_, i) => parts.reduce((sum, col) => sum + col[i], 0) / parts.length),
  );

  // перцентили дают крайним точкам p=0 и p=1 — probit клипят края в ±2.5σ,
  // но чтобы хвосты не слипались на границе, сжимаем p к (0.5±0.49)
  const soften = (p: number): number => 0.01 + p * 0.98;

  return usable.map((p, i) => ({
    x: gaussianAxis(soften(1 - minor[i])),
    y: gaussianAxis(soften(energy[i])),
    z: gaussianAxis(soften(bright[i])),
    color: clusterColor(clusters, p.cluster),
    label: p.label,
    meta: p.meta,
    mapX: p.x,
    mapY: p.y,
  }));
}

/* ---------- v0.10 §B: пылинки атмосферы ---------- */

interface Dust {
  x: number;
  y: number;
  z: number;
  /** Радиус, px (до глубинного масштаба) */
  r: number;
  alpha: number;
  /** Параметры дрейфа — сидированы при создании, в кадре только sin/cos */
  phase: number;
  freq: number;
  ampX: number;
  ampY: number;
}

/**
 * 2–3 десятка полупрозрачных микро-точек со случайным дрейфом.
 * Сид фиксирован (hash от id портрета / данных) — кадры детерминированы,
 * Math.random в рендер-цикле не используется.
 */
function buildDust(seedKey: string): Dust[] {
  const rnd = mulberry32(hashSeed(seedKey));
  const dust: Dust[] = [];
  for (let i = 0; i < DUST_COUNT; i++) {
    dust.push({
      // чуть шире облака звёзд — атмосфера обнимает галактику
      x: (rnd() * 2 - 1) * 1.15,
      y: (rnd() * 2 - 1) * 1.15,
      z: (rnd() * 2 - 1) * 1.15,
      r: 0.4 + rnd() * 0.8,
      alpha: 0.05 + rnd() * 0.1,
      phase: rnd() * Math.PI * 2,
      freq: 0.00015 + rnd() * 0.00035,
      ampX: 0.03 + rnd() * 0.05,
      ampY: 0.03 + rnd() * 0.05,
    });
  }
  return dust;
}

/** Поповер прицела над кликнутой звездой (координаты сцены в px). */
interface GalaxyAim {
  left: number;
  top: number;
  /** Поповер над звездой (низ сцены) или под ней */
  above: boolean;
  x: number;
  y: number;
}

export default function GalaxyView({
  points,
  clusters,
  onAim,
  variant = "default",
  seed,
}: {
  points: PortraitPoint[];
  clusters: Cluster[];
  /** v0.9: подтверждённый прицел по звезде → discovery по точке карты */
  onAim?: (x: number, y: number) => void;
  /** hero — приглушённый фон лендинга: медленнее, без HUD и тултипа */
  variant?: "default" | "hero";
  /** v0.10 §B: сид пылинок — id портрета; без него — от данных точек */
  seed?: string;
}) {
  const stars = useMemo(() => buildStars(points, clusters), [points, clusters]);
  const hero = variant === "hero";
  const dust = useMemo(
    () => buildDust(seed ?? `galaxy:${points.length}:${points[0]?.label ?? ""}`),
    [seed, points],
  );

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  const tipTitleRef = useRef<HTMLElement | null>(null);
  const tipMetaRef = useRef<HTMLElement | null>(null);

  // v0.9: прицел по клику на звезду; onAim в ref — не перезапускать сцену
  const [aim, setAim] = useState<GalaxyAim | null>(null);
  const onAimRef = useRef(onAim);
  useEffect(() => {
    onAimRef.current = onAim;
  });
  useEffect(() => setAim(null), [stars]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    // тултип есть только в обычном варианте — hero живёт без него
    const tip = tipRef.current;
    const tipTitle = tipTitleRef.current;
    const tipMeta = tipMetaRef.current;

    // prefers-reduced-motion: reduce → без автовращения (SPEC-v06-ux §B2)
    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;

    let W = 0;
    let H = 0;
    const DPR = Math.min(window.devicePixelRatio || 1, 2);
    const speed = hero ? AUTOROT_SPEED_HERO : AUTOROT_SPEED;

    let rotY = 0.6;
    let rotX = 0.25;
    let autorot = !reducedMotion;
    let dragging = false;
    let moved = false; // сдвиг за drag > 4px — это вращение, не клик
    let px = 0;
    let py = 0;
    let resumeTimer: number | undefined;
    let needsDraw = true;
    let raf = 0;

    const proj: ({ sx: number; sy: number; d: number; z: number } | null)[] =
      new Array(stars.length).fill(null);

    const resize = () => {
      W = canvas.clientWidth;
      H = canvas.clientHeight;
      canvas.width = Math.max(1, Math.round(W * DPR));
      canvas.height = Math.max(1, Math.round(H * DPR));
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
      glow = null; // размер сцены изменился — свечение пересоздаётся
      needsDraw = true;
    };

    // v0.10 §B: свечение центра кэшируется — пересоздаётся только на resize
    let glow: CanvasGradient | null = null;

    const buildGlow = (centerX: number) => {
      const radius = Math.max(1, Math.min(W, H) * 0.55);
      const g = ctx.createRadialGradient(
        centerX,
        H / 2,
        0,
        centerX,
        H / 2,
        radius,
      );
      g.addColorStop(0, "rgba(127, 119, 221, 0.14)");
      g.addColorStop(0.5, "rgba(127, 119, 221, 0.055)");
      g.addColorStop(1, "rgba(127, 119, 221, 0)");
      return g;
    };

    const draw = () => {
      ctx.clearRect(0, 0, W, H);
      // v0.10 §C: у hero-фона центр сцены смещён вправо от текстовой колонки
      const centerX = hero ? W * HERO_CENTER_X : W / 2;
      const cy = Math.cos(rotY);
      const sy = Math.sin(rotY);
      const cx = Math.cos(rotX);
      const sx = Math.sin(rotX);
      const scale = Math.min(W, H) * 0.36;
      const f = 2.6; // перспектива

      // (1) атмосфера: слабое фиолетовое свечение в центре сцены
      if (glow === null) glow = buildGlow(centerX);
      ctx.fillStyle = glow;
      ctx.fillRect(0, 0, W, H);

      // (3) пылинки: сидированный дрейф по времени — без Math.random в кадре
      const t = performance.now();
      for (const m of dust) {
        const dx = m.x + Math.sin(t * m.freq + m.phase) * m.ampX;
        const dy = m.y + Math.cos(t * m.freq * 0.83 + m.phase) * m.ampY;
        const x = dx * cy + m.z * sy;
        const z1 = -dx * sy + m.z * cy;
        const y = dy * cx - z1 * sx;
        const z = dy * sx + z1 * cx;
        const d = f / (f + z);
        ctx.globalAlpha = m.alpha;
        ctx.fillStyle = "#b8b4e6";
        ctx.beginPath();
        ctx.arc(
          centerX + x * scale * d,
          H / 2 - y * scale * d,
          m.r * d,
          0,
          7,
        );
        ctx.fill();
      }

      const order: number[] = [];
      for (let i = 0; i < stars.length; i++) {
        const p = stars[i];
        const x = p.x * cy + p.z * sy;
        const z1 = -p.x * sy + p.z * cy;
        const y = p.y * cx - z1 * sx;
        const z = p.y * sx + z1 * cx;
        const d = f / (f + z);
        proj[i] = {
          sx: centerX + x * scale * d,
          sy: H / 2 - y * scale * d,
          d,
          z,
        };
        order.push(i);
      }
      // дальние — первыми, ближние поверх
      order.sort((a, b) => (proj[b]?.z ?? 0) - (proj[a]?.z ?? 0));
      for (const i of order) {
        const q = proj[i];
        if (q === null) continue;
        // (2) глубинный туман, усиленная кривая (v0.10 §B): нормируем по
        // повёрнутой z (гауссово облако компактнее куба — d почти не
        // различает глубину); дальние (z→1) бледнее и меньше степенно
        const depth = Math.max(0, Math.min(1, (1 - q.z) / 2));
        const r = 1.4 + 3.4 * Math.pow(depth, 1.4);
        const a = Math.max(
          0.08,
          Math.min(0.95, 0.1 + 0.85 * Math.pow(depth, 1.6)),
        );
        ctx.globalAlpha = a;
        ctx.fillStyle = stars[i].color;
        ctx.beginPath();
        ctx.arc(q.sx, q.sy, r, 0, 7);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    };

    const frame = () => {
      if (autorot && !dragging) {
        rotY += speed;
        needsDraw = true;
      }
      if (needsDraw) {
        draw();
        needsDraw = false;
      }
      raf = requestAnimationFrame(frame);
    };

    const hideTip = () => {
      if (tip !== null) tip.style.opacity = "0";
    };

    /** Ближняя спроецированная звезда в радиусе 12px от точки сцены. */
    const hitTest = (mx: number, my: number): number => {
      let best = -1;
      let bestDist = HIT_RADIUS_SQ;
      for (let i = 0; i < proj.length; i++) {
        const q = proj[i];
        if (q === null) continue;
        const dd = (q.sx - mx) ** 2 + (q.sy - my) ** 2;
        if (dd < bestDist) {
          bestDist = dd;
          best = i;
        }
      }
      return best;
    };

    const onPointerDown = (e: PointerEvent) => {
      dragging = true;
      moved = false;
      autorot = false;
      window.clearTimeout(resumeTimer);
      px = e.clientX;
      py = e.clientY;
      canvas.setPointerCapture(e.pointerId);
      hideTip();
      setAim(null); // новый клик/drag прячет прежний поповер
    };

    const onPointerUp = (e: PointerEvent) => {
      const wasClick = dragging && !moved;
      dragging = false;
      // автовращение возвращается через 2.5 c; при reduce — никогда
      if (!reducedMotion) {
        resumeTimer = window.setTimeout(() => {
          autorot = true;
        }, 2500);
      }
      // v0.9: клик (не drag) по звезде → поповер прицела; мимо звёзд — ничего
      if (!wasClick || onAimRef.current === undefined) return;
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const best = hitTest(mx, my);
      if (best < 0) return;
      const q = proj[best];
      const star = stars[best];
      if (q === null || star === undefined) return;
      setAim({
        left: Math.min(Math.max(q.sx, 110), Math.max(110, W - 110)),
        top: q.sy,
        above: q.sy > H * 0.55,
        x: star.mapX,
        y: star.mapY,
      });
    };

    const onPointerMove = (e: PointerEvent) => {
      if (dragging) {
        // «схватил объект»: знаки выверены в прототипе — не менять
        const dx = e.clientX - px;
        const dy = e.clientY - py;
        if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
        rotY -= dx * 0.006;
        rotX -= dy * 0.006;
        rotX = Math.max(-1.3, Math.min(1.3, rotX));
        px = e.clientX;
        py = e.clientY;
        needsDraw = true;
        return;
      }
      if (tip === null || tipTitle === null || tipMeta === null) return;
      // hover: ближняя спроецированная точка в радиусе 12px
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const best = hitTest(mx, my);
      if (best >= 0) {
        const star = stars[best];
        tipTitle.textContent = star.label;
        tipMeta.textContent = star.meta;
        tip.style.opacity = "1";
        // не выпускаем тултип за края сцены
        const left = Math.min(mx + 14, Math.max(0, W - tip.offsetWidth - 8));
        const top = Math.min(my + 14, Math.max(0, H - tip.offsetHeight - 8));
        tip.style.left = `${left}px`;
        tip.style.top = `${top}px`;
      } else {
        hideTip();
      }
    };

    const onPointerLeave = () => hideTip();

    resize();
    // первый кадр — синхронно: в фоновой вкладке rAF не тикает,
    // а сцена должна быть нарисована сразу после переключения
    draw();
    needsDraw = false;
    window.addEventListener("resize", resize);
    // hero-фон лендинга не интерактивен (pointer-events: none) — слушатели
    // не нужны вовсе, только resize и автовращение
    if (!hero) {
      canvas.addEventListener("pointerdown", onPointerDown);
      canvas.addEventListener("pointerup", onPointerUp);
      canvas.addEventListener("pointermove", onPointerMove);
      canvas.addEventListener("pointerleave", onPointerLeave);
    }
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(resumeTimer);
      window.removeEventListener("resize", resize);
      if (!hero) {
        canvas.removeEventListener("pointerdown", onPointerDown);
        canvas.removeEventListener("pointerup", onPointerUp);
        canvas.removeEventListener("pointermove", onPointerMove);
        canvas.removeEventListener("pointerleave", onPointerLeave);
      }
    };
  }, [stars, dust, hero]);

  return (
    <div className={hero ? "galaxy-block galaxy-block-hero" : "galaxy-block"}>
      <div className="galaxy-stage">
        <canvas
          ref={canvasRef}
          role="img"
          aria-label="Галактика вкуса: настроение по X, энергия по Y, тембр в глубину"
        />
        {!hero && (
          <>
            <div className="galaxy-hud" aria-hidden>
              тяни — вращать · наведись — трек
              {onAim !== undefined ? " · клик — искать музыку" : ""}
            </div>
            <div className="galaxy-tip" ref={tipRef} role="status">
              <b ref={tipTitleRef} />
              <span ref={tipMetaRef} />
            </div>
            {aim !== null && onAim !== undefined && (
              <PointAimPopover
                x={aim.x}
                y={aim.y}
                className={
                  aim.above ? "point-popover-above" : "point-popover-below"
                }
                style={{ left: `${aim.left}px`, top: `${aim.top}px` }}
                onConfirm={() => {
                  onAim(aim.x, aim.y);
                  setAim(null);
                }}
                onCancel={() => setAim(null)}
              />
            )}
          </>
        )}
      </div>
      {!hero && (
        <p className="galaxy-axes">X настроение · Y энергия · Z тембр (глубина)</p>
      )}
    </div>
  );
}
