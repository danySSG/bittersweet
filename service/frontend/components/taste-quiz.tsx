"use client";

/**
 * v0.12 (SPEC-v12-human §D): квиз «Угадай себя» — до 5 вопросов о
 * собственном вкусе, собираются КЛИЕНТСКИ из уже загруженных
 * portrait + observatory. Никакой сети, кроме ленивых превью.
 *
 * Детерминизм: дистракторы и порядок вариантов выбирает mulberry32,
 * сидированный от portrait_id (lib/probit — тот же PRNG, что у галактики):
 * у одного портрета квиз всегда один и тот же.
 *
 * Честность: у вариантов есть ▶ ДО ответа — слушай и решай. Вопросы, для
 * которых не хватает данных (нет обсерватории, мало архетипов), молча
 * пропускаются — счёт считается из фактического числа вопросов.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ObservatoryData, ObservatoryState } from "@/components/observatory";
import {
  PreviewPlayButton,
  usePreviewResolver,
} from "@/components/preview-play";
import { hashSeed, mulberry32 } from "@/lib/probit";
import type { PreviewPlayer } from "@/lib/use-audio";
import type { Portrait, PortraitPoint } from "@/lib/types";

/* ================= Сборка вопросов ================= */

interface QuizOption {
  id: string;
  /** Текст варианта (лейбл трека, «ля-минор», число, архетип) */
  text: string;
  correct: boolean;
  /** Трек для ▶ (label обязателен, url/videoId — что есть) */
  playLabel?: string;
  previewUrl?: string | null;
  videoId?: string | null;
}

interface QuizQuestion {
  id: string;
  text: string;
  /** Короткая подпись после ответа */
  reveal: string;
  options: QuizOption[];
}

type Rnd = () => number;

/** Детерминированная выборка n элементов без повторов. */
function pickN<T>(items: T[], n: number, rnd: Rnd): T[] {
  const pool = [...items];
  const out: T[] = [];
  while (out.length < n && pool.length > 0) {
    const i = Math.floor(rnd() * pool.length);
    out.push(pool.splice(i, 1)[0]);
  }
  return out;
}

