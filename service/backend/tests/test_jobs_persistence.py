"""Долговечные джобы (таблица jobs в SQLite) — всё БЕЗ сети.

Контекст-инцидент: сервер умер посреди безлимитного анализа — in-memory
джоба погибла, кэш дескрипторов уцелел. Проверяем: запись/чтение джобы из
БД; переживание «рестарта» (горячий слой очищен — состояние читается из
нового подключения к БД); resume-on-startup перезапускает джобу с начала и
помечает progress.resumed; протухшие (старше 24ч) джобы — error;
single-flight 409 {detail, job_id}; троттлинг записи прогресса (не каждый
апдейт пишется на диск).
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import jobs, jobs_store
from app.config import settings
from app.main import app
from app.sources import takeout

client = TestClient(app)

URL = "https://music.youtube.com/playlist?list=PLdurable001"
OTHER_URL = "https://music.youtube.com/playlist?list=PLdurable002"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Своя SQLite на тест + пустой горячий слой (как свежий процесс)."""
    monkeypatch.setattr(settings, "cache_db", tmp_path / "cache.db")
    jobs._jobs.clear()
    jobs._last_write.clear()
    yield
    jobs._jobs.clear()
    jobs._last_write.clear()


def _restart() -> None:
    """«Рестарт» бэка: горячее in-memory состояние погибло, SQLite уцелела."""
    jobs._jobs.clear()
    jobs._last_write.clear()


# --- запись/чтение джобы из БД ---


