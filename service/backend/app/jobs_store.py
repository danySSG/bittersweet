"""Персистентность джоб — таблица jobs в той же SQLite (cache.db).

Слой ПОД app.jobs: in-memory dict остаётся кэшем горячего состояния (его
читает GET /api/jobs), а сюда состояние пишется сквозь него, чтобы джоба
переживала рестарт бэка (инцидент: сервер умер посреди безлимитного анализа
437 треков — in-memory джоба погибла, кэш дескрипторов уцелел).

jobs(job_id PK, kind, params_json, status, progress_json, result_json,
     error, owner, created_at, updated_at)

kind: playlist | takeout | youtube | discovery. params_json — параметры
запуска (по ним resume-on-startup перезапускает джобу с начала).
owner — channel_id (youtube) или portrait_id (discovery); по нему и по
params.url (playlist) работает single-flight (409 на дубликат).
status/progress_json/error — колонки-зеркала одноимённых полей состояния;
всё остальное (portrait, portrait_id, result, started_at, rate_per_min)
уезжает целиком в result_json — get_state собирает состояние обратно
в форму in-memory слоя.

Паттерн подключения — как в engine/store.py: та же база через
cache._connect, несовпадение схемы -> пересоздание таблицы (MVP, миграций нет).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.engine import cache

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    progress_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    owner TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_EXPECTED_COLUMNS = {
    "job_id", "kind", "params_json", "status", "progress_json",
    "result_json", "error", "owner", "created_at", "updated_at",
}

# статусы, при которых джоба считается живой (single-flight + resume)
ACTIVE_STATUSES = ("queued", "running")

# поля состояния, живущие в отдельных колонках; остальное — в result_json
_COLUMN_FIELDS = ("status", "progress", "error")


def _schema_matches(conn: sqlite3.Connection) -> bool:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    return not cols or _EXPECTED_COLUMNS <= cols


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Та же база, что у кэша признаков и портретов (паттерн engine/store.py)."""
    conn = cache._connect(db_path)
    if not _schema_matches(conn):
        # старая схема без нужных колонок — пересоздаём таблицу (MVP, без миграций)
        conn.execute("DROP TABLE jobs")
        conn.commit()
    conn.execute(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _split_state(state: dict) -> tuple[str, str, str | None, str]:
    """Состояние in-memory слоя -> (status, progress_json, error, result_json)."""
    extras = {k: v for k, v in state.items() if k not in _COLUMN_FIELDS}
    return (
        state["status"],
        json.dumps(state.get("progress") or {}, ensure_ascii=False),
        state.get("error"),
        json.dumps(extras, ensure_ascii=False),
    )


def _state_from_row(
    status: str, progress_json: str, result_json: str, error: str | None
) -> dict:
    """Обратная сборка состояния в форму in-memory слоя (для GET /api/jobs)."""
    state = json.loads(result_json or "{}")
    state["status"] = status
    state["progress"] = json.loads(progress_json or "{}")
    state["error"] = error
    return state


def insert_job(
    job_id: str,
    kind: str,
    state: dict,
    params: dict | None = None,
    owner: str | None = None,
    db_path: Path | str | None = None,
) -> None:
    """Новая строка джобы (status обычно queued)."""
    status, progress_json, error, result_json = _split_state(state)
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO jobs (job_id, kind, params_json, status, progress_json,"
            " result_json, error, owner, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id, kind, json.dumps(params or {}, ensure_ascii=False),
                status, progress_json, result_json, error, owner, now, now,
            ),
        )
        conn.commit()


def save_state(job_id: str, state: dict, db_path: Path | str | None = None) -> None:
    """UPDATE полного состояния джобы (+ updated_at). Троттлинг — в app.jobs."""
    status, progress_json, error, result_json = _split_state(state)
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET status = ?, progress_json = ?, result_json = ?,"
            " error = ?, updated_at = ? WHERE job_id = ?",
            (status, progress_json, result_json, error, _now(), job_id),
        )
        conn.commit()


def get_state(job_id: str, db_path: Path | str | None = None) -> dict | None:
    """Состояние джобы из БД в форме in-memory слоя, или None.

    Читается при промахе горячего кэша (после рестарта фронт продолжает
    поллить тот же job_id — GET /api/jobs/{id} обязан его найти).
    """
    if not job_id:
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT status, progress_json, result_json, error FROM jobs"
            " WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    if row is None:
        return None
    return _state_from_row(*row)


def find_active(
    kind: str,
    owner: str | None = None,
    url: str | None = None,
    db_path: Path | str | None = None,
) -> str | None:
    """job_id живой (queued/running) джобы того же kind — single-flight.

    Совпадение: тот же owner (youtube: channel_id, discovery: portrait_id)
    ИЛИ тот же params.url (playlist). Без owner и url возвращает None —
    анонимные kinds без ключа (takeout) дубликаты не ловят.
    """
    if owner is None and url is None:
        return None
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT job_id, params_json, owner FROM jobs"
            " WHERE kind = ? AND status IN (?, ?) ORDER BY created_at",
            (kind, *ACTIVE_STATUSES),
        ).fetchall()
    for job_id, params_json, row_owner in rows:
        if owner is not None and row_owner == owner:
            return job_id
        if url is not None:
            try:
                if json.loads(params_json or "{}").get("url") == url:
                    return job_id
            except ValueError:
                continue
    return None


def pending_jobs(db_path: Path | str | None = None) -> list[dict]:
    """Все queued/running джобы — кандидаты resume-on-startup."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT job_id, kind, params_json, owner, created_at,"
            " status, progress_json, result_json, error"
            " FROM jobs WHERE status IN (?, ?) ORDER BY created_at",
            ACTIVE_STATUSES,
        ).fetchall()
    return [
        {
            "job_id": r[0],
            "kind": r[1],
            "params": json.loads(r[2] or "{}"),
            "owner": r[3],
            "created_at": r[4],
            "state": _state_from_row(r[5], r[6], r[7], r[8]),
        }
        for r in rows
    ]
