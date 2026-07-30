/**
 * Типы под контракт GET /api/portrait и GET /api/demo/portrait
 * (см. docs/SPEC.md, раздел «Контракт /api/*\/portrait»).
 */

export interface ClusterMedians {
  tempo: number;
  minor_share: number;
  brightness: number;
}

/** Архетип кластера (v0.3), напр. {name: «грустный бэнгер», emoji: "🌒"}. */
export interface Archetype {
  name: string;
  emoji: string;
}

/**
 * Пример кластера с превью (v0.6, SPEC-v06-ux §A2). Аддитивно к examples:
 * label — тот же "artist — title", preview_url может быть null (в кэше
 * не оказалось URL — можно добрать через POST /api/preview/resolve).
 */
export interface ExampleRich {
  label: string;
  preview_url?: string | null;
  /**
   * videoId примера (v0.10, SPEC-v10-critique §D) — топливо режима
   * «Полностью» (YouTube IFrame API). ОПЦИОНАЛЬНО: старый бэк не отдаёт.
   */
  videoId?: string | null;
}

export interface Cluster {
  /** Человеческая метка, напр. «энергично-меланхоличное» */
  label: string;
  /** Количество треков в кластере */
  size: number;
  /** Доля от всех треков, % */
  share: number;
  medians: ClusterMedians;
  /** Примеры вида "artist — title" */
  examples: string[];
  /**
   * Примеры с превью (v0.6). ОПЦИОНАЛЬНО: старый бэк отдаёт только
   * examples-строки — фронт обязан рендерить без этого поля.
   */
  examples_rich?: ExampleRich[];
  /** HEX-цвет кластера, напр. "#7F77DD" */
  color: string;
  /**
   * Архетип кластера (v0.3). ОПЦИОНАЛЬНО: старый бэк поля не отдаёт —
   * фронт обязан рендерить без него.
   */
  archetype?: Archetype;
}

export interface Bittersweet {
  count: number;
  /** Доля от всех треков, % */
  share: number;
  /** Топ-треки категории, "artist — title" */
  top: string[];
  /**
   * Перцентиль (v0.3): доля сохранённых портретов с bittersweet.share ниже
   * текущего. null, если сохранённых портретов < 5. ОПЦИОНАЛЬНО для старого бэка.
   */
  percentile?: number | null;
}

/** Хайлайт «самый-самый» трек (v0.3). */
export interface Highlight {
  /** fastest | darkest | most_bittersweet | most_energetic | most_yours */
  kind: string;
  /** Например «самый быстрый» */
  title: string;
  /** "artist — title" */
  track: string;
  /** Например «172 bpm» */
  value: string;
  /**
   * Прямой URL превью (v0.6). ОПЦИОНАЛЬНО: старый бэк не отдаёт;
   * null — в кэше нет URL (можно добрать через /api/preview/resolve).
   */
  preview_url?: string | null;
  /**
   * videoId хайлайта (v0.10, SPEC-v10-critique §D) — топливо режима
   * «Полностью» (YouTube IFrame API). ОПЦИОНАЛЬНО: старый бэк не отдаёт.
   */
  videoId?: string | null;
}

/**
 * v0.10 (SPEC-v10-critique §A): бакет timeline «динамика вкуса по датам
 * лайков». Бэк строит по liked_at: месяцы, мелкие соседи слиты до
 * кварталов/полугодий. ОПЦИОНАЛЬНО всё, кроме дат — защищаемся от неполного
 * ответа.
 */
export interface TimelineBucket {
  /** ISO-дата начала периода */
  period_start: string;
  /** ISO-дата конца периода */
  period_end: string;
  n_tracks?: number | null;
  tempo_median?: number | null;
  minor_share?: number | null;
  bittersweet_share?: number | null;
  top_archetype?: Archetype | null;
}

