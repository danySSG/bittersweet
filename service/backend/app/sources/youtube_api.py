"""Источник «лайки YouTube» через YouTube Data API v3 (SPEC v0.5 §A3, v0.7 §A1).

Проверенные факты (исследование 2026-07-10):
- likes-плейлист: channels.list(mine=true) -> contentDetails.relatedPlaylists.likes;
- playlistItems.list отдаёт лайки свежие-первыми, 1 unit / 50 треков;
- лайки из YT Music попадают в этот же список; артист авто-треков =
  videoOwnerChannelTitle с суффиксом « - Topic» (общий срез в engine/topic.py);
- приватные/удалённые видео не возвращаются videos.list -> отфильтровываются;
- фильтр длительности: > 12 минут = не трек (миксы, подкасты, стримы).

v0.7: перед анализом лайки фильтруются на «только музыка» (is_music_item):
snippet.categoryId == "10" ИЛИ владелец « - Topic». Скан идёт до конца
плейлиста (или SCAN_CAP=5000), музыкальный фильтр — ДО расходования глубины.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

import httpx

from app.engine.topic import TOPIC_SUFFIX, strip_topic_suffix

log = logging.getLogger(__name__)

API_BASE = "https://www.googleapis.com/youtube/v3"

TIMEOUT = 15.0
PAGE_SIZE = 50          # максимум YouTube Data API
MAX_TRACK_SECONDS = 12 * 60  # длиннее — не трек

MUSIC_CATEGORY_ID = "10"  # категория YouTube «Music»
SCAN_CAP = 5000           # безопасный максимум скана likes-плейлиста (SPEC v0.7 §A1)

_DURATION_RE = re.compile(
    r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?$"
)

# заглушки playlistItems для недоступных видео (на всякий случай, поверх videos.list)
_UNAVAILABLE_TITLES = {"Private video", "Deleted video"}


class YouTubeAPIError(RuntimeError):
    """Ошибка YouTube Data API (сеть/статус/квота)."""


class YouTubeQuotaError(YouTubeAPIError):
    """403 quotaExceeded: дневная квота YouTube Data API (10k units) исчерпана.

    Отдельный класс, чтобы длинная джоба скана могла честно сказать
    «квота исчерпана, попробуй завтра», а не «API сломался» (SPEC v0.7 §A1).
    """


def _get(client: httpx.Client, path: str, params: dict, access_token: str) -> dict:
    try:
        r = client.get(
            f"{API_BASE}/{path}",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except httpx.HTTPError as e:
        raise YouTubeAPIError(f"YouTube API недоступен: {e}") from e
    if r.status_code == 403 and "quotaExceeded" in r.text:
        # дневная квота исчерпана — ретраить в рамках джобы бессмысленно
        raise YouTubeQuotaError(f"YouTube API {path}: 403 quotaExceeded")
    if r.status_code != 200:
        raise YouTubeAPIError(f"YouTube API {path}: {r.status_code} {r.text[:200]}")
    try:
        return r.json()
    except ValueError as e:
        raise YouTubeAPIError(f"YouTube API {path}: не-JSON ответ") from e


def fetch_channel_info(access_token: str) -> dict:
    """Свой канал: {channel_id, channel_title, likes_playlist_id}.

    channel_id — ключ пользователя (identity), likes_playlist_id — плейлист
    лайков из contentDetails.relatedPlaylists.likes.
    """
    with httpx.Client(timeout=TIMEOUT) as client:
        data = _get(
            client, "channels",
            {"part": "snippet,contentDetails", "mine": "true"},
            access_token,
        )
    items = data.get("items") or []
    if not items:
        raise YouTubeAPIError("у аккаунта Google нет YouTube-канала")
    ch = items[0]
    likes = (
        (ch.get("contentDetails") or {}).get("relatedPlaylists") or {}
    ).get("likes")
    return {
        "channel_id": ch.get("id"),
        "channel_title": (ch.get("snippet") or {}).get("title") or "",
        "likes_playlist_id": likes or None,
    }


def fetch_likes_total(access_token: str, playlist_id: str) -> int:
    """Сколько всего лайков: pageInfo.totalResults первого playlistItems.list.

    Один запрос part=id, maxResults=1 — 1 unit квоты (SPEC v0.6 §A1,
    GET /api/youtube/summary: пользователь выбирает глубину анализа осознанно).
    """
    with httpx.Client(timeout=TIMEOUT) as client:
        data = _get(
            client, "playlistItems",
            {"part": "id", "playlistId": playlist_id, "maxResults": 1},
            access_token,
        )
    try:
        return int((data.get("pageInfo") or {}).get("totalResults") or 0)
    except (TypeError, ValueError):
        return 0


def is_music_item(category_id: str | None, owner: str | None) -> bool:
    """Музыкальный ли лайк (SPEC v0.7 §A1) — чистый предикат без сети.

    snippet.categoryId == "10" (категория YouTube «Music») ИЛИ владелец
    оканчивается на " - Topic" (авто-каналы YT Music: у них категория
    изредка бывает не 10, поэтому условия объединены через ИЛИ).
    """
    if (category_id or "").strip() == MUSIC_CATEGORY_ID:
        return True
    return (owner or "").strip().endswith(TOPIC_SUFFIX)


def _fetch_video_details(
    client: httpx.Client, video_ids: list[str], access_token: str
) -> dict[str, dict]:
    """videos.list батчами по 50 (part=snippet,contentDetails) ->
    {videoId: {"category_id": str|None, "seconds": int|None}}.

    Приватные/удалённые видео в ответ не попадают — их просто нет в словаре.
    """
    details: dict[str, dict] = {}
    for i in range(0, len(video_ids), PAGE_SIZE):
        batch = video_ids[i : i + PAGE_SIZE]
        data = _get(
            client, "videos",
            {"part": "snippet,contentDetails", "id": ",".join(batch)},
            access_token,
        )
        for it in data.get("items") or []:
            vid = it.get("id")
            if not vid:
                continue
            details[vid] = {
                "category_id": (it.get("snippet") or {}).get("categoryId"),
                "seconds": parse_duration(
                    (it.get("contentDetails") or {}).get("duration")
                ),
            }
    return details


def scan_music_likes(
    access_token: str,
    playlist_id: str,
    limit: int | None = None,
    scan_cap: int = SCAN_CAP,
    progress_cb: Callable[[int, int, int], None] | None = None,
) -> list[dict]:
    """Скан likes-плейлиста с музыкальным фильтром (SPEC v0.7 §A1):
    [{artist, title, videoId, liked_at}] — только музыка, свежие первыми.

    liked_at (SPEC v0.10 §A) — snippet.publishedAt элемента likes-плейлиста:
    дата добавления в LL = дата лайка, ISO-строка или None. Поле уже приходит
    в запрошенном part=snippet — новых квот не тратим.

    Паджинация playlistItems до конца плейлиста (или до scan_cap=5000);
    каждая страница сразу пробивается videos.list (snippet+contentDetails):
    остаются is_music_item (категория 10 ИЛИ « - Topic»), доступные,
    длительностью <= 12 минут. limit применяется УЖЕ К ОТФИЛЬТРОВАННОМУ
    списку; порядок свежие-первыми, поэтому набрав limit музыкальных, скан
    останавливается — результат совпадает с полным сканом + срезом,
    а квота не тратится зря. limit=None/0 — без лимита (все музыкальные).

    progress_cb(scanned, total_estimate, music_found): просканировано лайков /
    оценка объёма (pageInfo.totalResults, но не больше scan_cap) / сколько
    музыкальных найдено на текущий момент.

    Квота (SPEC v0.7 §A1): полный скан 5000 лайков = ~100 units playlistItems
    (1 unit x 100 страниц по 50) + ~100 units videos.list (1 unit x 100 батчей
    по 50) — укладывается в дневные 10 000 units с большим запасом.
    """
    if not limit or limit <= 0:
        limit = None  # 0/None = без лимита (SPEC v0.7 §A2)
    tracks: list[dict] = []
    scanned = 0
    total_estimate = 0
    page_token: str | None = None
    with httpx.Client(timeout=TIMEOUT) as client:
        while scanned < scan_cap:
            params = {
                "part": "snippet",
                "playlistId": playlist_id,
                "maxResults": min(PAGE_SIZE, scan_cap - scanned),
            }
            if page_token:
                params["pageToken"] = page_token
            data = _get(client, "playlistItems", params, access_token)
            raw_items = data.get("items") or []
            scanned += len(raw_items)
            try:
                total_results = int(
                    (data.get("pageInfo") or {}).get("totalResults") or 0
                )
            except (TypeError, ValueError):
                total_results = 0
            total_estimate = max(
                total_estimate, min(total_results, scan_cap), scanned
            )

            # валидные элементы страницы (без заглушек приватных/удалённых)
            stubs: list[dict] = []
            for it in raw_items:
                sn = it.get("snippet") or {}
                video_id = (sn.get("resourceId") or {}).get("videoId")
                title = (sn.get("title") or "").strip()
                if not video_id or not title or title in _UNAVAILABLE_TITLES:
                    continue
                stubs.append({
                    "videoId": video_id,
                    "title": title,
                    "owner": sn.get("videoOwnerChannelTitle") or "",
                    # дата добавления в likes-плейлист = дата лайка (v0.10 §A)
                    "liked_at": sn.get("publishedAt") or None,
                })

            details = _fetch_video_details(
                client, [s["videoId"] for s in stubs], access_token
            )
            for s in stubs:
                det = details.get(s["videoId"])
                if det is None:
                    continue  # приватное/удалённое: videos.list его не вернул
                if not is_music_item(det["category_id"], s["owner"]):
                    continue  # не музыка — не тратим на него глубину анализа
                seconds = det["seconds"]
                if seconds is None or seconds > MAX_TRACK_SECONDS:
                    continue  # длиннее 12 минут — не трек (миксы/подкасты)
                tracks.append({
                    "artist": strip_topic_suffix(s["owner"]),
                    "title": s["title"],
                    "videoId": s["videoId"],
                    "liked_at": s["liked_at"],
                })
                if limit is not None and len(tracks) >= limit:
                    break

            if progress_cb:
                progress_cb(scanned, total_estimate, len(tracks))
            if limit is not None and len(tracks) >= limit:
                break
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    return tracks


def parse_duration(iso: str | None) -> int | None:
    """ISO-8601 длительность ("PT3M42S") -> секунды; None, если не разобралась."""
    if not iso:
        return None
    m = _DURATION_RE.match(iso.strip())
    if not m:
        return None
    days = int(m.group("d") or 0)
    hours = int(m.group("h") or 0)
    minutes = int(m.group("m") or 0)
    seconds = int(m.group("s") or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds
