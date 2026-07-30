"""GET /api/portraits (SPEC v0.9 §C) — лёгкий список «Мои портреты».

Поля, сортировка (новейшие первыми), limit и главное — лёгкость:
payload НЕ десериализуется целиком, points в Python не разворачиваются.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.engine import store
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cache_db", tmp_path / "cache.db")


def _portrait(
    n_tracks: int = 20,
    archetype: tuple[str, str] = ("тихая грусть", "🌫️"),
    share: int = 10,
    tempo_median: int = 90,
    n_points: int = 0,
) -> dict:
    name, emoji = archetype
    return {
        "n_tracks": n_tracks,
        "clusters": [
            {
                "label": "спокойное · меланхоличное · тёмное",
                "archetype": {"name": name, "emoji": emoji},
                "size": 15, "share": 75,
                "medians": {"tempo": tempo_median, "minor_share": 80, "brightness": 1200},
                "examples": ["A — B"], "color": "#7F77DD",
            },
            {
                "label": "энергичное · светлое · яркое",
                "archetype": {"name": "грув", "emoji": "🕺"},
                "size": 5, "share": 25,
                "medians": {"tempo": 140, "minor_share": 20, "brightness": 3000},
                "examples": ["C — D"], "color": "#5DCAA5",
            },
        ],
        "bittersweet": {"count": 2, "share": share, "top": [], "percentile": None},
        "highlights": [],
        "points": [
            {
                "x": 50.0, "y": 50.0, "cluster": 0, "label": f"Artist — Track {i}",
                "meta": "90bpm", "videoId": f"vid{i:08d}",
                "features": {
                    "tempo": 90.0, "minor_score": 0.7, "bittersweet": 0.6,
                    "percussive": 0.3, "energy_rms": 0.2, "brightness": 1200.0,
                    "onset_rate": 3.0,
                },
            }
            for i in range(n_points)
        ],
        "fingerprint": {
            "tempo_median": tempo_median, "minor_share": 80, "brightness_mean": 1200,
        },
    }


def test_portraits_list_fields_and_order():
    """Новейшие первыми; каждый элемент — сводка по контракту SPEC v0.9 §C."""
    first = store.save_portrait(
        _portrait(n_tracks=20, archetype=("тихая грусть", "🌫️"), share=10), "демо"
    )
    second = store.save_portrait(
        _portrait(n_tracks=33, archetype=("грустный бэнгер", "🌒"), share=25),
        "лайки YouTube",
    )
    third = store.save_portrait(
        _portrait(n_tracks=41, archetype=("биттерсвит", "🍬"), share=40),
        "плейлист YouTube Music",
    )

    r = client.get("/api/portraits")
    assert r.status_code == 200
    rows = r.json()
    assert [row["id"] for row in rows] == [third, second, first]  # новейшие первыми

    top = rows[0]
    assert set(top) == {
        "id", "source_label", "created_at", "n_tracks",
        "top_archetype", "fingerprint", "bittersweet_share", "has_timeline",
    }
    assert top["source_label"] == "плейлист YouTube Music"
    assert top["created_at"]
    assert top["n_tracks"] == 41
    # top_archetype — архетип крупнейшего кластера (clusters[0])
    assert top["top_archetype"] == {"name": "биттерсвит", "emoji": "🍬"}
    assert top["fingerprint"] == {
        "tempo_median": 90, "minor_share": 80, "brightness_mean": 1200,
    }
    assert top["bittersweet_share"] == 40
    assert top["has_timeline"] is False  # timeline в payload нет


def test_portraits_list_has_timeline_flag():
    """SPEC v0.10 §A: has_timeline — по нему фронт берёт новейший портрет с timeline."""
    without = store.save_portrait(_portrait(), "демо")
    payload = _portrait()
    payload["timeline"] = [
        {"period_start": "2024-01-01", "period_end": "2024-03-31", "n_tracks": 10,
         "tempo_median": 90, "minor_share": 80, "bittersweet_share": 10,
         "top_archetype": {"name": "тихая грусть", "emoji": "🌫️"}},
        {"period_start": "2024-04-01", "period_end": "2024-04-30", "n_tracks": 10,
         "tempo_median": 120, "minor_share": 40, "bittersweet_share": 20,
         "top_archetype": {"name": "грув", "emoji": "🕺"}},
    ]
    with_tl = store.save_portrait(payload, "лайки YouTube")
    # и timeline: null (не строился) — это НЕ timeline
    payload_null = _portrait()
    payload_null["timeline"] = None
    with_null = store.save_portrait(payload_null, "демо")

    flags = {row["id"]: row["has_timeline"] for row in client.get("/api/portraits").json()}
    assert flags == {without: False, with_tl: True, with_null: False}


def test_portraits_list_respects_limit():
    ids = [store.save_portrait(_portrait(), "демо") for _ in range(4)]
    r = client.get("/api/portraits", params={"limit": 2})
    assert r.status_code == 200
    assert [row["id"] for row in r.json()] == [ids[3], ids[2]]

    assert client.get("/api/portraits", params={"limit": 0}).status_code == 422
    assert client.get("/api/portraits", params={"limit": 999}).status_code == 422


def test_portraits_list_empty_db():
    assert client.get("/api/portraits").json() == []


def test_portraits_list_does_not_deserialize_points(monkeypatch):
    """Лёгкость: в json.loads НЕ попадает полный payload с points.

    Портрет на 300 точек; шпион на store.json.loads падает, если ему
    скормили строку с "points" (полный payload) — допустим только
    крошечный fingerprint из json_extract.
    """
    store.save_portrait(_portrait(n_points=300), "демо")

    seen: list[str] = []
    real_loads = json.loads

    def spy(s, *args, **kwargs):
        seen.append(s)
        return real_loads(s, *args, **kwargs)

    monkeypatch.setattr(store.json, "loads", spy)
    rows = store.list_portraits()
    assert len(rows) == 1
    assert rows[0]["n_tracks"] == 20
    assert rows[0]["fingerprint"]["tempo_median"] == 90
    assert seen, "fingerprint должен приезжать json_extract-ом и парситься точечно"
    for s in seen:
        assert '"points"' not in s
        assert len(s) < 500  # только крошечные фрагменты, не полный payload
