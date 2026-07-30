"""Spotify OAuth (Authorization Code + PKCE) и выгрузка библиотеки.

PKCE: client_secret не нужен — code_verifier генерируется на бэке,
challenge = base64url(sha256(verifier)). Всё за конфигом (app.config.settings).

fetch_user_tracks(token) -> [{isrc, artist, title}] из топ-50 (medium_term)
и первых 50 сохранённых треков.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import urlencode

import httpx

from app.config import settings

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"

TIMEOUT = 10.0


class SpotifyError(RuntimeError):
    """Ошибка обращения к Spotify (токен/сеть/квоты)."""


# ---- PKCE ----------------------------------------------------------------

def generate_code_verifier() -> str:
    """43-128 символов [A-Za-z0-9-._~] по RFC 7636."""
    return secrets.token_urlsafe(64)[:64]


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_authorize_url(verifier: str, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": settings.spotify_client_id,
        "redirect_uri": settings.spotify_redirect_uri,
        "scope": settings.spotify_scope,
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge(verifier),
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(code: str, verifier: str) -> dict:
    """Обмен authorization code на токен. Возвращает JSON токена."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.spotify_redirect_uri,
        "client_id": settings.spotify_client_id,
        "code_verifier": verifier,
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(TOKEN_URL, data=data)
    if r.status_code != 200:
        raise SpotifyError(f"token exchange failed: {r.status_code} {r.text[:200]}")
    return r.json()


# ---- библиотека пользователя ----------------------------------------------

def _track_entry(track: dict | None) -> dict | None:
    if not track:
        return None
    isrc = (track.get("external_ids") or {}).get("isrc")
    artists = ", ".join(a["name"] for a in track.get("artists", []) if a.get("name"))
    title = track.get("name", "")
    if not title or not artists:
        return None
    return {"isrc": isrc, "artist": artists, "title": title}


async def fetch_user_tracks(access_token: str) -> list[dict]:
    """Топ-50 (medium_term) + 50 сохранённых -> [{isrc, artist, title}], дедуп."""
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=headers) as client:
        top = await client.get(
            f"{API_BASE}/me/top/tracks", params={"limit": 50, "time_range": "medium_term"}
        )
        saved = await client.get(f"{API_BASE}/me/tracks", params={"limit": 50})

    if top.status_code == 401 or saved.status_code == 401:
        raise SpotifyError("token expired or invalid")

    tracks: list[dict] = []
    if top.status_code == 200:
        tracks += [t for t in (_track_entry(item) for item in top.json().get("items", [])) if t]
    if saved.status_code == 200:
        tracks += [
            t for t in (_track_entry(item.get("track")) for item in saved.json().get("items", [])) if t
        ]
    if not tracks:
        raise SpotifyError(f"no tracks fetched: top={top.status_code}, saved={saved.status_code}")

    seen: set[str] = set()
    unique: list[dict] = []
    for t in tracks:
        dedup_key = t["isrc"] or f"{t['artist']}|{t['title']}".lower()
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        unique.append(t)
    return unique