export interface PortraitPoint {
  /** valence-перцентиль 0..100 */
  x: number;
  /** energy-перцентиль 0..100 */
  y: number;
  /** Индекс кластера (соответствует порядку в clusters) */
  cluster: number;
  /** "artist — title" */
  label: string;
  /** Например "157bpm · A minor" */
  meta: string;
  /**
   * Сырые MOOD_FEATURES точки (tempo, minor_score, brightness, …) — бэк
   * отдаёт с v0.4 как топливо discovery; v0.6 строит из них оси 3D-галактики
   * (перцентили считаются на клиенте). ОПЦИОНАЛЬНО: у старых портретов нет —
   * тогда переключатель «Галактика» скрыт.
   */
  features?: Record<string, number> | null;
  /** videoId точки (v0.4); Spotify-поток может не отдавать. */
  videoId?: string | null;
}

export interface Fingerprint {
  tempo_median: number;
  minor_share: number;
  brightness_mean: number;
}

/**
 * v0.13 (SPEC-v13-pulse §B): честное покрытие анализа. candidates — сколько
 * музыкальных треков нашли ДО матчинга превью, analyzed — сколько реально
 * попало в портрет (нашлось 30-сек превью).
 */
export interface Coverage {
  candidates: number;
  analyzed: number;
}

export interface Portrait {
  n_tracks: number;
  clusters: Cluster[];
  bittersweet: Bittersweet;
  points: PortraitPoint[];
  fingerprint: Fingerprint;
  /**
   * «Самые-самые» треки (v0.3), до 5 штук. ОПЦИОНАЛЬНО: старый бэк поля
   * не отдаёт — фронт обязан рендерить без него.
   */
  highlights?: Highlight[];
  /**
   * v0.10 (SPEC-v10-critique §A): динамика вкуса по датам лайков.
   * Бэк кладёт, только если >=60% точек имеют liked_at и покрытие >=3
   * месяцев. ОПЦИОНАЛЬНО: старый бэк / короткая история — поля нет.
   */
  timeline?: TimelineBucket[] | null;
  /**
   * v0.13 (SPEC-v13-pulse §B): честное покрытие — приписка в шапке
   * «проанализировано N из M». ОПЦИОНАЛЬНО: старые payload'ы поля не
   * имеют — фронт обязан рендерить без него.
   */
  coverage?: Coverage | null;
}

/**
 * Сохранённый портрет (v0.3): GET /api/p/{id} возвращает портрет
 * + id, source_label, created_at. Поля опциональны — защищаемся от старого бэка.
 */
export interface SavedPortrait extends Portrait {
  id?: string;
  /** «плейлист YouTube Music» / «демо» / «Takeout» */
  source_label?: string;
  /** ISO-дата сохранения */
  created_at?: string;
  /**
   * Сохранённые находки discovery (v0.4) по индексу кластера: {"0": …}.
   * Значение — полный результат джобы или просто список находок
   * (store.update_discoveries пишет список). ОПЦИОНАЛЬНО для старого бэка.
   */
  discoveries?: Record<string, DiscoveryResult | Discovery[]>;
}

/* ---------- v0.4: discovery «найти ещё такого» (SPEC-v04-discover) ---------- */

/** Один найденный трек-кандидат. */
export interface Discovery {
  artist: string;
  title: string;
  videoId: string;
  /** Близость к центроиду кластера, 0..100 (бэк отдаёт только >= 35) */
  match: number;
  /** bpm */
  tempo?: number;
  /** Тональность, напр. "A" */
  key?: string;
  /** "minor" | "major" */
  mode?: string;
  /** Прямой URL превью (iTunes/Deezer), проигрывается как есть; может не быть */
  preview_url?: string | null;
}

/**
 * v0.9 (SPEC-v09-features §B): цель discovery в done-результате.
 * kind="point" — поиск по точке карты: x/y — координаты в осях карты
 * (valence/energy-перцентили 0..100), label — «точка карты: настроение N ·
 * энергия M», near — до 3 label ближайших твоих треков. Для cluster-режима
 * kind="cluster", прежние поля результата не меняются (аддитивно).
 */
