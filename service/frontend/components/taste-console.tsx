"use client";

/**
 * v0.12 (SPEC-v12-human §C): «Пульт вкуса» — два больших слайдера
 * («грустнее ↔ веселее» = valence 0..100, «тише ↔ мощнее» = energy 0..100)
 * и мини-карта с курсором-«пятном», едущим за ними синхронно.
 *
 * Логика отклика: при отпускании ползунка — мгновенно (<100 мс) играет
 * превью ближайшего к точке трека библиотеки; при драге — не чаще ~700 мс.
 * Последние 3 сыгранных трека исключаются из поиска, чтобы пульт не залипал
 * на одном и том же. Сыгранная точка пульсирует на мини-карте.
 *
 * Превью: points из payload не несут preview_url, поэтому пульт держит
 * собственный пул label→url. При появлении секции резолвится «сетка» из
 * ≤24 точек, равномерно покрывающая карту (мгновенный первый отклик);
 * после каждого выбора фоном дорезолвливаются ближайшие к курсору точки —
 * пул постепенно уплотняется там, где пользователь копает. Бэк без
 * /api/preview/resolve (404) — пул остаётся пустым, пульт честно говорит,
 * что превью не нашлись.
 *
 * «Копнуть здесь» — существующий point-discovery с текущими координатами
 * (onDig прокидывает discoverPoint из portrait-view; без него кнопки нет).
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { resolvePreviews } from "@/lib/api";
import MiniMap from "@/components/mini-map";
import { splitTrackLabel, type PreviewPlayer } from "@/lib/use-audio";
import type { Cluster, PortraitPoint } from "@/lib/types";

const RESOLVE_BATCH = 24; // лимит POST /api/preview/resolve
const DRAG_THROTTLE_MS = 700;
const RELEASE_DEBOUNCE_MS = 350; // отпускание сразу после драг-пика — не дублируем
const HISTORY = 3; // сколько последних сыгранных исключаем

const GRID_COLS = 6;
const GRID_ROWS = 4;

interface PickedTrack {
  label: string;
  meta: string;
}

/** Сид пула: по одной точке на клетку сетки 6×4 — ровное покрытие карты. */
function seedSample(points: PortraitPoint[]): string[] {
  const cells = new Map<number, { label: string; d: number }>();
  const cellW = 100 / GRID_COLS;
  const cellH = 100 / GRID_ROWS;
  for (const p of points) {
    if (p.label === "") continue;
    const cx = Math.min(GRID_COLS - 1, Math.floor(p.x / cellW));
    const cy = Math.min(GRID_ROWS - 1, Math.floor(p.y / cellH));
    const centerX = (cx + 0.5) * cellW;
    const centerY = (cy + 0.5) * cellH;
    const d = (p.x - centerX) ** 2 + (p.y - centerY) ** 2;
    const key = cy * GRID_COLS + cx;
    const cur = cells.get(key);
    if (cur === undefined || d < cur.d) cells.set(key, { label: p.label, d });
  }
  return [...cells.values()].map((c) => c.label);
}

