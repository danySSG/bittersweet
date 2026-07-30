"""Каскад поиска превью (без сети — фейковый httpx-клиент)."""

from __future__ import annotations

import pytest

from app.engine import previews


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class FakeClient:
    """Записывает вызовы; отвечает по подстроке URL (первое совпадение)."""

    def __init__(self, routes: list[tuple[str, FakeResponse]]):
        self.routes = routes
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url: str, params: dict | None = None) -> FakeResponse:
        self.calls.append((url, params))
        for needle, resp in self.routes:
            if needle in url:
                return resp
        return FakeResponse(404)

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    """Тесты не должны спать по 3.1 c на iTunes-троттлинге."""
    monkeypatch.setattr(previews, "ITUNES_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(previews, "DEEZER_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(previews, "_next_allowed", {"itunes": 0.0, "deezer": 0.0})


def test_with_isrc_deezer_isrc_first():
    client = FakeClient([
        ("api.deezer.com/track/isrc:", FakeResponse(200, {"id": 1, "preview": "http://dz/p.mp3"})),
    ])
    url = previews.match_track_to_preview("USUM71703861", "Artist", "Song", client=client)
    assert url == "http://dz/p.mp3"
    assert len(client.calls) == 1
    assert "track/isrc:USUM71703861" in client.calls[0][0]


def test_without_isrc_deezer_search_first_then_itunes():
    client = FakeClient([
        ("api.deezer.com/search", FakeResponse(200, {"data": []})),  # оба варианта q — пусто
        ("itunes.apple.com/search",
         FakeResponse(200, {"results": [{"previewUrl": "http://it/p.m4a"}]})),
    ])
    url = previews.match_track_to_preview(None, "Artist", "Song", client=client)
    assert url == "http://it/p.m4a"

    hosts = [u for u, _ in client.calls]
    # порядок: Deezer-search (точный q), Deezer-search (простой q), iTunes
    assert "deezer" in hosts[0] and "/search" in hosts[0]
    assert "deezer" in hosts[1]
    assert "itunes" in hosts[2]
    assert client.calls[0][1]["q"] == 'artist:"Artist" track:"Song"'
    assert client.calls[1][1]["q"] == "Artist Song"
    # Deezer-ISRC не дёргался — ISRC нет
    assert not any("track/isrc" in u for u in hosts)


def test_without_isrc_deezer_search_hit_skips_itunes():
    client = FakeClient([
        ("api.deezer.com/search", FakeResponse(200, {"data": [{"preview": "http://dz/s.mp3"}]})),
    ])
    url = previews.match_track_to_preview(None, "Artist", "Song", client=client)
    assert url == "http://dz/s.mp3"
    assert not any("itunes" in u for u, _ in client.calls)


def test_isrc_miss_falls_through_cascade():
    client = FakeClient([
        ("api.deezer.com/track/isrc:", FakeResponse(200, {"error": {"code": 800}})),
        ("api.deezer.com/search", FakeResponse(200, {"data": []})),
        ("itunes.apple.com/search",
         FakeResponse(200, {"results": [{"previewUrl": "http://it/f.m4a"}]})),
    ])
    url = previews.match_track_to_preview("GBAYE0601498", "Artist", "Song", client=client)
    assert url == "http://it/f.m4a"
    hosts = [u for u, _ in client.calls]
    assert "track/isrc" in hosts[0]
    assert "itunes" in hosts[-1]


def test_all_fail_returns_none():
    client = FakeClient([
        ("api.deezer.com/track/isrc:", FakeResponse(200, {"error": {"code": 800}})),
        ("api.deezer.com/search", FakeResponse(200, {"data": []})),
        ("itunes.apple.com/search", FakeResponse(200, {"results": []})),
    ])
    assert previews.match_track_to_preview("XX0000000000", "A", "B", client=client) is None


def test_old_name_is_alias():
    assert previews.match_isrc_to_preview is previews.match_track_to_preview
