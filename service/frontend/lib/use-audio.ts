"use client";

/**
 * v0.6 (SPEC-v06-ux, раздел B3): общий аудио-хук — ОДИН глобальный <audio>
 * на страницу. Вынесен из components/discover-section.tsx, потому что теперь
 * превью играют в трёх местах: находки discovery, хайлайты «самые-самые»
 * и примеры кластеров. Хук зовётся один раз в PortraitView, плеер передаётся
 * во все секции пропсом — два трека одновременно не играют.
 *
 * v0.9 (SPEC-v09-features §A): + режим очереди «радио архетипа»:
 * playQueue(tracks, startIndex) играет превью подряд с автопереходом по
 * ended, next/prev/pause и текущим индексом наружу (мини-плеер). Одиночный
 * режим (toggle) НЕ сломан — им пользуются хайлайты и примеры кластеров;
 * при активной очереди toggle её трека = прыжок очереди на него
 * (клик по строке находки синхронизирован с радио), toggle чужого трека =
 * выход из очереди в прежний одиночный режим.
 *
 * v0.10 (SPEC-v10-critique §D): + режим «Полностью» — официальный
 * YouTube IFrame Player API. Скрипт https://www.youtube.com/iframe_api
 * грузится ЛЕНИВО при первом включении режима; плеер живёт в ВИДИМОМ
 * окошке ~120×68 внутри мини-плеера (требование YouTube — НЕ display:none),
 * контейнер отдаёт мини-плеер через registerYtContainer. Очередь — через
 * loadVideoById, автопереход по onStateChange ENDED; onError → бейдж
 * «недоступен во встраивании» и фолбэк на превью (если есть) или пропуск.
 * Треки без videoId в «Полностью» играют превью с пометкой или пропускаются.
 */

import { useCallback, useEffect, useRef, useState } from "react";

/* ---------- v0.10 §D: минимальные типы YouTube IFrame API (без npm-зависимостей) ---------- */

interface YtPlayer {
  loadVideoById: (videoId: string) => void;
  playVideo: () => void;
  pauseVideo: () => void;
  stopVideo: () => void;
  destroy: () => void;
  getCurrentTime?: () => number;
  getDuration?: () => number;
}

interface YtNamespace {
  Player: new (
    el: Element,
    options: {
      width?: string | number;
      height?: string | number;
      playerVars?: Record<string, string | number>;
      events?: {
        onReady?: () => void;
        onStateChange?: (e: { data: number }) => void;
        onError?: (e: { data: number }) => void;
      };
    },
  ) => YtPlayer;
  PlayerState?: { ENDED: number; PLAYING: number; PAUSED: number };
}

declare global {
  interface Window {
    YT?: YtNamespace;
    onYouTubeIframeAPIReady?: () => void;
  }
}

/** Ленивая загрузка iframe_api — один раз на страницу (модульный синглтон). */
let ytApiPromise: Promise<YtNamespace> | null = null;

function loadYouTubeApi(): Promise<YtNamespace> {
  const existing = window.YT;
  if (existing !== undefined && existing.Player !== undefined) {
    return Promise.resolve(existing);
  }
  if (ytApiPromise !== null) return ytApiPromise;
  ytApiPromise = new Promise((resolve) => {
    const prev = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      prev?.();
      if (window.YT !== undefined) resolve(window.YT);
    };
    const script = document.createElement("script");
    script.src = "https://www.youtube.com/iframe_api";
    script.async = true;
    document.head.appendChild(script);
  });
  return ytApiPromise;
}

/* ---------- Публичный контракт хука ---------- */

/** v0.10 §D: режим воспроизведения — 30-сек превью или полный трек (YT). */
export type PlayerMode = "preview" | "full";

/**
 * Трек очереди радио. url — прямое превью (null, если его нет);
 * videoId (v0.10) — топливо режима «Полностью». Трек без обоих бессмысленен.
 */
