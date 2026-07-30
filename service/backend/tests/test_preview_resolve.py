"""POST /api/preview/resolve (SPEC v0.6 §A2) — БЕЗ сети.

Кэш-first (каскад не вызывается на кэш-хите), живой каскад замокан
(monkeypatch), дозапись найденного в кэш, null не кэшируется, батч <= 24.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.engine import cache, previews
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cache_db", tmp_path / "cache.db")


def _resolve(items: list[dict]):
    return client.post("/api/preview/resolve", json={"items": items})


def test_resolve_cache_hit_no_network(monkeypatch):
    """URL уже в кэше -> отдаётся без единого вызова каскада."""
    cache.set_features(
        cache.key_for(None, "Burial", "Archangel"),
        {"tempo": 139.0},
        preview_url="https://cdn/archangel.mp3",
    )

    def boom(*a, **kw):
        raise AssertionError("кэш-хит: живой каскад не должен вызываться")

    monkeypatch.setattr(previews, "match_track_to_preview", boom)
    r = _resolve([{"artist": "Burial", "title": "Archangel"}])
    assert r.status_code == 200
    assert r.json() == {"urls": ["https://cdn/archangel.mp3"]}


def test_resolve_miss_live_cascade_and_writeback(monkeypatch):
    """Мимо кэша -> живой каскад; найденное дозаписывается, null — нет."""
    calls: list[tuple[str, str]] = []

    def fake_match(isrc, artist, title, client=None):
        calls.append((artist, title))
        return "https://cdn/live.mp3" if title == "Known" else None

    monkeypatch.setattr(previews, "match_track_to_preview", fake_match)
    r = _resolve([{"artist": "A", "title": "Known"},
                  {"artist": "B", "title": "Unknown"}])
    assert r.status_code == 200
    assert r.json() == {"urls": ["https://cdn/live.mp3", None]}
    assert calls == [("A", "Known"), ("B", "Unknown")]

    # дозапись в кэш: найденный URL сохранён (старая запись «дорезолвлена»)
    assert cache.get_preview_url(cache.key_for(None, "A", "Known")) == "https://cdn/live.mp3"
    # не найдено — НЕ кэшируется: завтра трек может появиться в Deezer
    assert cache.get_preview_url(cache.key_for(None, "B", "Unknown")) is None

    # повторный запрос Known — уже кэш-хит, каскад не дёргается
    r2 = _resolve([{"artist": "A", "title": "Known"}])
    assert r2.json() == {"urls": ["https://cdn/live.mp3"]}
    assert len(calls) == 2


def test_resolve_batch_validation():
    """Батч строго 1..24 элементов (SPEC v0.6 §A2)."""
    too_many = [{"artist": f"A{i}", "title": f"T{i}"} for i in range(25)]
    assert _resolve(too_many).status_code == 422
    assert _resolve([]).status_code == 422


def test_resolve_batch_of_24_ok(monkeypatch):
    monkeypatch.setattr(previews, "match_track_to_preview", lambda *a, **kw: None)
    items = [{"artist": f"A{i}", "title": f"T{i}"} for i in range(24)]
    r = _resolve(items)
    assert r.status_code == 200
    assert r.json()["urls"] == [None] * 24
