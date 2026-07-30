"""Playlist-джоба end-to-end через TestClient — БЕЗ сети (всё monkeypatch).

Замечание к спеке: с 4 треками джоба по правилам (<8 сматчилось -> error)
не может дойти до done, поэтому happy-path гоняем на 16 синтетических треках,
а мало-трековый случай проверяем отдельным тестом.
"""

from __future__ import annotations

import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.engine import features, previews
from app.main import app
from app.sources import ytmusic

client = TestClient(app)

PLAYLIST_URL = "https://music.youtube.com/playlist?list=PLtest123"


def _synthetic_tracks(n: int) -> list[dict]:
    return [
        {"artist": f"Artist {i}", "title": f"Track {i}",
         "videoId": f"vid{i:03d}", "duration_seconds": 200 + i}
        for i in range(n)
    ]


def _synthetic_features(i: int) -> dict:
    """Две выраженные группы (как в test_engine), чтобы KMeans было что кластерить."""
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
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cache_db", tmp_path / "cache.db")


@pytest.fixture()
def _fake_pipeline(monkeypatch):
    """match -> фейковый url, download -> ok, analyze -> синтетические признаки."""
    counter = {"n": 0}

    def fake_match(isrc, artist, title, client=None):
        return f"http://fake/{artist}-{title}.mp3"

    def fake_download(url, dest_path, client=None):
        return True

    def fake_analyze(path):
        i = counter["n"]
        counter["n"] += 1
        return _synthetic_features(i)

    monkeypatch.setattr(previews, "match_track_to_preview", fake_match)
    monkeypatch.setattr(previews, "download_preview", fake_download)
    monkeypatch.setattr(features, "analyze", fake_analyze)


