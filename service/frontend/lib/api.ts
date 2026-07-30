import type {
  CompareResult,
  Job,
  Me,
  ObservatoryPayload,
  Portrait,
  PortraitListItem,
  SavedPortrait,
  StoryPayload,
  YoutubeSummary,
} from "./types";

/** Базовый URL бэкенда. Задаётся через NEXT_PUBLIC_API_URL, дефолт — локальный FastAPI. */
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Ссылка для кнопки «Войти через YouTube» (v0.5, официальный Google OAuth). */
export const YOUTUBE_LOGIN_URL = `${API_URL}/auth/google/login`;

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Достаёт человеческое сообщение из тела ошибки FastAPI:
 * detail может быть строкой (наша валидация) или массивом ошибок pydantic.
 */
async function readErrorDetail(res: Response): Promise<string | null> {
  try {
    const body: unknown = await res.json();
    if (body !== null && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string") return detail;
      if (Array.isArray(detail)) {
        const messages = detail
          .map((item) =>
            item !== null && typeof item === "object" && "msg" in item
              ? String((item as { msg: unknown }).msg)
              : null,
          )
          .filter((msg): msg is string => msg !== null);
        if (messages.length > 0) return messages.join("; ");
      }
    }
  } catch {
    // тело не JSON — вернём null, возьмём дефолтное сообщение
  }
  return null;
}

/**
 * Общий запрос к бэкенду: сеть → ApiError без статуса,
 * не-2xx → ApiError со статусом и человеческим detail (если пришёл).
 */
async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      credentials: "include",
      ...init,
      headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(
      `Не удалось подключиться к бэкенду по адресу ${API_URL}.`,
    );
  }
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    throw new ApiError(
      detail ?? `Бэкенд ответил ошибкой ${res.status} на ${path}.`,
      res.status,
    );
  }
  return (await res.json()) as T;
}

/**
 * Портрет. В MVP всегда ходим на демо-эндпоинт;
 * при demo=false пробуем сессионный /api/portrait и падаем обратно на демо.
 */
export async function fetchPortrait(demo: boolean): Promise<Portrait> {
  if (demo) {
    return requestJson<Portrait>("/api/demo/portrait");
  }
  try {
    return await requestJson<Portrait>("/api/portrait");
  } catch (err) {
    // Нет сессии Spotify (или роут ещё не готов) — MVP-фолбэк на демо.
    if (err instanceof ApiError && err.status !== undefined) {
      return requestJson<Portrait>("/api/demo/portrait");
    }
    throw err;
  }
}

/* ---------- v0.2: анализ плейлиста YouTube Music ---------- */

/**
 * POST /api/analyze/playlist → 202 {job_id}.
 * 422 приходит с человеческим сообщением в detail — пробрасываем его как есть.
 */
export async function startPlaylistAnalysis(
  url: string,
  limit = 40,
): Promise<{ job_id: string }> {
  return requestJson<{ job_id: string }>("/api/analyze/playlist", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, limit }),
  });
}

/* ---------- v0.2b: импорт выгрузки Google Takeout ---------- */

/**
 * POST /api/analyze/takeout → 202 {job_id}.
 * Multipart: .zip всей выгрузки или одиночный .csv (до 100 МБ, 413 при превышении).
 * Content-Type не ставим руками — браузер сам добавит boundary для FormData.
 */
export async function startTakeoutAnalysis(
  file: File,
  limit = 40,
): Promise<{ job_id: string }> {
  const form = new FormData();
  form.append("file", file);
  form.append("limit", String(limit));
  return requestJson<{ job_id: string }>("/api/analyze/takeout", {
    method: "POST",
    body: form,
  });
}

/** GET /api/jobs/{job_id} → полное состояние джобы (404, если джобы нет). */
export async function fetchJob(jobId: string): Promise<Job> {
  return requestJson<Job>(`/api/jobs/${encodeURIComponent(jobId)}`);
}

/* ---------- v0.3: постоянные ссылки и сравнение ---------- */

/** GET /api/p/{id} → сохранённый портрет (404, если нет). */
export async function fetchSavedPortrait(id: string): Promise<SavedPortrait> {
  return requestJson<SavedPortrait>(`/api/p/${encodeURIComponent(id)}`);
}

/**
 * v0.11 (SPEC-v11-observatory §B): GET /api/p/{id}/observatory —
 * аналитический этаж портрета. Первая сборка на бэке может занять секунды
 * (TSNE), повторные — мгновенно из payload. Ошибки статусами:
 * 404 — старый бэк без роута (секция скрыта молча),
 * 409 — портрет старой версии без features (подсказка «перестрой»).
 */
export async function fetchObservatory(
  id: string,
): Promise<ObservatoryPayload> {
  return requestJson<ObservatoryPayload>(
    `/api/p/${encodeURIComponent(id)}/observatory`,
  );
}

/**
 * v0.12 (SPEC-v12-human §A): GET /api/p/{id}/story — человеческая история
 * портрета (ленивая одноразовая сборка на бэке, повторные — из payload).
 * Ошибки статусами: 404 — старый бэк без роута (вид «История» показывает
 * мягкую заглушку «обнови бэкенд»), 409 — портрет старой версии.
 */
export async function fetchStory(id: string): Promise<StoryPayload> {
  return requestJson<StoryPayload>(`/api/p/${encodeURIComponent(id)}/story`);
}

