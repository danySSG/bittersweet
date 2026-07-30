"""Поиск 30-сек превью трека по ISRC (или artist+title).

Каскад (порядок из исследования 2026-07-10, см. docs/ARCHITECTURE.md + SPEC-v02):
  1. Deezer — /track/isrc:{isrc} (недокументированный, но рабочий) — если есть ISRC;
  2. Deezer search — artist:"…" track:"…" с фолбэком на простой q (лимит 50 req/5s);
  3. iTunes Search API — поиск по "artist title" (~20 req/min).

Важно: iTunes lookup по ISRC НЕ работает (живые тесты 2026-07-10 возвращают
resultCount:0 — параметра isrc в lookup API нет), поэтому его в каскаде нет.

Троттлинг (module-level, time.monotonic + threading.Lock):
  iTunes  — >= ITUNES_MIN_INTERVAL (3.1 c) между вызовами (~19 req/min < 20);
  Deezer  — >= DEEZER_MIN_INTERVAL (0.125 c) между вызовами (<= 8 req/s < 50/5s).

httpx, таймауты, простые ретраи. Ключей/авторизации не требуется.
"""

from __future__ import annotations

import threading
import time

import httpx

ITUNES_SEARCH = "https://itunes.apple.com/search"
DEEZER_TRACK_BY_ISRC = "https://api.deezer.com/track/isrc:{isrc}"
DEEZER_SEARCH = "https://api.deezer.com/search"

TIMEOUT = 8.0
RETRIES = 2
RETRY_DELAY = 0.5

# --- троттлинг: не долбить внешние API (см. докстринг модуля) ---
ITUNES_MIN_INTERVAL = 3.1
DEEZER_MIN_INTERVAL = 0.125

_throttle_lock = threading.Lock()
_next_allowed: dict[str, float] = {"itunes": 0.0, "deezer": 0.0}


def _throttle(service: str) -> None:
    """Резервирует слот вызова сервиса; при необходимости спит вне лока."""
    interval = ITUNES_MIN_INTERVAL if service == "itunes" else DEEZER_MIN_INTERVAL
    with _throttle_lock:
        now = time.monotonic()
        start = max(now, _next_allowed.get(service, 0.0))
        _next_allowed[service] = start + interval
        wait = start - now
    if wait > 0:
        time.sleep(wait)


def _get_json(
    client: httpx.Client,
    url: str,
    params: dict | None = None,
    service: str | None = None,
) -> dict | None:
    """GET с ретраями; None при любой ошибке (превью — best effort).

    service — имя сервиса для троттлинга; применяется перед КАЖДОЙ попыткой,
    чтобы ретраи тоже не нарушали лимит.
    """
    for attempt in range(RETRIES + 1):
        if service:
            _throttle(service)
        try:
            r = client.get(url, params=params)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
        except (httpx.HTTPError, ValueError):
            pass
        if attempt < RETRIES:
            time.sleep(RETRY_DELAY * (attempt + 1))
    return None


def _itunes_preview(data: dict | None) -> str | None:
    for item in (data or {}).get("results", []):
        url = item.get("previewUrl")
        if url:
            return url
    return None


def _deezer_first_preview(data: dict | None) -> str | None:
    for item in (data or {}).get("data", []):
        url = item.get("preview")
        if url:
            return url
    return None


def _deezer_search_preview(client: httpx.Client, artist: str, title: str) -> str | None:
    """Deezer /search: сперва точный artist:"…" track:"…", потом простой q."""
    artist, title = artist.strip(), title.strip()
    if not (artist or title):
        return None
    exact = f'artist:"{artist}" track:"{title}"'
    url = _deezer_first_preview(
        _get_json(client, DEEZER_SEARCH, {"q": exact, "limit": 5}, service="deezer")
    )
    if url:
        return url
    return _deezer_first_preview(
        _get_json(
            client, DEEZER_SEARCH, {"q": f"{artist} {title}".strip(), "limit": 5},
            service="deezer",
        )
    )


def match_track_to_preview(
    isrc: str | None,
    artist: str,
    title: str,
    client: httpx.Client | None = None,
) -> str | None:
    """URL 30-сек превью или None. Каскад: Deezer-ISRC -> Deezer-search -> iTunes."""
    own_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=True)
    try:
        # 1. Deezer по ISRC (iTunes ISRC-lookup не существует — см. докстринг)
        if isrc:
            data = _get_json(
                client, DEEZER_TRACK_BY_ISRC.format(isrc=isrc), service="deezer"
            )
            if data and not data.get("error") and data.get("preview"):
                return data["preview"]

        # 2. Deezer search по artist+title (у YT Music-треков нет ISRC)
        url = _deezer_search_preview(client, artist, title)
        if url:
            return url

        # 3. iTunes search по artist+title
        term = f"{artist} {title}".strip()
        if term:
            url = _itunes_preview(
                _get_json(
                    client, ITUNES_SEARCH,
                    {"term": term, "entity": "song", "limit": 5},
                    service="itunes",
                )
            )
            if url:
                return url
        return None
    finally:
        if own_client:
            client.close()


# Старое имя — алиас для обратной совместимости (v0.1).
match_isrc_to_preview = match_track_to_preview


def download_preview(url: str, dest_path: str, client: httpx.Client | None = None) -> bool:
    """Скачивает превью в dest_path. True при успехе."""
    own_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=True)
    try:
        for attempt in range(RETRIES + 1):
            try:
                r = client.get(url)
                if r.status_code == 200 and r.content:
                    with open(dest_path, "wb") as f:
                        f.write(r.content)
                    return True
            except httpx.HTTPError:
                pass
            if attempt < RETRIES:
                time.sleep(RETRY_DELAY * (attempt + 1))
        return False
    finally:
        if own_client:
            client.close()
