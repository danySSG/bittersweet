"use client";

import Link from "next/link";
import SiteFooter from "@/components/site-footer";
import {
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useSearchParams } from "next/navigation";
import {
  API_URL,
  ApiError,
  fetchMe,
  fetchPortrait,
  fetchYoutubeSummary,
  startPlaylistAnalysis,
  startYoutubeAnalysis,
  YOUTUBE_LOGIN_URL,
} from "@/lib/api";
import {
  JobTimeoutError,
  POLL_TIMEOUT_MS,
  pollJob,
} from "@/lib/job-polling";
import type {
  Job,
  JobStage,
  Me,
  Portrait,
  YoutubeSummary,
} from "@/lib/types";
import PortraitView from "@/components/portrait-view";

/* ---------- Состояния ---------- */

function LoadingState() {
  return (
    <div className="state-box">
      <div className="spinner" aria-hidden />
      <h2>Считаем портрет…</h2>
      <p>Раскладываем треки по настроению и энергии.</p>
    </div>
  );
}

function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="state-box">
      <h2>Не получилось загрузить портрет</h2>
      <p>{message}</p>
      <p>
        Похоже, бэкенд не запущен. Запусти его командой{" "}
        <code>cd service/backend &amp;&amp; uv run uvicorn app.main:app</code> и
        убедись, что он слушает <code>{API_URL}</code>.
      </p>
      <div className="state-actions">
        <button type="button" className="btn btn-primary" onClick={onRetry}>
          Попробовать снова
        </button>
        <Link href="/" className="btn btn-ghost">
          На главную
        </Link>
      </div>
    </div>
  );
}

/* ---------- Состояния джобы анализа плейлиста (v0.2) ---------- */

interface PlaylistError {
  title: string;
  message: string;
  /** Показать подсказку про запуск бэкенда */
  backendHint?: boolean;
}

/** Ошибки сети/HTTP при старте джобы или поллинге. */
function describeApiError(err: unknown): PlaylistError {
  if (err instanceof ApiError) {
    if (err.status === undefined) {
      // fetch кинул исключение — бэкенд недоступен
      return {
        title: "Бэкенд недоступен",
        message: err.message,
        backendHint: true,
      };
    }
    if (err.status === 404) {
      return {
        title: "Задача анализа потерялась",
        message:
          "Бэкенд не нашёл джобу — возможно, его перезапустили посреди анализа. Попробуй ещё раз.",
      };
    }
    if (err.status === 422) {
      // валидация url на бэкенде — detail уже человеческий
      return { title: "Ссылка не подошла", message: err.message };
    }
    if (err.status === 401) {
      // v0.5: /api/analyze/youtube без живой сессии
      return {
        title: "Нужно войти через YouTube",
        message:
          "Сессия не найдена или истекла. Вернись на главную и войди через YouTube ещё раз.",
      };
    }
    if (err.status === 503) {
      // v0.5: бэк без GOOGLE_CLIENT_ID отвечает 503 с человеческим detail
      return {
        title: "Бэкенд не настроен",
        message: `${err.message} См. README: настройка Google OAuth (ключи GOOGLE_* в .env бэкенда).`,
      };
    }
    return { title: "Ошибка бэкенда", message: err.message };
  }
  return {
    title: "Неизвестная ошибка",
    message: err instanceof Error ? err.message : "Что-то пошло не так.",
  };
}

/** Ошибки самой джобы (status=error): приватный плейлист / мало треков / прочее. */
function describeJobError(raw: string | null): PlaylistError {
  const text = (raw ?? "").toLowerCase();
  if (
    text.includes("приват") ||
    text.includes("private") ||
    text.includes("не найден") ||
    text.includes("not found")
  ) {
    return {
      title: "Плейлист недоступен",
      message:
        "Похоже, плейлист приватный или удалён. Открой настройки доступа в YouTube Music, выбери «Не в списке» или «Публичный» и попробуй снова.",
    };
  }
  if (text.includes("мало треков") || text.includes("сматч")) {
    return {
      title: "Слишком мало треков",
      message:
        "Не удалось найти превью для достаточного числа треков — для портрета нужно хотя бы 8. Попробуй плейлист побольше, от 15 треков.",
    };
  }
  return {
    title: "Не получилось построить портрет",
    message: raw !== null && raw !== "" ? raw : "Неизвестная ошибка анализа.",
  };
}

