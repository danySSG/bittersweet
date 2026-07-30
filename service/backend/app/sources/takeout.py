"""Источник «выгрузка Google Takeout (YouTube Music)» — zip или одиночный CSV.

Факты о формате (SPEC-v02b, живое исследование 2026-07-10):
- имена папок И заголовки CSV локализуются (нем. «YouTube und YouTube Music»,
  zh-TW заголовок «影片 ID,播放清單影片的建立時間戳記») → нужные CSV определяем
  ПО СОДЕРЖИМОМУ, а колонки — СТРОГО ПОЗИЦИОННО, не по именам;
- music-library-songs.csv — единственный файл с готовыми метаданными:
  4 колонки (Song URL, Song Title, Album Title, Artist Names) →
  col0 = URL с v= (video_id), col1 = title, col3 = artist;
- пер-плейлистные CSV — 2 колонки: (Video ID, ISO-8601 timestamp), названий НЕТ;
- playlists.csv (индекс плейлистов) не содержит video id — его строки
  не проходят валидацию колонки 0 и отбрасываются сами собой.

Валидация строки: колонка 0 обязана быть 11-символьным YouTube ID
(``^[A-Za-z0-9_-]{11}$``) ИЛИ URL с параметром v=. Всё остальное — мусор.

zip-safety: из архива читаем ТОЛЬКО *.csv (watch-history.json и прочее
не трогаем), суммарный размер РАСПАКОВАННЫХ прочитанных файлов ограничен
MAX_TOTAL_UNCOMPRESSED (защита от zip-bomb, лимит проверяется и по
заявленному размеру, и по факту при чтении).
"""

from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)

# 11-символьный YouTube video id (спека: строгая позиционная валидация)
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
# ISO-8601-подобное начало: 2024-05-01T12:34 / 2024-05-01 12:34
_ISO_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 413 при превышении (спека)
MAX_TOTAL_UNCOMPRESSED = 50 * 1024 * 1024  # суммарный лимит распаковки csv
_READ_CHUNK = 1 << 20  # 1 МБ


class TakeoutError(Exception):
    """Базовая ошибка разбора выгрузки Takeout (человеческое сообщение)."""


class TakeoutEmpty(TakeoutError):
    """В выгрузке не нашлось ни одного музыкального трека."""


class TakeoutTooLarge(TakeoutError):
    """Распакованное содержимое больше лимита (zip-bomb или не-музыкальная выгрузка)."""


@dataclass
class RawEntry:
    """Одна строка музыкального CSV. artist/title есть только у library-songs."""

    video_id: str
    artist: str | None = None
    title: str | None = None
    added_at: datetime | None = None
    source_file: str = ""


def entry_to_dict(entry: RawEntry) -> dict:
    """RawEntry -> JSON-сериализуемый dict (params_json долговечных джоб)."""
    return {
        "video_id": entry.video_id,
        "artist": entry.artist,
        "title": entry.title,
        "added_at": entry.added_at.isoformat() if entry.added_at else None,
        "source_file": entry.source_file,
    }


def entry_from_dict(data: dict) -> RawEntry:
    """Обратно из params_json джобы (resume-on-startup)."""
    added_at = data.get("added_at")
    return RawEntry(
        video_id=data["video_id"],
        artist=data.get("artist"),
        title=data.get("title"),
        added_at=datetime.fromisoformat(added_at) if added_at else None,
        source_file=data.get("source_file") or "",
    )


def extract_video_id(cell: str) -> str | None:
    """11-символьный ID из ячейки: чистый ID или любой URL с v=<id>. Иначе None."""
    cell = (cell or "").strip()
    if not cell:
        return None
    if VIDEO_ID_RE.match(cell):
        return cell
    if "//" in cell or cell.startswith(("http://", "https://", "www.")):
        url = cell if "://" in cell else f"https://{cell}"
        qs = parse_qs(urlparse(url).query)
        candidate = (qs.get("v") or [""])[0].strip()
        if VIDEO_ID_RE.match(candidate):
            return candidate
    return None


def _parse_timestamp(cell: str) -> datetime | None:
    """ISO-8601 ячейка -> datetime (Z-суффикс тоже понимаем). Иначе None."""
    cell = (cell or "").strip()
    if not _ISO_TS_RE.match(cell):
        return None
    try:
        return datetime.fromisoformat(cell.replace("Z", "+00:00"))
    except ValueError:
        return None


def _sniff_delimiter(text: str) -> str:
    """',' или ';' — по первой непустой строке (в природе встречаются оба)."""
    for line in text.splitlines():
        if line.strip():
            return ";" if line.count(";") > line.count(",") else ","
    return ","