export interface QueueTrack {
  /** Прямой URL превью — проигрывается как есть; null — превью нет */
  url: string | null;
  /** v0.10 §D: videoId для режима «Полностью» */
  videoId?: string | null;
  /** "artist — title" для мини-плеера и синхронизации со строками находок */
  label: string;
  /** Эмодзи источника (архетип кластера, 🎯 для точки) — для мини-плеера */
  meta?: string;
}

/** v0.10 §D: контекст кнопки ▶ строки — чтобы toggle уважал режим. */
export interface ToggleExtras {
  videoId?: string | null;
  label?: string;
  meta?: string;
}

export interface PreviewPlayer {
  /** URL превью, которое сейчас играет (null — тишина или пауза) */
  playingUrl: string | null;
  /** Клик по ▶/⏸: тот же URL — пауза, другой — прежний трек останавливается */
  toggle: (url: string, extras?: ToggleExtras) => void;
  /* ---- v0.9: очередь радио ---- */
  /** Активная очередь (null — одиночный режим, мини-плеер скрыт) */
  queue: QueueTrack[] | null;
  /** Индекс текущего трека очереди; -1 без очереди */
  queueIndex: number;
  /** Очередь стоит на паузе (мини-плеер показывает ▶) */
  queuePaused: boolean;
  /** Запуск очереди с трека startIndex (по умолчанию с первого) */
  playQueue: (tracks: QueueTrack[], startIndex?: number) => void;
  queueNext: () => void;
  queuePrev: () => void;
  queueTogglePause: () => void;
  /** Крестик мини-плеера: стоп и скрыть */
  stopQueue: () => void;
  /** <audio> для подписки мини-плеера на timeupdate (прогресс превью) */
  getAudio: () => HTMLAudioElement | null;
  /* ---- v0.10 §D: режим «Полностью» ---- */
  /** Текущий режим: превью 30с / полный трек через YouTube */
  mode: PlayerMode;
  setMode: (mode: PlayerMode) => void;
  /** true — текущий трек играет через видимый YouTube-плеер */
  ytActive: boolean;
  /** Пометка режима «Полностью»: пропуск/фолбэк — бейдж в мини-плеере */
  fullNote: string | null;
  /** Мини-плеер отдаёт контейнер ВИДИМОГО окна YT (~120×68); null — снят */
  registerYtContainer: (el: HTMLDivElement | null) => void;
  /** Позиция/длительность YT-трека для прогресса; null — плеер не готов */
  getYtTime: () => { current: number; duration: number } | null;
}

function trackUrl(track: QueueTrack): string | null {
  return typeof track.url === "string" && track.url !== "" ? track.url : null;
}

function trackVideoId(track: QueueTrack): string | null {
  return typeof track.videoId === "string" && track.videoId !== ""
    ? track.videoId
    : null;
}