/**
 * Человеческие подписи этапов джобы (v0.2b, поле progress.stage).
 * Record<string, …>, а не Record<JobStage, …>: бэк может прислать
 * незнакомый этап — тогда падаем на дефолтные подписи потока.
 */
const STAGE_LABELS: Record<
  string,
  { title: string; verb: string; idle: string } | undefined
> = {
  parsing: {
    title: "Разбираем файл…",
    verb: "обработано",
    idle: "Ищем музыкальные CSV внутри выгрузки.",
  },
  // v0.7: этап fetching youtube-джобы — скан лайков с музыкальным фильтром
  fetching: {
    title: "Отбираем музыку из лайков…",
    verb: "просканировано",
    idle: "Сканируем лайки и отбираем музыкальные (категория Music и треки YT Music).",
  },
  resolving: {
    title: "Определяем треки…",
    verb: "определено",
    idle: "Узнаём названия треков по их YouTube-id.",
  },
  analyzing: {
    title: "Анализируем…",
    verb: "проанализировано",
    idle: "Слушаем превью и считаем признаки звука.",
  },
};

/* v0.7: ETA этапа analyzing из rate_per_min (SPEC-v07-music-filter §B) */

/** ETA не показываем первые 30с analyzing: скорость ещё не устоялась, шумно. */
const ETA_SHOW_DELAY_MS = 30_000;
/** Показанную цифру обновляем не чаще раза в 5с (поллинг идёт каждые 2с). */
const ETA_REFRESH_MS = 5_000;

/**
 * «осталось ~N мин» из job.rate_per_min (скользящая скорость, бэк v0.7)
 * или null, пока показывать нечего: не этап analyzing, старый бэк без
 * rate_per_min, либо ещё не прошло 30с. Старт analyzing засекаем на клиенте:
 * started_at бэка — старт всей джобы (вместе со сканом fetching), для
 * правила «30 секунд анализа» он не годится. Значение мемоизировано в
 * стейте и пересчитывается не чаще ETA_REFRESH_MS — без дёрганья на каждый
 * 2-секундный снапшот поллинга.
 */
function useAnalyzingEta(job: Job | null): string | null {
  const [eta, setEta] = useState<string | null>(null);
  // момент первого появления stage=analyzing (клиентские часы)
  const analyzingSinceRef = useRef<number | null>(null);
  // когда в последний раз обновляли показанное значение
  const shownAtRef = useRef(0);

  const stage = job?.progress.stage;
  const done = job?.progress.done ?? 0;
  const total = job?.progress.total ?? 0;
  const rate = job?.rate_per_min ?? null;

  useEffect(() => {
    if (stage !== "analyzing") {
      analyzingSinceRef.current = null;
      shownAtRef.current = 0;
      setEta(null);
      return;
    }
    const now = Date.now();
    analyzingSinceRef.current ??= now;
    // первые 30 секунд молчим — цифры по 1-2 трекам врут
    if (now - analyzingSinceRef.current < ETA_SHOW_DELAY_MS) return;
    // уже показываем — обновляем плавно, не чаще раза в 5 секунд
    if (eta !== null && now - shownAtRef.current < ETA_REFRESH_MS) return;
    if (rate === null || rate <= 0 || total <= 0 || done >= total) return;
    const minutes = Math.max(1, Math.ceil((total - done) / rate));
    shownAtRef.current = now;
    setEta(`осталось ~${minutes} мин`);
  }, [stage, done, total, rate, eta]);

  return eta;
}

