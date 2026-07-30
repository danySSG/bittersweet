"use client";

/**
 * v0.12 (SPEC-v12-human §B): вид «История» — флагманский режим страницы
 * портрета. Та же математика, что в портрете, но наружу — рассказ:
 * бэкенд (GET /api/p/{id}/story) отдаёт готовые главы на русском, фронт
 * рисует вертикальный скроллителлинг с мягкими появлениями глав
 * (IntersectionObserver; prefers-reduced-motion выключает анимацию),
 * крупной типографикой и интерактивами:
 *   - archetype_chips — чипы «личностей», клик подсвечивает точки на мини-карте;
 *   - play_track / play_list — карточки с ▶ (общий use-audio, ленивый resolve);
 *   - timeline_mini — компактный существующий график динамики;
 *   - cta (финал) — три больших кнопки: радио / пульт / квиз.
 *
 * Деградации: 404 старого бэка — мягкая заглушка «истории пока нет»;
 * 409 — «перестрой портрет»; прочее — ошибка с retry. Ответ бэка
 * нормализуется защитно: кривые главы/интерактивы молча пропускаются.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ApiError, fetchStory, resolvePreviews } from "@/lib/api";
import MiniMap from "@/components/mini-map";
import TasteTimeline, { prepareBuckets } from "@/components/taste-timeline";
import {
  PreviewPlayButton,
  usePreviewResolver,
  type PreviewButtonState,
} from "@/components/preview-play";
import {
  splitTrackLabel,
  type PreviewPlayer,
  type QueueTrack,
} from "@/lib/use-audio";
import type {
  Cluster,
  Portrait,
  PortraitPoint,
  StoryChapter,
} from "@/lib/types";

/* ================= Нормализация ответа бэка ================= */

interface PlayItem {
  label: string;
  videoId: string | null;
  previewUrl: string | null;
}

interface ChipItem {
  cluster: number;
  name: string;
  emoji: string;
  share: number | null;
  color: string | null;
}

interface CtaAction {
  id: "radio" | "dig" | "quiz";
  label: string;
}

type Interactive =
  | { kind: "archetype_chips"; archetypes: ChipItem[] }
  | { kind: "play_track"; item: PlayItem }
  | { kind: "play_list"; items: PlayItem[] }
  | { kind: "timeline_mini" }
  | { kind: "cta"; actions: CtaAction[] };

