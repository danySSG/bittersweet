"""
fetch_tracks.py — вытащить треки из YouTube Music в локальный файл data/tracks.json.

Два режима:

  1) Публичный плейлист — БЕЗ логина, самый простой старт:
       uv run fetch_tracks.py --playlist "https://music.youtube.com/playlist?list=PL..."

  2) Твои лайки / вся библиотека — нужна разовая авторизация (см. auth_setup.py):
       uv run auth_setup.py           # один раз, вставить заголовки из браузера
       uv run fetch_tracks.py --liked
       uv run fetch_tracks.py --playlist LM     # LM = "Мне понравилось" (Liked Music)

Результат: data/tracks.json — список треков вида
  {title, artists, album, videoId, duration_seconds}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ytmusicapi import YTMusic

DATA = Path(__file__).parent / "data"
AUTH_FILE = Path(__file__).parent / "auth" / "browser.json"


def extract_playlist_id(value: str) -> str:
    """Принимает либо чистый ID плейлиста, либо ссылку music.youtube.com/...list=ID."""
    if value.startswith("http"):
        qs = parse_qs(urlparse(value).query)
        if "list" in qs:
            return qs["list"][0]
        raise SystemExit(f"Не нашёл параметр list= в ссылке: {value}")
    return value


def parse_duration(text: str | None) -> int | None:
    """'3:45' -> 225 секунд. '1:02:03' -> 3723. None если не разобрать."""
    if not text:
        return None
    parts = text.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    seconds = 0
    for n in nums:
        seconds = seconds * 60 + n
    return seconds


def normalize(track: dict) -> dict | None:
    """Приводим сырой трек ytmusicapi к нашему компактному виду."""
    video_id = track.get("videoId")
    if not video_id:
        return None  # недоступный/удалённый трек
    artists = [a["name"] for a in (track.get("artists") or []) if a.get("name")]
    album = track.get("album")
    album_name = album["name"] if isinstance(album, dict) else album
    return {
        "title": track.get("title"),
        "artists": artists,
        "album": album_name,
        "videoId": video_id,
        "duration_seconds": parse_duration(track.get("duration")),
    }


def get_client(need_auth: bool) -> YTMusic:
    if need_auth:
        if not AUTH_FILE.exists():
            raise SystemExit(
                f"Нет файла авторизации {AUTH_FILE}.\n"
                "Сначала запусти:  uv run auth_setup.py"
            )
        return YTMusic(str(AUTH_FILE))
    return YTMusic()  # анонимно — только публичные плейлисты


def main() -> None:
    ap = argparse.ArgumentParser(description="Забор треков из YouTube Music")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--playlist", help="URL или ID плейлиста (LM = твои лайки, нужен --auth)")
    group.add_argument("--liked", action="store_true", help="Твои лайкнутые треки (нужна авторизация)")
    ap.add_argument("--limit", type=int, default=None, help="Ограничить число треков (для пробы)")
    ap.add_argument("--auth", action="store_true", help="Использовать авторизацию (для приватных плейлистов/библиотеки)")
    args = ap.parse_args()

    DATA.mkdir(exist_ok=True)

    if args.liked:
        yt = get_client(need_auth=True)
        print("Тяну лайкнутые треки…", file=sys.stderr)
        raw = yt.get_liked_songs(limit=args.limit or 5000)
        raw_tracks = raw["tracks"]
        source = "liked"
    else:
        pid = extract_playlist_id(args.playlist)
        need_auth = args.auth or pid == "LM"
        yt = get_client(need_auth=need_auth)
        print(f"Тяну плейлист {pid}…", file=sys.stderr)
        raw = yt.get_playlist(pid, limit=args.limit)
        raw_tracks = raw["tracks"]
        source = f"playlist:{pid}"

    tracks = [t for t in (normalize(r) for r in raw_tracks) if t]

    out = DATA / "tracks.json"
    out.write_text(
        json.dumps({"source": source, "count": len(tracks), "tracks": tracks},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✓ Сохранил {len(tracks)} треков в {out}", file=sys.stderr)

    # маленький previm, чтобы сразу увидеть, что получилось
    for t in tracks[:8]:
        print(f"   {', '.join(t['artists'])} — {t['title']}", file=sys.stderr)
    if len(tracks) > 8:
        print(f"   … и ещё {len(tracks) - 8}", file=sys.stderr)


if __name__ == "__main__":
    main()
