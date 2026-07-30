"""Джоба «лайки YouTube» end-to-end (SPEC v0.5 §A3/§C2, v0.7 §A) — БЕЗ сети.

Google OAuth и YouTube Data API замоканы respx'ом (2 страницы playlistItems,
videos.list с snippet+contentDetails), превью/анализ — monkeypatch (как в
test_takeout_job). Проверяем: 202 -> done, портрет с source_label «лайки
YouTube», api_fetched_at и source в payload, срез « - Topic», музыкальный
фильтр (категория 10 / « - Topic», не-музыка категории 22 — мимо), фильтры
длинных/удалённых/приватных, limit ПОСЛЕ фильтра, limit=0/null = все,
music_found/started_at/rate_per_min в состоянии джобы, ветка квоты, рефреш
протухшего access_token, disconnect удаляет портрет.
"""

from __future__ import annotations

import time
from datetime import datetime

import numpy as np
import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from app.config import settings
from app.engine import features, google_accounts, google_oauth, previews
from app.main import app

TOKEN_URL = google_oauth.TOKEN_URL
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

CHANNEL_JSON = {
    "items": [{
        "id": "UCowner001",
        "snippet": {"title": "Даниил"},
        "contentDetails": {"relatedPlaylists": {"likes": "LLowner001"}},
    }]
}

N_TRACKS = 16
LONG_ID = "longvideo01"      # музыка (категория 10), но 1ч5м — мимо по длительности
DELETED_ID = "deleted00001"  # нет в ответе videos.list — фильтруется
VLOG_ID = "vlogvideo001"     # категория 22, обычный канал — мимо по музыкальности


def _vid(i: int) -> str:
    return f"vid{i:08d}"


def _item(
    video_id: str, title: str, owner: str, published_at: str | None = None
) -> dict:
    sn = {
        "title": title,
        "videoOwnerChannelTitle": owner,
        "resourceId": {"videoId": video_id},
    }
    if published_at:
        # дата добавления в likes-плейлист = дата лайка (SPEC v0.10 §A)
        sn["publishedAt"] = published_at
    return {"snippet": sn}


def _published_at(i: int) -> str:
    """Дата лайка i-го трека: свежие первыми, по 4 трека на месяц (4 месяца).

    Месяцы 2024-04 (i=0..3) ... 2024-01 (i=12..15): покрытие >= 3 месяцев,
    бакеты по 4 — timeline обязан слить их до >= 8 (SPEC v0.10 §A).
    """
    return f"2024-{4 - i // 4:02d}-{28 - i % 4:02d}T12:00:00Z"


def _liked_items() -> list[dict]:
    """16 музыкальных треков + мусор (длинное, удалённое, приватное, влог).

    Чётные — авто-каналы « - Topic» (категория в моке 22 — музыкальны
    ТОЛЬКО по суффиксу), нечётные — обычные каналы с категорией 10
    (музыкальны ТОЛЬКО по категории): проверяем оба плеча ИЛИ.
    """
    items = []
    for i in range(N_TRACKS):
        owner = f"Artist {i} - Topic" if i % 2 == 0 else f"Artist {i}"
        items.append(_item(_vid(i), f"Song {i}", owner, _published_at(i)))
    items.append(_item(LONG_ID, "Full Album Mix", "Some Artist - Topic"))
    items.append(_item(DELETED_ID, "Song deleted", "Artist X - Topic"))
    items.append(_item("private0001", "Private video", ""))  # заглушка YouTube
    items.append(_item(VLOG_ID, "Мой влог", "Some Vlogger"))
    return items


def _video_details() -> dict[str, dict]:
    """videos.list: categoryId + duration на каждый доступный ролик."""
    det = {}
    for i in range(N_TRACKS):
        cat = "22" if i % 2 == 0 else "10"  # чётные музыкальны по « - Topic»
        det[_vid(i)] = {"categoryId": cat, "duration": "PT3M20S"}
    det[LONG_ID] = {"categoryId": "10", "duration": "PT1H5M0S"}  # > 12 минут
    det[VLOG_ID] = {"categoryId": "22", "duration": "PT4M0S"}    # не музыка
    # DELETED_ID отсутствует — videos.list не возвращает удалённые
    return det


