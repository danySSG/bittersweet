"""Общий помощник: артист авто-каналов YouTube.

У авто-треков (official audio, лайки из YT Music) канал называется
"<Artist> - Topic" — суффикс « - Topic» отрезаем, остаётся чистый артист.
Используется резолвером oEmbed (engine/resolver.py) и источником
«лайки YouTube» (sources/youtube_api.py) — одна функция, не копии.
"""

from __future__ import annotations

TOPIC_SUFFIX = " - Topic"


def strip_topic_suffix(name: str | None) -> str:
    """"Artist - Topic" -> "Artist"; обычные имена возвращаются как есть (strip)."""
    name = (name or "").strip()
    if name.endswith(TOPIC_SUFFIX):
        name = name[: -len(TOPIC_SUFFIX)].strip()
    return name
