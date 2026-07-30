"""Google OAuth (SPEC v0.5 §A2/§A4) — БЕЗ сети: Google замокан respx'ом.

login-URL, state (CSRF), happy-path callback (cookie + аккаунт + шифрование
refresh_token), /api/me, refresh/invalid_grant, disconnect, purge (30 дней),
503-ветки без GOOGLE_CLIENT_ID.
"""

from __future__ import annotations

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response, URL

from app.config import settings
from app.engine import google_accounts, google_oauth, store
from app.main import app

TOKEN_URL = google_oauth.TOKEN_URL
REVOKE_URL = google_oauth.REVOKE_URL
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

REFRESH_TOKEN = "super-secret-refresh-token-value"

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


@pytest.fixture()
def client():
    # свой клиент на тест: cookie не утекают между тестами
    return TestClient(app)


@pytest.fixture()
def _google_creds(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "test-client-secret")


def _mock_google(rsx, refresh_token: str | None = REFRESH_TOKEN):
    """Стандартные моки token+channels; testserver (TestClient) — pass through."""
    rsx.route(host="testserver").pass_through()
    token_json = {"access_token": "access-1", "expires_in": 3600, "token_type": "Bearer"}
    if refresh_token:
        token_json["refresh_token"] = refresh_token
    rsx.post(TOKEN_URL).respond(json=token_json)
    rsx.get(CHANNELS_URL).respond(json=CHANNEL_JSON)


def _login(client) -> None:
    """Happy-path callback -> session-cookie в client (Google замокан)."""
    with respx.mock(assert_all_called=False) as rsx:
        _mock_google(rsx)
        r = client.get(
            "/auth/google/callback",
            params={"code": "auth-code", "state": google_oauth.make_state()},
            follow_redirects=False,
        )
    assert r.status_code in (302, 303, 307), r.text


# ---- 503-ветки без GOOGLE_CLIENT_ID -----------------------------------------

def test_all_google_auth_routes_503_without_client_id(client, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "")
    for method, path in [
        ("GET", "/auth/google/login"),
        ("GET", "/auth/google/callback"),
        ("POST", "/auth/google/disconnect"),
    ]:
        r = client.request(method, path)
        assert r.status_code == 503, path
        body = r.json()
        assert body["error"] == "google_not_configured"
        assert "GOOGLE_CLIENT_ID" in body["detail"]
        assert body["demo_endpoint"] == "/api/demo/portrait"


def test_me_works_without_credentials(client, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "")
    r = client.get("/api/me")
    assert r.status_code == 200
    assert r.json() == {"connected": False}


# ---- login: URL авторизации --------------------------------------------------