interface Chapter {
  key: string;
  emoji: string;
  title: string;
  paragraphs: string[];
  interactive: Interactive | null;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function str(v: unknown): string | null {
  return typeof v === "string" && v.trim() !== "" ? v.trim() : null;
}

function numOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function normalizePlayItem(v: unknown): PlayItem | null {
  if (!isRecord(v)) return null;
  const label = str(v.label);
  if (label === null) return null;
  return {
    label,
    videoId: str(v.videoId),
    previewUrl: str(v.preview_url),
  };
}

function normalizeInteractive(raw: unknown): Interactive | null {
  if (!isRecord(raw)) return null;
  const kind = str(raw.kind);
  const data = raw.data;
  if (kind === "archetype_chips" && isRecord(data)) {
    const list = Array.isArray(data.archetypes) ? data.archetypes : [];
    const archetypes: ChipItem[] = [];
    for (const item of list) {
      if (!isRecord(item)) continue;
      const name = str(item.name);
      const cluster = numOrNull(item.cluster);
      if (name === null || cluster === null) continue;
      archetypes.push({
        cluster,
        name,
        emoji: str(item.emoji) ?? "🎵",
        share: numOrNull(item.share),
        color: str(item.color),
      });
    }
    return archetypes.length > 0 ? { kind: "archetype_chips", archetypes } : null;
  }
  if (kind === "play_track") {
    const item = normalizePlayItem(data);
    return item !== null && (item.previewUrl !== null || item.videoId !== null || item.label !== "")
      ? { kind: "play_track", item }
      : null;
  }
  if (kind === "play_list" && isRecord(data)) {
    const list = Array.isArray(data.items) ? data.items : [];
    const items = list
      .map(normalizePlayItem)
      .filter((item): item is PlayItem => item !== null);
    return items.length > 0 ? { kind: "play_list", items } : null;
  }
  if (kind === "timeline_mini") return { kind: "timeline_mini" };
  if (kind === "cta" && isRecord(data)) {
    const list = Array.isArray(data.actions) ? data.actions : [];
    const actions: CtaAction[] = [];
    for (const item of list) {
      if (!isRecord(item)) continue;
      const id = str(item.id);
      const label = str(item.label);
      if (label === null) continue;
      if (id === "radio" || id === "dig" || id === "quiz") {
        actions.push({ id, label });
      }
    }
    return actions.length > 0 ? { kind: "cta", actions } : null;
  }
  return null; // незнакомый интерактив — молча без него
}

function normalizeChapters(raw: unknown): Chapter[] {
  if (!isRecord(raw) || !Array.isArray(raw.chapters)) return [];
  const out: Chapter[] = [];
  (raw.chapters as StoryChapter[]).forEach((chapter, i) => {
    if (!isRecord(chapter)) return;
    const paragraphs = Array.isArray(chapter.paragraphs)
      ? chapter.paragraphs
          .map((p) => str(p))
          .filter((p): p is string => p !== null)
      : [];
    const title = str(chapter.title);
    if (paragraphs.length === 0 && title === null) return;
    out.push({
      key: str(chapter.id) ?? `chapter-${i}`,
      emoji: str(chapter.emoji) ?? "✨",
      title: title ?? "",
      paragraphs,
      interactive: normalizeInteractive(chapter.interactive),
    });
  });
  return out;
}

/* ================= Появление глав при доскролле ================= */

/** Глава: fade/slide при входе в вьюпорт; reduced-motion — сразу видима. */
function ChapterReveal({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLElement | null>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (el === null) return;
    const reduced =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced || typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
          io.disconnect();
        }
      },
      { threshold: 0.1, rootMargin: "0px 0px -6% 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <section
      ref={ref}
      className={visible ? "story-chapter story-chapter-visible" : "story-chapter"}
    >
      {children}
    </section>
  );
}

/* ================= Интерактивы ================= */

function ArchetypeChips({
  archetypes,
  points,
  clusters,
}: {
  archetypes: ChipItem[];
  points: PortraitPoint[];
  clusters: Cluster[];
}) {
  const [active, setActive] = useState<number | null>(null);
  return (
    <div className="story-interactive">
      <div className="story-chips">
        {archetypes.map((a) => (
          <button
            key={a.cluster}
            type="button"
            className={
              active === a.cluster ? "story-chip story-chip-active" : "story-chip"
            }
            aria-pressed={active === a.cluster}
            onClick={() =>
              setActive((cur) => (cur === a.cluster ? null : a.cluster))
            }
          >
            <span
              className="cluster-dot"
              style={{ background: a.color ?? undefined }}
              aria-hidden
            />
            <span aria-hidden>{a.emoji}</span> {a.name}
            {a.share !== null && (
              <span className="mono story-chip-share">
                {Math.round(a.share)}%
              </span>
            )}
          </button>
        ))}
      </div>
      <MiniMap
        points={points}
        clusters={clusters}
        highlightCluster={active}
      />
      <p className="story-hint">
        нажми на настроение — подсветим его треки на карте
      </p>
    </div>
  );
}

function PlayRow({
  item,
  player,
  stateFor,
  onLazy,
}: {
  item: PlayItem;
  player: PreviewPlayer;
  stateFor: (label: string, known?: string | null) => PreviewButtonState;
  onLazy: (label: string) => void;
}) {
  return (
    <div className="story-track">
      <PreviewPlayButton
        label={item.label}
        state={stateFor(item.label, item.previewUrl)}
        player={player}
        onLazy={onLazy}
        videoId={item.videoId}
      />
      <span className="story-track-label" title={item.label}>
        {item.label}
      </span>
    </div>
  );
}

