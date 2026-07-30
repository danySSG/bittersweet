"""GET /api/youtube/summary (SPEC v0.6 §A1) — БЕЗ сети (respx).

401 без сессии, 200 со сводкой {channel_title, likes_total} из
pageInfo.totalResults первого playlistItems.list (1 unit), 502 при сбое API.
"""

from __future__ import annotations

import pytest
import respx
from fastapi.testclient import TestClient

from app.config import settings
from app.engine import google_oauth
from app.main import app

TOKEN_URL = google_oauth.TOKEN_URL
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"

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
def _google_creds(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "test-client-secret")


@pytest.fixture()
def client():
    return TestClient(app)


def _login(client) -> None:
    with respx.mock(assert_all_called=False) as rsx:
        rsx.route(host="testserver").pass_through()
        rsx.post(TOKEN_URL).respond(json={
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "expires_in": 3600,
        })
        rsx.get(CHANNELS_URL).respond(json=CHANNEL_JSON)
        r = client.get(
            "/auth/google/callback",
            params={"code": "auth-code", "state": google_oauth.make_state()},
            follow_redirects=False,
        )
    assert r.status_code in (302, 303, 307), r.text


def test_summary_401_without_session(client):
    r = client.get("/api/youtube/summary")
    assert r.status_code == 401
    assert "auth/google/login" in r.json()["detail"]


def test_summary_ok(client, _google_creds):
    """200: channel_title из аккаунта, likes_total из pageInfo.totalResults,
    note про автоотбор музыки (SPEC v0.7 §A2 — без предварительного скана)."""
    _login(client)
    with respx.mock(assert_all_called=False) as rsx:
        rsx.route(host="testserver").pass_through()
        pl = rsx.get(PLAYLIST_ITEMS_URL).respond(json={
            "pageInfo": {"totalResults": 873, "resultsPerPage": 1},
            "items": [{"id": "x"}],
        })
        r = client.get("/api/youtube/summary")
    assert r.status_code == 200
    assert r.json() == {
        "channel_title": "Даниил",
        "likes_total": 873,
        "note": "музыкальные отбираются автоматически при анализе",
    }

    # ровно один дешёвый запрос: part=id, maxResults=1, likes-плейлист аккаунта
    assert pl.call_count == 1
    params = pl.calls[0].request.url.params
    assert params["playlistId"] == "LLowner001"
    assert params["part"] == "id" and params["maxResults"] == "1"


def test_summary_502_when_api_fails(client, _google_creds):
    _login(client)
    with respx.mock(assert_all_called=False) as rsx:
        rsx.route(host="testserver").pass_through()
        rsx.get(PLAYLIST_ITEMS_URL).respond(
            status_code=403, json={"error": {"message": "quotaExceeded"}}
        )
        r = client.get("/api/youtube/summary")
    assert r.status_code == 502
    assert "сводку лайков" in r.json()["detail"]
