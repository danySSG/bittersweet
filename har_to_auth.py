"""
har_to_auth.py — собрать авторизацию ytmusicapi из HAR-файла.

Ты экспортируешь HAR из DevTools (Network → правый клик → "Save all as HAR with content"
или иконка Export), а скрипт находит в нём запрос к API YouTube Music, вытаскивает
заголовки (cookie и пр.) и создаёт auth/browser.json.

Запуск:
  uv run har_to_auth.py /путь/к/файлу.har
  uv run har_to_auth.py            # сам поищет auth/*.har, затем свежий ~/Downloads/*.har

Куки в HAR лежат открытым текстом — держи файл локально (лучше в auth/, он в .gitignore).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ytmusicapi import setup

ROOT = Path(__file__).parent
AUTH_DIR = ROOT / "auth"
OUT = AUTH_DIR / "browser.json"

# заголовки, которые реально нужны ytmusicapi / полезны для запроса
KEEP = {
    "cookie", "authorization", "user-agent", "accept", "accept-language",
    "content-type", "x-goog-authuser", "x-goog-visitor-id", "x-origin",
    "origin", "referer", "x-youtube-client-name", "x-youtube-client-version",
}


def find_har() -> Path | None:
    cands = sorted(AUTH_DIR.glob("*.har"), key=lambda p: p.stat().st_mtime, reverse=True)
    if cands:
        return cands[0]
    dl = Path.home() / "Downloads"
    if dl.exists():
        cands = sorted(dl.glob("*.har"), key=lambda p: p.stat().st_mtime, reverse=True)
        if cands:
            return cands[0]
    return None


def pick_entry(entries: list[dict]) -> dict | None:
    """Ищем запрос к music.youtube.com/youtubei/... с заголовком cookie (в идеале /browse)."""
    scored = []
    for e in entries:
        req = e.get("request", {})
        url = req.get("url", "")
        if "music.youtube.com/youtubei" not in url:
            continue
        headers = {h["name"].lower(): h["value"] for h in req.get("headers", [])}
        if "cookie" not in headers or "SAPISID" not in headers["cookie"]:
            continue
        score = 2 if "/browse" in url else 1
        scored.append((score, e))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def main() -> None:
    AUTH_DIR.mkdir(exist_ok=True)

    har_path = Path(sys.argv[1]) if len(sys.argv) > 1 else find_har()
    if not har_path or not har_path.exists():
        raise SystemExit(
            "Не нашёл HAR. Укажи путь:  uv run har_to_auth.py /путь/к/файлу.har\n"
            "или положи .har в папку auth/"
        )
    print(f"Читаю HAR: {har_path}", file=sys.stderr)

    data = json.loads(har_path.read_text(encoding="utf-8", errors="ignore"))
    entries = data.get("log", {}).get("entries", [])
    entry = pick_entry(entries)
    if entry is None:
        raise SystemExit(
            f"В HAR ({len(entries)} запросов) нет запроса к music.youtube.com/youtubei c cookie.\n"
            "Убедись, что запись сделана на залогиненной вкладке и фильтр был 'browse'."
        )

    req = entry["request"]
    headers = {h["name"]: h["value"] for h in req["headers"] if not h["name"].startswith(":")}
    # соберём "сырой" блок заголовков только из нужных
    lines = [f"{k}: {v}" for k, v in headers.items() if k.lower() in KEEP]
    raw = "\n".join(lines)

    cookie_len = len(headers.get("cookie") or headers.get("Cookie") or "")
    print(f"Нашёл запрос: {req['url'].split('?')[0]}", file=sys.stderr)
    print(f"Заголовков взято: {len(lines)}, длина cookie: {cookie_len} симв.", file=sys.stderr)

    setup(filepath=str(OUT), headers_raw=raw)
    print(f"✓ Авторизация сохранена: {OUT}", file=sys.stderr)
    print("Дальше:  uv run fetch_tracks.py --liked", file=sys.stderr)


if __name__ == "__main__":
    main()