export function usePreviewPlayer(): PreviewPlayer {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const playingRef = useRef<string | null>(null);
  const [playingUrl, setPlayingUrl] = useState<string | null>(null);

  // очередь — ref-дубли для обработчиков <audio> (слушатели вешаются один раз)
  const queueRef = useRef<QueueTrack[] | null>(null);
  const indexRef = useRef(-1);
  const [queue, setQueue] = useState<QueueTrack[] | null>(null);
  const [queueIndex, setQueueIndex] = useState(-1);
  const [queuePaused, setQueuePaused] = useState(false);
  const queuePausedRef = useRef(false);

  // v0.10 §D: режим и состояние YT-плеера
  const modeRef = useRef<PlayerMode>("preview");
  const [mode, setModeState] = useState<PlayerMode>("preview");
  const [ytActive, setYtActive] = useState(false);
  const ytActiveRef = useRef(false);
  const [fullNote, setFullNote] = useState<string | null>(null);
  const ytHostRef = useRef<HTMLDivElement | null>(null);
  const ytPlayerRef = useRef<YtPlayer | null>(null);
  const ytReadyRef = useRef(false);
  const ytCreatingRef = useRef(false);
  // поколение создания: destroy инвалидирует создание «в полёте», иначе
  // dev-StrictMode (реф снялся/вернулся, пока грузится iframe_api) плодит
  // ДВА YT-плеера — двойной iframe, двойной звук
  const ytCreateSeqRef = useRef(0);
  const pendingVideoIdRef = useRef<string | null>(null);

  // «трек закончился/сломался»: логика зависит от режима, поэтому живёт
  // в ref — сам <audio> и его слушатели создаются один раз
  const trackDoneRef = useRef<() => void>(() => {});
  // onError YT-плеера — фолбэк/пропуск, тоже через ref (слушатель один)
  const ytErrorRef = useRef<() => void>(() => {});

  const setPaused = useCallback((value: boolean) => {
    queuePausedRef.current = value;
    setQueuePaused(value);
  }, []);

  const setYtActiveBoth = useCallback((value: boolean) => {
    ytActiveRef.current = value;
    setYtActive(value);
  }, []);

  /* ---- YT-плеер: создание, загрузка, остановка ---- */

  const destroyYtPlayer = useCallback(() => {
    ytCreateSeqRef.current += 1; // создание в полёте теперь устарело
    const player = ytPlayerRef.current;
    ytPlayerRef.current = null;
    ytReadyRef.current = false;
    ytCreatingRef.current = false;
    if (player !== null) {
      try {
        player.destroy();
      } catch {
        // iframe мог уже уйти из DOM вместе с мини-плеером
      }
    }
  }, []);

  /** Останов YT-воспроизведения (сам плеер живёт, пока виден контейнер). */
  const stopYt = useCallback(() => {
    pendingVideoIdRef.current = null;
    if (ytActiveRef.current) setYtActiveBoth(false);
    const player = ytPlayerRef.current;
    if (player !== null && ytReadyRef.current) {
      try {
        player.stopVideo();
      } catch {
        // плеер мог не успеть инициализироваться — не страшно
      }
    }
  }, [setYtActiveBoth]);

  /** Если плеер готов и есть отложенный videoId — загружаем его. */
  const flushYtLoad = useCallback(() => {
    const player = ytPlayerRef.current;
    const videoId = pendingVideoIdRef.current;
    if (player === null || !ytReadyRef.current || videoId === null) return;
    pendingVideoIdRef.current = null;
    try {
      player.loadVideoById(videoId);
    } catch {
      // не смогли загрузить — onError не придёт, но и падать нельзя
    }
  }, []);

  /**
   * Создаёт YT.Player в контейнере мини-плеера (видимое окно ~120×68).
   * Скрипт iframe_api грузится лениво — только при первом включении режима.
   */
  const ensureYtPlayer = useCallback(async () => {
    if (ytPlayerRef.current !== null) {
      flushYtLoad();
      return;
    }
    if (ytCreatingRef.current) return; // создание в полёте — onReady дожмёт
    const host = ytHostRef.current;
    if (host === null) return; // контейнер ещё не в DOM — registerYtContainer дожмёт
    ytCreatingRef.current = true;
    const seq = ytCreateSeqRef.current;
    const api = await loadYouTubeApi();
    if (seq !== ytCreateSeqRef.current || ytPlayerRef.current !== null) {
      // пока грузился скрипт, плеер снесли (registerYtContainer(null) —
      // StrictMode-ремонт, закрытие) — этим создание устарело; актуальным
      // ytCreatingRef владеет уже более позднее включение
      return;
    }
    const mountHost = ytHostRef.current;
    if (mountHost === null) {
      // за время загрузки скрипта мини-плеер закрыли
      ytCreatingRef.current = false;
      return;
    }
    // YT.Player подменяет переданный элемент iframe'ом — отдаём ему
    // одноразовый дочерний div, чтобы React-узел остался нетронутым
    const mount = document.createElement("div");
    mountHost.appendChild(mount);
    ytPlayerRef.current = new api.Player(mount, {
      width: "120",
      height: "68",
      playerVars: { playsinline: 1, rel: 0 },
      events: {
        onReady: () => {
          ytReadyRef.current = true;
          flushYtLoad();
        },
        onStateChange: (e) => {
          if (!ytActiveRef.current) return;
          const states = window.YT?.PlayerState;
          if (e.data === (states?.ENDED ?? 0)) {
            trackDoneRef.current();
          } else if (e.data === (states?.PAUSED ?? 2)) {
            // пауза могла прийти из контролов самого iframe — синхронизируемся
            setPaused(true);
          } else if (e.data === (states?.PLAYING ?? 1)) {
            setPaused(false);
          }
        },
        onError: () => ytErrorRef.current(),
      },
    });
  }, [flushYtLoad, setPaused]);

  /** Полный трек через YT: превью замолкает, видимый плеер играет. */
  const playViaYt = useCallback(
    (videoId: string) => {
      audioRef.current?.pause();
      pendingVideoIdRef.current = videoId;
      setYtActiveBoth(true);
      void ensureYtPlayer();
    },
    [ensureYtPlayer, setYtActiveBoth],
  );

  /** Мини-плеер отдаёт/забирает контейнер видимого окна YT. */
  const registerYtContainer = useCallback(
    (el: HTMLDivElement | null) => {
      if (ytHostRef.current === el) return;
      ytHostRef.current = el;
      if (el === null) {
        // окно ушло из DOM (закрыли плеер / вернулись на превью) — сносим
        destroyYtPlayer();
        return;
      }
      if (pendingVideoIdRef.current !== null) void ensureYtPlayer();
    },
    [destroyYtPlayer, ensureYtPlayer],
  );

  const getYtTime = useCallback(() => {
    const player = ytPlayerRef.current;
    if (player === null || !ytReadyRef.current) return null;
    try {
      return {
        current: player.getCurrentTime?.() ?? 0,
        duration: player.getDuration?.() ?? 0,
      };
    } catch {
      return null;
    }
  }, []);

  // unmount страницы — останавливаем воспроизведение
  useEffect(() => {
    return () => {
      queueRef.current = null;
      playingRef.current = null;
      const audio = audioRef.current;
      if (audio !== null) {
        audio.pause();
        audio.removeAttribute("src");
        audioRef.current = null;
      }
      destroyYtPlayer();
    };
  }, [destroyYtPlayer]);

  const ensureAudio = useCallback((): HTMLAudioElement => {
    let audio = audioRef.current;
    if (audio === null) {
      // единственный на страницу элемент <audio>, живёт в ref
      audio = new Audio();
      audio.preload = "none";
      audio.addEventListener("ended", () => trackDoneRef.current());
      audio.addEventListener("error", () => trackDoneRef.current());
      audioRef.current = audio;
    }
    return audio;
  }, []);

  /** Выход из режима очереди; pauseAudio — с полной остановкой звука. */
  const clearQueue = useCallback(
    (pauseAudio: boolean) => {
      queueRef.current = null;
      indexRef.current = -1;
      setQueue(null);
      setQueueIndex(-1);
      setPaused(false);
      setFullNote(null);
      if (pauseAudio) {
        audioRef.current?.pause();
        stopYt();
        playingRef.current = null;
        setPlayingUrl(null);
      }
    },
    [setPaused, stopYt],
  );

  /** Превью трека очереди через общий <audio> (без смены индекса). */
  const playPreviewOfTrack = useCallback(
    (tracks: QueueTrack[], url: string) => {
      const audio = ensureAudio();
      if (audio.src !== url) {
        audio.src = url;
      } else {
        // прыжок на уже загруженный трек — с начала
        audio.currentTime = 0;
      }
      playingRef.current = url;
      setPlayingUrl(url);
      void audio.play().catch(() => {
        // автоплей-политика: остаёмся на треке в паузе; битый URL добьёт
        // событие error — очередь шагнёт дальше сама (см. trackDoneRef)
        if (playingRef.current === url && queueRef.current === tracks) {
          playingRef.current = null;
          setPlayingUrl(null);
          setPaused(true);
        }
      });
    },
    [ensureAudio, setPaused],
  );

  /**
   * Запуск трека очереди по индексу с учётом режима (v0.10 §D):
   * «Полностью» — videoId в видимый YT-плеер; без videoId — превью с
   * пометкой; без того и другого — пропуск. «Превью» — как раньше;
   * треки без превью пропускаются. За последним треком — стоп и скрыть.
   * note — пометка от вызывающего (напр. «недоступен во встраивании»).
   */
  const startTrack = useCallback(
    (tracks: QueueTrack[], index: number, note: string | null = null) => {
      let i = Math.max(0, index);
      let skipNote = note;
      while (i < tracks.length) {
        const track = tracks[i];
        const url = trackUrl(track);
        const videoId = trackVideoId(track);

        if (modeRef.current === "full") {
          if (videoId !== null) {
            indexRef.current = i;
            setQueueIndex(i);
            setPaused(false);
            setFullNote(skipNote);
            playingRef.current = url;
            setPlayingUrl(url ?? `yt:${videoId}`);
            playViaYt(videoId);
            return;
          }
          if (url !== null) {
            // полной версии нет — превью с пометкой (спека разрешает)
            indexRef.current = i;
            setQueueIndex(i);
            setPaused(false);
            setFullNote(
              skipNote ?? "у трека нет полной версии — играет превью",
            );
            stopYt();
            playPreviewOfTrack(tracks, url);
            return;
          }
          skipNote = `«${track.label}» без превью и полной версии — пропущен`;
          i += 1;
          continue;
        }

        // режим «Превью 30с»
        if (url !== null) {
          indexRef.current = i;
          setQueueIndex(i);
          setPaused(false);
          setFullNote(null);
          stopYt();
          playPreviewOfTrack(tracks, url);
          return;
        }
        i += 1;
      }
      // конец очереди (или все оставшиеся непроигрываемы) — мини-плеер прячется
      clearQueue(true);
    },
    [clearQueue, playPreviewOfTrack, playViaYt, setPaused, stopYt],
  );

  // ended/error превью: в очереди — автопереход на следующий, иначе сброс.
  // ENDED YT-плеера приходит сюда же (onStateChange → trackDoneRef).
  useEffect(() => {
    trackDoneRef.current = () => {
      const tracks = queueRef.current;
      if (tracks === null) {
        playingRef.current = null;
        setPlayingUrl(null);
        return;
      }
      startTrack(tracks, indexRef.current + 1);
    };
  }, [startTrack]);

  // onError YT: бейдж «недоступен во встраивании», фолбэк на превью/пропуск
  useEffect(() => {
    ytErrorRef.current = () => {
      if (modeRef.current !== "full" || !ytActiveRef.current) return;
      const tracks = queueRef.current;
      const index = indexRef.current;
      const track = tracks?.[index];
      if (tracks === null || track === undefined) return;
      setYtActiveBoth(false);
      const url = trackUrl(track);
      if (url !== null) {
        setFullNote(`«${track.label}» недоступен во встраивании — играет превью`);
        playPreviewOfTrack(tracks, url);
      } else {
        startTrack(
          tracks,
          index + 1,
          `«${track.label}» недоступен во встраивании — пропущен`,
        );
      }
    };
  }, [playPreviewOfTrack, setYtActiveBoth, startTrack]);

  const playQueue = useCallback(
    (tracks: QueueTrack[], startIndex = 0) => {
      if (tracks.length === 0) return;
      queueRef.current = tracks;
      setQueue(tracks);
      startTrack(tracks, Math.min(Math.max(startIndex, 0), tracks.length - 1));
    },
    [startTrack],
  );

  const queueNext = useCallback(() => {
    const tracks = queueRef.current;
    if (tracks !== null) startTrack(tracks, indexRef.current + 1);
  }, [startTrack]);

  const queuePrev = useCallback(() => {
    const tracks = queueRef.current;
    // на первом треке — просто с начала
    if (tracks !== null) startTrack(tracks, Math.max(0, indexRef.current - 1));
  }, [startTrack]);

  const queueTogglePause = useCallback(() => {
    const tracks = queueRef.current;
    if (tracks === null) return;
    const current = tracks[indexRef.current];
    if (current === undefined) return;

    // v0.10 §D: трек играет в YT-плеере — пауза/пуск через его API
    if (ytActiveRef.current) {
      const player = ytPlayerRef.current;
      if (player === null || !ytReadyRef.current) return;
      try {
        if (queuePausedRef.current) {
          player.playVideo();
          setPaused(false);
        } else {
          player.pauseVideo();
          setPaused(true);
        }
      } catch {
        // плеер в переходном состоянии — следующий клик сработает
      }
      return;
    }

    const audio = audioRef.current;
    const url = trackUrl(current);
    if (audio === null || url === null) return;
    if (audio.paused) {
      playingRef.current = url;
      setPlayingUrl(url);
      setPaused(false);
      void audio.play().catch(() => {
        if (playingRef.current === url) {
          playingRef.current = null;
          setPlayingUrl(null);
          setPaused(true);
        }
      });
    } else {
      audio.pause();
      playingRef.current = null;
      setPlayingUrl(null);
      setPaused(true);
    }
  }, [setPaused]);

  const stopQueue = useCallback(() => clearQueue(true), [clearQueue]);

  /** v0.10 §D: переключение режима; активный трек перезапускается в новом. */
  const setMode = useCallback(
    (next: PlayerMode) => {
      if (modeRef.current === next) return;
      modeRef.current = next;
      setModeState(next);
      const tracks = queueRef.current;
      if (tracks !== null && indexRef.current >= 0) {
        startTrack(tracks, indexRef.current);
      }
    },
    [startTrack],
  );

  const toggle = useCallback(
    (url: string, extras?: ToggleExtras) => {
      // при активной очереди клик по её треку — прыжок очереди на него
      // (тот же трек — пауза/продолжить); чужой трек — выход в одиночный режим
      const tracks = queueRef.current;
      if (tracks !== null) {
        const at = tracks.findIndex((t) => t.url === url);
        if (at >= 0) {
          if (at === indexRef.current) queueTogglePause();
          else startTrack(tracks, at);
          return;
        }
        clearQueue(false);
      }
      // v0.10 §D: режим «Полностью» + известный videoId → одиночная очередь:
      // мини-плеер с ВИДИМЫМ YT-окном (прятать плеер запрещает YouTube)
      const videoId = extras?.videoId;
      if (
        modeRef.current === "full" &&
        typeof videoId === "string" &&
        videoId !== ""
      ) {
        playQueue(
          [
            {
              url,
              videoId,
              label: extras?.label ?? url,
              meta: extras?.meta,
            },
          ],
          0,
        );
        return;
      }
      const audio = ensureAudio();
      if (playingRef.current === url) {
        // повторный клик по играющему треку — пауза
        audio.pause();
        playingRef.current = null;
        setPlayingUrl(null);
        return;
      }
      // одиночное превью глушит возможный YT-хвост
      stopYt();
      // переключение трека: смена src останавливает предыдущий;
      // тот же src после паузы — продолжаем с места остановки
      if (audio.src !== url) {
        audio.src = url;
      }
      playingRef.current = url;
      setPlayingUrl(url);
      void audio.play().catch(() => {
        // автоплей-политика или битый URL — сбрасываем, если трек ещё «текущий»
        if (playingRef.current === url) {
          playingRef.current = null;
          setPlayingUrl(null);
        }
      });
    },
    [queueTogglePause, startTrack, clearQueue, ensureAudio, playQueue, stopYt],
  );

  const getAudio = useCallback(() => audioRef.current, []);

  return {
    playingUrl,
    toggle,
    queue,
    queueIndex,
    queuePaused,
    playQueue,
    queueNext,
    queuePrev,
    queueTogglePause,
    stopQueue,
    getAudio,
    mode,
    setMode,
    ytActive,
    fullNote,
    registerYtContainer,
    getYtTime,
  };
}

/**
 * "artist — title" → {artist, title} для POST /api/preview/resolve.
 * Разделитель — эм-даш с пробелами (так собирает лейблы бэк). Без него
 * весь лейбл уходит в title — каскад на бэке ищет по склейке и так.
 */
export function splitTrackLabel(label: string): {
  artist: string;
  title: string;
} {
  const sep = " — ";
  const at = label.indexOf(sep);
  if (at === -1) return { artist: "", title: label.trim() };
  return {
    artist: label.slice(0, at).trim(),
    title: label.slice(at + sep.length).trim(),
  };
}
