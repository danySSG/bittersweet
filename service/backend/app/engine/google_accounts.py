"""Google-аккаунты (SPEC v0.5 §A2): таблица в cache.db + шифрование refresh_token.

google_accounts(channel_id PK, channel_title, refresh_token_enc, access_token,
token_expiry, created_at, likes_playlist_id). channel_id — ключ пользователя.

refresh_token в базе только шифрованный (Fernet); ключ выводится из
SESSION_SECRET через HKDF-SHA256 — отдельного секрета в .env не нужно,
но смена SESSION_SECRET инвалидирует сохранённые токены (пользователь
просто логинится заново — MVP-компромисс).

ПРАВИЛО 30 ДНЕЙ (Google Dev Policies III.E.4.c): purge_stale_youtube_data()
удаляет youtube-портреты старше 30 дней и осиротевшие аккаунты; вызывается
на старте приложения и в начале каждой youtube-джобы.
"""

from __future__ import annotations

import base64
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import settings
from app.engine import store

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS google_accounts (
    channel_id TEXT PRIMARY KEY,
    channel_title TEXT NOT NULL,
    refresh_token_enc TEXT NOT NULL,
    access_token TEXT NOT NULL,
    token_expiry TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    likes_playlist_id TEXT
)
"""

RETENTION_DAYS = 30  # данные из YouTube Data API храним не дольше (политика Google)

_HKDF_SALT = b"tastemap-google-refresh-token"
_HKDF_INFO = b"fernet-key-v1"


# ---- шифрование refresh_token ---------------------------------------------

def _fernet() -> Fernet:
    """Fernet-ключ из SESSION_SECRET: HKDF-SHA256 -> 32 байта -> urlsafe base64."""
    key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=_HKDF_SALT, info=_HKDF_INFO
    ).derive(settings.session_secret.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_token(token_enc: str) -> str | None:
    """None, если токен не расшифровался (сменился SESSION_SECRET и т.п.)."""
    try:
        return _fernet().decrypt(token_enc.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


# ---- таблица ----------------------------------------------------------------

def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Та же база, что и портреты (store гарантирует актуальную схему portraits)."""
    conn = store._connect(db_path)
    conn.execute(_SCHEMA)
    return conn


def expiry_from_now(expires_in: int) -> str:
    """expires_in (сек) -> ISO-строка UTC для колонки token_expiry."""
    return (datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))).isoformat()


def token_expired(token_expiry: str, leeway_seconds: int = 60) -> bool:
    """True, если access_token истёк (или истечёт в ближайшие leeway_seconds)."""
    try:
        expiry = datetime.fromisoformat(token_expiry)
    except (TypeError, ValueError):
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) + timedelta(seconds=leeway_seconds) >= expiry


def upsert_account(
    channel_id: str,
    channel_title: str,
    refresh_token: str,
    access_token: str,
    expires_in: int,
    likes_playlist_id: str | None,
    db_path: Path | str | None = None,
) -> None:
    """Создаёт/обновляет аккаунт; повторный вход освежает created_at (30 дней)."""
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO google_accounts (channel_id, channel_title, refresh_token_enc,"
            " access_token, token_expiry, likes_playlist_id) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(channel_id) DO UPDATE SET"
            " channel_title = excluded.channel_title,"
            " refresh_token_enc = excluded.refresh_token_enc,"
            " access_token = excluded.access_token,"
            " token_expiry = excluded.token_expiry,"
            " likes_playlist_id = excluded.likes_playlist_id,"
            " created_at = datetime('now')",
            (
                channel_id,
                channel_title,
                encrypt_token(refresh_token),
                access_token,
                expiry_from_now(expires_in),
                likes_playlist_id,
            ),
        )
        conn.commit()


def get_account(channel_id: str, db_path: Path | str | None = None) -> dict | None:
    """Аккаунт с УЖЕ расшифрованным refresh_token (или None)."""
    if not channel_id:
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT channel_id, channel_title, refresh_token_enc, access_token,"
            " token_expiry, created_at, likes_playlist_id"
            " FROM google_accounts WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "channel_id": row[0],
        "channel_title": row[1],
        "refresh_token": decrypt_token(row[2]),
        "access_token": row[3],
        "token_expiry": row[4],
        "created_at": row[5],
        "likes_playlist_id": row[6],
    }


def update_tokens(
    channel_id: str,
    access_token: str,
    expires_in: int,
    refresh_token: str | None = None,
    db_path: Path | str | None = None,
) -> None:
    """Обновляет access_token (+ refresh_token, если Google выдал новый)."""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE google_accounts SET access_token = ?, token_expiry = ?"
            " WHERE channel_id = ?",
            (access_token, expiry_from_now(expires_in), channel_id),
        )
        if refresh_token:
            conn.execute(
                "UPDATE google_accounts SET refresh_token_enc = ? WHERE channel_id = ?",
                (encrypt_token(refresh_token), channel_id),
            )
        conn.commit()


def delete_account(channel_id: str, db_path: Path | str | None = None) -> bool:
    """Удаляет строку аккаунта; True, если она существовала."""
    if not channel_id:
        return False
    with _connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM google_accounts WHERE channel_id = ?", (channel_id,)
        )
        conn.commit()
    return cur.rowcount > 0


# ---- правило 30 дней ---------------------------------------------------------

def purge_stale_youtube_data(db_path: Path | str | None = None) -> dict:
    """Удаляет youtube-портреты старше RETENTION_DAYS и осиротевшие аккаунты.

    Осиротевший аккаунт — старше RETENTION_DAYS и без единого живого
    youtube-портрета (его API-данные — channel_title, токены — не обновлялись
    и не нужны). Возвращает {"portraits": n, "accounts": m} для логов/тестов.
    """
    cutoff = f"-{RETENTION_DAYS} days"
    with _connect(db_path) as conn:
        # datetime(...) нормализует ISO-строку с 'T'/таймзоной к формату SQLite
        portraits = conn.execute(
            "DELETE FROM portraits WHERE source = 'youtube'"
            " AND datetime(COALESCE(api_fetched_at, created_at)) < datetime('now', ?)",
            (cutoff,),
        ).rowcount
        accounts = conn.execute(
            "DELETE FROM google_accounts WHERE created_at < datetime('now', ?)"
            " AND channel_id NOT IN"
            " (SELECT owner FROM portraits WHERE owner IS NOT NULL)",
            (cutoff,),
        ).rowcount
        conn.commit()
    if portraits or accounts:
        log.info(
            "purge (правило 30 дней): портретов удалено %d, аккаунтов %d",
            portraits, accounts,
        )
    return {"portraits": portraits, "accounts": accounts}
