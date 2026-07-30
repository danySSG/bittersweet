"""oEmbed-резолвер — БЕЗ сети (httpx.MockTransport), кэш в tmp_path-SQLite."""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import settings
from app.engine import resolver

VID = "dQw4w9WgXcQ"


@pytest.fixture(autouse=True)
def _fast_and_isolated(tmp_path, monkeypatch):
    """Нулевые троттлинг/бэкофф (тестам сеть всё равно замокана) + свой cache.db."""
    monkeypatch.setattr(resolver, "MIN_INTERVAL", 0.0)
    monkeypatch.setattr(resolver, "BACKOFF_BASE", 0.0)
    monkeypatch.setattr(resolver, "_next_allowed", 0.0)
    monkeypatch.setattr(settings, "cache_db", tmp_path / "cache.db")


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _oembed_json(title: str, author: str) -> dict:
    return {"title": title, "author_name": author, "provider_name": "YouTube"}


def test_topic_suffix_stripped():
    """author_name «<Artist> - Topic» -> чистый артист; title как есть."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.url.host == "www.youtube.com"
        assert request.url.params["url"] == f"https://www.youtube.com/watch?v={VID}"
        return httpx.Response(200, json=_oembed_json("Never Gonna Give You Up", "Rick Astley - Topic"))

    meta = resolver.resolve_video(VID, client=_client(handler))
    assert meta == {"artist": "Rick Astley", "title": "Never Gonna Give You Up"}
    assert len(calls) == 1


def test_regular_channel_author_kept():
    def handler(request):
        return httpx.Response(200, json=_oembed_json("Some Video", "SomeChannel"))

    meta = resolver.resolve_video(VID, client=_client(handler))
    assert meta == {"artist": "SomeChannel", "title": "Some Video"}


def test_cache_hit_no_second_request():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json=_oembed_json("Song", "Artist - Topic"))

    client = _client(handler)
    first = resolver.resolve_video(VID, client=client)
    second = resolver.resolve_video(VID, client=client)
    assert first == second == {"artist": "Artist", "title": "Song"}
    assert calls["n"] == 1  # второй раз — из SQLite video_meta


def test_404_returns_none_no_fallback():
    """404 = видео удалено: None сразу, noembed НЕ дёргается."""
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(404)

    assert resolver.resolve_video(VID, client=_client(handler)) is None
    assert hosts == ["www.youtube.com"]


def test_401_embed_forbidden_returns_none():
    def handler(request):
        return httpx.Response(401)

    assert resolver.resolve_video(VID, client=_client(handler)) is None


def test_backoff_on_429_then_success():
    """429 -> экспоненциальный бэкофф -> повтор -> успех (без фолбэка)."""
    statuses = iter([429, 429, 200])
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        s = next(statuses)
        if s == 200:
            return httpx.Response(200, json=_oembed_json("T", "A - Topic"))
        return httpx.Response(s)

    meta = resolver.resolve_video(VID, client=_client(handler))
    assert meta == {"artist": "A", "title": "T"}
    assert hosts == ["www.youtube.com"] * 3


def test_fallback_to_noembed_on_persistent_403():
    """403 исчерпал ретраи -> noembed.com (тот же формат ответа)."""
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        if request.url.host == "www.youtube.com":
            return httpx.Response(403)
        return httpx.Response(200, json=_oembed_json("Shape of You", "Ed Sheeran - Topic"))

    meta = resolver.resolve_video(VID, client=_client(handler))
    assert meta == {"artist": "Ed Sheeran", "title": "Shape of You"}
    assert hosts.count("noembed.com") == 1
    assert hosts.count("www.youtube.com") == resolver.BACKOFF_ATTEMPTS + 1


def test_noembed_error_payload_is_none():
    """noembed отдаёт 200 + {"error": …} на недоступные видео -> None."""
    def handler(request):
        if request.url.host == "www.youtube.com":
            return httpx.Response(500)
        return httpx.Response(200, json={"error": "no matching providers found"})

    assert resolver.resolve_video(VID, client=_client(handler)) is None


def test_network_error_falls_back_then_none():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    assert resolver.resolve_video(VID, client=_client(handler)) is None


def test_failure_not_cached():
    """Неуспех не пишется в кэш: следующий вызов пробует сеть снова."""
    calls = {"n": 0}

    def fail_then_ok(request):
        calls["n"] += 1
        if request.url.host == "noembed.com":
            return httpx.Response(500)
        if calls["n"] <= 1:
            return httpx.Response(404)
        return httpx.Response(200, json=_oembed_json("T", "A"))

    assert resolver.resolve_video(VID, client=_client(fail_then_ok)) is None
    meta = resolver.resolve_video(VID, client=_client(fail_then_ok))
    assert meta == {"artist": "A", "title": "T"}


def test_cache_roundtrip_direct(tmp_path):
    db = tmp_path / "own.db"
    assert resolver.get_cached(VID, db_path=db) is None
    resolver.set_cached(VID, "Artist", "Title", db_path=db)
    assert resolver.get_cached(VID, db_path=db) == {"artist": "Artist", "title": "Title"}
