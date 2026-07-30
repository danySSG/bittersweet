"""Постоянные ссылки (SPEC v0.3 §B): save/load, перцентиль, роуты /api/p и demo save."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.engine import store
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cache_db", tmp_path / "cache.db")


def _portrait(share: int = 10) -> dict:
    return {
        "n_tracks": 20,
        "clusters": [{
            "label": "спокойное · меланхоличное · тёмное",
            "archetype": {"name": "тихая грусть", "emoji": "🌫️"},
            "size": 20, "share": 100,
            "medians": {"tempo": 90, "minor_share": 80, "brightness": 1200},
            "examples": ["A — B"], "color": "#7F77DD",
        }],
        "bittersweet": {"count": 2, "share": share, "top": [], "percentile": None},
        "highlights": [],
        "points": [],
        "fingerprint": {"tempo_median": 90, "minor_share": 80, "brightness_mean": 1200},
    }


def test_save_and_load_roundtrip():
    pid = store.save_portrait(_portrait(), source_label="демо")
    assert isinstance(pid, str) and len(pid) == 8

    row = store.get_portrait(pid)
    assert row is not None
    assert row["id"] == pid
    assert row["source_label"] == "демо"
    assert row["created_at"]
    assert row["portrait"] == _portrait()


def test_get_missing_returns_none():
    assert store.get_portrait("nope1234") is None
    assert store.get_portrait("") is None


def test_ids_are_unique_and_base32():
    ids = {store.save_portrait(_portrait(), "демо") for _ in range(10)}
    assert len(ids) == 10
    alphabet = set("abcdefghijklmnopqrstuvwxyz234567")
    assert all(set(i) <= alphabet for i in ids)


def test_percentile_none_below_min_portraits():
    for _ in range(store.PERCENTILE_MIN_PORTRAITS - 1):
        store.save_portrait(_portrait(share=5), "демо")
    assert store.bittersweet_percentile(50) is None
    assert store.bittersweet_percentile(None) is None


def test_percentile_counts_share_below():
    for share in (0, 5, 10, 20, 30, 40):
        store.save_portrait(_portrait(share=share), "демо")
    # ниже 25 — четыре портрета из шести
    assert store.bittersweet_percentile(25) == round(100 * 4 / 6)
    assert store.bittersweet_percentile(0) == 0
    assert store.bittersweet_percentile(100) == 100


def test_attach_percentile_mutates_bittersweet():
    for share in (0, 5, 10, 20, 30):
        store.save_portrait(_portrait(share=share), "демо")
    portrait = _portrait(share=25)
    store.attach_bittersweet_percentile(portrait)
    assert portrait["bittersweet"]["percentile"] == round(100 * 4 / 5)


def test_demo_save_and_permalink_api():
    r = client.post("/api/demo/portrait/save")
    assert r.status_code == 200
    pid = r.json()["portrait_id"]
    assert len(pid) == 8

    saved = client.get(f"/api/p/{pid}")
    assert saved.status_code == 200
    data = saved.json()
    assert data["id"] == pid
    assert data["source_label"] == "демо"
    assert data["created_at"]
    assert data["n_tracks"] > 0 and data["clusters"]
    assert "percentile" in data["bittersweet"]


def test_permalink_404_on_unknown_id():
    r = client.get("/api/p/zzzzzzzz")
    assert r.status_code == 404
