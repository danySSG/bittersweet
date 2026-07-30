"""SQLite-кэш признаков по обобщённому ключу.

Популярный трек анализируется один раз на всех пользователей:
features(key PK, json, created_at, preview_url). Файл backend/cache.db (см. config).

Ключ (key_for): `isrc:{ISRC}` если ISRC есть, иначе `at:{norm(artist)}|{norm(title)}`
— у треков из YT Music нет ISRC, матчим по artist+title.
Старую схему (isrc PK, v0.1) не мигрируем (MVP): при несовпадении схемы
файл пересоздаётся.

preview_url (SPEC v0.6 §A2) добавляется к СУЩЕСТВУЮЩЕЙ таблице аккуратным
ALTER TABLE ... ADD COLUMN (идемпотентно): в кэше ценные дескрипторы,
пересоздавать таблицу нельзя. json='null' допустим — строка «только превью»
(дозапись из /api/preview/resolve до того, как трек проанализирован).
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from app.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS features (
    key TEXT PRIMARY KEY,
    json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    preview_url TEXT
)
"""

# скобочные хвосты: "(slowed + reverb)", "(feat. …)", "[remix]" и т.п.
_BRACKETS_RE = re.compile(r"[(\[][^)\]]*[)\]]")
_SPACES_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    """lower + убрать скобочные хвосты + схлопнуть пробелы."""
    text = _BRACKETS_RE.sub(" ", text or "")
    return _SPACES_RE.sub(" ", text).strip().lower()


def key_for(isrc: str | None, artist: str, title: str) -> str:
    """Обобщённый ключ кэша: ISRC-приоритет, иначе нормализованные artist+title."""
    if isrc and isrc.strip():
        return f"isrc:{isrc.strip().upper()}"
    return f"at:{_norm(artist)}|{_norm(title)}"


def _schema_matches(conn: sqlite3.Connection) -> bool:
    cols = [row[1] for row in conn.execute("PRAGMA table_info(features)")]
    return not cols or "key" in cols


def _ensure_preview_column(conn: sqlite3.Connection) -> None:
    """Идемпотентный ALTER TABLE ... ADD COLUMN preview_url (SPEC v0.6 §A2).

    Таблицу НЕ пересоздаём — в ней ценные дескрипторы. Проверка по
    PRAGMA table_info + страховка на «duplicate column name» (гонка
    двух подключений между PRAGMA и ALTER).
    """
    cols = [row[1] for row in conn.execute("PRAGMA table_info(features)")]
    if "preview_url" in cols:
        return
    try:
        conn.execute("ALTER TABLE features ADD COLUMN preview_url TEXT")
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else settings.cache_db
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    if not _schema_matches(conn):
        # старая v0.1-схема (isrc PK) — пересоздаём файл, миграции нет (MVP)
        conn.close()
        path.unlink(missing_ok=True)
        conn = sqlite3.connect(path)
    conn.execute(_SCHEMA)
    _ensure_preview_column(conn)
    return conn


def get_features(key: str, db_path: Path | str | None = None) -> dict | None:
    """Признаки из кэша или None.

    Строки «только превью» (json='null' из set_preview_url) — это None:
    признаков у трека ещё нет, конвейер пойдёт анализировать как обычно.
    """
    if not key:
        return None
    with _connect(db_path) as conn:
        row = conn.execute("SELECT json FROM features WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def set_features(
    key: str,
    features: dict,
    preview_url: str | None = None,
    db_path: Path | str | None = None,
) -> None:
    """Кладёт признаки в кэш (перезаписывает при повторе).

    preview_url (SPEC v0.6 §A2) пишется вместе с признаками; None НЕ затирает
    уже сохранённый URL (COALESCE) — превью могло быть дозаписано раньше.
    """
    if not key:
        return
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO features (key, json, preview_url) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET json = excluded.json, "
            "preview_url = COALESCE(excluded.preview_url, features.preview_url)",
            (key, json.dumps(features, ensure_ascii=False), preview_url),
        )
        conn.commit()


def get_preview_url(key: str, db_path: Path | str | None = None) -> str | None:
    """URL 30-сек превью из кэша или None (нет строки / превью не разрешалось)."""
    if not key:
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT preview_url FROM features WHERE key = ?", (key,)
        ).fetchone()
    return row[0] if row and row[0] else None


def set_preview_url(key: str, url: str, db_path: Path | str | None = None) -> None:
    """Дозаписывает preview_url (SPEC v0.6 §A2: /api/preview/resolve).

    Если строки нет (трек ещё не анализировался) — создаёт «только превью»
    строку с json='null': get_features по ней честно вернёт None.
    """
    if not key or not url:
        return
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO features (key, json, preview_url) VALUES (?, 'null', ?) "
            "ON CONFLICT(key) DO UPDATE SET preview_url = excluded.preview_url",
            (key, url),
        )
        conn.commit()