function JobProgressState({
  job,
  fallbackTitle,
  fallbackIdle,
  note,
}: {
  job: Job | null;
  /** Заголовок, пока бэк не прислал stage (или прислал незнакомый) */
  fallbackTitle: string;
  /** Текст до появления total */
  fallbackIdle: string;
  note: string;
}) {
  const total = job?.progress.total ?? 0;
  const done = job?.progress.done ?? 0;
  const matched = job?.progress.matched ?? 0;
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;

  const stage: JobStage | null | undefined = job?.progress.stage;
  const stageInfo = stage != null ? STAGE_LABELS[stage] : undefined;
  // «сматчено» имеет смысл только на этапе анализа (или без этапов вовсе)
  const showMatched = stage == null || stage === "analyzing";
  // v0.7: на скане лайков показываем счётчик найденных музыкальных
  const musicFound = job?.progress.music_found;
  // v0.7: ETA анализа (null, пока рано или бэк не отдаёт rate_per_min)
  const eta = useAnalyzingEta(job);

  return (
    <div className="state-box">
      <div className="spinner" aria-hidden />
      <h2>{stageInfo?.title ?? fallbackTitle}</h2>
      {total > 0 ? (
        <>
          <div
            className="progress-track"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={total}
            aria-valuenow={done}
            aria-label="Прогресс анализа треков"
          >
            <div className="progress-fill" style={{ width: `${pct}%` }} />
          </div>
          <p className="progress-label">
            {stageInfo?.verb ?? "проанализировано"}{" "}
            <span className="mono">{done}</span> из{" "}
            <span className="mono">{total}</span>
            {showMatched && (
              <>
                {" "}
                · сматчено <span className="mono">{matched}</span>
              </>
            )}
            {stage === "fetching" && typeof musicFound === "number" && (
              <>
                {" "}
                · найдено <span className="mono">{musicFound}</span>{" "}
                музыкальных
              </>
            )}
            {eta !== null && <> · {eta}</>}
          </p>
        </>
      ) : (
        <p>{stageInfo?.idle ?? fallbackIdle}</p>
      )}
      <p className="progress-note">{note}</p>
    </div>
  );
}

function PlaylistErrorState({
  error,
  onRetry,
}: {
  error: PlaylistError;
  onRetry: () => void;
}) {
  return (
    <div className="state-box">
      <h2>{error.title}</h2>
      <p>{error.message}</p>
      {error.backendHint && (
        <p>
          Запусти бэкенд командой{" "}
          <code>cd service/backend &amp;&amp; uv run uvicorn app.main:app</code>{" "}
          и убедись, что он слушает <code>{API_URL}</code>.
        </p>
      )}
      <div className="state-actions">
        <button type="button" className="btn btn-primary" onClick={onRetry}>
          Попробовать снова
        </button>
        <Link href="/" className="btn btn-ghost">
          На главную
        </Link>
      </div>
    </div>
  );
}

/* ---------- Поток v0.1: demo / сессия Spotify ---------- */

