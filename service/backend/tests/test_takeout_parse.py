"""Парсер Takeout-выгрузки — БЕЗ сети, всё на синтетических фикстурах.

Проверяем факты формата из SPEC-v02b: позиционный парсинг (заголовки
локализуются — EN и zh-TW-подобные), library-songs (4 колонки, URL с v=),
пер-плейлистные CSV (2 колонки: ID + ISO timestamp), BOM, ';' как
разделитель, пустые строки, zip со структурой Takeout, zip-safety.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone

import pytest

from app.sources import takeout

# 11-символьные валидные видео-ID
VID1 = "dQw4w9WgXcQ"
VID2 = "kJQP7kiw5Fk"
VID3 = "9bZkp7q19f0"
VID4 = "fJ9rUzIMcZQ"


# --- library-songs: 4 колонки, EN-заголовок, URL с v= --------------------------

LIBRARY_CSV_EN = (
    "Song URL,Song Title,Album Title,Artist Names\n"
    f"https://music.youtube.com/watch?v={VID1},Never Gonna Give You Up,"
    "Whenever You Need Somebody,Rick Astley\n"
    f"https://music.youtube.com/watch?v={VID2}&feature=share,Despacito,"
    "VIDA,Luis Fonsi\n"
).encode("utf-8")


def test_library_csv_en_headers():
    entries = takeout.parse_csv(LIBRARY_CSV_EN, "music-library-songs.csv")
    assert len(entries) == 2
    e1, e2 = entries
    assert e1.video_id == VID1
    assert e1.title == "Never Gonna Give You Up"
    assert e1.artist == "Rick Astley"  # позиционно col3, НЕ col2 (альбом)
    assert e1.added_at is None
    assert e2.video_id == VID2  # v= извлечён и при лишних query-параметрах
    assert e2.artist == "Luis Fonsi"


# --- плейлистный CSV: 2 колонки, zh-TW-подобный (не-латиница) заголовок --------

PLAYLIST_CSV_ZH = (
    "影片 ID,播放清單影片的建立時間戳記\n"
    f"{VID1},2024-03-01T10:00:00+00:00\n"
    f"{VID2},2023-01-15T08:30:00Z\n"
).encode("utf-8")


def test_playlist_csv_zh_headers_two_columns():
    """Заголовки локализуются (факт №5) — парсинг обязан быть позиционным."""
    entries = takeout.parse_csv(PLAYLIST_CSV_ZH, "自訂清單.csv")
    assert len(entries) == 2
    assert entries[0].video_id == VID1
    assert entries[0].artist is None and entries[0].title is None  # названий НЕТ
    assert entries[0].added_at == datetime(2024, 3, 1, 10, 0, tzinfo=timezone.utc)
    assert entries[1].added_at == datetime(2023, 1, 15, 8, 30, tzinfo=timezone.utc)


# --- устойчивость: BOM, ';', пустые строки -------------------------------------

def test_bom_semicolon_and_blank_lines():
    csv_data = b"\xef\xbb\xbf" + (  # UTF-8 BOM, в природе встречается
        "Video ID;Playlist Video Creation Timestamp\n"
        "\n"
        f"{VID3};2022-07-04T12:00:00+00:00\n"
        "\n"
    ).encode("utf-8")
    entries = takeout.parse_csv(csv_data, "songs.csv")
    assert len(entries) == 1
    assert entries[0].video_id == VID3
    assert entries[0].added_at is not None


def test_headerless_csv_first_row_is_data():
    """Если первая строка валидна как ID — это данные, а не заголовок."""
    csv_data = f"{VID1},2024-01-01T00:00:00+00:00\n{VID2},2024-01-02T00:00:00+00:00\n"
    entries = takeout.parse_csv(csv_data.encode(), "x.csv")
    assert [e.video_id for e in entries] == [VID1, VID2]


def test_non_music_csv_yields_nothing():
    """playlists.csv (индекс) и прочие CSV без видео-ID отбрасываются содержимым."""
    index_csv = (
        "Playlist ID,Playlist Title (Original),Playlist Create Timestamp\n"
        "PL123,My playlist,2024-01-01T00:00:00+00:00\n"
    ).encode()
    assert takeout.parse_csv(index_csv, "playlists.csv") == []


def test_invalid_ids_rejected():
    """Валидация строгая: ровно 11 символов [A-Za-z0-9_-] или URL с v=."""
    bad = (
        "Video ID,Timestamp\n"
        "short,2024-01-01T00:00:00+00:00\n"           # короткий
        "waytoolong12chars,2024-01-01T00:00:00+00:00\n"  # длинный
        "has spaces!,2024-01-01T00:00:00+00:00\n"     # мусор
        "https://music.youtube.com/playlist?list=PL1,2024-01-01T00:00:00+00:00\n"  # без v=
    ).encode()
    assert takeout.parse_csv(bad, "x.csv") == []


# --- extract_video_id ----------------------------------------------------------

@pytest.mark.parametrize("cell,expected", [
    (VID1, VID1),
    (f"https://music.youtube.com/watch?v={VID1}", VID1),
    (f"https://www.youtube.com/watch?v={VID1}&list=RD{VID1}", VID1),
    (f"  {VID1}  ", VID1),
    ("", None),
    ("Song URL", None),
    ("影片 ID", None),
    (f"https://youtu.be/{VID1}", None),  # без v= — в Takeout не встречается
])
def test_extract_video_id(cell, expected):
    assert takeout.extract_video_id(cell) == expected


# --- zip со структурой Takeout --------------------------------------------------

def _takeout_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_zip_with_localized_takeout_structure():
    """Папки локализованы (RU-подобные имена) — файлы находим по содержимому."""
    playlist_ru = (
        "Идентификатор видео,Отметка времени создания\n"
        f"{VID3},2024-06-01T09:00:00+00:00\n"
        f"{VID4},2024-06-02T09:00:00+00:00\n"
    ).encode()
    zip_bytes = _takeout_zip({
        "Takeout/YouTube и YouTube Music/музыкальная библиотека/песни.csv": LIBRARY_CSV_EN,
        "Takeout/YouTube и YouTube Music/плейлисты/Мне понравилось.csv": playlist_ru,
        "Takeout/YouTube и YouTube Music/плейлисты/playlists.csv": (
            "Playlist ID,Title\nPL1,fav\n".encode()
        ),
        "Takeout/YouTube и YouTube Music/история/watch-history.json": b"[]",
    })
    entries = takeout.parse_takeout(zip_bytes, "takeout.zip")
    ids = {e.video_id for e in entries}
    assert ids == {VID1, VID2, VID3, VID4}
    by_id = {e.video_id: e for e in entries}
    assert by_id[VID1].artist == "Rick Astley"  # метаданные из library
    assert by_id[VID3].artist is None           # плейлистный — только ID+время


def test_zip_dedup_and_metadata_merge():
    """Один трек в library и плейлисте -> одна запись: метаданные + свежий timestamp."""
    playlist = f"Video ID,Timestamp\n{VID1},2024-05-05T00:00:00+00:00\n".encode()
    zip_bytes = _takeout_zip({
        "Takeout/YouTube and YouTube Music/music-library-songs/music-library-songs.csv":
            LIBRARY_CSV_EN,
        "Takeout/YouTube and YouTube Music/playlists/Liked Music.csv": playlist,
    })
    entries = takeout.parse_takeout(zip_bytes, "takeout.zip")
    by_id = {e.video_id: e for e in entries}
    assert len(by_id) == 2
    merged = by_id[VID1]
    assert merged.artist == "Rick Astley" and merged.title == "Never Gonna Give You Up"
    assert merged.added_at == datetime(2024, 5, 5, tzinfo=timezone.utc)


def test_zip_foreign_content_raises_empty():
    zip_bytes = _takeout_zip({
        "Takeout/Chrome/bookmarks.csv": b"url,name\nhttp://x,y\n",
        "readme.txt": b"hello",
    })
    with pytest.raises(takeout.TakeoutEmpty):
        takeout.parse_takeout(zip_bytes, "takeout.zip")


def test_corrupted_zip_raises_human_error():
    with pytest.raises(takeout.TakeoutError, match="повреждён"):
        takeout.parse_takeout(b"PK\x03\x04garbage-not-a-zip", "takeout.zip")


def test_zip_bomb_declared_size(monkeypatch):
    """Суммарный распакованный размер нужных CSV ограничен (защита от zip-bomb)."""
    monkeypatch.setattr(takeout, "MAX_TOTAL_UNCOMPRESSED", 1024)
    big_csv = ("Video ID,Timestamp\n" + f"{VID1},2024-01-01T00:00:00+00:00\n" * 200).encode()
    assert len(big_csv) > 1024
    zip_bytes = _takeout_zip({"Takeout/YT/playlists/big.csv": big_csv})
    with pytest.raises(takeout.TakeoutTooLarge):
        takeout.parse_takeout(zip_bytes, "takeout.zip")


def test_zip_bomb_cumulative_budget(monkeypatch):
    """Лимит суммарный: несколько файлов по отдельности меньше лимита."""
    monkeypatch.setattr(takeout, "MAX_TOTAL_UNCOMPRESSED", 2048)
    row = f"{VID1},2024-01-01T00:00:00+00:00\n"
    csv_1kb = ("Video ID,Timestamp\n" + row * 30).encode()
    assert 1024 < len(csv_1kb) < 2048
    zip_bytes = _takeout_zip({
        "Takeout/YT/playlists/a.csv": csv_1kb,
        "Takeout/YT/playlists/b.csv": csv_1kb,
    })
    with pytest.raises(takeout.TakeoutTooLarge):
        takeout.parse_takeout(zip_bytes, "takeout.zip")


def test_zip_reads_only_csv_members(monkeypatch):
    """Не-CSV члены не читаются вовсе — их размер не тратит бюджет распаковки."""
    monkeypatch.setattr(takeout, "MAX_TOTAL_UNCOMPRESSED", 4096)
    huge_json = b"x" * 100_000  # больше бюджета, но это .json — не читаем
    playlist = f"Video ID,Timestamp\n{VID1},2024-01-01T00:00:00+00:00\n".encode()
    zip_bytes = _takeout_zip({
        "Takeout/YT/история/watch-history.json": huge_json,
        "Takeout/YT/плейлисты/likes.csv": playlist,
    })
    entries = takeout.parse_takeout(zip_bytes, "takeout.zip")
    assert [e.video_id for e in entries] == [VID1]


# --- одиночный CSV и выбор «последних по времени» -------------------------------

def test_single_csv_upload():
    entries = takeout.parse_takeout(LIBRARY_CSV_EN, "music-library-songs.csv")
    assert len(entries) == 2


def test_empty_file_raises():
    with pytest.raises(takeout.TakeoutEmpty):
        takeout.parse_takeout(b"", "x.csv")


def test_select_latest_freshest_first():
    def entry(vid, ts):
        return takeout.RawEntry(video_id=vid, added_at=ts)

    e_old = entry("old00000000", datetime(2020, 1, 1, tzinfo=timezone.utc))
    e_new = entry("new00000000", datetime(2025, 1, 1, tzinfo=timezone.utc))
    e_mid = entry("mid00000000", datetime(2023, 1, 1, tzinfo=timezone.utc))
    e_none = entry("none0000000", None)  # library-строки без времени — в конце

    picked = takeout.select_latest([e_old, e_none, e_new, e_mid], limit=3)
    assert [e.video_id for e in picked] == ["new00000000", "mid00000000", "old00000000"]

    picked_all = takeout.select_latest([e_old, e_none, e_new, e_mid], limit=10)
    assert picked_all[-1].video_id == "none0000000"
