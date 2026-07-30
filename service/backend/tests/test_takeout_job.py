"""Takeout-джоба end-to-end через TestClient — БЕЗ сети (всё monkeypatch).

Синтетический zip в структуре реального Takeout; oEmbed-резолвер и
превью/анализ замоканы. Проверяем 202 -> done, этапы stage, портрет,
источник «выгрузка Google Takeout», и человеческие 4xx на кривые файлы.
"""

from __future__ import annotations

import io
import time
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.engine import features, previews, resolver
from app.main import app
from app.sources import takeout

client = TestClient(app)


def _vid(i: int) -> str:
    return f"vid{i:08d}"  # ровно 11 символов — валидный YouTube ID


def _playlist_csv(n: int, start: int = 0) -> bytes:
    """2-колоночный пер-плейлистный CSV: свежесть растёт с индексом."""
    rows = "".join(
        f"{_vid(i)},2024-01-{(i % 27) + 1:02d}T10:{i % 60:02d}:00+00:00\n"
        for i in range(start, start + n)
    )
    return ("Video ID,Playlist Video Creation Timestamp\n" + rows).encode()


def _takeout_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _library_csv(n: int) -> bytes:
    rows = "".join(
        f"https://music.youtube.com/watch?v={_vid(i)},Track {i},Album {i},Artist {i}\n"
        for i in range(n)
    )
    return ("Song URL,Song Title,Album Title,Artist Names\n" + rows).encode()


def _synthetic_features(i: int) -> dict:
    """Две выраженные группы (как в test_playlist_job) — KMeans есть что кластерить."""
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
def _fake_resolver(monkeypatch):
    """oEmbed без сети: любой vidNNNNNNNN -> Artist/Track; счётчик вызовов."""
    calls = {"ids": []}

    def fake_resolve(video_id, client=None, db_path=None):
        calls["ids"].append(video_id)
        return {"artist": f"Artist {video_id}", "title": f"Track {video_id}"}

    monkeypatch.setattr(resolver, "resolve_video", fake_resolve)
    return calls


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


def _upload(zip_bytes: bytes, filename: str = "takeout.zip", query: str = ""):
    return client.post(
        f"/api/analyze/takeout{query}",
        files={"file": (filename, zip_bytes, "application/zip")},
    )


def test_takeout_job_happy_path_playlist_zip(_fake_pipeline, _fake_resolver):
    """zip с плейлистным CSV (только ID) -> резолвинг -> анализ -> done."""
    zip_bytes = _takeout_zip({
        "Takeout/YouTube и YouTube Music/плейлисты/Мне понравилось.csv": _playlist_csv(16),
        "Takeout/YouTube и YouTube Music/история/watch-history.json": b"[]",
    })
    r = _upload(zip_bytes)
    assert r.status_code == 202
    state = _poll_until_final(r.json()["job_id"])
    assert state["status"] == "done", state["error"]

    assert state["progress"]["stage"] == "analyzing"  # финальный этап
    assert state["progress"]["done"] == 16
    assert state["progress"]["matched"] == 16
    assert sorted(_fake_resolver["ids"]) == sorted(_vid(i) for i in range(16))

    portrait = state["portrait"]
    assert portrait["n_tracks"] == 16
    assert len(portrait["clusters"]) >= 2
    assert len(portrait["points"]) == 16

    # SPEC v0.10 §A: liked_at из timestamp-колонки CSV — сквозь конвейер до точек
    assert all(p["liked_at"] and p["liked_at"].startswith("2024-01-")
               for p in portrait["points"])
    # все лайки в одном месяце — покрытие < 3 месяцев, timeline честно null
    assert portrait["timeline"] is None

    # источник в постоянной ссылке — «выгрузка Google Takeout»
    assert state["portrait_id"]
    saved = client.get(f"/api/p/{state['portrait_id']}")
    assert saved.status_code == 200
    assert saved.json()["source_label"] == "выгрузка Google Takeout"


def test_takeout_job_library_csv_no_resolver(monkeypatch, _fake_pipeline):
    """library-songs CSV несёт artist/title — oEmbed НЕ дёргается вовсе."""
    def boom(*a, **kw):
        raise AssertionError("резолвер не должен вызываться: метаданные в CSV")

    monkeypatch.setattr(resolver, "resolve_video", boom)
    r = client.post(
        "/api/analyze/takeout",
        files={"file": ("music-library-songs.csv", _library_csv(16), "text/csv")},
    )
    assert r.status_code == 202
    state = _poll_until_final(r.json()["job_id"])
    assert state["status"] == "done", state["error"]
    assert state["portrait"]["n_tracks"] == 16


def test_takeout_job_coverage_in_done_and_payload(monkeypatch, _fake_pipeline, _fake_resolver):
    """SPEC v0.13 §B: candidates = распознанные строки файла (16), analyzed —
    сколько дошло до признаков (12: у 4 «редких» не нашлось превью)."""
    def match_skips_some(isrc, artist, title, client=None):
        # artist = "Artist vidNNNNNNNN" (из фейк-резолвера)
        return None if int(artist[-2:]) >= 12 else "http://fake/p.mp3"

    monkeypatch.setattr(previews, "match_track_to_preview", match_skips_some)
    zip_bytes = _takeout_zip({
        "Takeout/YouTube and YouTube Music/playlists/Liked Music.csv": _playlist_csv(16),
    })
    state = _poll_until_final(_upload(zip_bytes).json()["job_id"])
    assert state["status"] == "done", state["error"]
    assert state["coverage"] == {"candidates": 16, "analyzed": 12}
    assert state["portrait"]["coverage"] == {"candidates": 16, "analyzed": 12}

    saved = client.get(f"/api/p/{state['portrait_id']}")
    assert saved.json()["coverage"] == {"candidates": 16, "analyzed": 12}