def parse_csv(data: bytes, source_file: str = "") -> list[RawEntry]:
    """Один CSV -> список RawEntry. Позиционно, локале-независимо.

    Первая строка считается заголовком и пропускается, ЕСЛИ её колонка 0
    не валидируется как video id (защита от headerless-файлов).
    Не-музыкальные CSV (playlists.csv-индекс, subscriptions и т.п.)
    просто дают 0 строк — их колонка 0 не проходит валидацию.
    """
    text = data.decode("utf-8-sig", errors="replace")  # utf-8-sig съедает BOM
    delimiter = _sniff_delimiter(text)
    entries: list[RawEntry] = []
    first_row = True
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        cells = [c.strip() for c in row]
        if not any(cells):
            continue  # пустые строки в природе встречаются
        video_id = extract_video_id(cells[0])
        if first_row:
            first_row = False
            if video_id is None:
                continue  # обычный заголовок (EN/RU/zh-TW — неважно)
        if video_id is None:
            continue  # мусорная/служебная строка
        added_at = None
        for cell in cells[1:]:
            added_at = _parse_timestamp(cell)
            if added_at is not None:
                break
        title = artist = None
        if len(cells) >= 4:
            # library-songs-формат: col1 = title, col3 = artist (позиционно)
            if cells[1] and _parse_timestamp(cells[1]) is None:
                title = cells[1]
            if cells[3] and _parse_timestamp(cells[3]) is None:
                artist = cells[3]
        entries.append(RawEntry(
            video_id=video_id, artist=artist, title=title,
            added_at=added_at, source_file=source_file,
        ))
    return entries


def _read_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, budget: int) -> bytes:
    """Читает член архива, не доверяя заявленному размеру (лимит по факту)."""
    chunks: list[bytes] = []
    read = 0
    with zf.open(info) as fh:
        while True:
            chunk = fh.read(_READ_CHUNK)
            if not chunk:
                break
            read += len(chunk)
            if read > budget:
                raise TakeoutTooLarge(
                    "распакованные CSV больше 50 МБ — похоже на некорректный архив; "
                    "выберите в Takeout только музыкальную библиотеку и плейлисты"
                )
            chunks.append(chunk)
    return b"".join(chunks)


def _parse_zip(data: bytes) -> list[RawEntry]:
    """Все *.csv из архива (только их — zip-safety) -> RawEntry."""
    entries: list[RawEntry] = []
    budget = MAX_TOTAL_UNCOMPRESSED
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".csv"):
                    continue  # читаем ТОЛЬКО csv: history/*.json и прочее не распаковываем
                if info.file_size > budget:
                    raise TakeoutTooLarge(
                        "распакованные CSV больше 50 МБ — похоже на некорректный архив; "
                        "выберите в Takeout только музыкальную библиотеку и плейлисты"
                    )
                raw = _read_member(zf, info, budget)
                budget -= len(raw)
                entries.extend(parse_csv(raw, source_file=info.filename))
    except zipfile.BadZipFile as e:
        raise TakeoutError("не удалось открыть zip — файл повреждён или это не архив") from e
    except (RuntimeError, NotImplementedError) as e:  # зашифрованный/экзотика
        raise TakeoutError(f"не удалось прочитать архив: {e}") from e
    return entries


def merge_entries(entries: list[RawEntry]) -> list[RawEntry]:
    """Дедуп по video_id: метаданные — от library-строк (приоритет «песни
    библиотеки» — единственные с artist/title), added_at — самый свежий."""
    merged: dict[str, RawEntry] = {}
    for e in entries:
        cur = merged.get(e.video_id)
        if cur is None:
            merged[e.video_id] = e
            continue
        if e.artist and not cur.artist:
            cur.artist = e.artist
        if e.title and not cur.title:
            cur.title = e.title
        if e.added_at and (cur.added_at is None or e.added_at > cur.added_at):
            cur.added_at = e.added_at
    return list(merged.values())


def select_latest(entries: list[RawEntry], limit: int) -> list[RawEntry]:
    """ПОСЛЕДНИЕ по времени добавления limit треков (свежие лайки интереснее);
    строки без timestamp (library-songs) идут после датированных, в порядке файла."""
    ordered = sorted(
        entries,
        key=lambda e: e.added_at.timestamp() if e.added_at else float("-inf"),
        reverse=True,
    )
    return ordered[:limit]


def parse_takeout(data: bytes, filename: str = "") -> list[RawEntry]:
    """Выгрузка (zip ИЛИ одиночный csv) -> дедуплицированные RawEntry.

    Кидает TakeoutError-наследников с человеческими сообщениями:
    TakeoutEmpty (пустой/чужой файл), TakeoutTooLarge (zip-bomb).
    """
    if not data:
        raise TakeoutEmpty("файл пустой — выгрузите Takeout заново")
    if zipfile.is_zipfile(io.BytesIO(data)):
        entries = _parse_zip(data)
    elif filename.lower().endswith(".zip"):
        raise TakeoutError("не удалось открыть zip — файл повреждён или это не архив")
    else:
        entries = parse_csv(data, source_file=filename or "upload.csv")
    entries = merge_entries(entries)
    if not entries:
        raise TakeoutEmpty(
            "в выгрузке не нашлось музыкальных треков — проверьте, что в Takeout "
            "выбраны «музыкальная библиотека» и «плейлисты» YouTube Music"
        )
    log.info("takeout: %d уникальных треков из «%s»", len(entries), filename or "csv")
    return entries