def test_login_redirects_to_google_with_offline_consent_state(client, _google_creds):
    r = client.get("/auth/google/login", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    url = URL(r.headers["location"])
    assert url.host == "accounts.google.com"
    assert url.path == "/o/oauth2/v2/auth"
    params = url.params
    assert params["client_id"] == "test-client-id"
    assert params["redirect_uri"] == settings.google_redirect_uri
    assert params["scope"] == "https://www.googleapis.com/auth/youtube.readonly"
    assert params["response_type"] == "code"
    assert params["access_type"] == "offline"
    assert params["prompt"] == "consent"
    assert google_oauth.verify_state(params["state"])


# ---- callback ----------------------------------------------------------------

def test_callback_bad_state_400(client, _google_creds):
    r = client.get(
        "/auth/google/callback", params={"code": "x", "state": "forged-state"}
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_state"


def test_callback_missing_code_400(client, _google_creds):
    r = client.get("/auth/google/callback")
    assert r.status_code == 400
    assert r.json()["error"] == "missing_code"


def test_callback_denied_400(client, _google_creds):
    r = client.get("/auth/google/callback", params={"error": "access_denied"})
    assert r.status_code == 400
    assert r.json()["error"] == "google_denied"


def test_callback_happy_path_sets_cookie_and_stores_account(client, _google_creds):
    with respx.mock(assert_all_called=False) as rsx:
        _mock_google(rsx)
        r = client.get(
            "/auth/google/callback",
            params={"code": "auth-code", "state": google_oauth.make_state()},
            follow_redirects=False,
        )
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == f"{settings.frontend_url}/portrait?source=youtube"
    assert "session" in r.cookies  # httpOnly signed session-cookie

    # аккаунт в БД, refresh_token расшифровывается обратно
    account = google_accounts.get_account("UCowner001")
    assert account is not None
    assert account["channel_title"] == "Даниил"
    assert account["likes_playlist_id"] == "LLowner001"
    assert account["refresh_token"] == REFRESH_TOKEN
    assert not google_accounts.token_expired(account["token_expiry"])

    # /api/me по cookie
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json() == {"connected": True, "channel_title": "Даниил"}


def test_refresh_token_not_stored_plaintext(client, _google_creds):
    _login(client)
    raw = settings.cache_db.read_bytes()
    assert REFRESH_TOKEN.encode() not in raw  # в базе только шифртекст
    account = google_accounts.get_account("UCowner001")
    assert account["refresh_token"] == REFRESH_TOKEN  # но расшифровывается


def test_me_false_without_session(client):
    assert client.get("/api/me").json() == {"connected": False}


# ---- шифрование --------------------------------------------------------------

def test_encrypt_decrypt_roundtrip():
    enc = google_accounts.encrypt_token("тайный токен")
    assert enc != "тайный токен"
    assert google_accounts.decrypt_token(enc) == "тайный токен"


def test_decrypt_garbage_returns_none():
    assert google_accounts.decrypt_token("not-a-fernet-token") is None


def test_key_depends_on_session_secret(monkeypatch):
    enc = google_accounts.encrypt_token("token")
    monkeypatch.setattr(settings, "session_secret", "совсем-другой-секрет")
    assert google_accounts.decrypt_token(enc) is None  # ключ сменился


# ---- refresh access_token ------------------------------------------------------

def _make_expired_account():
    google_accounts.upsert_account(
        channel_id="UCowner001", channel_title="Даниил",
        refresh_token=REFRESH_TOKEN, access_token="stale-access",
        expires_in=-100, likes_playlist_id="LLowner001",
    )
    return google_accounts.get_account("UCowner001")


def test_ensure_access_token_refreshes_expired(_google_creds):
    account = _make_expired_account()
    with respx.mock() as rsx:
        route = rsx.post(TOKEN_URL).respond(
            json={"access_token": "fresh-access", "expires_in": 3600}
        )
        token = google_oauth.ensure_access_token(account)
    assert token == "fresh-access"
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "grant_type=refresh_token" in body
    assert REFRESH_TOKEN in body
    updated = google_accounts.get_account("UCowner001")
    assert updated["access_token"] == "fresh-access"
    assert not google_accounts.token_expired(updated["token_expiry"])


def test_ensure_access_token_valid_token_no_network(_google_creds):
    google_accounts.upsert_account(
        channel_id="UCowner001", channel_title="Даниил",
        refresh_token=REFRESH_TOKEN, access_token="live-access",
        expires_in=3600, likes_playlist_id="LLowner001",
    )
    account = google_accounts.get_account("UCowner001")
    with respx.mock():  # ни одного разрешённого маршрута: любой запрос = ошибка
        assert google_oauth.ensure_access_token(account) == "live-access"


def test_ensure_access_token_invalid_grant_deletes_account(_google_creds):
    account = _make_expired_account()
    with respx.mock() as rsx:
        rsx.post(TOKEN_URL).respond(status_code=400, json={"error": "invalid_grant"})
        with pytest.raises(google_oauth.InvalidGrant):
            google_oauth.ensure_access_token(account)
    assert google_accounts.get_account("UCowner001") is None


def test_analyze_youtube_invalid_grant_401_and_account_deleted(client, _google_creds):
    _login(client)
    # состариваем access_token, refresh ответит invalid_grant
    google_accounts.update_tokens("UCowner001", access_token="stale", expires_in=-100)
    with respx.mock(assert_all_called=False) as rsx:
        rsx.route(host="testserver").pass_through()
        rsx.post(TOKEN_URL).respond(status_code=400, json={"error": "invalid_grant"})
        r = client.post("/api/analyze/youtube", json={"limit": 12})
    assert r.status_code == 401
    assert google_accounts.get_account("UCowner001") is None
    assert client.get("/api/me").json() == {"connected": False}  # cookie снята


def test_analyze_youtube_401_without_session(client, _google_creds):
    r = client.post("/api/analyze/youtube")
    assert r.status_code == 401
    assert "/auth/google/login" in r.json()["detail"]


# ---- disconnect ----------------------------------------------------------------

def test_disconnect_revokes_deletes_and_clears_cookie(client, _google_creds):
    _login(client)
    # два портрета владельца (youtube) + чужой анонимный
    mine1 = store.save_portrait(
        {"n_tracks": 10}, "лайки YouTube",
        owner="UCowner001", source="youtube", api_fetched_at="2026-07-10T00:00:00+00:00",
    )
    mine2 = store.save_portrait(
        {"n_tracks": 12}, "лайки YouTube",
        owner="UCowner001", source="youtube", api_fetched_at="2026-07-10T00:00:00+00:00",
    )
    other = store.save_portrait({"n_tracks": 5}, "демо")

    with respx.mock(assert_all_called=False) as rsx:
        rsx.route(host="testserver").pass_through()
        revoke = rsx.post(REVOKE_URL).respond(200)
        r = client.post("/auth/google/disconnect")

    assert r.status_code == 200
    assert r.json()["disconnected"] is True
    assert r.json()["portraits_deleted"] == 2
    assert revoke.called
    assert REFRESH_TOKEN in revoke.calls.last.request.content.decode()

    assert google_accounts.get_account("UCowner001") is None
    assert store.get_portrait(mine1) is None
    assert store.get_portrait(mine2) is None
    assert store.get_portrait(other) is not None  # чужие не трогаем
    assert client.get("/api/me").json() == {"connected": False}


def test_disconnect_without_session(client, _google_creds):
    r = client.post("/auth/google/disconnect")
    assert r.status_code == 200
    assert r.json()["disconnected"] is False


def test_disconnect_survives_revoke_failure(client, _google_creds):
    """Google лежит — локальные данные всё равно удаляются немедленно."""
    _login(client)
    with respx.mock(assert_all_called=False) as rsx:
        rsx.route(host="testserver").pass_through()
        rsx.post(REVOKE_URL).respond(500)
        r = client.post("/auth/google/disconnect")
    assert r.status_code == 200
    assert r.json()["disconnected"] is True
    assert google_accounts.get_account("UCowner001") is None


# ---- purge: правило 30 дней ------------------------------------------------------

def _backdate_portrait(pid: str, days: int) -> None:
    with store._connect() as conn:
        conn.execute(
            "UPDATE portraits SET created_at = datetime('now', ?),"
            " api_fetched_at = datetime('now', ?) WHERE id = ?",
            (f"-{days} days", f"-{days} days", pid),
        )
        conn.commit()


def _backdate_account(channel_id: str, days: int) -> None:
    with google_accounts._connect() as conn:
        conn.execute(
            "UPDATE google_accounts SET created_at = datetime('now', ?)"
            " WHERE channel_id = ?",
            (f"-{days} days", channel_id),
        )
        conn.commit()


def test_purge_deletes_stale_youtube_keeps_fresh_and_foreign():
    stale = store.save_portrait({"n": 1}, "лайки YouTube", owner="UCa", source="youtube")
    _backdate_portrait(stale, 31)
    fresh = store.save_portrait({"n": 2}, "лайки YouTube", owner="UCa", source="youtube")
    _backdate_portrait(fresh, 29)
    foreign_old = store.save_portrait({"n": 3}, "выгрузка Google Takeout")
    _backdate_portrait(foreign_old, 400)

    result = google_accounts.purge_stale_youtube_data()

    assert result["portraits"] == 1
    assert store.get_portrait(stale) is None          # youtube старше 30 дней
    assert store.get_portrait(fresh) is not None      # свежий youtube остаётся
    assert store.get_portrait(foreign_old) is not None  # не-youtube не трогаем


def test_purge_deletes_orphaned_accounts_keeps_live_ones():
    for cid in ("UCorphan", "UCwithportrait", "UCfresh"):
        google_accounts.upsert_account(
            channel_id=cid, channel_title=cid, refresh_token="rt",
            access_token="at", expires_in=3600, likes_playlist_id="LL",
        )
    _backdate_account("UCorphan", 31)
    _backdate_account("UCwithportrait", 31)
    pid = store.save_portrait(
        {"n": 1}, "лайки YouTube", owner="UCwithportrait", source="youtube"
    )
    _backdate_portrait(pid, 5)  # свежий портрет держит аккаунт живым

    result = google_accounts.purge_stale_youtube_data()

    assert result["accounts"] == 1
    assert google_accounts.get_account("UCorphan") is None
    assert google_accounts.get_account("UCwithportrait") is not None
    assert google_accounts.get_account("UCfresh") is not None


def test_purge_stale_portrait_frees_stale_account_next_run():
    """Протухший портрет удаляется -> аккаунт осиротел -> удаляется тем же вызовом."""
    google_accounts.upsert_account(
        channel_id="UCa", channel_title="a", refresh_token="rt",
        access_token="at", expires_in=3600, likes_playlist_id="LL",
    )
    _backdate_account("UCa", 40)
    pid = store.save_portrait({"n": 1}, "лайки YouTube", owner="UCa", source="youtube")
    _backdate_portrait(pid, 40)

    result = google_accounts.purge_stale_youtube_data()
    assert result == {"portraits": 1, "accounts": 1}


# ---- миграция схемы portraits ----------------------------------------------------

def test_old_portraits_schema_recreated():
    """v0.3-таблица без owner/source/api_fetched_at пересоздаётся (как в cache.py)."""
    import sqlite3

    settings.cache_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.cache_db)
    conn.execute(
        "CREATE TABLE portraits (id TEXT PRIMARY KEY,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        " source_label TEXT NOT NULL, payload TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO portraits (id, source_label, payload) VALUES ('old12345', 'демо', '{}')"
    )
    conn.commit()
    conn.close()

    assert store.get_portrait("old12345") is None  # таблица пересоздана
    pid = store.save_portrait({"n": 1}, "демо")     # новая схема работает
    assert store.get_portrait(pid) is not None
