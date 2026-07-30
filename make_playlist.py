"""
make_playlist.py — создать ПРИВАТНЫЙ плейлист в YouTube Music из data/discoveries.csv.

Пишет в твой аккаунт (создаёт плейлист + добавляет треки). Приватный — виден только тебе.
Запуск: uv run make_playlist.py ["Название"]
"""

import csv
import sys
from pathlib import Path

from ytmusicapi import YTMusic

DATA = Path(__file__).parent / "data"
AUTH = Path(__file__).parent / "auth" / "browser.json"


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else "Открытия · под твой вкус"
    rows = list(csv.DictReader((DATA / "discoveries.csv").open(encoding="utf-8")))
    vids = [r["videoId"] for r in rows if r.get("videoId")]
    print(f"треков к добавлению: {len(vids)}", file=sys.stderr)

    yt = YTMusic(str(AUTH))
    pid = yt.create_playlist(
        title,
        "Собрано анализатором по моим лайкам — новое в моих настроениях (биттерсвит, тёмный минор, атмосфера).",
        privacy_status="PRIVATE",
    )
    if not isinstance(pid, str):
        raise SystemExit(f"Не удалось создать плейлист: {pid}")
    res = yt.add_playlist_items(pid, vids, duplicates=False)
    print(f"✓ плейлист создан: {pid}")
    print(f"  ссылка: https://music.youtube.com/playlist?list={pid}")
    print(f"  статус добавления: {res.get('status') if isinstance(res, dict) else res}", file=sys.stderr)


if __name__ == "__main__":
    main()