/* ================= Сам вид «История» ================= */

type StoryStatus = "loading" | "ready" | "missing" | "stale" | "error";

const RADIO_BATCH = 24;

export default function StoryView({
  portraitId,
  portrait,
  player,
  onOpenQuiz,
  onDig,
  onShowPortrait,
}: {
  portraitId: string;
  portrait: Portrait;
  player: PreviewPlayer;
  /** CTA финала «квиз» — открыть «Угадай себя» */
  onOpenQuiz: () => void;
  /** CTA финала «пульт» — переключить на «Портрет» и подвести к пульту */
  onDig: () => void;
  /** Заглушки без истории предлагают вид «Портрет» */
  onShowPortrait: () => void;
}) {
  const [status, setStatus] = useState<StoryStatus>("loading");
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    void attempt;
    let cancelled = false;
    setStatus("loading");
    fetchStory(portraitId)
      .then((raw) => {
        if (cancelled) return;
        const normalized = normalizeChapters(raw);
        if (normalized.length === 0) {
          setStatus("missing"); // пустой ответ — честно как «истории нет»
          return;
        }
        setChapters(normalized);
        setStatus("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setStatus("missing");
          return;
        }
        if (err instanceof ApiError && err.status === 409) {
          setStatus("stale");
          return;
        }
        setError(
          err instanceof Error ? err.message : "История не рассказалась.",
        );
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [portraitId, attempt]);

  /* ---- ленивые превью для треков глав ---- */

  const lazyLabels = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const chapter of chapters) {
      const it = chapter.interactive;
      if (it === null) continue;
      const items =
        it.kind === "play_track"
          ? [it.item]
          : it.kind === "play_list"
            ? it.items
            : [];
      for (const item of items) {
        if (item.previewUrl === null && !seen.has(item.label)) {
          seen.add(item.label);
          out.push(item.label);
        }
      }
    }
    return out.slice(0, 24);
  }, [chapters]);

  const { stateFor, onLazy } = usePreviewResolver(lazyLabels, player);

  /* ---- CTA «радио»: очередь превью главного настроения ---- */

  const [radioBusy, setRadioBusy] = useState(false);
  const [radioNote, setRadioNote] = useState<string | null>(null);

  const startRadio = useCallback(async () => {
    if (radioBusy) return;
    const emoji = portrait.clusters[0]?.archetype?.emoji ?? "📻";
    const candidates = portrait.points
      .filter((p) => p.cluster === 0 && p.label !== "")
      .slice(0, RADIO_BATCH);
    if (candidates.length === 0) {
      setRadioNote("в главном настроении не нашлось треков — странно, но бывает");
      return;
    }
    setRadioBusy(true);
    setRadioNote(null);
    try {
      const { urls } = await resolvePreviews(
        candidates.map((p) => splitTrackLabel(p.label)),
      );
      const tracks: QueueTrack[] = candidates
        .map((p, i) => ({
          url: typeof urls[i] === "string" && urls[i] !== "" ? urls[i] : null,
          videoId: p.videoId,
          label: p.label,
          meta: emoji,
        }))
        .filter((t) => t.url !== null || (t.videoId ?? null) !== null);
      if (tracks.length === 0) {
        setRadioNote("не нашли ни одного превью — радио сегодня молчит");
      } else {
        player.playQueue(tracks, 0);
      }
    } catch {
      setRadioNote("не получилось собрать радио — попробуй ещё раз");
    } finally {
      setRadioBusy(false);
    }
  }, [portrait, player, radioBusy]);

  const ctaHandler = useCallback(
    (id: CtaAction["id"]) => {
      if (id === "radio") void startRadio();
      else if (id === "dig") onDig();
      else onOpenQuiz();
    },
    [startRadio, onDig, onOpenQuiz],
  );

  const hasTimeline = useMemo(
    () => prepareBuckets(portrait.timeline).length >= 2,
    [portrait.timeline],
  );

  /* ---- состояния без истории ---- */

  if (status === "loading") {
    return (
      <div className="story-view story-state" role="status">
        <span className="spinner" aria-hidden />
        <p>Подбираем слова к твоей музыке…</p>
      </div>
    );
  }
  if (status === "missing" || status === "stale" || status === "error") {
    return (
      <div className="story-view story-state">
        <p className="story-state-emoji" aria-hidden>
          📖
        </p>
        {status === "missing" && (
          <>
            <h3>История ещё не написана</h3>
            <p>
              Этому серверу пока не завезли рассказчика. Сам портрет на месте —
              переключись на вид «Портрет», а история появится после обновления
              бэкенда.
            </p>
          </>
        )}
        {status === "stale" && (
          <>
            <h3>Портрет из прошлой эпохи</h3>
            <p>
              Он сохранён старой версией, и историю из него не собрать.
              Перестрой портрет заново — и он заговорит.
            </p>
          </>
        )}
        {status === "error" && (
          <>
            <h3>История споткнулась</h3>
            <p>{error !== "" ? error : "Что-то пошло не так."}</p>
          </>
        )}
        <div className="state-actions">
          {status === "error" && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setAttempt((n) => n + 1)}
            >
              Попробовать снова
            </button>
          )}
          <button type="button" className="btn btn-ghost" onClick={onShowPortrait}>
            Смотреть портрет
          </button>
        </div>
      </div>
    );
  }

  /* ---- главы ---- */

  return (
    <div className="story-view">
      {chapters.map((chapter) => (
        <ChapterReveal key={chapter.key}>
          <div className="story-chapter-emoji" aria-hidden>
            {chapter.emoji}
          </div>
          {chapter.title !== "" && <h3>{chapter.title}</h3>}
          {chapter.paragraphs.map((p, i) => (
            <p key={i}>{p}</p>
          ))}
          {chapter.interactive?.kind === "archetype_chips" && (
            <ArchetypeChips
              archetypes={chapter.interactive.archetypes}
              points={portrait.points}
              clusters={portrait.clusters}
            />
          )}
          {chapter.interactive?.kind === "play_track" && (
            <div className="story-interactive">
              <PlayRow
                item={chapter.interactive.item}
                player={player}
                stateFor={stateFor}
                onLazy={onLazy}
              />
            </div>
          )}
          {chapter.interactive?.kind === "play_list" && (
            <div className="story-interactive">
              {chapter.interactive.items.map((item) => (
                <PlayRow
                  key={item.label}
                  item={item}
                  player={player}
                  stateFor={stateFor}
                  onLazy={onLazy}
                />
              ))}
            </div>
          )}
          {chapter.interactive?.kind === "timeline_mini" && hasTimeline && (
            <div className="story-interactive">
              <TasteTimeline timeline={portrait.timeline} variant="compact" />
            </div>
          )}
          {chapter.interactive?.kind === "cta" && (
            <div className="story-interactive">
              <div className="story-cta-row">
                {chapter.interactive.actions.map((action) => (
                  <button
                    key={action.id}
                    type="button"
                    className="story-cta"
                    onClick={() => ctaHandler(action.id)}
                    disabled={action.id === "radio" && radioBusy}
                  >
                    {action.id === "radio" && radioBusy ? (
                      <>
                        <span className="btn-spinner" aria-hidden /> Собираем
                        радио…
                      </>
                    ) : (
                      action.label
                    )}
                  </button>
                ))}
              </div>
              {radioNote !== null && (
                <p className="story-hint" role="status">
                  {radioNote}
                </p>
              )}
            </div>
          )}
        </ChapterReveal>
      ))}
    </div>
  );
}