def _mock_youtube_api(rsx) -> dict:
    """playlistItems (2 страницы) + videos.list; возвращает route'ы для asserts."""
    items = _liked_items()
    page1, page2 = items[:10], items[10:]
    page_info = {"totalResults": len(items), "resultsPerPage": 10}

    def playlist_cb(request):
        if request.url.params.get("pageToken") == "page2":
            return Response(200, json={"items": page2, "pageInfo": page_info})
        return Response(200, json={
            "items": page1, "nextPageToken": "page2", "pageInfo": page_info,
        })

    details = _video_details()

    def videos_cb(request):
        ids = (request.url.params.get("id") or "").split(",")
        return Response(200, json={
            "items": [
                {
                    "id": v,
                    "snippet": {"categoryId": details[v]["categoryId"]},
                    "contentDetails": {"duration": details[v]["duration"]},
                }
                for v in ids if v in details
            ]
        })

    return {
        "playlist": rsx.get(PLAYLIST_ITEMS_URL).mock(side_effect=playlist_cb),
        "videos": rsx.get(VIDEOS_URL).mock(side_effect=videos_cb),
    }


def _synthetic_features(i: int) -> dict:
    """Две выраженные группы (как в test_takeout_job) — KMeans есть что кластерить."""
    rng = np.random.default_rng(100 + i)
    calm = i % 2 == 0
    minor_score = float(rng.uniform(0.68, 0.85) if calm else rng.uniform(0.15, 0.32))
    return {
        "tempo": float(rng.uniform(65, 85) if calm else rng.uniform(150, 175)),
        "key": "A",
        "mode": "minor" if calm else "major",
        "minor_score": round(minor_score, 3),
        "bittersweet": round(1 - 2 * abs(minor_score - 0.5), 3),
        "percussive": float(rng.uniform(0.05, 0.15) if calm else rng.uniform(0.45, 0.6)),
        "energy_rms": float(rng.uniform(0.05, 0.1) if calm else rng.uniform(0.25, 0.35)),
        "brightness": float(rng.uniform(900, 1300) if calm else rng.uniform(2600, 3400)),
        "onset_rate": float(rng.uniform(0.8, 1.6) if calm else rng.uniform(4.0, 6.0)),
    }


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cache_db", tmp_path / "cache.db")


@pytest.fixture()
def _google_creds(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "test-client-secret")


@pytest.fixture()
def _fake_pipeline(monkeypatch):
    counter = {"n": 0}

    def fake_analyze(path):
        i = counter["n"]
        counter["n"] += 1
        return _synthetic_features(i)

    monkeypatch.setattr(previews, "match_track_to_preview",
                        lambda isrc, artist, title, client=None: "http://fake/p.mp3")
    monkeypatch.setattr(previews, "download_preview", lambda url, dest, client=None: True)
    monkeypatch.setattr(features, "analyze", fake_analyze)


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