export interface DiscoveryTarget {
  kind?: "cluster" | "point" | string;
  x?: number;
  y?: number;
  label?: string;
  near?: string[];
}

/** Результат discovery-джобы (status=done). */
export interface DiscoveryResult {
  discoveries: Discovery[];
  /** Человеческая метка кластера-источника */
  cluster_label?: string;
  archetype?: Archetype | null;
  /** youtube.com/watch_videos?video_ids=… — плейлист из топ-находок */
  listen_all_url?: string | null;
  /**
   * v0.9: цель поиска (point-режим). ОПЦИОНАЛЬНО: старый бэк и
   * cluster-режим могут поля не отдавать — фронт обязан жить без него.
   */
  target?: DiscoveryTarget | null;
}

/* ---------- v0.11: «Обсерватория» (SPEC-v11-observatory) ---------- */

/**
 * Ответ GET /api/p/{id}/observatory — аналитический этаж портрета.
 * Бэкенд считает модели и отдаёт ГОТОВЫЕ К ОТРИСОВКЕ данные; фронт только
 * рисует. ВСЕ поля опциональны: рендерим только валидные панели, любая
 * недостающая/кривая — молча пропускается (защита от неполного бэка).
 */
export interface ObservatoryKeyWheel {
  /** 12 тональностей в кварто-квинтовом порядке [C,G,D,A,E,B,F#,…] */
  order?: string[];
  /** counts мажора по 12 позициям колеса (бэк отдаёт major_counts) */
  major_counts?: number[];
  /** counts минора по 12 позициям колеса (бэк отдаёт minor_counts) */
  minor_counts?: number[];
  /** legacy-имена counts — на случай старого бэка */
  major?: number[];
  minor?: number[];
  major_share?: number[];
  minor_share?: number[];
  /** Топ-тональность: строка «A minor» или объект с label/count */
  top?:
    | string
    | { label?: string; key?: string; mode?: string; count?: number }
    | null;
}

/** KDE-профиль одного признака (64 точки сетки, p1..p99). */
export interface ObservatoryRidgeline {
  /** tempo | brightness | minor_score | bittersweet */
  feature?: string;
  grid?: number[];
  /** Плотность, нормированная к максимуму 1 */
  density?: number[];
  median?: number;
  p25?: number;
  p75?: number;
}

export interface ObservatoryRadarAxis {
  name_ru?: string;
  /** Перцентиль медианы портрета относительно всех портретов базы, 0..100 */
  self_pct?: number;
  /** Средний портрет = 50 */
  avg_pct?: number;
}

export interface ObservatoryPcaAxis {
  /** Веса признаков компоненты: {tempo: 0.52, …} */
  loadings?: Record<string, number>;
  /** Человеческая интерпретация топ-3 |loadings|: «быстрее · ритмичнее» */
  label_pos?: string;
  label_neg?: string;
}

export interface ObservatoryOutlier {
  /** "artist — title" */
  label?: string;
  videoId?: string | null;
  /** Аномальность 0..100 (нормированный ранг) */
  score?: number;
  /** Топ-2 признака с максимальным |z| по-русски; строка или массив */
  why?: string[] | string;
}

export interface ObservatoryClock {
  /** 24 часовых бакета по liked_at */
  hour_counts?: number[];
  /** Доля биттерсвита в часе 0..1; null, если <3 треков */
  hour_bittersweet?: (number | null)[];
  weekday_counts?: number[];
  weekday_bittersweet?: (number | null)[];
  peak_hour?: number | null;
  /** «биттерсвит чаще всего ты лайкаешь в 22—24» или null */
  insight?: string | null;
}

export interface ObservatoryEntropy {
  /** Шеннон по долям кластеров, в битах (бэк отдаёт h_bits; legacy: bits/h/entropy) */
  h_bits?: number;
  bits?: number;
  h?: number;
  entropy?: number;
  /** 2^H, округлено до 0.1 */
  effective_moods?: number;
  /** «монолитный вкус» / «сфокусированный» / «многоликий» */
  verdict?: string;
}

