"""POST /api/preview/resolve (SPEC v0.6 §A2): превью для «слушать всё».

Дорезолвинг preview_url для строк портрета, у которых его нет (старые
кэш-записи, сохранённые до v0.6): для каждого трека — сначала кэш, иначе
живой превью-каскад (engine.previews с его троттлингом Deezer/iTunes)
с дозаписью найденного URL в кэш. Не найдено — null (и НЕ кэшируется:
завтра трек может появиться в Deezer).

Батч ограничен 24 элементами: фронт шлёт только видимые строки, а живой
каскад в худшем случае (всё мимо Deezer -> iTunes с интервалом 3.1 с)
держит синхронный запрос ~24 × 3-7 с — больше держать HTTP нельзя.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.engine import cache, previews

router = APIRouter(prefix="/api", tags=["preview"])

MAX_BATCH = 24


class PreviewItem(BaseModel):
    artist: str
    title: str


class PreviewResolveRequest(BaseModel):
    items: list[PreviewItem] = Field(min_length=1, max_length=MAX_BATCH)


@router.post("/preview/resolve")
def resolve_previews(req: PreviewResolveRequest) -> dict:
    """{items: [{artist, title}]} -> {urls: [str|null]} (позиции совпадают)."""
    urls: list[str | None] = []
    for item in req.items:
        key = cache.key_for(None, item.artist, item.title)
        url = cache.get_preview_url(key)
        if url is None:
            url = previews.match_track_to_preview(None, item.artist, item.title)
            if url:
                cache.set_preview_url(key, url)  # дозапись для старых записей
        urls.append(url)
    return {"urls": urls}
