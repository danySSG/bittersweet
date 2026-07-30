"""Сравнение вкусов: GET /api/compare?a={id}&b={id} (SPEC v0.3 §C)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.engine import compare as compare_engine
from app.engine import store

router = APIRouter(prefix="/api", tags=["compare"])


@router.get("/compare")
def compare(a: str, b: str) -> dict:
    """«Совместимость по звуку» двух сохранённых портретов; 404/422 на плохие id."""
    if a == b:
        raise HTTPException(status_code=422, detail="нужны два разных портрета (a == b)")
    row_a = store.get_portrait(a)
    row_b = store.get_portrait(b)
    missing = [pid for pid, row in ((a, row_a), (b, row_b)) if row is None]
    if missing:
        raise HTTPException(status_code=404, detail=f"портрет не найден: {', '.join(missing)}")
    return compare_engine.compare_portraits(row_a, row_b)