def _poll_until_final(job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200
        state = r.json()
        if state["status"] in ("done", "error"):
            return state
        time.sleep(0.05)
    raise AssertionError(f"джоба {job_id} не завершилась за {timeout} c: {state}")


def test_playlist_job_happy_path(monkeypatch, _fake_pipeline):
    monkeypatch.setattr(ytmusic, "fetch_playlist", lambda url, limit=25: _synthetic_tracks(16))

    r = client.post("/api/analyze/playlist", json={"url": PLAYLIST_URL})
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    state = _poll_until_final(job_id)
    assert state["status"] == "done", state["error"]
    assert state["error"] is None
    assert state["progress"] == {"done": 16, "total": 16, "matched": 16}

    portrait = state["portrait"]
    assert portrait["n_tracks"] == 16
    assert len(portrait["clusters"]) >= 2
    assert sum(c["size"] for c in portrait["clusters"]) == 16
    for c in portrait["clusters"]:
        assert set(c) == {"label", "archetype", "size", "share", "medians",
                          "examples", "examples_rich", "color"}
        assert c["archetype"]["name"] and c["archetype"]["emoji"]
        assert {"tempo", "minor_share", "brightness"} <= set(c["medians"])
        # SPEC v0.6 §A2: превью сохранились в кэш при анализе -> examples_rich живые
        assert [e["label"] for e in c["examples_rich"]] == c["examples"]
        assert all(e["preview_url"].startswith("http://fake/") for e in c["examples_rich"])
    assert len(portrait["points"]) == 16
    for p in portrait["points"]:
        assert 0 <= p["x"] <= 100 and 0 <= p["y"] <= 100
        assert 0 <= p["cluster"] < len(portrait["clusters"])
        assert " — " in p["label"] and p["meta"]
    assert {"count", "share", "top"} <= set(portrait["bittersweet"])
    assert {"tempo_median", "minor_share", "brightness_mean"} <= set(portrait["fingerprint"])

    # по done портрет сохранён — постоянная ссылка живёт
    assert state["portrait_id"] and len(state["portrait_id"]) == 8
    saved = client.get(f"/api/p/{state['portrait_id']}")
    assert saved.status_code == 200
    assert saved.json()["source_label"] == "плейлист YouTube Music"


def test_playlist_job_coverage_in_done_and_payload(monkeypatch, _fake_pipeline):
    """SPEC v0.13 §B: coverage = {candidates, analyzed} в done-состоянии
    и в payload портрета; 4 трека из 16 без превью — молча не выпадают."""
    monkeypatch.setattr(ytmusic, "fetch_playlist", lambda url, limit=25: _synthetic_tracks(16))

    def match_skips_last_four(isrc, artist, title, client=None):
        if int(artist.split()[-1]) >= 12:
            return None  # у «редких» треков превью не нашлось
        return f"http://fake/{artist}-{title}.mp3"

    monkeypatch.setattr(previews, "match_track_to_preview", match_skips_last_four)

    state = _poll_until_final(
        client.post("/api/analyze/playlist", json={"url": PLAYLIST_URL}).json()["job_id"]
    )
    assert state["status"] == "done", state["error"]
    assert state["coverage"] == {"candidates": 16, "analyzed": 12}
    assert state["portrait"]["coverage"] == {"candidates": 16, "analyzed": 12}

    # покрытие переживает сохранение — приписка «12 из 16» живёт по пермалинку
    saved = client.get(f"/api/p/{state['portrait_id']}")
    assert saved.status_code == 200
    assert saved.json()["coverage"] == {"candidates": 16, "analyzed": 12}


def test_playlist_job_too_few_tracks(monkeypatch, _fake_pipeline):
    """4 трека (как PLfoNjwA6k-mY) < 8 -> честный error 'мало треков'."""
    monkeypatch.setattr(ytmusic, "fetch_playlist", lambda url, limit=25: _synthetic_tracks(4))

    r = client.post("/api/analyze/playlist", json={"url": PLAYLIST_URL})
    assert r.status_code == 202
    state = _poll_until_final(r.json()["job_id"])
    assert state["status"] == "error"
    assert "мало треков" in state["error"]
    assert state["portrait"] is None
    assert state["progress"]["total"] == 4


def test_playlist_job_nothing_matched(monkeypatch):
    monkeypatch.setattr(ytmusic, "fetch_playlist", lambda url, limit=25: _synthetic_tracks(16))
    monkeypatch.setattr(previews, "match_track_to_preview", lambda *a, **kw: None)

    r = client.post("/api/analyze/playlist", json={"url": PLAYLIST_URL})
    state = _poll_until_final(r.json()["job_id"])
    assert state["status"] == "error"
    assert "мало треков" in state["error"]
    assert state["progress"] == {"done": 16, "total": 16, "matched": 0}


def test_playlist_job_private_playlist(monkeypatch):
    def raise_private(url, limit=25):
        raise ytmusic.PlaylistPrivate("плейлист приватный — откройте доступ")

    monkeypatch.setattr(ytmusic, "fetch_playlist", raise_private)

    r = client.post("/api/analyze/playlist", json={"url": PLAYLIST_URL})
    assert r.status_code == 202
    state = _poll_until_final(r.json()["job_id"])
    assert state["status"] == "error"
    assert "приватный" in state["error"]


def test_playlist_job_not_found(monkeypatch):
    def raise_not_found(url, limit=25):
        raise ytmusic.PlaylistNotFound("плейлист не найден")

    monkeypatch.setattr(ytmusic, "fetch_playlist", raise_not_found)

    state = _poll_until_final(
        client.post("/api/analyze/playlist", json={"url": PLAYLIST_URL}).json()["job_id"]
    )
    assert state["status"] == "error"
    assert "не найден" in state["error"]


def test_invalid_url_422():
    r = client.post(
        "/api/analyze/playlist",
        json={"url": "https://music.youtube.com/watch?v=abc"},  # нет list=
    )
    assert r.status_code == 422
    assert "list=" in r.json()["detail"]


def test_limit_validation(monkeypatch, _fake_pipeline):
    """SPEC v0.6 §A1: потолок 200 — limit=200 валиден, 201 нет."""
    r = client.post("/api/analyze/playlist", json={"url": PLAYLIST_URL, "limit": 201})
    assert r.status_code == 422  # limit <= 200 (pydantic)

    monkeypatch.setattr(ytmusic, "fetch_playlist", lambda url, limit=25: _synthetic_tracks(16))
    r = client.post("/api/analyze/playlist", json={"url": PLAYLIST_URL, "limit": 200})
    assert r.status_code == 202


def test_unknown_job_404():
    assert client.get("/api/jobs/no-such-job").status_code == 404


def test_second_analysis_hits_cache(monkeypatch, _fake_pipeline):
    """Повторная джоба тех же треков не должна ходить за превью (кэш artist+title)."""
    monkeypatch.setattr(ytmusic, "fetch_playlist", lambda url, limit=25: _synthetic_tracks(16))

    state1 = _poll_until_final(
        client.post("/api/analyze/playlist", json={"url": PLAYLIST_URL}).json()["job_id"]
    )
    assert state1["status"] == "done"

    def boom(*a, **kw):
        raise AssertionError("кэш-хит ожидался — превью не должно запрашиваться")

    monkeypatch.setattr(previews, "match_track_to_preview", boom)
    state2 = _poll_until_final(
        client.post("/api/analyze/playlist", json={"url": PLAYLIST_URL}).json()["job_id"]
    )
    assert state2["status"] == "done"
    assert state2["progress"]["matched"] == 16
