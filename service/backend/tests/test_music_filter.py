"""Музыкальный фильтр лайков и метрика скорости (SPEC v0.7 §A1/§A2) — БЕЗ сети.

Юниты: чистый предикат is_music_item (категория 10 / « - Topic» / отказ),
скан scan_music_likes через respx (паджинация, фильтры, limit ПОСЛЕ фильтра,
limit=0/None = все, scan_cap, progress с music_found, ветка квоты 403),
RateMeter (скользящее окно <= 20 тиков, инъецируемые часы).
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from app import jobs
from app.sources import youtube_api

PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


# ---------------------------------------------------------------- предикат

@pytest.mark.parametrize("category_id,owner,expected", [
    ("10", "Ordinary Channel", True),    # категория Music — берётся
    ("10", None, True),
    ("10", "", True),
    ("22", "Artist - Topic", True),      # авто-канал YT Music — берётся
    (None, "Artist - Topic", True),
    ("22", "Ordinary Channel", False),   # категория 22 без Topic — мимо
    ("24", "Some Vlogger", False),
    (None, None, False),
    ("", "", False),
    ("22", "Topical News", False),       # «Topic» не суффиксом — мимо
])
def test_is_music_item(category_id, owner, expected):
    """SPEC v0.7 §A1: categoryId == "10" ИЛИ владелец оканчивается " - Topic"."""
    assert youtube_api.is_music_item(category_id, owner) is expected


# ------------------------------------------------------- скан likes (respx)

def _pl_item(
    video_id: str, title: str, owner: str, published_at: str | None = None
) -> dict:
    sn = {
        "title": title,
        "videoOwnerChannelTitle": owner,
        "resourceId": {"videoId": video_id},
    }
    if published_at:
        # дата добавления в likes-плейлист = дата лайка (SPEC v0.10 §A)
        sn["publishedAt"] = published_at
    return {"snippet": sn}


def _video(video_id: str, category: str, duration: str) -> dict:
    return {
        "id": video_id,
        "snippet": {"categoryId": category},
        "contentDetails": {"duration": duration},
    }


def _three_pages() -> tuple[list, dict]:
    """3 страницы по 2 лайка: 3 музыкальных + влог/длинное/удалённое."""
    pages = [
        ([_pl_item("music00001", "Song A", "Artist A - Topic",
                   published_at="2024-06-01T12:00:00Z"),
          _pl_item("vlog000001", "My Vlog", "Some Vlogger")], "p2"),
        ([_pl_item("music00002", "Song B", "Artist B"),  # без publishedAt -> None
          _pl_item("long000001", "Album Mix", "Artist C - Topic")], "p3"),
        ([_pl_item("music00003", "Song C", "Artist D - Topic",
                   published_at="2024-04-15T08:30:00Z"),
          _pl_item("gone000001", "Deleted track", "Artist E - Topic")], None),
    ]
    details = {
        "music00001": _video("music00001", "22", "PT3M0S"),   # музыка: « - Topic»
        "vlog000001": _video("vlog000001", "22", "PT3M0S"),   # не музыка — мимо
        "music00002": _video("music00002", "10", "PT4M0S"),   # музыка: категория 10
        "long000001": _video("long000001", "10", "PT20M0S"),  # музыка, но > 12 мин
        "music00003": _video("music00003", "22", "PT2M30S"),
        # gone000001 отсутствует — videos.list не возвращает удалённые
    }
    return pages, details


def _mock_scan(rsx, pages: list, details: dict, total_results: int) -> dict:
    def playlist_cb(request):
        token = request.url.params.get("pageToken")
        idx = int(token[1:]) - 1 if token else 0  # токены "p2", "p3"
        items, nxt = pages[idx]
        body = {"items": items, "pageInfo": {"totalResults": total_results}}
        if nxt:
            body["nextPageToken"] = nxt
        return Response(200, json=body)

    def videos_cb(request):
        ids = (request.url.params.get("id") or "").split(",")
        return Response(200, json={
            "items": [details[v] for v in ids if v in details]
        })

    return {
        "playlist": rsx.get(PLAYLIST_ITEMS_URL).mock(side_effect=playlist_cb),
        "videos": rsx.get(VIDEOS_URL).mock(side_effect=videos_cb),
    }


def test_scan_filters_and_keeps_freshest_order():
    """Паджинация до конца; влог/длинное/удалённое — мимо; порядок свежие-первыми."""
    pages, details = _three_pages()
    with respx.mock(assert_all_called=False) as rsx:
        routes = _mock_scan(rsx, pages, details, total_results=6)
        tracks = youtube_api.scan_music_likes("tok", "LL")
    assert [t["videoId"] for t in tracks] == [
        "music00001", "music00002", "music00003",
    ]
    # « - Topic» срезан, обычный канал — как есть
    assert tracks[0]["artist"] == "Artist A"
    # SPEC v0.10 §A: liked_at = snippet.publishedAt (дата лайка), нет — None
    assert tracks[0]["liked_at"] == "2024-06-01T12:00:00Z"
    assert tracks[1] == {"artist": "Artist B", "title": "Song B",
                         "videoId": "music00002", "liked_at": None}
    assert tracks[2]["liked_at"] == "2024-04-15T08:30:00Z"
    assert routes["playlist"].call_count == 3
    assert routes["videos"].call_count == 3
    # videos.list просит и категорию (фильтр), и длительность
    params = routes["videos"].calls[0].request.url.params
    assert params["part"] == "snippet,contentDetails"


def test_scan_limit_applies_after_music_filter():
    """limit считает ТОЛЬКО музыкальные: влог на 1-й странице не входит в 2."""
    pages, details = _three_pages()
    with respx.mock(assert_all_called=False) as rsx:
        routes = _mock_scan(rsx, pages, details, total_results=6)
        tracks = youtube_api.scan_music_likes("tok", "LL", limit=2)
    assert [t["videoId"] for t in tracks] == ["music00001", "music00002"]
    # набрали limit музыкальных — 3-я страница не сканируется (экономия квоты)
    assert routes["playlist"].call_count == 2


@pytest.mark.parametrize("limit", [None, 0])
def test_scan_limit_none_or_zero_means_all(limit):
    """SPEC v0.7 §A2: limit=None/0 — без лимита, скан до конца плейлиста."""
    pages, details = _three_pages()
    with respx.mock(assert_all_called=False) as rsx:
        routes = _mock_scan(rsx, pages, details, total_results=6)
        tracks = youtube_api.scan_music_likes("tok", "LL", limit=limit)
    assert len(tracks) == 3
    assert routes["playlist"].call_count == 3


def test_scan_progress_reports_scanned_total_and_music_found():
    """progress_cb(scanned, total, music_found) — по странице, счётчики растут."""
    pages, details = _three_pages()
    calls: list[tuple[int, int, int]] = []
    with respx.mock(assert_all_called=False) as rsx:
        _mock_scan(rsx, pages, details, total_results=6)
        youtube_api.scan_music_likes(
            "tok", "LL",
            progress_cb=lambda s, t, f: calls.append((s, t, f)),
        )
    assert calls == [(2, 6, 1), (4, 6, 2), (6, 6, 3)]


def test_scan_stops_at_scan_cap():
    """Безопасный максимум: scan_cap=2 — только первая страница (maxResults=2)."""
    pages, details = _three_pages()
    with respx.mock(assert_all_called=False) as rsx:
        routes = _mock_scan(rsx, pages, details, total_results=6)
        tracks = youtube_api.scan_music_likes("tok", "LL", scan_cap=2)
    assert [t["videoId"] for t in tracks] == ["music00001"]
    assert routes["playlist"].call_count == 1
    assert routes["playlist"].calls[0].request.url.params["maxResults"] == "2"


_QUOTA_BODY = {"error": {
    "code": 403,
    "message": "The request cannot be completed because you have exceeded "
               "your quota.",
    "errors": [{"reason": "quotaExceeded", "domain": "youtube.quota"}],
}}


def test_scan_quota_exceeded_raises_quota_error():
    """403 quotaExceeded на playlistItems -> YouTubeQuotaError (SPEC v0.7 §A1)."""
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(PLAYLIST_ITEMS_URL).respond(status_code=403, json=_QUOTA_BODY)
        with pytest.raises(youtube_api.YouTubeQuotaError):
            youtube_api.scan_music_likes("tok", "LL")


def test_scan_quota_exceeded_on_videos_list_raises_too():
    """Квота может кончиться и на батче videos.list посреди скана."""
    pages, details = _three_pages()
    with respx.mock(assert_all_called=False) as rsx:
        _mock_scan(rsx, pages, details, total_results=6)
        rsx.get(VIDEOS_URL).respond(status_code=403, json=_QUOTA_BODY)
        with pytest.raises(youtube_api.YouTubeQuotaError):
            youtube_api.scan_music_likes("tok", "LL")


def test_scan_403_without_quota_reason_is_generic_error():
    """Обычный 403 (не квота) — генерический YouTubeAPIError, не квотная ветка."""
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(PLAYLIST_ITEMS_URL).respond(
            status_code=403,
            json={"error": {"errors": [{"reason": "forbidden"}]}},
        )
        with pytest.raises(youtube_api.YouTubeAPIError) as exc_info:
            youtube_api.scan_music_likes("tok", "LL")
    assert not isinstance(exc_info.value, youtube_api.YouTubeQuotaError)


# ------------------------------------------------------------- rate_per_min

def _clock_from(values: list[float]):
    it = iter(values)
    return lambda: next(it)


def test_rate_meter_none_until_two_ticks():
    meter = jobs.RateMeter(clock=_clock_from([0.0, 1.0]))
    assert meter.tick() is None       # один тик — скорость не определена
    assert meter.tick() == 60.0       # 2 тика за 1 c -> 60 треков/мин


def test_rate_meter_steady_rate():
    # тик каждые 3 секунды -> 20 треков/мин
    meter = jobs.RateMeter(clock=_clock_from([0.0, 3.0, 6.0, 9.0]))
    rates = [meter.tick() for _ in range(4)]
    assert rates == [None, 20.0, 20.0, 20.0]


def test_rate_meter_sliding_window_keeps_last_20():
    """SPEC v0.7 §A2: окно по последним <= 20 тикам — медленный старт
    (10 c/трек) не тянет скорость вниз после разгона (1 c/трек)."""
    slow = [i * 10.0 for i in range(10)]            # 10 тиков по 10 секунд
    fast = [90.0 + i for i in range(1, 21)]         # 20 тиков по 1 секунде
    meter = jobs.RateMeter(window=20, clock=_clock_from(slow + fast))
    rate = None
    for _ in range(30):
        rate = meter.tick()
    # окно = последние 20 тиков (все быстрые): 19 интервалов за 19 c -> 60/мин
    assert rate == 60.0


def test_rate_meter_zero_elapsed_is_none():
    meter = jobs.RateMeter(clock=_clock_from([5.0, 5.0]))
    meter.tick()
    assert meter.tick() is None  # два тика в один момент — деления на 0 нет
