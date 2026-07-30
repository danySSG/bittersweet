"""app.sources.ytmusic: разбор ссылки, фильтр >12 мин, маппинг ошибок. Без сети."""

from __future__ import annotations

import pytest
import ytmusicapi

from app.sources import ytmusic


# --- extract_playlist_id ---

def test_extract_from_url():
    url = "https://music.youtube.com/playlist?list=PLfoNjwA6k-mY&si=xyz"
    assert ytmusic.extract_playlist_id(url) == "PLfoNjwA6k-mY"


def test_extract_bare_id():
    assert ytmusic.extract_playlist_id("PLfoNjwA6k-mY") == "PLfoNjwA6k-mY"


@pytest.mark.parametrize("bad", ["", "   ", "https://music.youtube.com/watch?v=abc",
                                 "not a playlist id"])
def test_extract_rejects_garbage(bad):
    with pytest.raises(ValueError):
        ytmusic.extract_playlist_id(bad)


# --- fetch_playlist (YTMusic замокан) ---

def _raw_track(i: int, duration: str = "3:30", video_id: str | None = "x") -> dict:
    return {
        "videoId": f"{video_id}{i}" if video_id else None,
        "title": f"Track {i}",
        "artists": [{"name": f"Artist {i}"}],
        "duration": duration,
    }


class FakeYTMusic:
    payload: dict = {}
    exc: Exception | None = None

    def __init__(self, *a, **kw):
        pass

    def get_playlist(self, playlist_id, limit=25):
        if FakeYTMusic.exc is not None:
            raise FakeYTMusic.exc
        return FakeYTMusic.payload


@pytest.fixture(autouse=True)
def _fake_yt(monkeypatch):
    FakeYTMusic.payload = {}
    FakeYTMusic.exc = None
    monkeypatch.setattr(ytmusicapi, "YTMusic", FakeYTMusic)


def test_fetch_filters_long_and_unavailable():
    FakeYTMusic.payload = {"tracks": [
        _raw_track(0),                             # ok
        _raw_track(1, duration="14:00"),           # >12 мин — фильтруется
        _raw_track(2, video_id=None),              # недоступный — фильтруется
        _raw_track(3, duration="11:59"),           # на границе — остаётся
    ]}
    tracks = ytmusic.fetch_playlist("PLtest")
    assert [t["title"] for t in tracks] == ["Track 0", "Track 3"]
    # SPEC v0.10 §A: liked_at в контракте; ytmusicapi метку не отдал -> None
    assert tracks[0] == {"artist": "Artist 0", "title": "Track 0",
                         "videoId": "x0", "duration_seconds": 210,
                         "liked_at": None}


def test_fetch_extracts_liked_at_when_present():
    """SPEC v0.10 §A: ISO-метка добавления из ytmusicapi, если есть; мусор — нет."""
    with_ts = _raw_track(0)
    with_ts["timestamp"] = "2024-05-01T12:34:56+00:00"
    garbage_ts = _raw_track(1)
    garbage_ts["timestamp"] = "3:42"  # не ISO — не утаскиваем мусор
    FakeYTMusic.payload = {"tracks": [with_ts, garbage_ts]}
    tracks = ytmusic.fetch_playlist("PLtest")
    assert tracks[0]["liked_at"] == "2024-05-01T12:34:56+00:00"
    assert tracks[1]["liked_at"] is None


def test_fetch_respects_limit():
    FakeYTMusic.payload = {"tracks": [_raw_track(i) for i in range(30)]}
    assert len(ytmusic.fetch_playlist("PLtest", limit=5)) == 5


def test_fetch_maps_404_to_not_found():
    FakeYTMusic.exc = Exception("Server returned HTTP 404: Not Found.")
    with pytest.raises(ytmusic.PlaylistNotFound):
        ytmusic.fetch_playlist("PLmissing")


def test_fetch_maps_403_to_private():
    FakeYTMusic.exc = Exception("Server returned HTTP 403: private playlist")
    with pytest.raises(ytmusic.PlaylistPrivate):
        ytmusic.fetch_playlist("PLprivate")


def test_fetch_wraps_unknown_errors():
    FakeYTMusic.exc = RuntimeError("boom")
    with pytest.raises(ytmusic.PlaylistError):
        ytmusic.fetch_playlist("PLtest")
