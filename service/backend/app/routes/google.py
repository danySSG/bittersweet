"""«Войти через YouTube» (SPEC v0.5 §A2): /auth/google/*, /api/me,
/api/youtube/summary (SPEC v0.6 §A1).

Без GOOGLE_CLIENT_ID все /auth/google/* отвечают 503 с человеческим JSON
(паттерн Spotify-заглушки). Сессия — существующая httpOnly signed cookie
(SessionMiddleware, itsdangerous, samesite=lax): в ней только channel_id.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import settings
from app.engine import google_accounts, google_oauth, store
from app.sources import youtube_api

log = logging.getLogger(__name__)

router = APIRouter(tags=["google"])

SESSION_KEY = "google_channel_id"


def _not_configured_response() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "google_not_configured",
            "detail": (
                "GOOGLE_CLIENT_ID не задан — вход через YouTube выключен. "
                "Создайте OAuth-клиент в Google Cloud Console (см. README, "
                "«Настройка Google OAuth / YouTube») и заполните .env. "
                "Без него доступен демо-режим: GET /api/demo/portrait."
            ),
            "demo_endpoint": "/api/demo/portrait",
        },
    )


def session_channel_id(request: Request) -> str | None:
    """channel_id из подписанной session-cookie (или None)."""
    value = request.session.get(SESSION_KEY)
    return value if isinstance(value, str) and value else None


def require_live_account(request: Request) -> tuple[dict, str]:
    """(аккаунт, живой access_token) по session-cookie; иначе HTTPException.

    Семантика v0.5 (общая для /api/analyze/youtube и /api/youtube/summary):
    нет сессии / аккаунт удалён / invalid_grant -> 401 (+ снятие cookie),
    прочие сбои OAuth -> 502. Рефреш протухшего access_token — здесь же
    (быстрый POST к Google внутри ensure_access_token).
    """
    channel_id = session_channel_id(request)
    if not channel_id:
        raise HTTPException(
            status_code=401,
            detail="нет сессии YouTube — начните с /auth/google/login",
        )
    account = google_accounts.get_account(channel_id)
    if account is None:
        request.session.pop(SESSION_KEY, None)
        raise HTTPException(
            status_code=401,
            detail="аккаунт отключён — войдите заново через /auth/google/login",
        )
    try:
        access_token = google_oauth.ensure_access_token(account)
    except google_oauth.InvalidGrant:
        request.session.pop(SESSION_KEY, None)
        raise HTTPException(
            status_code=401,
            detail="доступ отозван или истёк — войдите заново через /auth/google/login",
        ) from None
    except google_oauth.GoogleOAuthError as e:
        raise HTTPException(status_code=502, detail=f"Google OAuth: {e}") from e
    return account, access_token


@router.get("/auth/google/login")
def google_login():
    """Redirect на consent-экран Google: youtube.readonly, offline, CSRF-state."""
    if not settings.google_client_id:
        return _not_configured_response()
    state = google_oauth.make_state()
    return RedirectResponse(google_oauth.build_authorize_url(state))


@router.get("/auth/google/callback")
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Проверка state -> code на токены -> identity -> аккаунт в БД -> cookie."""
    if not settings.google_client_id:
        return _not_configured_response()
    if error:
        return JSONResponse(
            status_code=400,
            content={"error": "google_denied", "detail": error},
        )
    if not code:
        return JSONResponse(status_code=400, content={"error": "missing_code"})
    if not google_oauth.verify_state(state):
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_state",
                "detail": "state не прошёл проверку — начните заново с /auth/google/login",
            },
        )

    try:
        token = google_oauth.exchange_code(code)
    except google_oauth.GoogleOAuthError as e:
        return JSONResponse(
            status_code=502,
            content={"error": "token_exchange_failed", "detail": str(e)},
        )

    try:
        info = youtube_api.fetch_channel_info(token["access_token"])
    except youtube_api.YouTubeAPIError as e:
        return JSONResponse(
            status_code=502,
            content={"error": "channel_fetch_failed", "detail": str(e)},
        )

    channel_id = info["channel_id"]
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        # prompt=consent обязан вернуть refresh_token; страховка на повторный вход
        existing = google_accounts.get_account(channel_id)
        refresh_token = (existing or {}).get("refresh_token")
    if not refresh_token:
        return JSONResponse(
            status_code=502,
            content={
                "error": "no_refresh_token",
                "detail": "Google не вернул refresh_token — попробуйте войти ещё раз",
            },
        )

    google_accounts.upsert_account(
        channel_id=channel_id,
        channel_title=info["channel_title"],
        refresh_token=refresh_token,
        access_token=token["access_token"],
        expires_in=int(token.get("expires_in", 3600)),
        likes_playlist_id=info["likes_playlist_id"],
    )
    request.session[SESSION_KEY] = channel_id
    return RedirectResponse(f"{settings.frontend_url}/portrait?source=youtube")


@router.get("/api/me")
def me(request: Request) -> dict:
    """{connected, channel_title?} по session-cookie. Работает и без GOOGLE_*."""
    channel_id = session_channel_id(request)
    account = google_accounts.get_account(channel_id) if channel_id else None
    if account is None:
        if channel_id:
            request.session.pop(SESSION_KEY, None)  # протухшая cookie без аккаунта
        return {"connected": False}
    return {"connected": True, "channel_title": account["channel_title"]}


@router.get("/api/youtube/summary")
def youtube_summary(request: Request) -> dict:
    """{channel_title, likes_total, note} для экрана «перед стартом»
    (SPEC v0.6 §A1, v0.7 §A2).

    likes_total — pageInfo.totalResults первого playlistItems.list (1 unit):
    пользователь видит объём библиотеки и выбирает глубину анализа сам.
    Предварительного скана «сколько музыкальных» здесь НЕТ — он дорогой по
    времени и делается в джобе (SPEC v0.7 §A2), поэтому note.
    401 без сессии (require_live_account), 502 при сбое YouTube API.
    """
    account, access_token = require_live_account(request)
    playlist_id = account.get("likes_playlist_id") or "LL"  # LL = алиас likes
    try:
        likes_total = youtube_api.fetch_likes_total(access_token, playlist_id)
    except youtube_api.YouTubeAPIError as e:
        raise HTTPException(
            status_code=502, detail=f"не удалось прочитать сводку лайков: {e}"
        ) from e
    return {
        "channel_title": account["channel_title"],
        "likes_total": likes_total,
        "note": "музыкальные отбираются автоматически при анализе",
    }


@router.post("/auth/google/disconnect")
def google_disconnect(request: Request):
    """Revoke + удалить аккаунт + удалить youtube-портреты + снять cookie.

    Данные чистим немедленно (правило «7 дней по запросу» перекрываем с запасом).
    Revoke — best effort: недоступность Google не мешает локальному удалению.
    """
    if not settings.google_client_id:
        return _not_configured_response()
    channel_id = session_channel_id(request)
    request.session.pop(SESSION_KEY, None)
    if not channel_id:
        return {"disconnected": False, "detail": "нет активной сессии YouTube"}

    account = google_accounts.get_account(channel_id)
    if account is not None and account.get("refresh_token"):
        google_oauth.revoke_token(account["refresh_token"])

    deleted_account = google_accounts.delete_account(channel_id)
    deleted_portraits = store.delete_portraits_by_owner(channel_id, source="youtube")
    return {
        "disconnected": deleted_account,
        "portraits_deleted": deleted_portraits,
    }
