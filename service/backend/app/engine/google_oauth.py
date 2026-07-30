"""Google OAuth (Authorization Code) для scope youtube.readonly (SPEC v0.5 §A2).

- state — подписанный itsdangerous-токен (CSRF), TTL 10 минут;
- обмен code -> токены и рефреш: POST oauth2.googleapis.com/token (httpx);
- access_type=offline + prompt=consent — иначе Google не выдаёт refresh_token;
- invalid_grant при рефреше = доступ отозван/refresh истёк (в Testing-режиме
  GCP refresh живёт 7 дней) -> аккаунт удаляется, пользователь логинится заново;
- revoke — POST oauth2.googleapis.com/revoke (best effort).

Все запросы синхронные (httpx.Client): роуты работают в threadpool FastAPI,
джобы — в BackgroundTasks; в тестах мокается respx'ом.
"""

from __future__ import annotations

import logging
import secrets

import httpx
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import settings
from app.engine import google_accounts

log = logging.getLogger(__name__)

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

SCOPE = "https://www.googleapis.com/auth/youtube.readonly"

TIMEOUT = 10.0
STATE_MAX_AGE = 600  # сек; state живёт 10 минут — дольше consent-флоу не идёт
_STATE_SALT = "google-oauth-state"


class GoogleOAuthError(RuntimeError):
    """Ошибка обращения к Google OAuth (сеть/статус/формат)."""


class InvalidGrant(GoogleOAuthError):
    """refresh_token отозван или истёк — аккаунт считать отключённым."""


# ---- CSRF state -------------------------------------------------------------

def _state_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt=_STATE_SALT)


def make_state() -> str:
    """Подписанный одноразовый nonce; проверяется в callback (CSRF)."""
    return _state_serializer().dumps({"n": secrets.token_urlsafe(8)})


def verify_state(state: str | None, max_age: int = STATE_MAX_AGE) -> bool:
    if not state:
        return False
    try:
        _state_serializer().loads(state, max_age=max_age)
        return True
    except BadSignature:  # включая SignatureExpired
        return False


# ---- authorize / token -------------------------------------------------------

def build_authorize_url(state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "scope": SCOPE,
        # offline + consent: гарантированно получить refresh_token (факт из спеки)
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return str(httpx.URL(AUTHORIZE_URL, params=params))


def _post_token(data: dict) -> dict:
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            r = client.post(TOKEN_URL, data=data)
    except httpx.HTTPError as e:
        raise GoogleOAuthError(f"oauth2.googleapis.com недоступен: {e}") from e
    if r.status_code != 200:
        try:
            err = r.json().get("error", "")
        except ValueError:
            err = ""
        if err == "invalid_grant":
            raise InvalidGrant("invalid_grant: доступ отозван или refresh_token истёк")
        raise GoogleOAuthError(f"token endpoint: {r.status_code} {r.text[:200]}")
    return r.json()


def exchange_code(code: str) -> dict:
    """code -> {access_token, refresh_token?, expires_in, ...}."""
    return _post_token({
        "grant_type": "authorization_code",
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": settings.google_redirect_uri,
    })


def refresh_access_token(refresh_token: str) -> dict:
    """refresh_token -> свежий {access_token, expires_in, ...}. InvalidGrant -> отозван."""
    return _post_token({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
    })


def revoke_token(token: str) -> bool:
    """Отзыв токена (refresh отзывает весь grant). Best effort: сбой -> False."""
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            r = client.post(REVOKE_URL, data={"token": token})
        return r.status_code == 200
    except httpx.HTTPError:
        log.warning("revoke не удался (сеть) — токен удаляем локально всё равно")
        return False


def ensure_access_token(account: dict) -> str:
    """Живой access_token аккаунта; рефрешит и сохраняет при истечении.

    InvalidGrant (или нечитаемый refresh_token) -> аккаунт удаляется из БД,
    исключение InvalidGrant пробрасывается вызывающему (роут отвечает 401).
    """
    if not google_accounts.token_expired(account["token_expiry"]):
        return account["access_token"]

    channel_id = account["channel_id"]
    refresh_token = account.get("refresh_token")
    if not refresh_token:
        # не расшифровался (сменился SESSION_SECRET) — равносильно отзыву
        google_accounts.delete_account(channel_id)
        raise InvalidGrant("refresh_token нечитаем — войдите заново")
    try:
        token = refresh_access_token(refresh_token)
    except InvalidGrant:
        google_accounts.delete_account(channel_id)
        raise
    google_accounts.update_tokens(
        channel_id,
        access_token=token["access_token"],
        expires_in=int(token.get("expires_in", 3600)),
        refresh_token=token.get("refresh_token"),
    )
    return token["access_token"]