def _poll_until_final(client, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200
        state = r.json()
        if state["status"] in ("done", "error"):
            return state
        time.sleep(0.05)
    raise AssertionError(f"джоба {job_id} не завершилась за {timeout} c: {state}")


def _run_youtube_job(client, json_body: dict | None = None) -> dict:
    with respx.mock(assert_all_called=False) as rsx:
        rsx.route(host="testserver").pass_through()
        routes = _mock_youtube_api(rsx)
        r = client.post("/api/analyze/youtube", json=json_body)
        assert r.status_code == 202, r.text
        state = _poll_until_final(client, r.json()["job_id"])
    state["_routes"] = routes
    return state


def test_youtube_job_e2e_portrait_saved_and_deleted_on_disconnect(
    client, _google_creds, _fake_pipeline
):
    """DoD §C2: login -> analyze -> done -> /api/p/{id} -> disconnect -> 404."""
    _login(client)
    state = _run_youtube_job(client)
    assert state["status"] == "done", state["error"]

    # длинное/удалённое/приватное/не-музыка отфильтрованы: ровно 16 треков
    assert state["progress"]["stage"] == "analyzing"
    assert state["progress"]["done"] == N_TRACKS
    assert state["progress"]["matched"] == N_TRACKS
    assert state["progress"]["music_found"] == N_TRACKS  # SPEC v0.7 §A1
    assert state["_routes"]["playlist"].call_count == 2  # паджинация: 2 страницы

    # SPEC v0.7 §A2: поля для ETA на фронте
    datetime.fromisoformat(state["started_at"])  # валидный ISO
    assert "rate_per_min" in state
    assert state["rate_per_min"] is None or state["rate_per_min"] > 0

    # SPEC v0.13 §B: честное покрытие — в done-состоянии и в payload
    assert state["coverage"] == {"candidates": N_TRACKS, "analyzed": N_TRACKS}
    assert state["portrait"]["coverage"] == {
        "candidates": N_TRACKS, "analyzed": N_TRACKS,
    }

    portrait = state["portrait"]
    assert portrait["n_tracks"] == N_TRACKS
    assert portrait["source"] == "youtube"
    datetime.fromisoformat(portrait["api_fetched_at"])  # валидный ISO

    # « - Topic» срезан: артисты чистые
    labels = [p["label"] for p in portrait["points"]]
    assert any("Artist 0" in lbl for lbl in labels)
    assert all(" - Topic" not in lbl for lbl in labels)

    # SPEC v0.10 §A: liked_at (= snippet.publishedAt) сквозь конвейер до точек
    liked = {p["label"]: p["liked_at"] for p in portrait["points"]}
    assert liked["Artist 0 — Song 0"] == _published_at(0)
    assert all(v for v in liked.values())

    # SPEC v0.10 §A: timeline построен — 4 месяца по 4 трека слиты в 2 бакета по 8
    timeline = portrait["timeline"]
    assert [b["n_tracks"] for b in timeline] == [8, 8]
    assert timeline[0]["period_start"] == "2024-01-01"
    assert timeline[0]["period_end"] == "2024-02-29"
    assert timeline[1]["period_start"] == "2024-03-01"
    assert timeline[1]["period_end"] == "2024-04-30"
    for b in timeline:
        assert b["top_archetype"]["name"] and b["top_archetype"]["emoji"]

    # постоянная ссылка с source_label «лайки YouTube»
    pid = state["portrait_id"]
    assert pid
    saved = client.get(f"/api/p/{pid}")
    assert saved.status_code == 200
    assert saved.json()["source_label"] == "лайки YouTube"
    assert saved.json()["api_fetched_at"] == portrait["api_fetched_at"]

    # disconnect: аккаунт и youtube-портрет удаляются, пермалинк умирает
    with respx.mock(assert_all_called=False) as rsx:
        rsx.route(host="testserver").pass_through()
        revoke = rsx.post(google_oauth.REVOKE_URL).respond(200)
        r = client.post("/auth/google/disconnect")
    assert r.status_code == 200 and r.json()["disconnected"] is True
    assert revoke.called
    assert client.get(f"/api/p/{pid}").status_code == 404
    assert google_accounts.get_account("UCowner001") is None


def test_youtube_job_limit_takes_freshest(client, _google_creds, _fake_pipeline):
    """limit=12 -> первые (самые свежие) 12 МУЗЫКАЛЬНЫХ лайков (после фильтра)."""
    _login(client)
    state = _run_youtube_job(client, {"limit": 12})
    assert state["status"] == "done", state["error"]
    assert state["progress"]["total"] == 12
    labels = " | ".join(p["label"] for p in state["portrait"]["points"])
    assert "Song 0" in labels        # свежие — в начале списка лайков
    assert "Song 15" not in labels   # хвост не взяли


def test_youtube_job_negative_limit_422(client, _google_creds):
    """SPEC v0.7 §A2: отрицательный limit -> 422."""
    _login(client)
    r = client.post("/api/analyze/youtube", json={"limit": -1})
    assert r.status_code == 422


def test_youtube_job_ceiling_removed_limit_above_200_valid(
    client, _google_creds, _fake_pipeline
):
    """SPEC v0.7 §A2: потолок 200 снят — limit=500 принимается (202 -> done)."""
    _login(client)
    state = _run_youtube_job(client, {"limit": 500})  # 202 внутри хелпера
    assert state["status"] == "done", state["error"]
    # лайков в моке всего 20 (16 музыкальных + мусор) — забраны все страницы
    assert state["_routes"]["playlist"].call_count == 2
    assert state["progress"]["matched"] == N_TRACKS


@pytest.mark.parametrize("body", [{"limit": 0}, {"limit": None}])
def test_youtube_job_limit_zero_or_null_means_all(
    client, _google_creds, _fake_pipeline, body
):
    """SPEC v0.7 §A2: limit=0 или null -> все музыкальные лайки (16 из мока)."""
    _login(client)
    state = _run_youtube_job(client, body)
    assert state["status"] == "done", state["error"]
    assert state["progress"]["total"] == N_TRACKS
    assert state["progress"]["music_found"] == N_TRACKS
    assert state["portrait"]["n_tracks"] == N_TRACKS


def test_pipeline_stores_preview_url_in_cache(client, _google_creds, _fake_pipeline):
    """SPEC v0.6 §A2: успешный матч кладёт preview_url в кэш вместе с признаками."""
    from app.engine import cache

    _login(client)
    state = _run_youtube_job(client, {"limit": 12})
    assert state["status"] == "done", state["error"]

    key = cache.key_for(None, "Artist 0", "Song 0")
    assert cache.get_features(key) is not None
    assert cache.get_preview_url(key) == "http://fake/p.mp3"

    # и портрет отдаёт его в examples_rich (SPEC v0.6 §A2)
    portrait = state["portrait"]
    rich = [e for c in portrait["clusters"] for e in c["examples_rich"]]
    assert rich and all(e["preview_url"] == "http://fake/p.mp3" for e in rich)
    assert all(h["preview_url"] == "http://fake/p.mp3" for h in portrait["highlights"])


def test_expired_access_token_refreshed_before_job(client, _google_creds, _fake_pipeline):
    """Истёкший access_token -> рефреш по refresh_token -> джоба идёт как обычно."""
    _login(client)
    google_accounts.update_tokens("UCowner001", access_token="stale", expires_in=-100)

    with respx.mock(assert_all_called=False) as rsx:
        rsx.route(host="testserver").pass_through()
        refresh = rsx.post(TOKEN_URL).respond(
            json={"access_token": "fresh-access", "expires_in": 3600}
        )
        _mock_youtube_api(rsx)
        r = client.post("/api/analyze/youtube", json={"limit": 12})
        assert r.status_code == 202, r.text
        state = _poll_until_final(client, r.json()["job_id"])

    assert refresh.called
    assert state["status"] == "done", state["error"]
    account = google_accounts.get_account("UCowner001")
    assert account["access_token"] == "fresh-access"


def test_youtube_job_error_when_no_suitable_tracks(client, _google_creds):
    """Только длинная музыка -> честная ошибка «нет музыкальных треков»."""
    _login(client)
    with respx.mock(assert_all_called=False) as rsx:
        rsx.route(host="testserver").pass_through()
        rsx.get(PLAYLIST_ITEMS_URL).respond(json={
            "items": [_item(LONG_ID, "Full Album Mix", "Some Artist - Topic")]
        })
        rsx.get(VIDEOS_URL).respond(json={
            "items": [{
                "id": LONG_ID,
                "snippet": {"categoryId": "10"},
                "contentDetails": {"duration": "PT1H5M0S"},
            }]
        })
        r = client.post("/api/analyze/youtube")
        assert r.status_code == 202
        state = _poll_until_final(client, r.json()["job_id"])
    assert state["status"] == "error"
    assert "не нашлось музыкальных треков" in state["error"]


def test_youtube_job_error_when_api_fails(client, _google_creds):
    """YouTube API отвечает 500 -> error-джоба с человеческим текстом."""
    _login(client)
    with respx.mock(assert_all_called=False) as rsx:
        rsx.route(host="testserver").pass_through()
        rsx.get(PLAYLIST_ITEMS_URL).respond(
            status_code=500, json={"error": {"message": "backendError"}}
        )
        r = client.post("/api/analyze/youtube")
        assert r.status_code == 202
        state = _poll_until_final(client, r.json()["job_id"])
    assert state["status"] == "error"
    assert "лайки YouTube" in state["error"]


def test_youtube_job_quota_exceeded_honest_error(client, _google_creds):
    """SPEC v0.7 §A1: 403 quotaExceeded -> «квота YouTube исчерпана, завтра»."""
    _login(client)
    with respx.mock(assert_all_called=False) as rsx:
        rsx.route(host="testserver").pass_through()
        rsx.get(PLAYLIST_ITEMS_URL).respond(
            status_code=403,
            json={"error": {
                "code": 403,
                "message": "The request cannot be completed because you have "
                           "exceeded your quota.",
                "errors": [{"reason": "quotaExceeded",
                            "domain": "youtube.quota"}],
            }},
        )
        r = client.post("/api/analyze/youtube")
        assert r.status_code == 202
        state = _poll_until_final(client, r.json()["job_id"])
    assert state["status"] == "error"
    assert "квота YouTube" in state["error"]
    assert "завтра" in state["error"]


def test_youtube_job_purges_stale_portraits_first(client, _google_creds, _fake_pipeline):
    """Правило 30 дней: старый youtube-портрет исчезает при запуске новой джобы."""
    from app.engine import store

    _login(client)
    stale = store.save_portrait({"n": 1}, "лайки YouTube", owner="UCx", source="youtube")
    with store._connect() as conn:
        conn.execute(
            "UPDATE portraits SET created_at = datetime('now', '-40 days'),"
            " api_fetched_at = datetime('now', '-40 days') WHERE id = ?",
            (stale,),
        )
        conn.commit()

    state = _run_youtube_job(client, {"limit": 12})
    assert state["status"] == "done", state["error"]
    assert store.get_portrait(stale) is None