export default function TasteConsole({
  points,
  clusters,
  player,
  onDig,
}: {
  points: PortraitPoint[];
  clusters: Cluster[];
  player: PreviewPlayer;
  /** «Копнуть здесь» → point-discovery; undefined — кнопка скрыта */
  onDig?: (x: number, y: number) => void;
}) {
  // стартуем из центра масс библиотеки — первый же сдвиг попадает «в гущу»
  const start = useMemo(() => {
    if (points.length === 0) return { x: 50, y: 50 };
    let sx = 0;
    let sy = 0;
    for (const p of points) {
      sx += p.x;
      sy += p.y;
    }
    return {
      x: Math.round(sx / points.length),
      y: Math.round(sy / points.length),
    };
  }, [points]);

  const [x, setX] = useState(start.x);
  const [y, setY] = useState(start.y);
  const xRef = useRef(start.x);
  const yRef = useRef(start.y);

  const [card, setCard] = useState<PickedTrack | null>(null);
  const [pulse, setPulse] = useState<{ x: number; y: number; key: number } | null>(
    null,
  );
  const [note, setNote] = useState<string | null>(null);

  // пул превью: label → url | null («null» = превью нет, больше не спрашиваем)
  const poolRef = useRef<Map<string, string | null>>(new Map());
  const inFlightRef = useRef<Set<string>>(new Set());
  const resolvingRef = useRef(false);
  const pendingPickRef = useRef<{ x: number; y: number } | null>(null);
  const seededRef = useRef(false);

  const playedRef = useRef<string[]>([]);
  const pulseKeyRef = useRef(0);
  const lastPickRef = useRef<{ x: number; y: number; t: number }>({
    x: -1,
    y: -1,
    t: 0,
  });

  // player меняется каждый рендер — колбэкам ниже нужна свежая ссылка
  const playerRef = useRef(player);
  useEffect(() => {
    playerRef.current = player;
  });

  const pickAtRef = useRef<(vx: number, vy: number) => void>(() => {});

  /** Один батч resolve; по завершении — отложенный пик, если накопился. */
  const requestResolve = useCallback(async (labels: string[]) => {
    const pool = poolRef.current;
    const fresh = labels
      .filter((l) => !pool.has(l) && !inFlightRef.current.has(l))
      .slice(0, RESOLVE_BATCH);
    if (fresh.length === 0 || resolvingRef.current) return;
    resolvingRef.current = true;
    for (const l of fresh) inFlightRef.current.add(l);
    try {
      const { urls } = await resolvePreviews(fresh.map(splitTrackLabel));
      fresh.forEach((label, i) => {
        const url = urls[i];
        pool.set(label, typeof url === "string" && url !== "" ? url : null);
      });
    } catch {
      // 404 старого бэка / сеть: помечаем «нет», чтобы не долбить повторно
      for (const l of fresh) pool.set(l, null);
    } finally {
      for (const l of fresh) inFlightRef.current.delete(l);
      resolvingRef.current = false;
      const pending = pendingPickRef.current;
      if (pending !== null) {
        pendingPickRef.current = null;
        pickAtRef.current(pending.x, pending.y);
      }
    }
  }, []);

  /** Фоном уплотняем пул вокруг курсора — следующие пики точнее. */
  const resolveNear = useCallback(
    (vx: number, vy: number) => {
      if (resolvingRef.current) return;
      const pool = poolRef.current;
      const unresolved = points
        .filter(
          (p) =>
            p.label !== "" &&
            !pool.has(p.label) &&
            !inFlightRef.current.has(p.label),
        )
        .map((p) => ({
          label: p.label,
          d: (p.x - vx) ** 2 + (p.y - vy) ** 2,
        }))
        .sort((a, b) => a.d - b.d)
        .slice(0, RESOLVE_BATCH)
        .map((p) => p.label);
      if (unresolved.length > 0) void requestResolve(unresolved);
    },
    [points, requestResolve],
  );

  const seedPool = useCallback(() => {
    if (seededRef.current) return;
    seededRef.current = true;
    const sample = seedSample(points);
    if (sample.length > 0) void requestResolve(sample);
  }, [points, requestResolve]);

  /** Сердце пульта: ближайший трек с превью, минус последние 3 сыгранных. */
  const pickAt = useCallback(
    (vx: number, vy: number) => {
      const pool = poolRef.current;
      const played = playedRef.current;
      const playingUrl = playerRef.current.playingUrl;
      let best: { p: PortraitPoint; d: number; url: string } | null = null;
      for (const p of points) {
        const url = pool.get(p.label);
        if (typeof url !== "string" || url === "") continue;
        if (played.includes(p.label)) continue;
        if (url === playingUrl) continue; // toggle поставил бы паузу
        const d = (p.x - vx) ** 2 + (p.y - vy) ** 2;
        if (best === null || d < best.d) best = { p, d, url };
      }
      if (best === null) {
        // пул ещё пуст (или всё вокруг без превью) — доиграем после resolve
        const anyUnresolved = points.some(
          (p) => p.label !== "" && !pool.has(p.label),
        );
        if (anyUnresolved) {
          pendingPickRef.current = { x: vx, y: vy };
          setNote("ищем превью рядом…");
          seedPool();
          resolveNear(vx, vy);
        } else {
          setNote("у треков вокруг не нашлось превью — подвигай в другую сторону");
        }
        return;
      }
      const { p, url } = best;
      playerRef.current.toggle(url, { videoId: p.videoId, label: p.label });
      playedRef.current = [...played, p.label].slice(-HISTORY);
      pulseKeyRef.current += 1;
      setCard({ label: p.label, meta: p.meta });
      setPulse({ x: p.x, y: p.y, key: pulseKeyRef.current });
      setNote(null);
      resolveNear(vx, vy);
    },
    [points, resolveNear, seedPool],
  );
  useEffect(() => {
    pickAtRef.current = pickAt;
  }, [pickAt]);

  /* ---- сид пула при появлении секции на экране ---- */

  const rootRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const el = rootRef.current;
    if (el === null) return;
    if (typeof IntersectionObserver === "undefined") {
      seedPool();
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          seedPool();
          io.disconnect();
        }
      },
      { rootMargin: "200px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [seedPool]);

  /* ---- обработчики слайдеров ---- */

  const doPick = useCallback(
    (immediate: boolean) => {
      const now = Date.now();
      const last = lastPickRef.current;
      const vx = xRef.current;
      const vy = yRef.current;
      if (immediate) {
        // отпускание сразу после драг-пика той же точки — не дублируем звук
        if (
          last.x === vx &&
          last.y === vy &&
          now - last.t < RELEASE_DEBOUNCE_MS
        ) {
          return;
        }
      } else if (now - last.t < DRAG_THROTTLE_MS) {
        return;
      }
      lastPickRef.current = { x: vx, y: vy, t: now };
      pickAt(vx, vy);
    },
    [pickAt],
  );

  const onSlide = useCallback(
    (axis: "x" | "y", value: number) => {
      if (axis === "x") {
        xRef.current = value;
        setX(value);
      } else {
        yRef.current = value;
        setY(value);
      }
      doPick(false); // драг: не чаще ~700 мс
    },
    [doPick],
  );

  const onRelease = useCallback(() => doPick(true), [doPick]);

  const dig = useCallback(() => {
    if (onDig === undefined) return;
    onDig(xRef.current, yRef.current);
    // панель появится в «Ещё такого» ниже — подводим взгляд
    const reduced =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.setTimeout(() => {
      document
        .querySelector(".discover-panels")
        ?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
    }, 200);
  }, [onDig]);

  return (
    <section id="taste-console" className="console-section" ref={rootRef}>
      <h2 className="section-title">Пульт вкуса</h2>
      <p className="console-sub">
        крути настроение — библиотека мгновенно отвечает ближайшим треком
      </p>
      <div className="console-grid">
        <div className="console-controls">
          <label className="console-slider">
            <span className="console-slider-ends" aria-hidden>
              <span>грустнее</span>
              <span>веселее</span>
            </span>
            <input
              type="range"
              className="console-range"
              min={0}
              max={100}
              step={1}
              value={x}
              aria-label="Настроение: грустнее — веселее"
              onChange={(e) => onSlide("x", Number(e.target.value))}
              onPointerUp={onRelease}
              onTouchEnd={onRelease}
              onKeyUp={onRelease}
            />
          </label>
          <label className="console-slider">
            <span className="console-slider-ends" aria-hidden>
              <span>тише</span>
              <span>мощнее</span>
            </span>
            <input
              type="range"
              className="console-range"
              min={0}
              max={100}
              step={1}
              value={y}
              aria-label="Энергия: тише — мощнее"
              onChange={(e) => onSlide("y", Number(e.target.value))}
              onPointerUp={onRelease}
              onTouchEnd={onRelease}
              onKeyUp={onRelease}
            />
          </label>
          <div className="console-readout" aria-live="polite">
            {card !== null ? (
              <span className="console-card" title={`${card.label} · ${card.meta}`}>
                <span className="console-card-note" aria-hidden>
                  ♪
                </span>
                <span className="console-card-label">{card.label}</span>
                <span className="console-card-meta mono">{card.meta}</span>
              </span>
            ) : (
              <span className="console-card console-card-empty">
                {note ?? "подвигай ползунки — что-нибудь заиграет"}
              </span>
            )}
            {card !== null && note !== null && (
              <span className="console-note">{note}</span>
            )}
          </div>
          {onDig !== undefined && (
            <button
              type="button"
              className="btn btn-ghost btn-small console-dig-btn"
              onClick={dig}
            >
              ⛏️ Копнуть здесь
            </button>
          )}
        </div>
        <MiniMap
          points={points}
          clusters={clusters}
          cursor={{ x, y }}
          pulse={pulse}
        />
      </div>
    </section>
  );
}