/** POST /api/demo/portrait/save → {portrait_id} — постоянная ссылка на демо. */
export async function saveDemoPortrait(): Promise<{ portrait_id: string }> {
  return requestJson<{ portrait_id: string }>("/api/demo/portrait/save", {
    method: "POST",
  });
}

/* ---------- v0.4: discovery «найти ещё такого» ---------- */

/**
 * POST /api/discover → 202 {job_id}; дальше — общий поллинг /api/jobs/{id}.
 * 404 незнакомый портрет, 422 кривой кластер, 409 портрет без videoId
 * (сохранён старой версией) — detail человеческий, пробрасываем как есть.
 */
export async function startDiscovery(
  portraitId: string,
  cluster: number,
  limit = 15,
): Promise<{ job_id: string }> {
  return requestJson<{ job_id: string }>("/api/discover", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ portrait_id: portraitId, cluster, limit }),
  });
}

/**
 * v0.9 (SPEC-v09-features §B): discovery по точке карты —
 * POST /api/discover {portrait_id, point: {x, y}} (альтернатива cluster).
 * x/y — координаты в осях карты (valence/energy-перцентили 0..100).
 * 409 — рядом мало точек с превью (detail человеческий), 422 — валидация.
 */
export async function startPointDiscovery(
  portraitId: string,
  x: number,
  y: number,
  limit = 15,
): Promise<{ job_id: string }> {
  return requestJson<{ job_id: string }>("/api/discover", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ portrait_id: portraitId, point: { x, y }, limit }),
  });
}

/**
 * v0.9 (SPEC-v09-features §C): GET /api/portraits?limit= — список новейших
 * сохранённых портретов для страницы «Мои портреты». Бэк отдаёт голый массив;
 * на всякий случай принимаем и {portraits: […]}. Старый бэк без роута
 * ответит 404 (ApiError) — страница обязана показать человеческую ошибку.
 */
export async function fetchPortraitsList(
  limit = 50,
): Promise<PortraitListItem[]> {
  const body = await requestJson<unknown>(`/api/portraits?limit=${limit}`);
  if (Array.isArray(body)) return body as PortraitListItem[];
  if (body !== null && typeof body === "object") {
    const nested = (body as { portraits?: unknown }).portraits;
    if (Array.isArray(nested)) return nested as PortraitListItem[];
  }
  return [];
}

/** GET /api/compare?a=&b= → совместимость по звуку (404/422 — человеческий detail). */
export async function fetchCompare(
  a: string,
  b: string,
): Promise<CompareResult> {
  const qs = new URLSearchParams({ a, b });
  return requestJson<CompareResult>(`/api/compare?${qs.toString()}`);
}

/* ---------- v0.5: «Войти через YouTube» (официальный OAuth) ---------- */

/**
 * GET /api/me → {connected, channel_title?} по httpOnly session-cookie.
 * Старый бэк без роута ответит 404 (ApiError) — вызывающий код обязан
 * переживать это молча, не роняя страницу.
 */
export async function fetchMe(): Promise<Me> {
  return requestJson<Me>("/api/me");
}

/**
 * POST /api/analyze/youtube → 202 {job_id} — анализ лайков залогиненного
 * аккаунта. 401 без сессии, 503 если бэк без GOOGLE_CLIENT_ID.
 * v0.6: limit до 200 (глубина выбирается на экране «перед стартом»).
 * v0.7: limit 0 = БЕЗ ЛИМИТА (все музыкальные лайки, SPEC-v07 §A2);
 * старый бэк (валидация ge=1) ответит 422 — вызывающий код обязан
 * показать человеческое «обнови бэкенд».
 */
export async function startYoutubeAnalysis(
  limit = 40,
): Promise<{ job_id: string }> {
  return requestJson<{ job_id: string }>("/api/analyze/youtube", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ limit }),
  });
}

/* ---------- v0.6: экран «перед стартом» и превью для прослушивания ---------- */

/**
 * GET /api/youtube/summary → {channel_title, likes_total} — сводка для
 * карточки выбора глубины. 401 без сессии; старый бэк без роута ответит 404 —
 * вызывающий код обязан переживать это молча (карточка без числа лайков).
 */
export async function fetchYoutubeSummary(): Promise<YoutubeSummary> {
  return requestJson<YoutubeSummary>("/api/youtube/summary");
}

/** Элемент батча /api/preview/resolve. */
export interface PreviewResolveItem {
  artist: string;
  title: string;
}

/**
 * POST /api/preview/resolve {items: [{artist, title}], max 24} → {urls} —
 * ленивое дорезолвливание превью для старых кэш-записей (SPEC-v06-ux §A2).
 * urls позиционно соответствуют items; null — превью не нашлось.
 */
export async function resolvePreviews(
  items: PreviewResolveItem[],
): Promise<{ urls: (string | null)[] }> {
  return requestJson<{ urls: (string | null)[] }>("/api/preview/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items: items.slice(0, 24) }),
  });
}

/**
 * POST /auth/google/disconnect → revoke токена + удаление аккаунта и всех
 * youtube-портретов, cookie снимается. Тело ответа может быть пустым —
 * поэтому не requestJson (он требует JSON).
 */
export async function disconnectYoutube(): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/auth/google/disconnect`, {
      method: "POST",
      credentials: "include",
      headers: { Accept: "application/json" },
    });
  } catch {
    throw new ApiError(
      `Не удалось подключиться к бэкенду по адресу ${API_URL}.`,
    );
  }
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    throw new ApiError(
      detail ?? `Бэкенд ответил ошибкой ${res.status} на /auth/google/disconnect.`,
      res.status,
    );
  }
}
