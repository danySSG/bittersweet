"""key_for и обобщённый кэш (isrc-приоритет, нормализация, пересоздание схемы,
идемпотентный ALTER preview_url — SPEC v0.6 §A2)."""

from __future__ import annotations

import sqlite3

from app.engine.cache import (
    _ensure_preview_column,
    get_features,
    get_preview_url,
    key_for,
    set_features,
    set_preview_url,
)


def test_isrc_priority():
    assert key_for("USUM71703861", "Artist", "Song") == "isrc:USUM71703861"


def test_isrc_normalized_upper_strip():
    assert key_for(" usum71703861 ", "a", "b") == "isrc:USUM71703861"


def test_no_isrc_uses_artist_title():
    assert key_for(None, "Artist", "Song") == "at:artist|song"
    assert key_for("", "Artist", "Song") == "at:artist|song"
    assert key_for("   ", "Artist", "Song") == "at:artist|song"


def test_norm_lower_and_collapse_spaces():
    assert key_for(None, "  My   Artist ", " The  Song  ") == "at:my artist|the song"


def test_norm_strips_bracket_tails():
    assert key_for(None, "Artist", "Song (slowed + reverb)") == "at:artist|song"
    assert key_for(None, "Artist", "Song (feat. Кто-то)") == "at:artist|song"
    assert key_for(None, "Artist", "Song [Remix] (Live)") == "at:artist|song"


def test_same_track_different_tails_share_key():
    assert key_for(None, "Artist", "Song") == key_for(None, "ARTIST", "Song (Slowed)")


def test_roundtrip(tmp_path):
    db = tmp_path / "cache.db"
    set_features("at:a|b", {"tempo": 120.0}, db_path=db)
    assert get_features("at:a|b", db_path=db) == {"tempo": 120.0}
    assert get_features("at:missing|x", db_path=db) is None


def test_old_schema_recreated(tmp_path):
    """v0.1-схема (isrc PK) не мигрируется — файл пересоздаётся."""
    db = tmp_path / "cache.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE features (isrc TEXT PRIMARY KEY, json TEXT NOT NULL, "
                     "created_at TEXT NOT NULL DEFAULT (datetime('now')))")
        conn.execute("INSERT INTO features (isrc, json) VALUES ('X', '{}')")
        conn.commit()
    set_features("isrc:NEW", {"tempo": 100.0}, db_path=db)
    assert get_features("isrc:NEW", db_path=db) == {"tempo": 100.0}
    with sqlite3.connect(db) as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(features)")]
    assert "key" in cols and "isrc" not in cols


# ---------- preview_url: идемпотентный ALTER, дозапись, COALESCE (SPEC v0.6 §A2) ----------

def _pre_v06_db(tmp_path) -> str:
    """Кэш, созданный ДО v0.6: key-схема без preview_url + ценный дескриптор."""
    db = tmp_path / "cache.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE features (key TEXT PRIMARY KEY, json TEXT NOT NULL, "
                     "created_at TEXT NOT NULL DEFAULT (datetime('now')))")
        conn.execute("INSERT INTO features (key, json) VALUES ('at:a|b', '{\"tempo\": 120.0}')")
        conn.commit()
    return db


def test_preview_column_alter_is_idempotent_and_keeps_data(tmp_path):
    """ALTER ADD COLUMN preview_url на старой таблице: данные живы, повтор — не падает."""
    db = _pre_v06_db(tmp_path)
    # первое подключение добавляет колонку; дескрипторы НЕ пересоздаются
    assert get_features("at:a|b", db_path=db) == {"tempo": 120.0}
    with sqlite3.connect(db) as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(features)")]
    assert "preview_url" in cols
    # повторные подключения идемпотентны — данные по-прежнему на месте
    assert get_preview_url("at:a|b", db_path=db) is None
    set_preview_url("at:a|b", "https://cdn/p.mp3", db_path=db)
    assert get_preview_url("at:a|b", db_path=db) == "https://cdn/p.mp3"
    assert get_features("at:a|b", db_path=db) == {"tempo": 120.0}


class _RacyConn:
    """PRAGMA «не видит» колонку — симуляция гонки двух подключений."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def execute(self, sql: str, *args):
        if sql.startswith("PRAGMA"):
            return iter([])  # прикидываемся, что preview_url ещё нет
        return self._conn.execute(sql, *args)


def test_preview_column_duplicate_alter_swallowed(tmp_path):
    """Гонка: колонка уже есть, а ALTER всё равно выполнился — ошибка глотается."""
    db = _pre_v06_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("ALTER TABLE features ADD COLUMN preview_url TEXT")
        _ensure_preview_column(_RacyConn(conn))  # не должен упасть


def test_set_features_with_preview_and_coalesce(tmp_path):
    db = tmp_path / "cache.db"
    set_features("at:a|b", {"tempo": 120.0}, preview_url="https://p/1.mp3", db_path=db)
    assert get_preview_url("at:a|b", db_path=db) == "https://p/1.mp3"
    # повторный set_features без preview_url НЕ затирает сохранённый URL
    set_features("at:a|b", {"tempo": 121.0}, db_path=db)
    assert get_features("at:a|b", db_path=db) == {"tempo": 121.0}
    assert get_preview_url("at:a|b", db_path=db) == "https://p/1.mp3"


def test_preview_only_row_gives_no_features(tmp_path):
    """Дозапись превью до анализа: get_features честно None, превью живо."""
    db = tmp_path / "cache.db"
    set_preview_url("at:x|y", "https://p/2.mp3", db_path=db)
    assert get_features("at:x|y", db_path=db) is None
    assert get_preview_url("at:x|y", db_path=db) == "https://p/2.mp3"
    # признаки доехали позже — превью не потерялось
    set_features("at:x|y", {"tempo": 100.0}, db_path=db)
    assert get_features("at:x|y", db_path=db) == {"tempo": 100.0}
    assert get_preview_url("at:x|y", db_path=db) == "https://p/2.mp3"
