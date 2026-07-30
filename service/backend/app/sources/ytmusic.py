"""Источник «плейлист YouTube Music» — анонимное чтение публичных плейлистов.

Самодостаточная адаптация идей корневого fetch_tracks.py
(extract_playlist_id, normalize) — НЕ импорт из корня.

fetch_playlist(url_or_id, limit) -> list[Track], где Track =
{"artist": str, "title": str, "videoId": str, "duration_seconds": int|None,
 "liked_at": str|None}.
Треки длиннее MAX_DURATION_S (12 мин — mix/ambient-простыни, не песни)
отфильтровываются здесь же.

liked_at (SPEC v0.10 §A) — временная метка добавления трека в плейлист
(аналог «Playlist Video Creation Timestamp» из Takeout), ЕСЛИ ytmusicapi
её отдал; у анонимных публичных плейлистов её обычно нет -> None.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)

MAX_DURATION_S = 12 * 60  # >12 мин — не песня, а mix/ambient-простыня

# ISO-8601-подобное начало: 2024-05-01T12:34 (как в takeout._ISO_TS_RE)
_ISO_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")

# под какими ключами ytmusicapi встречалась метка добавления в плейлист
_LIKED_AT_KEYS = ("timestamp", "added_at", "addedAt", "publishedAt")


class PlaylistError(Exception):
    """Базовая ошибка источника-плейлиста."""


class PlaylistNotFound(PlaylistError):
    """Плейлист не найден (или недоступен анонимно — YT отдаёт 404 и на приватные)."""


class PlaylistPrivate(PlaylistError):
    """Плейлист приватный/недоступен без авторизации."""


def extract_playlist_id(value: str) -> str:
    """Чистый ID плейлиста ИЛИ ссылка music.youtube.com/...?list=ID -> ID.

    ValueError с человеческим сообщением, если разобрать не удалось.
    """
    value = (value or "").strip()
    if not value:
        raise ValueError("пустая ссылка на плейлист")
    if value.startswith(("http://", "https://")):
        qs = parse_qs(urlparse(value).query)
        if "list" in qs and qs["list"][0]:
            return qs["list"][0]
        raise ValueError(
            f"не нашёл параметр list= в ссылке: {value}. "
            "Нужна ссылка вида https://music.youtube.com/playlist?list=…"
        )
    if "/" in value or " " in value:
        raise ValueError(f"не похоже ни на ссылку, ни на ID плейлиста: {value}")
    return value


def _parse_duration(text: str | None) -> int | None:
    """'3:45' -> 225 секунд. '1:02:03' -> 3723. None если не разобрать."""
    if not text:
        return None
    try:
        nums = [int(p) for p in text.split(":")]
    except ValueError:
        return None
    seconds = 0
    for n in nums:
        seconds = seconds * 60 + n
    return seconds


def _extract_liked_at(track: dict) -> str | None:
    """ISO-метка добавления трека в плейлист из сырого ytmusicapi-трека.

    Best effort (SPEC v0.10 §A): проверяем известные ключи-кандидаты и
    валидируем как ISO-8601 (иначе легко утащить мусор вроде '3:42').
    Нет метки — None: у анонимных публичных плейлистов её обычно нет.
    """
    for key in _LIKED_AT_KEYS:
        value = track.get(key)
        if isinstance(value, str) and _ISO_TS_RE.match(value.strip()):
            return value.strip()
    return None


def _normalize(track: dict) -> dict | None:
    """Сырой трек ytmusicapi -> Track; None для недоступных/удалённых."""
    video_id = track.get("videoId")
    title = track.get("title")
    if not video_id or not title:
        return None
    artists = [a["name"] for a in (track.get("artists") or []) if a.get("name")]
    duration = track.get("duration_seconds")
    if duration is None:
        duration = _parse_duration(track.get("duration"))
    return {
        "artist": ", ".join(artists),
        "title": title,
        "videoId": video_id,
        "duration_seconds": duration,
        "liked_at": _extract_liked_at(track),
    }


def fetch_playlist(url_or_id: str, limit: int = 25) -> list[dict]:
    """Треки публичного плейлиста YT Music (анонимно), уже без >12-мин простыней.

    Кидает ValueError (кривая ссылка), PlaylistNotFound, PlaylistPrivate.
    """
    # ленивый импорт: ytmusicapi не нужен ни demo-режиму, ни большинству тестов
    from ytmusicapi import YTMusic

    playlist_id = extract_playlist_id(url_or_id)
    yt = YTMusic()  # анонимно — только публичные/unlisted плейлисты
    try:
        raw = yt.get_playlist(playlist_id, limit=limit)
    except Exception as e:  # ytmusicapi кидает разнотипные исключения — маппим
        msg = str(e).lower()
        if "404" in msg or "not found" in msg or "not exist" in msg or "invalid" in msg:
            raise PlaylistNotFound(
                f"плейлист {playlist_id} не найден — проверьте ссылку "
                "(приватные плейлисты YT Music тоже отдают 404)"
            ) from e
        if "403" in msg or "private" in msg or "unauthorized" in msg or "login" in msg:
            raise PlaylistPrivate(
                f"плейлист {playlist_id} приватный — откройте доступ "
                "(«не в списке» достаточно) и попробуйте снова"
            ) from e
        raise PlaylistError(f"не удалось прочитать плейлист {playlist_id}: {e}") from e

    tracks: list[dict] = []
    for raw_track in (raw.get("tracks") or [])[:limit]:
        t = _normalize(raw_track)
        if t is None:
            continue
        d = t["duration_seconds"]
        if d is not None and d > MAX_DURATION_S:
            log.info("фильтрую длинный трек (%s c): %s — %s", d, t["artist"], t["title"])
            continue
        tracks.append(t)
    return tracks
