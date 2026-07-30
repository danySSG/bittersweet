"""Резолвер video_id -> {artist, title} через YouTube oEmbed (без ключа).

Живой тест 2026-07-10: https://www.youtube.com/oembed?url=…&format=json
отдаёт title + author_name; у авто-треков author_name = "<Artist> - Topic"
(суффикс « - Topic» отрезаем — это артист авто-канала, title — чистое название).

Отказы oEmbed: 401 (embed запрещён), 403 (эпизодически), 404 (видео удалено).
Лимиты не документированы → троттлинг <= 4 req/s (module-level lock, как в
previews.py), экспоненциальный бэкофф на 403/429, fallback noembed.com
(жив, тот же формат ответа). 404 = видео удалено — фолбэк бессмыслен, сразу None.

Кэш: таблица video_meta(video_id PK, artist, title, resolved_at) в той же
SQLite-базе cache.db (паттерн engine/store.py). Кэшируются только успехи.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

import httpx

from app.engine import cache
from app.engine.topic import TOPIC_SUFFIX, strip_topic_suffix  # noqa: F401 (реэкспорт)

log = logging.getLogger(__name__)

OEMBED_URL = "https://www.youtube.com/oembed"
NOEMBED_URL = "https://noembed.com/embed"
WATCH_URL = "https://www.youtube.com/watch?v={video_id}"

TIMEOUT = 8.0
MIN_INTERVAL = 0.25  # <= 4 req/s (лимиты oEmbed не документированы)
BACKOFF_ATTEMPTS = 3  # доп. попытки на 403/429
BACKOFF_BASE = 1.0    # сек; экспонента: 1, 2, 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS video_meta (
    video_id TEXT PRIMARY KEY,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    resolved_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_throttle_lock = threading.Lock()
_next_allowed = 0.0


def _throttle() -> None:
    """Резервирует слот вызова (<= 4 req/s); спит вне лока (как previews._throttle)."""
    global _next_allowed
    with _throttle_lock:
        now = time.monotonic()
        start = max(now, _next_allowed)
        _next_allowed = start + MIN_INTERVAL
        wait = start - now
    if wait > 0:
        time.sleep(wait)


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Та же база, что у кэша признаков (+ своя таблица video_meta)."""
    conn = cache._connect(db_path)
    conn.execute(_SCHEMA)
    return conn


def get_cached(video_id: str, db_path: Path | str | None = None) -> dict | None:
    """{artist, title} из кэша или None."""
    if not video_id:
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT artist, title FROM video_meta WHERE video_id = ?", (video_id,)
        ).fetchone()
    return {"artist": row[0], "title": row[1]} if row else None


def set_cached(video_id: str, artist: str, title: str,
               db_path: Path | str | None = None) -> None:
    """Кладёт разрешённые метаданные в кэш (перезаписывает при повторе)."""
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO video_meta (video_id, artist, title) VALUES (?, ?, ?) "
            "ON CONFLICT(video_id) DO UPDATE SET artist = excluded.artist, "
            "title = excluded.title, resolved_at = datetime('now')",
            (video_id, artist, title),
        )
        conn.commit()


def _meta_from_payload(data: dict) -> dict | None:
    """oEmbed/noembed JSON -> {artist, title}; « - Topic» отрезается."""
    if not isinstance(data, dict) or data.get("error"):
        return None  # noembed отдаёт 200 + {"error": …} на недоступные видео
    title = (data.get("title") or "").strip()
    if not title:
        return None
    artist = strip_topic_suffix(data.get("author_name"))
    return {"artist": artist, "title": title}


def _fetch(client: httpx.Client, url: str, params: dict) -> tuple[str, dict | None]:
    """GET с троттлингом и бэкофом. -> ("ok", json) | ("gone", None) | ("fail", None).

    "gone" — определённый отказ (404 удалено / 400-401 embed запрещён), ретраи
    и фолбэк бессмысленны. 403/429 — бэкофф (экспонента), затем "fail".
    """
    delay = BACKOFF_BASE
    for attempt in range(BACKOFF_ATTEMPTS + 1):
        _throttle()
        try:
            r = client.get(url, params=params)
        except httpx.HTTPError:
            return "fail", None
        if r.status_code == 200:
            try:
                return "ok", r.json()
            except ValueError:
                return "fail", None
        if r.status_code in (400, 401, 404):
            return "gone", None
        if r.status_code in (403, 429) and attempt < BACKOFF_ATTEMPTS:
            time.sleep(delay)
            delay *= 2
            continue
        return "fail", None
    return "fail", None


def resolve_video(
    video_id: str,
    client: httpx.Client | None = None,
    db_path: Path | str | None = None,
) -> dict | None:
    """video_id -> {artist, title} (кэш -> oEmbed -> noembed) или None.

    None = видео удалено/embed запрещён/сервисы недоступны — трек
    пропускается и считается в unmatched (спека).
    """
    cached = get_cached(video_id, db_path)
    if cached is not None:
        return cached

    watch_url = WATCH_URL.format(video_id=video_id)
    own_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=True)
    try:
        status, data = _fetch(client, OEMBED_URL, {"url": watch_url, "format": "json"})
        if status == "gone":
            return None  # 404/401 — удалено или embed запрещён, noembed не спасёт
        meta = _meta_from_payload(data) if status == "ok" else None
        if meta is None:
            # fallback: noembed.com (жив, тот же формат) — на «fail» oEmbed
            status, data = _fetch(client, NOEMBED_URL, {"url": watch_url})
            meta = _meta_from_payload(data) if status == "ok" else None
        if meta is not None:
            set_cached(video_id, meta["artist"], meta["title"], db_path)
        else:
            log.info("резолвер: %s не разрешился (удалён/недоступен)", video_id)
        return meta
    finally:
        if own_client:
            client.close()