def test_takeout_job_limit_takes_freshest(_fake_pipeline, _fake_resolver):
    """limit=12 из 16 -> берутся 12 ПОСЛЕДНИХ по времени добавления."""
    zip_bytes = _takeout_zip({
        "Takeout/YouTube and YouTube Music/playlists/Liked Music.csv": _playlist_csv(16),
    })
    r = _upload(zip_bytes, query="?limit=12")
    assert r.status_code == 202
    state = _poll_until_final(r.json()["job_id"])
    assert state["status"] == "done", state["error"]
    assert state["progress"]["total"] == 12
    # свежие = старшие индексы (timestamp растёт с i): vid4..vid15
    assert sorted(_fake_resolver["ids"]) == sorted(_vid(i) for i in range(4, 16))


def test_takeout_job_unresolvable_videos_error(monkeypatch, _fake_pipeline):
    """Все видео удалены (резолвер -> None) -> честная ошибка, не крэш."""
    monkeypatch.setattr(resolver, "resolve_video", lambda *a, **kw: None)
    zip_bytes = _takeout_zip({
        "Takeout/YouTube and YouTube Music/playlists/x.csv": _playlist_csv(12),
    })
    state = _poll_until_final(_upload(zip_bytes).json()["job_id"])
    assert state["status"] == "error"
    assert "не удалось определить" in state["error"]
    assert state["progress"]["matched"] == 0
    assert state["progress"]["stage"] == "resolving"


def test_takeout_job_partial_resolve_below_minimum(monkeypatch, _fake_pipeline):
    """Сматчилось меньше MIN_MATCHED -> error 'мало треков'."""
    def flaky_resolve(video_id, client=None, db_path=None):
        i = int(video_id[3:])
        if i % 4 != 0:
            return None  # 12 из 16 удалены
        return {"artist": f"A{i}", "title": f"T{i}"}

    monkeypatch.setattr(resolver, "resolve_video", flaky_resolve)
    zip_bytes = _takeout_zip({
        "Takeout/YouTube and YouTube Music/playlists/x.csv": _playlist_csv(16),
    })
    state = _poll_until_final(_upload(zip_bytes).json()["job_id"])
    assert state["status"] == "error"
    assert "мало треков" in state["error"]


def test_empty_foreign_zip_422():
    zip_bytes = _takeout_zip({"Takeout/Chrome/bookmarks.html": b"<html/>"})
    r = _upload(zip_bytes)
    assert r.status_code == 422
    assert "Takeout" in r.json()["detail"]  # человеческое сообщение с подсказкой


def test_corrupted_zip_422():
    r = _upload(b"PK\x03\x04not-really-a-zip")
    assert r.status_code == 422
    assert "повреждён" in r.json()["detail"]


def test_oversized_upload_413(monkeypatch):
    monkeypatch.setattr(takeout, "MAX_UPLOAD_BYTES", 1024)
    r = _upload(b"x" * 2048, filename="takeout.zip")
    assert r.status_code == 413
    assert "МБ" in r.json()["detail"]


def test_zip_bomb_413(monkeypatch):
    monkeypatch.setattr(takeout, "MAX_TOTAL_UNCOMPRESSED", 512)
    zip_bytes = _takeout_zip({
        "Takeout/YT/playlists/big.csv": _playlist_csv(100),  # > 512 байт распаковки
    })
    r = _upload(zip_bytes)
    assert r.status_code == 413


def test_limit_validation_422(_fake_pipeline, _fake_resolver):
    """SPEC v0.6 §A1: потолок 200 — limit=200 валиден, 201 нет."""
    zip_bytes = _takeout_zip({
        "Takeout/YT/playlists/x.csv": _playlist_csv(10),
    })
    assert _upload(zip_bytes, query="?limit=201").status_code == 422  # le=200
    assert _upload(zip_bytes, query="?limit=200").status_code == 202


def test_playlist_job_progress_has_no_stage(monkeypatch, _fake_pipeline):
    """Playlist-джоба v0.2 не трогается: её progress остаётся трёхпольным."""
    from app.sources import ytmusic

    tracks = [
        {"artist": f"Artist {i}", "title": f"Track {i}",
         "videoId": f"vid{i:03d}", "duration_seconds": 200}
        for i in range(16)
    ]
    monkeypatch.setattr(ytmusic, "fetch_playlist", lambda url, limit=25: tracks)
    r = client.post(
        "/api/analyze/playlist",
        json={"url": "https://music.youtube.com/playlist?list=PLtest123"},
    )
    state = _poll_until_final(r.json()["job_id"])
    assert state["status"] == "done"
    assert "stage" not in state["progress"]
