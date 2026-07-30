"""Secure-флаг session-cookie (SPEC v0.14 §B) — БЕЗ сети, Google замокан respx'ом.

Дефолт cookie_secure=False (локалка, http://localhost): Secure в Set-Cookie НЕТ.
COOKIE_SECURE=1 (прод за HTTPS-прокси): session-cookie ставится с Secure,
samesite остаётся lax. Middleware конфигурируется при импорте app.main, поэтому
для проверки флага модуль перезагружается с пропатченными settings.
"""

from __future__ import annotations

import importlib

import pytest
import respx
from fastapi.testclient import TestClient

from app import main as main_module
from app.config import Settings, settings
from app.engine import google_oauth

CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

CHANNEL_JSON = {
    "items": [{
        "id": "UCowner001",
        "snippet": {"title": "Даниил"},
        "contentDetails": {"relatedPlaylists": {"likes": "LLowner001"}},
    }]
}


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cache_db", tmp_path / "cache.db")


@pytest.fixture(autouse=True)
def _google_creds(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "test-client-secret")


@pytest.fixture()
def secure_app(monkeypatch):
    """app.main, пересобранный с cookie_secure=True; после теста — откат."""
    monkeypatch.setattr(settings, "cookie_secure", True)
    module = importlib.reload(main_module)
    yield module.app
    monkeypatch.setattr(settings, "cookie_secure", False)
    importlib.reload(main_module)


def _login_set_cookie_header(app) -> str:
    """Happy-path /auth/google/callback -> сырой заголовок Set-Cookie."""
    client = TestClient(app)
    with respx.mock(assert_all_called=False) as rsx:
        rsx.route(host="testserver").pass_through()
        rsx.post(google_oauth.TOKEN_URL).respond(json={
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "expires_in": 3600,
            "token_type": "Bearer",
        })
        rsx.get(CHANNELS_URL).respond(json=CHANNEL_JSON)
        r = client.get(
            "/auth/google/callback",
            params={"code": "auth-code", "state": google_oauth.make_state()},
            follow_redirects=False,
        )
    assert r.status_code in (302, 303, 307), r.text
    header = r.headers.get("set-cookie", "")
    assert "session=" in header, header
    return header


def test_default_cookie_without_secure():
    """cookie_secure=False (дефолт, локалка): Secure не выставляется, samesite=lax."""
    header = _login_set_cookie_header(main_module.app)
    assert "secure" not in header.lower()
    assert "samesite=lax" in header.lower()
    assert "httponly" in header.lower()


def test_cookie_secure_flag_sets_secure(secure_app):
    """COOKIE_SECURE=1: session-cookie содержит Secure; samesite остаётся lax."""
    header = _login_set_cookie_header(secure_app)
    assert "secure" in header.lower()
    assert "samesite=lax" in header.lower()
    assert "httponly" in header.lower()


def test_settings_default_is_insecure_for_localhost():
    """Дефолт настроек — False: локалка по http работает без COOKIE_SECURE."""
    assert Settings.model_fields["cookie_secure"].default is False