export interface ObservatoryPayload {
  observatory_version?: number;
  key_wheel?: ObservatoryKeyWheel | null;
  /** Массив профилей или словарь {feature: профиль} */
  ridgelines?:
    | ObservatoryRidgeline[]
    | Record<string, ObservatoryRidgeline>
    | null;
  radar?: { axes?: ObservatoryRadarAxis[] } | null;
  pca?: {
    /** Доли объяснённой дисперсии двух компонент, % */
    explained?: number[];
    axes?: ObservatoryPcaAxis[];
  } | null;
  /** Координаты 0..100 ПО ИНДЕКСАМ points (та же длина и порядок) */
  tsne?: { x?: number[]; y?: number[] } | null;
  outliers?: ObservatoryOutlier[] | null;
  /** null, если liked_at меньше чем у 50 точек */
  clock?: ObservatoryClock | null;
  entropy?: ObservatoryEntropy | null;
}

/* ---------- v0.12: «История» (SPEC-v12-human §A/§B) ---------- */

/**
 * Интерактив главы истории. kind: archetype_chips | play_track | play_list |
 * timeline_mini | …; data — сырое топливо интерактива (форма зависит от kind,
 * фронт нормализует защитно и молча пропускает незнакомое).
 */
export interface StoryInteractive {
  kind?: string | null;
  data?: unknown;
}

/**
 * Глава истории GET /api/p/{id}/story. ВСЕ поля опциональны — рендерим
 * только валидные главы, защищаемся от неполного ответа бэка.
 */
export interface StoryChapter {
  /** identity | sound | home_key | rare_birds | story_of_time | clock | finale */
  id?: string | null;
  emoji?: string | null;
  title?: string | null;
  paragraphs?: unknown;
  interactive?: StoryInteractive | null;
}

/** Ответ GET /api/p/{id}/story — структурированная история портрета. */
export interface StoryPayload {
  story_version?: number;
  chapters?: StoryChapter[] | null;
}

/**
 * Типы под контракт POST /api/analyze/playlist и GET /api/jobs/{job_id}
 * (см. docs/SPEC-v02-ytmusic.md, раздел «Backend — изменения»).
 */

export type JobStatus = "queued" | "running" | "done" | "error";

/**
 * Этап джобы: Takeout (v0.2b) — parsing → resolving → analyzing;
 * discovery (v0.4) — seeding → radio → analyzing;
 * лайки YouTube (v0.5) — fetching → analyzing.
 */
export type JobStage =
  | "parsing"
  | "resolving"
  | "analyzing"
  | "seeding"
  | "radio"
  | "fetching";

export interface JobProgress {
  /** Сколько треков уже проанализировано */
  done: number;
  /** Сколько треков всего в очереди */
  total: number;
  /** Сколько треков удалось сматчить с превью */
  matched: number;
  /**
   * Текущий этап (v0.2b). ОПЦИОНАЛЬНО: старый бэк поля не отдаёт —
   * фронт обязан рендерить без него.
   */
  stage?: JobStage | null;
  /**
   * v0.7: сколько МУЗЫКАЛЬНЫХ лайков найдено к текущему моменту сканирования
   * (этап fetching youtube-джобы, SPEC-v07-music-filter §A1). ОПЦИОНАЛЬНО:
   * старый бэк поля не отдаёт — фронт обязан рендерить без него.
   */
  music_found?: number | null;
}