function DefaultFlow({ demo }: { demo: boolean }) {
  const [portrait, setPortrait] = useState<Portrait | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPortrait(demo)
      .then((data) => {
        if (!cancelled) setPortrait(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Неизвестная ошибка.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [demo]);

  useEffect(() => load(), [load]);

  if (loading) return <LoadingState />;
  if (error || !portrait) {
    return (
      <ErrorState message={error ?? "Пустой ответ бэкенда."} onRetry={load} />
    );
  }
  return (
    <PortraitView
      portrait={portrait}
      modeNote={demo ? " · демо-режим" : ""}
      isDemo={demo}
    />
  );
}

/* ---------- Потоки через джобу: плейлист (v0.2) и Takeout (v0.2b) ---------- */

/** Общий поллинг джобы. `start` либо стартует новую джобу, либо отдаёт готовый id. */
function JobFlow({
  start,
  modeNote,
  progressTitle,
  progressIdle,
  progressNote,
  timeoutMs,
  donePrefix,
  describeError,
}: {
  start: () => Promise<{ job_id: string }>;
  modeNote: string;
  progressTitle: string;
  progressIdle: string;
  progressNote: string;
  /** v0.6: потолок ожидания джобы (глубокий анализ идёт до ~20 мин) */
  timeoutMs?: number;
  /**
   * v0.6: подпись над готовым портретом (напр. «глубину можно увеличить»);
   * v0.7: вторым аргументом — финальный снапшот джобы (music_found и т.п.).
   */
  donePrefix?: (portrait: Portrait, job: Job | null) => ReactNode;
  /**
   * v0.7: переопределение описания ошибок старта/поллинга — youtube-поток
   * объясняет 422 на limit:0 («обнови бэкенд»). Дефолт — describeApiError.
   */
  describeError?: (err: unknown) => PlaylistError;
}) {
  const [job, setJob] = useState<Job | null>(null);
  const [portrait, setPortrait] = useState<Portrait | null>(null);
  const [portraitId, setPortraitId] = useState<string | null>(null);
  const [error, setError] = useState<PlaylistError | null>(null);
  const [attempt, setAttempt] = useState(0);
  // StrictMode в dev маунтит эффект дважды — без дедупликации улетало два POST
  // и бэкенд заводил джобу-дубль (для безлимитной глубины это двойная квота).
  // Оба маунта делят ОДИН start()-промис; поллинг ведёт выживший инстанс.
  const startRef = useRef<{ attempt: number; promise: Promise<{ job_id: string }> } | null>(null);

  useEffect(() => {
    // attempt в зависимостях — перезапуск по кнопке «Попробовать снова»
    void attempt;

    let cancelled = false;

    setJob(null);
    setPortrait(null);
    setPortraitId(null);
    setError(null);

    const fail = (e: PlaylistError) => {
      if (!cancelled) setError(e);
    };

    const run = async () => {
      if (!startRef.current || startRef.current.attempt !== attempt) {
        startRef.current = { attempt, promise: start() };
      }
      const { job_id } = await startRef.current.promise;
      // общий поллинг джоб (v0.4: он же используется в discovery)
      const state = await pollJob(job_id, {
        onUpdate: (snapshot) => {
          if (!cancelled) setJob(snapshot);
        },
        isCancelled: () => cancelled,
        timeoutMs,
      });
      if (state === null || cancelled) return; // отменили при unmount
      if (state.status === "error") {
        fail(describeJobError(state.error));
        return;
      }
      if (state.portrait) {
        setPortrait(state.portrait);
        // v0.3: постоянный id сохранённого портрета (старый бэк не отдаёт)
        setPortraitId(state.portrait_id ?? null);
      } else {
        fail({
          title: "Пустой портрет",
          message: "Джоба завершилась, но портрет не пришёл. Попробуй ещё раз.",
        });
      }
    };

    run().catch((err: unknown) => {
      if (err instanceof JobTimeoutError) {
        const minutes = Math.round((timeoutMs ?? POLL_TIMEOUT_MS) / 60_000);
        fail({
          title: "Анализ занял слишком много времени",
          message: `Прошло больше ${minutes} минут, а джоба так и не завершилась. Попробуй меньшую глубину или повтори позже.`,
        });
        return;
      }
      fail((describeError ?? describeApiError)(err));
    });

    // отмена поллинга при unmount / смене источника
    return () => {
      cancelled = true;
    };
  }, [start, attempt, timeoutMs, describeError]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  if (error) return <PlaylistErrorState error={error} onRetry={retry} />;
  if (portrait) {
    return (
      <>
        {donePrefix?.(portrait, job)}
        <PortraitView
          portrait={portrait}
          modeNote={modeNote}
          portraitId={portraitId}
        />
      </>
    );
  }
  return (
    <JobProgressState
      job={job}
      fallbackTitle={progressTitle}
      fallbackIdle={progressIdle}
      note={progressNote}
    />
  );
}

/** v0.2: плейлист YouTube Music — стартуем джобу по ссылке и поллим. */
function PlaylistFlow({ url }: { url: string }) {
  const start = useCallback(() => startPlaylistAnalysis(url), [url]);
  return (
    <JobFlow
      start={start}
      modeNote=" · плейлист YouTube Music"
      progressTitle="Анализируем плейлист…"
      progressIdle="Читаем плейлист и ищем превью треков."
      progressNote="Обычно это занимает одну-две минуты — зависит от длины плейлиста."
    />
  );
}

/**
 * v0.2b: джоба уже запущена (импорт Takeout стартует с лендинга) —
 * только поллим существующий id, ничего не стартуем.
 */
function ExistingJobFlow({ jobId }: { jobId: string }) {
  const start = useCallback(() => Promise.resolve({ job_id: jobId }), [jobId]);
  return (
    <JobFlow
      start={start}
      modeNote=" · выгрузка Google Takeout"
      progressTitle="Строим портрет…"
      progressIdle="Разбираем выгрузку и готовим треки."
      progressNote="Обычно это занимает несколько минут — зависит от размера выгрузки."
    />
  );
}

/* ---------- Поток v0.5: лайки YouTube (официальный OAuth) ---------- */

/** Пока не пришёл ответ /api/me. */
function YoutubeCheckingState() {
  return (
    <div className="state-box">
      <div className="spinner" aria-hidden />
      <h2>Проверяем подключение…</h2>
      <p>Спрашиваем бэкенд, подключён ли твой YouTube-аккаунт.</p>
    </div>
  );
}

/** Не залогинен: человеческая карточка с кнопкой входа. */
function YoutubeLoginState() {
  return (
    <div className="state-box">
      <h2>Подключи YouTube</h2>
      <p>
        Построим портрет по твоим лайкам: войди через Google — мы читаем
        только список понравившихся треков и ничего не пишем.
      </p>
      <div className="state-actions">
        <a href={YOUTUBE_LOGIN_URL} className="btn btn-primary">
          Войти через YouTube
        </a>
        <Link href="/" className="btn btn-ghost">
          На главную
        </Link>
      </div>
      <p className="progress-note">
        официальный вход Google · читаем только лайки · отключение в один клик
      </p>
    </div>
  );
}

/**
 * Ошибки GET /api/me. Отдельно от describeApiError: там 404 описан как
 * «джоба потерялась», а здесь 404 — это старый бэк без роутов v0.5.
 */
function describeMeError(err: unknown): PlaylistError {
  if (err instanceof ApiError && err.status === 404) {
    return {
      title: "Бэкенд не поддерживает вход через YouTube",
      message:
        "Похоже, запущена старая версия бэкенда — у него нет роута /api/me. Обнови и перезапусти бэкенд.",
    };
  }
  return describeApiError(err);
}

/* ---------- v0.6, B1: экран «перед стартом» — выбор глубины ---------- */

interface AnalysisDepth {
  /** Сколько музыкальных лайков анализировать; 0 = БЕЗ ЛИМИТА (v0.7) */
  limit: number;
  name: string;
  detail: string;
  /** Фронтовой потолок ожидания джобы этой глубины */
  timeoutMs: number;
  progressNote: string;
}

/** v0.7: поллинг-таймаут для безлимита («все музыкальные лайки») — 90 мин. */
const UNLIMITED_TIMEOUT_MS = 90 * 60_000;

/**
 * Радио-чипы глубины (SPEC-v07-music-filter §B). limit применяется бэком
 * уже к отфильтрованным МУЗЫКАЛЬНЫМ лайкам; limit 0 — все музыкальные
 * (потолок 200 снят, старый бэк на 0 ответит 422 — см. YoutubeAnalysisRun).
 */
const ANALYSIS_DEPTHS: AnalysisDepth[] = [
  {
    limit: 40,
    name: "Быстрый",
    detail: "40 треков · ~2-3 мин",
    timeoutMs: 8 * 60_000,
    progressNote: "Обычно это занимает две-три минуты.",
  },
  {
    limit: 200,
    name: "Глубокий",
    detail: "200 · ~15-20 мин",
    timeoutMs: 28 * 60_000,
    progressNote:
      "Глубокий анализ идёт до двадцати минут — вкладку можно свернуть, анализ идёт на сервере.",
  },
  {
    limit: 0,
    name: "Все музыкальные лайки",
    detail: "время зависит от их числа",
    timeoutMs: UNLIMITED_TIMEOUT_MS,
    progressNote:
      "Анализируем все музыкальные лайки — на тысячах треков это займёт больше часа; вкладку можно свернуть, анализ идёт на сервере.",
  },
];

/**
 * Карточка «перед стартом»: сводка аккаунта + выбор глубины + явная кнопка.
 * Анализ НЕ стартует сам — только по клику (фидбек основателя, v0.6).
 */
function YoutubeStartCard({
  channelTitle,
  summary,
  onStart,
}: {
  /** channel_title из /api/me — фолбэк, пока summary не пришла */
  channelTitle: string | null;
  summary: YoutubeSummary | null;
  onStart: (depth: AnalysisDepth) => void;
}) {
  const [depth, setDepth] = useState<AnalysisDepth>(ANALYSIS_DEPTHS[0]);
  const title = summary?.channel_title ?? channelTitle;
  const likes = summary?.likes_total;

  return (
    <div className="state-box">
      <h2>Портрет по лайкам YouTube</h2>
      <p className="youtube-summary-line">
        YouTube: <b>{title ?? "аккаунт"}</b>
      </p>
      {/* v0.7: подпись под сводкой — про автоотбор музыки (SPEC-v07 §B).
          Текст рендерится и без likes_total (старый бэк / SSR). */}
      <p className="progress-note">
        {typeof likes === "number" && (
          <>
            <span className="mono">{likes}</span> лайков YouTube всего —{" "}
          </>
        )}
        музыкальные отберём автоматически (категория Music и треки YT Music)
      </p>
      <p>
        Выбери глубину — сколько последних музыкальных лайков разобрать по
        звуку.
      </p>
      <div
        className="depth-chips"
        role="radiogroup"
        aria-label="Глубина анализа"
      >
        {ANALYSIS_DEPTHS.map((d) => (
          <label
            key={d.limit}
            className={
              d.limit === depth.limit
                ? "depth-chip depth-chip-active"
                : "depth-chip"
            }
          >
            <input
              type="radio"
              name="analysis-depth"
              className="depth-chip-input"
              checked={d.limit === depth.limit}
              onChange={() => setDepth(d)}
            />
            <span className="depth-chip-name">{d.name}</span>
            <span className="depth-chip-detail mono">{d.detail}</span>
          </label>
        ))}
      </div>
      <div className="state-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => onStart(depth)}
        >
          Построить портрет
        </button>
        <Link href="/" className="btn btn-ghost">
          На главную
        </Link>
      </div>
      <p className="progress-note">
        анализ стартует только по этой кнопке · глубину можно поменять после
        <br />
        ~2–4 сек на новый трек · повторные берутся из кэша
      </p>
    </div>
  );
}

/** Запущенный анализ выбранной глубины + подпись «глубину можно увеличить». */
function YoutubeAnalysisRun({
  depth,
  likesTotal,
  onChangeDepth,
}: {
  depth: AnalysisDepth;
  likesTotal: number | null;
  onChangeDepth: () => void;
}) {
  // v0.7: limit 0 = все музыкальные лайки (безлимит)
  const unlimited = depth.limit === 0;

  const start = useCallback(
    () => startYoutubeAnalysis(depth.limit),
    [depth.limit],
  );

  /**
   * v0.7: старый бэк валидирует limit >= 1 и на limit: 0 отвечает 422 —
   * вместо сырого текста pydantic объясняем, что нужно обновить бэкенд.
   */
  const describeError = useCallback(
    (err: unknown): PlaylistError => {
      if (unlimited && err instanceof ApiError && err.status === 422) {
        return {
          title: "Бэкенд не поддерживает «все музыкальные лайки»",
          message:
            "Запущена старая версия бэкенда — она не принимает безлимитный анализ (limit: 0). Обнови и перезапусти бэкенд или выбери глубину поменьше.",
        };
      }
      return describeApiError(err);
    },
    [unlimited],
  );

  // после done (v0.7): «проанализировано N музыкальных лайков из K найденных»
  // + пере-анализ; K = music_found из финального снапшота джобы (старый бэк
  // его не отдаёт — тогда прежняя подпись «из {likes_total} лайков»)
  const donePrefix = useCallback(
    (portrait: Portrait, job: Job | null) => {
      const found = job?.progress.music_found;
      return (
        <div className="reanalyze-note">
          <span>
            Проанализировано <span className="mono">{portrait.n_tracks}</span>{" "}
            {typeof found === "number" ? (
              <>
                музыкальных лайков из <span className="mono">{found}</span>{" "}
                найденных
              </>
            ) : typeof likesTotal === "number" ? (
              <>
                из <span className="mono">{likesTotal}</span> лайков
              </>
            ) : (
              <>лайков</>
            )}
            {unlimited ? "." : " — глубину можно увеличить."}
          </span>
          <button
            type="button"
            className="btn btn-ghost btn-small"
            onClick={onChangeDepth}
          >
            {unlimited ? "Изменить глубину" : "Проанализировать глубже"}
          </button>
        </div>
      );
    },
    [likesTotal, onChangeDepth, unlimited],
  );

  return (
    <JobFlow
      start={start}
      modeNote=" · лайки YouTube"
      progressTitle="Анализируем лайки…"
      progressIdle="Читаем лайки и отбираем музыкальные треки."
      progressNote={depth.progressNote}
      timeoutMs={depth.timeoutMs}
      donePrefix={donePrefix}
      describeError={describeError}
    />
  );
}

/**
 * ?source=youtube: GET /api/me → connected ? экран выбора глубины (v0.6,
 * БЕЗ автостарта после OAuth-редиректа) : карточка входа. 503 (бэк без
 * GOOGLE_CLIENT_ID) и сеть превращаются в подсказки через describeApiError.
 */
function YoutubeFlow() {
  const [me, setMe] = useState<Me | null>(null);
  const [summary, setSummary] = useState<YoutubeSummary | null>(null);
  const [error, setError] = useState<PlaylistError | null>(null);
  const [attempt, setAttempt] = useState(0);
  // null — экран выбора глубины; token растёт с каждым запуском,
  // чтобы пере-анализ монтировал свежий JobFlow (key)
  const [run, setRun] = useState<{
    depth: AnalysisDepth;
    token: number;
  } | null>(null);

  useEffect(() => {
    // attempt в зависимостях — перезапуск по кнопке «Попробовать снова»
    void attempt;
    let cancelled = false;
    setMe(null);
    setError(null);
    fetchMe()
      .then((data) => {
        if (cancelled) return;
        setMe(data);
        if (data.connected) {
          // сводка для карточки; старый бэк без роута — молча без лайков
          fetchYoutubeSummary()
            .then((s) => {
              if (!cancelled) setSummary(s);
            })
            .catch(() => {});
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(describeMeError(err));
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  const startRun = useCallback((depth: AnalysisDepth) => {
    setRun((prev) => ({ depth, token: (prev?.token ?? 0) + 1 }));
  }, []);
  const backToDepth = useCallback(() => setRun(null), []);

  if (error) return <PlaylistErrorState error={error} onRetry={retry} />;
  if (me === null) return <YoutubeCheckingState />;
  if (!me.connected) return <YoutubeLoginState />;
  if (run === null) {
    return (
      <YoutubeStartCard
        channelTitle={me.channel_title ?? null}
        summary={summary}
        onStart={startRun}
      />
    );
  }
  return (
    <YoutubeAnalysisRun
      key={run.token}
      depth={run.depth}
      likesTotal={summary?.likes_total ?? null}
      onChangeDepth={backToDepth}
    />
  );
}

/* ---------- Страница ---------- */

function PortraitContent() {
  const searchParams = useSearchParams();
  const playlist = searchParams.get("playlist");
  const jobId = searchParams.get("job");
  const demo = searchParams.get("demo") === "1";

  // v0.5: ?source=youtube — портрет по лайкам залогиненного аккаунта
  if (searchParams.get("source") === "youtube") {
    return <YoutubeFlow />;
  }
  if (playlist !== null && playlist !== "") {
    return <PlaylistFlow url={playlist} />;
  }
  // v0.2b: ?job={id} — поллинг уже запущенной джобы (импорт Takeout)
  if (jobId !== null && jobId !== "") {
    return <ExistingJobFlow jobId={jobId} />;
  }
  return <DefaultFlow demo={demo} />;
}

export default function PortraitPage() {
  return (
    <main className="portrait-page container">
      <Link href="/" className="back-link">
        ← Bittersweet
      </Link>
      <Suspense fallback={<LoadingState />}>
        <PortraitContent />
      </Suspense>
      <SiteFooter />
    </main>
  );
}