function shuffle<T>(items: T[], rnd: Rnd): T[] {
  const out = [...items];
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rnd() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/** Ноты по-русски для вопроса про «музыкальный дом». */
const NOTE_RU: Record<string, string> = {
  C: "до", "C#": "до-диез", D: "ре", "D#": "ре-диез", E: "ми",
  F: "фа", "F#": "фа-диез", G: "соль", "G#": "соль-диез",
  A: "ля", "A#": "ля-диез", B: "си",
};

/** "A minor" → «ля-минор»; незнакомое — как есть. */
function keyRu(label: string): string {
  const [note, mode] = label.split(" ");
  const ru = NOTE_RU[note];
  if (ru === undefined || (mode !== "minor" && mode !== "major")) return label;
  return `${ru}-${mode === "minor" ? "минор" : "мажор"}`;
}

/** Точка-пример в тональности «A minor» — по хвосту meta («157bpm · A minor»). */
function exampleInKey(points: PortraitPoint[], keyLabel: string): PortraitPoint | null {
  const matches = points.filter(
    (p) => p.label !== "" && p.meta.trim().endsWith(`· ${keyLabel}`),
  );
  if (matches.length === 0) return null;
  return matches.find((p) => p.videoId) ?? matches[0];
}

function trackOption(
  id: string,
  label: string,
  correct: boolean,
  previewUrl?: string | null,
  videoId?: string | null,
): QuizOption {
  return { id, text: label, correct, playLabel: label, previewUrl, videoId };
}

export function buildQuizQuestions(
  portrait: Portrait,
  obs: ObservatoryData | null,
  portraitId: string,
): QuizQuestion[] {
  const rnd = mulberry32(hashSeed(`${portraitId}:quiz`));
  const questions: QuizQuestion[] = [];
  const points = portrait.points.filter((p) => p.label !== "");
  const highlights = portrait.highlights ?? [];
  const highlightLabels = new Set(highlights.map((h) => h.track));

  /* 1. самый быстрый трек */
  const fastest = highlights.find((h) => h.kind === "fastest");
  if (fastest !== undefined && fastest.track !== "") {
    const others = points.filter(
      (p) => p.label !== fastest.track && !highlightLabels.has(p.label),
    );
    const distractors = pickN(others, 2, rnd);
    if (distractors.length === 2) {
      questions.push({
        id: "fastest",
        text: "Какой из этих треков у тебя самый быстрый?",
        reveal: `Самый быстрый — «${fastest.track}»${
          fastest.value !== "" ? `: ${fastest.value}` : ""
        }.`,
        options: shuffle(
          [
            trackOption("a", fastest.track, true, fastest.preview_url, fastest.videoId),
            ...distractors.map((p, i) =>
              trackOption(`d${i}`, p.label, false, null, p.videoId),
            ),
          ],
          rnd,
        ),
      });
    }
  }

  /* 2. белая ворона — против самых типичных треков */
  const crow = obs?.outliers[0];
  if (crow !== undefined && crow.label !== "") {
    const crowLabels = new Set((obs?.outliers ?? []).map((o) => o.label));
    // «типичность» по-клиентски: ближе всех к центру масс карты
    let cx = 0;
    let cy = 0;
    for (const p of points) {
      cx += p.x;
      cy += p.y;
    }
    cx /= Math.max(1, points.length);
    cy /= Math.max(1, points.length);
    const typical = points
      .filter((p) => !crowLabels.has(p.label) && p.label !== crow.label)
      .sort(
        (a, b) =>
          (a.x - cx) ** 2 + (a.y - cy) ** 2 - ((b.x - cx) ** 2 + (b.y - cy) ** 2),
      )
      .slice(0, 2);
    if (typical.length === 2) {
      questions.push({
        id: "crow",
        text: "Кто из этих троих — твоя «белая ворона»?",
        reveal: `Белая ворона — «${crow.label}»${
          crow.why !== "" ? `: ${crow.why}` : ""
        }. Остальные двое — самые типичные твои треки.`,
        options: shuffle(
          [
            trackOption("a", crow.label, true, null, crow.videoId),
            ...typical.map((p, i) =>
              trackOption(`d${i}`, p.label, false, null, p.videoId),
            ),
          ],
          rnd,
        ),
      });
    }
  }

  /* 3. домашняя тональность */
  const wheel = obs?.wheel ?? null;
  if (wheel !== null && wheel.topLabel !== null) {
    const present: string[] = [];
    for (let i = 0; i < 12; i++) {
      if (wheel.major[i] > 0) present.push(`${wheel.order[i]} major`);
      if (wheel.minor[i] > 0) present.push(`${wheel.order[i]} minor`);
    }
    const others = present.filter((k) => k !== wheel.topLabel);
    const distractors = pickN(others, 2, rnd);
    if (distractors.length === 2) {
      const toOption = (keyLabel: string, correct: boolean, id: string): QuizOption => {
        const example = exampleInKey(points, keyLabel);
        return {
          id,
          text: keyRu(keyLabel),
          correct,
          ...(example !== null
            ? { playLabel: example.label, previewUrl: null, videoId: example.videoId }
            : {}),
        };
      };
      questions.push({
        id: "home_key",
        text: "Какая тональность — твой музыкальный дом?",
        reveal: `Твой дом — ${keyRu(wheel.topLabel)}${
          wheel.topCount !== null
            ? `: там живёт ${wheel.topCount} твоих треков`
            : ""
        }. К каждому варианту прилагался трек-подсказка из этой тональности.`,
        options: shuffle(
          [
            toOption(wheel.topLabel, true, "a"),
            ...distractors.map((k, i) => toOption(k, false, `d${i}`)),
          ],
          rnd,
        ),
      });
    }
  }

  /* 4. главное настроение */
  const withArch = portrait.clusters
    .map((c, i) => ({ c, i }))
    .filter(({ c }) => c.archetype !== undefined);
  if (withArch.length >= 3) {
    const top = withArch[0];
    const distractors = pickN(withArch.slice(1), 2, rnd);
    const toOption = (
      entry: (typeof withArch)[number],
      correct: boolean,
      id: string,
    ): QuizOption => {
      const arch = entry.c.archetype;
      const example = entry.c.examples_rich?.[0];
      return {
        id,
        text: `${arch?.emoji ?? "🎵"} ${arch?.name ?? entry.c.label}`,
        correct,
        ...(example !== undefined
          ? {
              playLabel: example.label,
              previewUrl: example.preview_url,
              videoId: example.videoId,
            }
          : {}),
      };
    };
    questions.push({
      id: "archetype",
      text: "Какое настроение у тебя главное?",
      reveal: `Главное — ${top.c.archetype?.emoji ?? ""} «${
        top.c.archetype?.name ?? top.c.label
      }»: ${top.c.share}% библиотеки.`,
      options: shuffle(
        [toOption(top, true, "a"), ...distractors.map((d, i) => toOption(d, false, `d${i}`))],
        rnd,
      ),
    });
  }

  /* 5. сколько треков в портрете */
  const n = portrait.n_tracks;
  if (Number.isFinite(n) && n > 0) {
    let d1 = Math.max(1, Math.round(n * (0.6 + rnd() * 0.22)));
    let d2 = Math.round(n * (1.2 + rnd() * 0.3));
    if (d1 === n) d1 = Math.max(1, d1 - 7);
    if (d2 === n || d2 === d1) d2 += 9;
    questions.push({
      id: "n_tracks",
      text: "Сколько треков в твоём портрете?",
      reveal: `Ровно ${n} — все посчитаны, никто не потерялся.`,
      options: shuffle(
        [
          { id: "a", text: String(n), correct: true },
          { id: "d0", text: String(d1), correct: false },
          { id: "d1", text: String(d2), correct: false },
        ],
        rnd,
      ),
    });
  }

  return questions;
}

/* ================= Компонент-модалка ================= */

function scorePhrase(score: number, total: number): string {
  if (total > 0 && score === total) {
    return "Ты знаешь себя как свои пять плейлистов.";
  }
  if (score <= 1) return "Твой вкус загадочнее, чем ты думал.";
  if (score / Math.max(1, total) >= 0.6) {
    return "Крепкое знакомство с собой — но пара сюрпризов нашлась.";
  }
  return "Вы с твоим вкусом явно давно не созванивались.";
}

export default function TasteQuiz({
  portraitId,
  portrait,
  observatory,
  player,
  onClose,
}: {
  portraitId: string;
  portrait: Portrait;
  /** Хук обсерватории из portrait-view: квиз сам дожимает load() */
  observatory: ObservatoryState;
  player: PreviewPlayer;
  onClose: () => void;
}) {
  // вопросы 2 и 3 питаются обсерваторией — дожимаем её загрузку
  const loadRef = useRef(observatory.load);
  useEffect(() => {
    loadRef.current = observatory.load;
  });
  useEffect(() => {
    loadRef.current();
  }, []);

  // Esc закрывает, скролл страницы под модалкой замирает
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

  const obsSettled =
    observatory.status === "ready" ||
    observatory.status === "hidden" ||
    observatory.status === "stale" ||
    observatory.status === "error";

  const questions = useMemo(
    () =>
      obsSettled
        ? buildQuizQuestions(portrait, observatory.data, portraitId)
        : null,
    [obsSettled, portrait, observatory.data, portraitId],
  );

  const [step, setStep] = useState(0);
  const [picked, setPicked] = useState<string | null>(null);
  const [hits, setHits] = useState(0);
  const [copied, setCopied] = useState(false);

  // ленивые превью для всех вариантов без известного url
  const lazyLabels = useMemo(() => {
    if (questions === null) return [];
    const seen = new Set<string>();
    const out: string[] = [];
    for (const q of questions) {
      for (const o of q.options) {
        if (
          o.playLabel !== undefined &&
          (o.previewUrl === undefined || o.previewUrl === null) &&
          !seen.has(o.playLabel)
        ) {
          seen.add(o.playLabel);
          out.push(o.playLabel);
        }
      }
    }
    return out.slice(0, 24);
  }, [questions]);
  const { stateFor, onLazy } = usePreviewResolver(lazyLabels, player);

  // ref-гейт: клики по вариантам после ответа игнорируются, инкремент
  // счёта не задваивается (updater-функции обязаны быть чистыми)
  const answeredRef = useRef(false);

  const answer = useCallback((option: QuizOption) => {
    if (answeredRef.current) return;
    answeredRef.current = true;
    setPicked(option.id);
    if (option.correct) setHits((h) => h + 1);
  }, []);

  const next = useCallback(() => {
    answeredRef.current = false;
    setPicked(null);
    setStep((s) => s + 1);
  }, []);

  const restart = useCallback(() => {
    answeredRef.current = false;
    setStep(0);
    setPicked(null);
    setHits(0);
    setCopied(false);
  }, []);

  const share = useCallback(
    (score: number, total: number) => {
      void navigator.clipboard
        .writeText(`Я угадал ${score}/${total} фактов о своём вкусе — bittersweet`)
        .then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 2000);
        })
        .catch(() => {
          // clipboard недоступен — молча
        });
    },
    [],
  );

  let body: React.ReactNode;
  if (questions === null) {
    body = (
      <div className="quiz-state" role="status">
        <span className="spinner" aria-hidden />
        <p>Подбираем каверзные вопросы…</p>
      </div>
    );
  } else if (questions.length === 0) {
    body = (
      <div className="quiz-state">
        <p>
          Для квиза не хватает данных — этому портрету нечего от тебя скрывать.
        </p>
        <button type="button" className="btn btn-ghost" onClick={onClose}>
          Закрыть
        </button>
      </div>
    );
  } else if (step >= questions.length) {
    const total = questions.length;
    body = (
      <div className="quiz-result">
        <div className="quiz-score mono">
          {hits}/{total}
        </div>
        <p className="quiz-phrase">{scorePhrase(hits, total)}</p>
        <div className="quiz-result-actions">
          <span className="share-wrap">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => share(hits, total)}
            >
              Поделиться результатом
            </button>
            {copied && (
              <span className="share-tooltip" role="status">
                скопировано
              </span>
            )}
          </span>
          <button type="button" className="btn btn-ghost" onClick={restart}>
            Ещё раз
          </button>
        </div>
      </div>
    );
  } else {
    const q = questions[step];
    const answered = picked !== null;
    body = (
      <>
        <p className="quiz-progress mono">
          {step + 1} / {questions.length}
        </p>
        <h3 className="quiz-question">{q.text}</h3>
        <div className="quiz-options">
          {q.options.map((o) => {
            let cls = "quiz-option";
            if (answered && o.correct) cls += " quiz-option-correct";
            else if (answered && picked === o.id) cls += " quiz-option-wrong";
            return (
              <div key={o.id} className={cls}>
                {o.playLabel !== undefined && (
                  <PreviewPlayButton
                    label={o.playLabel}
                    state={stateFor(o.playLabel, o.previewUrl)}
                    player={player}
                    onLazy={onLazy}
                    videoId={o.videoId}
                  />
                )}
                <button
                  type="button"
                  className="quiz-option-btn"
                  disabled={answered}
                  onClick={() => answer(o)}
                >
                  {o.text}
                </button>
                {answered && o.correct && (
                  <span className="quiz-mark" aria-hidden>
                    ✓
                  </span>
                )}
                {answered && !o.correct && picked === o.id && (
                  <span className="quiz-mark" aria-hidden>
                    ✕
                  </span>
                )}
              </div>
            );
          })}
        </div>
        {answered && (
          <div className="quiz-reveal">
            <p>{q.reveal}</p>
            <button type="button" className="btn btn-primary" onClick={next}>
              {step + 1 < questions.length ? "Дальше" : "К результату"}
            </button>
          </div>
        )}
      </>
    );
  }

  return (
    <div className="quiz-overlay" role="presentation" onClick={onClose}>
      <div
        className="quiz-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Квиз «Угадай себя»"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="quiz-head">
          <span className="quiz-title">🎯 Угадай себя</span>
          <button
            type="button"
            className="quiz-close"
            aria-label="Закрыть квиз"
            onClick={onClose}
          >
            ✕
          </button>
        </div>
        {body}
      </div>
    </div>
  );
}
