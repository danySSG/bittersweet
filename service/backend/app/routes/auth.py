"""Spotify OAuth (PKCE): /auth/spotify/login и /auth/spotify/callback.

Если SPOTIFY_CLIENT_ID не задан — сервис живёт в demo-режиме,
login отвечает 503 с понятным JSON.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import settings
from app.engine import spotify

router = APIRouter(prefix="/auth/spotify", tags=["auth"])


def _demo_mode_response() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "spotify_not_configured",
            "detail": (
                "SPOTIFY_CLIENT_ID не задан — сервис работает в demo-режиме. "
                "Используйте GET /api/demo/portrait или зарегистрируйте "
                "Spotify-приложение (см. README) и заполните .env."
            ),
            "demo_endpoint": "/api/demo/portrait",
        },
    )


@router.get("/login")
def login(request: Request):
    if not settings.spotify_client_id:
        return _demo_mode_response()
    verifier = spotify.generate_code_verifier()
    state = secrets.token_urlsafe(16)
    request.session["pkce_verifier"] = verifier
    request.session["oauth_state"] = state
    return RedirectResponse(spotify.build_authorize_url(verifier, state))


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if not settings.spotify_client_id:
        return _demo_mode_response()
    if error:
        return JSONResponse(status_code=400, content={"error": "spotify_denied", "detail": error})
    if not code:
        return JSONResponse(status_code=400, content={"error": "missing_code"})

    verifier = request.session.pop("pkce_verifier", None)
    saved_state = request.session.pop("oauth_state", None)
    if not verifier or not saved_state or state != saved_state:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_state", "detail": "начните заново с /auth/spotify/login"},
        )

    try:
        token = await spotify.exchange_code(code, verifier)
    except spotify.SpotifyError as e:
        return JSONResponse(status_code=502, content={"error": "token_exchange_failed", "detail": str(e)})

    request.session["spotify_access_token"] = token["access_token"]
    if token.get("refresh_token"):
        request.session["spotify_refresh_token"] = token["refresh_token"]
    return RedirectResponse(f"{settings.frontend_url}/portrait")