export interface Job {
  status: JobStatus;
  progress: JobProgress;
  /** Готовый портрет при status=done, иначе null */
  portrait: Portrait | null;
  /** Человеческое сообщение об ошибке при status=error, иначе null */
  error: string | null;
  /**
   * Постоянный id сохранённого портрета (v0.3), появляется при status=done.
   * ОПЦИОНАЛЬНО: старый бэк поля не отдаёт.
   */
  portrait_id?: string | null;
  /**
   * v0.4, discovery-джоба: результат при status=done — либо поля лежат
   * прямо в джобе (как portrait), либо вложены в result. Портретные джобы
   * этих полей не отдают — все опциональны.
   */
  discoveries?: Discovery[];
  cluster_label?: string;
  archetype?: Archetype | null;
  listen_all_url?: string | null;
  result?: DiscoveryResult | null;
  /**
   * v0.9: цель discovery (point-режим) при status=done — либо прямо в джобе,
   * либо внутри result. ОПЦИОНАЛЬНО: старый бэк поля не отдаёт.
   */
  target?: DiscoveryTarget | null;
  /**
   * v0.7: ISO-время старта джобы (безлимитные прогоны идут часами,
   * SPEC-v07-music-filter §A2). ОПЦИОНАЛЬНО: старый бэк поля не отдаёт.
   */
  started_at?: string | null;
  /**
   * v0.7: скорость анализа, треков в минуту (скользящее по последним 20) —
   * из неё фронт считает ETA на этапе analyzing. ОПЦИОНАЛЬНО: старый бэк
   * поля не отдаёт — тогда ETA просто не показываем.
   */
  rate_per_min?: number | null;
}

/**
 * v0.5: GET /api/me — состояние YouTube-сессии (httpOnly cookie,
 * SPEC-v05-youtube-oauth раздел A2). channel_title приходит только при
 * connected=true; поле опционально — не падаем на старом бэке.
 */
export interface Me {
  connected: boolean;
  channel_title?: string | null;
}

/**
 * v0.6: GET /api/youtube/summary — сводка подключённого аккаунта для экрана
 * «перед стартом» (SPEC-v06-ux §A1/B1). Поля опциональны — не падаем,
 * если бэк прислал неполный ответ.
 */
export interface YoutubeSummary {
  channel_title?: string | null;
  likes_total?: number | null;
}

/**
 * v0.9 (SPEC-v09-features §C): элемент списка GET /api/portraits —
 * лёгкая сводка сохранённого портрета для страницы «Мои портреты».
 * Все поля кроме id опциональны — не падаем на неполном ответе.
 */
export interface PortraitListItem {
  id: string;
  /** «плейлист YouTube Music» / «демо» / «Takeout» / «лайки YouTube» */
  source_label?: string | null;
  /** ISO-дата сохранения */
  created_at?: string | null;
  n_tracks?: number | null;
  /** Архетип крупнейшего кластера */
  top_archetype?: Archetype | null;
  fingerprint?: Fingerprint | null;
  /** Доля биттерсвита, % */
  bittersweet_share?: number | null;
  /**
   * v0.10 §A: есть ли в payload портрета timeline динамики вкуса —
   * бэк отдаёт флагом (сам массив в сводку не кладёт); по нему фронт
   * выбирает новейший портрет с timeline и тянет один полный payload.
   * ОПЦИОНАЛЬНО: старый бэк поля не отдаёт — фронт пробует кандидатов сам.
   */
  has_timeline?: boolean | null;
  /**
   * v0.10 §A: timeline может прийти и в лёгкой сводке (если бэк начнёт
   * отдавать) — тогда /portraits не ходит за полным payload. ОПЦИОНАЛЬНО.
   */
  timeline?: TimelineBucket[] | null;
}

/**
 * Типы под контракт GET /api/compare?a=&b= (v0.3, SPEC-v03-max раздел C).
 */

export interface CompareFacet {
  /** «темп» / «минор» / «яркость» / «биттерсвит» */
  name: string;
  a: number;
  b: number;
  /** "bpm" / "%" / "Гц" */
  unit: string;
}

export interface CompareResult {
  /** 0..100 */
  score: number;
  /** Имена архетипов, которые есть у обоих */
  common_archetypes: string[];
  facets: CompareFacet[];
  /** «музыкальные близнецы» и т.п. + 1 предложение */
  verdict: string;
}