def test_create_job_writes_row_and_reads_back():
    job_id = jobs.create_job(kind="playlist", params={"url": URL, "limit": 25})

    with sqlite3.connect(settings.cache_db) as conn:
        row = conn.execute(
            "SELECT kind, status, owner, params_json, created_at, updated_at"
            " FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    assert row is not None
    kind, status, owner, params_json, created_at, updated_at = row
    assert kind == "playlist"
    assert status == "queued"
    assert owner is None
    assert URL in params_json
    datetime.fromisoformat(created_at)  # валидный ISO
    datetime.fromisoformat(updated_at)

    # состояние собирается обратно в форму in-memory слоя
    state = jobs_store.get_state(job_id)
    assert state["status"] == "queued"
    assert state["progress"] == {"done": 0, "total": 0, "matched": 0}
    assert state["portrait"] is None
    assert state["portrait_id"] is None
    assert state["error"] is None


def test_status_transition_persisted_immediately():
    job_id = jobs.create_job(kind="playlist", params={"url": URL})
    jobs._update(job_id, status="error", error="упс")
    state = jobs_store.get_state(job_id)
    assert state["status"] == "error"
    assert state["error"] == "упс"


# --- переживание «рестарта»: новый инстанс стора читает job_id и статус ---


def test_job_survives_restart_and_get_endpoint_falls_back_to_db():
    job_id = jobs.create_job(kind="playlist", params={"url": URL})
    jobs._update(
        job_id, status="done", portrait={"n_tracks": 16}, portrait_id="abcd2345"
    )

    _restart()
    assert jobs._jobs == {}  # горячего состояния нет — только БД

    # jobs.get_job падает в SQLite
    state = jobs.get_job(job_id)
    assert state is not None
    assert state["status"] == "done"
    assert state["portrait"] == {"n_tracks": 16}
    assert state["portrait_id"] == "abcd2345"

    # и фронт продолжает поллить ТОТ ЖЕ job_id по HTTP
    r = client.get(f"/api/jobs/{job_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert r.json()["portrait_id"] == "abcd2345"


def test_unknown_job_still_404_after_db_check():
    assert client.get("/api/jobs/no-such-job").status_code == 404


# --- resume-on-startup ---


def test_resume_restarts_running_job_from_scratch_and_marks_resumed(monkeypatch):
    job_id = jobs.create_job(kind="playlist", params={"url": URL, "limit": 25})
    jobs._update(job_id, status="running")
    _restart()

    calls: list[tuple] = []

    def fake_body(jid: str, url: str, limit: int) -> None:
        calls.append((jid, url, limit))
        # resumed переживает перезапись progress телом джобы
        jobs._set_progress(jid, 3, 16, 3)
        jobs._update(jid, status="done", portrait={"n_tracks": 16})

    monkeypatch.setattr(jobs, "run_playlist_job", fake_body)
    resumed = jobs.resume_pending_jobs(runner=lambda fn: fn())  # синхронно

    assert resumed == [job_id]
    assert calls == [(job_id, URL, 25)]  # перезапуск С НАЧАЛА, с теми же params

    state = jobs.get_job(job_id)
    assert state["status"] == "done"
    assert state["progress"]["resumed"] is True
    assert state["progress"]["done"] == 3
    # resumed дожил и до БД (переживёт ещё один рестарт)
    assert jobs_store.get_state(job_id)["progress"]["resumed"] is True


def test_resume_queued_job_also_restarted(monkeypatch):
    job_id = jobs.create_job(kind="playlist", params={"url": URL, "limit": 10})
    _restart()

    monkeypatch.setattr(
        jobs, "run_playlist_job",
        lambda jid, url, limit: jobs._update(jid, status="done"),
    )
    assert jobs.resume_pending_jobs(runner=lambda fn: fn()) == [job_id]
    assert jobs.get_job(job_id)["status"] == "done"


def test_resume_stale_job_marked_error_not_restarted(monkeypatch):
    job_id = jobs.create_job(kind="playlist", params={"url": URL})
    jobs._update(job_id, status="running")
    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    with sqlite3.connect(settings.cache_db) as conn:
        conn.execute(
            "UPDATE jobs SET created_at = ? WHERE job_id = ?", (stale_ts, job_id)
        )
        conn.commit()
    _restart()

    def boom(*a, **kw):
        raise AssertionError("устаревшая джоба не должна перезапускаться")

    monkeypatch.setattr(jobs, "run_playlist_job", boom)
    assert jobs.resume_pending_jobs(runner=lambda fn: fn()) == []

    state = jobs.get_job(job_id)
    assert state["status"] == "error"
    assert "прервана" in state["error"] and "устарела" in state["error"]
    # и по HTTP фронт видит честную ошибку под тем же id
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "error"


def test_resume_youtube_without_account_is_honest_error():
    """Аккаунт удалён (disconnect) между стартом джобы и рестартом -> error."""
    job_id = jobs.create_job(
        kind="youtube",
        params={"channel_id": "UCgone", "limit": None},
        owner="UCgone",
    )
    jobs._update(job_id, status="running")
    _restart()

    assert jobs.resume_pending_jobs(runner=lambda fn: fn()) == [job_id]
    state = jobs.get_job(job_id)
    assert state["status"] == "error"
    assert "войдите заново" in state["error"]
    assert state["progress"]["resumed"] is True


def test_lifespan_triggers_resume(monkeypatch):
    """Интеграция: старт приложения (lifespan) сам перезапускает джобу."""
    job_id = jobs.create_job(kind="playlist", params={"url": URL, "limit": 25})
    jobs._update(job_id, status="running")
    _restart()

    monkeypatch.setattr(
        jobs, "run_playlist_job",
        lambda jid, url, limit: jobs._update(jid, status="done"),
    )
    with TestClient(app) as c:  # контекст-менеджер гоняет lifespan
        deadline = time.monotonic() + 5.0
        state = None
        while time.monotonic() < deadline:
            state = c.get(f"/api/jobs/{job_id}").json()
            if state["status"] in ("done", "error"):
                break
            time.sleep(0.05)
    assert state is not None
    assert state["status"] == "done", state
    assert state["progress"]["resumed"] is True


# --- single-flight: 409 {detail, job_id} на дубликат ---


def test_single_flight_same_playlist_url_409_with_job_id(monkeypatch):
    running = jobs.create_job(kind="playlist", params={"url": URL, "limit": 25})
    jobs._update(running, status="running")

    r = client.post("/api/analyze/playlist", json={"url": URL})
    assert r.status_code == 409
    body = r.json()
    assert body["job_id"] == running  # фронт просто поллит его
    assert running in body["detail"]  # и может извлечь id из detail
    assert set(body) == {"detail", "job_id"}

    # другой url — не дубликат: обычный 202 с новой джобой
    monkeypatch.setattr(
        jobs, "run_playlist_job",
        lambda jid, url, limit: jobs._update(jid, status="done"),
    )
    r2 = client.post("/api/analyze/playlist", json={"url": OTHER_URL})
    assert r2.status_code == 202
    assert r2.json()["job_id"] != running


def test_single_flight_released_after_job_finishes(monkeypatch):
    finished = jobs.create_job(kind="playlist", params={"url": URL, "limit": 25})
    jobs._update(finished, status="done")

    monkeypatch.setattr(
        jobs, "run_playlist_job",
        lambda jid, url, limit: jobs._update(jid, status="done"),
    )
    r = client.post("/api/analyze/playlist", json={"url": URL})
    assert r.status_code == 202  # завершённая джоба не блокирует новую


def test_single_flight_same_owner_same_kind():
    """youtube: дубликат ловится по owner (channel_id), не по url."""
    a = jobs.create_job(kind="youtube", params={"limit": 40}, owner="UCowner001")
    jobs._update(a, status="running")

    assert jobs.find_active_job("youtube", owner="UCowner001") == a
    assert jobs.find_active_job("youtube", owner="UCother") is None
    assert jobs.find_active_job("playlist", owner="UCowner001") is None  # другой kind

    jobs._update(a, status="done")
    assert jobs.find_active_job("youtube", owner="UCowner001") is None


# --- троттлинг записи прогресса ---


def test_progress_writes_throttled(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(jobs, "_clock", lambda: now["t"])

    job_id = jobs.create_job(kind="playlist", params={"url": URL})
    jobs._set_progress(job_id, 1, 10, 1)  # первая запись проходит
    now["t"] += 0.5
    jobs._set_progress(job_id, 2, 10, 2)  # < 2 c — только память
    now["t"] += 0.5
    jobs._set_progress(job_id, 3, 10, 3)  # всё ещё < 2 c

    assert jobs.get_job(job_id)["progress"]["done"] == 3  # память горячая
    assert jobs_store.get_state(job_id)["progress"]["done"] == 1  # диск отстаёт

    now["t"] += 2.0
    jobs._set_progress(job_id, 4, 10, 4)  # окно прошло — записалось
    assert jobs_store.get_state(job_id)["progress"]["done"] == 4

    now["t"] += 0.1
    jobs._set_progress(job_id, 5, 10, 5)  # снова только память
    assert jobs_store.get_state(job_id)["progress"]["done"] == 4

    # переход статуса — немедленно и с последним прогрессом
    jobs._update(job_id, status="done")
    final = jobs_store.get_state(job_id)
    assert final["status"] == "done"
    assert final["progress"]["done"] == 5


# --- сериализация RawEntry для params_json takeout-джоб ---


def test_takeout_entry_roundtrip():
    entry = takeout.RawEntry(
        video_id="vid00000001",
        artist="Artist",
        title="Track",
        added_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        source_file="Liked Music.csv",
    )
    assert takeout.entry_from_dict(takeout.entry_to_dict(entry)) == entry

    bare = takeout.RawEntry(video_id="vid00000002")
    assert takeout.entry_from_dict(takeout.entry_to_dict(bare)) == bare
