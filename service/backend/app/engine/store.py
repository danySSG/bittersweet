"""Постоянное хранилище портретов (SPEC v0.3 §B) — та же SQLite-база cache.db.

portraits(id TEXT PK, created_at, source_label TEXT, payload TEXT,
          owner TEXT, source TEXT, api_fetched_at TEXT);
id — 8 символов base32 из uuid4: короткий, url-friendly, неугадываемый
(лимит хранения в MVP не вводим). payload — полный JSON портрета.

owner/source/api_fetched_at (SPEC v0.5 §A3) — привязка к google-аккаунту:
owner = channel_id, source = "youtube", api_fetched_at = ISO-момент выгрузки
из YouTube Data API (правило 30 дней). У анонимных портретов эти колонки NULL.
Старую схему без этих колонок не мигрируем (MVP): при несовпадении таблица
portraits пересоздаётся (как features-кэш в cache.py).

Биттерсвит-перцентиль считается по этой же таблице «на лету»
(bittersweet_percentile / attach_bittersweet_percentile).
"""

from __future__ import annotations

import base64
import json
import sqlite3
import statistics
import uuid
from collections.abc import Sequence
from pathlib import Path

from app.engine import cache

_SCHEMA = """
CREATE TABLE IF NOT EXISTS portraits (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    source_label TEXT NOT NULL,
    payload TEXT NOT NULL,
    owner TEXT,
    source TEXT,
    api_fetched_at TEXT
)
"""

_EXPECTED_COLUMNS = {
    "id", "created_at", "source_label", "payload", "owner", "source", "api_fetched_at",
}

ID_LENGTH = 8

# ниже этого числа сохранённых портретов перцентиль не считаем (фронт не показывает)
PERCENTILE_MIN_PORTRAITS = 5


def _schema_matches(conn: sqlite3.Connection) -> bool:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(portraits)")}
    return not cols or _EXPECTED_COLUMNS <= cols


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Та же база (и контроль legacy-схемы features), что у кэша признаков."""
    conn = cache._connect(db_path)
    if not _schema_matches(conn):
        # схема v0.3 без owner/source/api_fetched_at — пересоздаём таблицу (MVP)
        conn.execute("DROP TABLE portraits")
        conn.commit()
    conn.execute(_SCHEMA)
    return conn


def _new_id() -> str:
    """8 символов base32 (a-z, 2-7) из uuid4 — 40 бит случайности."""
    return base64.b32encode(uuid.uuid4().bytes).decode("ascii").lower()[:ID_LENGTH]


def save_portrait(
    portrait: dict,
    source_label: str,
    db_path: Path | str | None = None,
    *,
    owner: str | None = None,
    source: str | None = None,
    api_fetched_at: str | None = None,
) -> str:
    """Сохраняет полный JSON портрета, возвращает его постоянный id.

    owner/source/api_fetched_at — для портретов из OAuth-источников (v0.5):
    по ним работают disconnect (удалить все портреты владельца) и purge
    (правило 30 дней). Анонимные источники их не передают (NULL).
    """
    payload = json.dumps(portrait, ensure_ascii=False)
    with _connect(db_path) as conn:
        for _ in range(5):  # коллизия id почти невозможна, но переигрываем
            portrait_id = _new_id()
            try:
                conn.execute(
                    "INSERT INTO portraits (id, source_label, payload, owner, source,"
                    " api_fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (portrait_id, source_label, payload, owner, source, api_fetched_at),
                )
                conn.commit()
                return portrait_id
            except sqlite3.IntegrityError:
                continue
    raise RuntimeError("не удалось выделить id портрета")


def delete_portraits_by_owner(
    owner: str, source: str = "youtube", db_path: Path | str | None = None
) -> int:
    """Удаляет все портреты владельца из данного источника; возвращает число."""
    if not owner:
        return 0
    with _connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM portraits WHERE owner = ? AND source = ?", (owner, source)
        )
        conn.commit()
    return cur.rowcount


def get_portrait(portrait_id: str, db_path: Path | str | None = None) -> dict | None:
    """{id, source_label, created_at, portrait} или None, если id нет."""
    if not portrait_id:
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, source_label, created_at, payload FROM portraits WHERE id = ?",
            (portrait_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "source_label": row[1],
        "created_at": row[2],
        "portrait": json.loads(row[3]),
    }


def update_discoveries(
    portrait_id: str,
    key: int | str,
    discoveries: dict,
    db_path: Path | str | None = None,
) -> bool:
    """Дописывает результат discovery в payload портрета (SPEC v0.4 §A2, v0.9 §B).

    payload["discoveries"] — словарь {str(key): результат discovery}; ключ —
    индекс кластера ("0") или округлённая точка карты ("point:50:50").
    Перезапуск того же ключа перезаписывает его запись.
    /p/{id} отдаёт payload как есть — находки видны без пересчёта.
    False, если портрета нет.
    """
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT payload FROM portraits WHERE id = ?", (portrait_id,)
        ).fetchone()
        if row is None:
            return False
        payload = json.loads(row[0])
        payload.setdefault("discoveries", {})[str(key)] = discoveries
        conn.execute(
            "UPDATE portraits SET payload = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), portrait_id),
        )
        conn.commit()
    return True


def _merge_into_payload(
    portrait_id: str, updates: dict, db_path: Path | str | None = None
) -> bool:
    """Дописывает ключи верхнего уровня в payload портрета; False — портрета нет."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT payload FROM portraits WHERE id = ?", (portrait_id,)
        ).fetchone()
        if row is None:
            return False
        payload = json.loads(row[0])
        payload.update(updates)
        conn.execute(
            "UPDATE portraits SET payload = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), portrait_id),
        )
        conn.commit()
    return True


def save_observatory(
    portrait_id: str,
    observatory: dict,
    version: int = 1,
    db_path: Path | str | None = None,
) -> bool:
    """Кладёт готовую обсерваторию в payload портрета (SPEC v0.11 §A).

    Ленивая одноразовая сборка: payload["observatory"] + payload
    ["observatory_version"]; повторные GET отдают из payload без пересчёта.
    False, если портрета нет.
    """
    return _merge_into_payload(
        portrait_id,
        {"observatory": observatory, "observatory_version": version},
        db_path,
    )


def save_story(
    portrait_id: str,
    story: dict,
    version: int = 1,
    db_path: Path | str | None = None,
) -> bool:
    """Кладёт готовую историю в payload портрета (SPEC v0.12 §A).

    Та же ленивая одноразовая механика, что у save_observatory:
    payload["story"] + payload["story_version"]; повторные GET отдают
    из payload без пересчёта. False, если портрета нет.
    """
    return _merge_into_payload(
        portrait_id, {"story": story, "story_version": version}, db_path
    )


def list_feature_medians(
    features: Sequence[str], db_path: Path | str | None = None
) -> list[dict[str, float]]:
    """Медианы признаков по каждому сохранённому портрету (радар SPEC v0.11 §A3).

    Считаются из payload.points[].features (лежат там с v0.4); портреты
    без признаков в точках пропускаются. Тяжелее list_portraits (payload
    разворачивается целиком), но вызывается только при одноразовой
    ленивой сборке обсерватории.
    """
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT payload FROM portraits").fetchall()
    out: list[dict[str, float]] = []
    for (payload,) in rows:
        try:
            points = json.loads(payload).get("points") or []
        except (ValueError, AttributeError):
            continue
        cols: dict[str, list[float]] = {f: [] for f in features}
        for p in points:
            ft = p.get("features") if isinstance(p, dict) else None
            if not isinstance(ft, dict):
                continue
            try:
                vals = {f: float(ft[f]) for f in features}
            except (KeyError, TypeError, ValueError):
                continue
            for f, v in vals.items():
                cols[f].append(v)
        if all(cols[f] for f in features):
            out.append({f: statistics.median(cols[f]) for f in features})
    return out


def list_portraits(limit: int = 50, db_path: Path | str | None = None) -> list[dict]:
    """Новейшие сохранённые портреты — лёгкая сводка для «Мои портреты» (SPEC v0.9 §C).

    Payload НЕ десериализуется целиком (points на сотни треков — тяжёлые):
    нужные ключи достаёт json_extract прямо в SQLite, в Python приезжают
    только скаляры и крошечный fingerprint. top_archetype — архетип
    крупнейшего кластера: clusters отсортированы по размеру в build_portrait,
    поэтому clusters[0] и есть топ. has_timeline (SPEC v0.10 §A) — есть ли
    в payload timeline динамики вкуса (json_type, сам массив не тянем):
    по нему фронт выбирает новейший портрет с timeline.

    ВАЖНО (privacy): перед публичным деплоем скоупить выдачу по owner —
    сейчас продукт однопользовательский до деплоя, отдаём все (MVP).
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, source_label, created_at,
                   json_extract(payload, '$.n_tracks'),
                   json_extract(payload, '$.clusters[0].archetype.name'),
                   json_extract(payload, '$.clusters[0].archetype.emoji'),
                   json_extract(payload, '$.fingerprint'),
                   json_extract(payload, '$.bittersweet.share'),
                   CASE WHEN json_type(payload, '$.timeline') = 'array'
                        THEN 1 ELSE 0 END
            FROM portraits
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "id": pid,
            "source_label": source_label,
            "created_at": created_at,
            "n_tracks": n_tracks,
            "top_archetype": (
                {"name": a_name, "emoji": a_emoji} if a_name is not None else None
            ),
            "fingerprint": json.loads(fingerprint) if fingerprint else None,
            "bittersweet_share": bs_share,
            "has_timeline": bool(has_timeline),
        }
        for pid, source_label, created_at, n_tracks, a_name, a_emoji, fingerprint,
            bs_share, has_timeline
        in rows
    ]


def bittersweet_percentile(share: float | None, db_path: Path | str | None = None) -> int | None:
    """Доля (0..100) сохранённых портретов, у которых bittersweet.share НИЖЕ текущего.

    None, если сохранённых портретов < PERCENTILE_MIN_PORTRAITS или share неизвестен.
    Считается по таблице portraits на лету — без отдельных агрегатов.
    """
    if share is None:
        return None
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT payload FROM portraits").fetchall()
    shares: list[float] = []
    for (payload,) in rows:
        try:
            s = json.loads(payload).get("bittersweet", {}).get("share")
        except (ValueError, AttributeError):
            continue
        if isinstance(s, (int, float)):
            shares.append(float(s))
    if len(shares) < PERCENTILE_MIN_PORTRAITS:
        return None
    below = sum(1 for s in shares if s < float(share))
    return round(100 * below / len(shares))


def attach_bittersweet_percentile(portrait: dict, db_path: Path | str | None = None) -> dict:
    """Проставляет bittersweet.percentile «на лету» (мутирует и возвращает portrait)."""
    bs = portrait.get("bittersweet")
    if isinstance(bs, dict):
        bs["percentile"] = bittersweet_percentile(bs.get("share"), db_path)
    return portrait
