"""Discovery «найти ещё такого» (SPEC v0.4 §A2, v0.9 §B): POST /api/discover.

Ставит discovery-джобу по сохранённому портрету и цели — выбранному кластеру
ИЛИ точке карты (x/y в перцентильных осях, ровно один из cluster/point);
состояние — общий GET /api/jobs/{job_id} (routes/analyze.py).
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field, model_validator

from app import jobs
from app.engine import discovery, store
from app.routes.analyze import job_conflict_response

router = APIRouter(prefix="/api", tags=["discover"])


class MapPoint(BaseModel):
    """Точка карты в осях портрета: x = valence-, y = energy-перцентиль."""

    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)


class DiscoverRequest(BaseModel):
    portrait_id: str
    cluster: int | None = Field(default=None, ge=0)  # индекс в clusters[] портрета
    point: MapPoint | None = None  # SPEC v0.9 §B: discovery по точке карты
    # потолок 25: каждый кандидат — потенциальный iTunes-запрос (троттлинг
    # 3.1 c, см. previews.py), 25*3 кандидатов в худшем случае — минуты.
    limit: int = Field(default=15, ge=1, le=25)

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "DiscoverRequest":
        """Ровно один из cluster/point — оба или ни одного дают 422."""
        if (self.cluster is None) == (self.point is None):
            raise ValueError("нужен ровно один из cluster или point")
        return self


@router.post("/discover", status_code=202)
def discover(req: DiscoverRequest, background_tasks: BackgroundTasks):
    """Ставит джобу «найти ещё такого» по кластеру ИЛИ точке карты. 202 {job_id}.

    404 — незнакомый portrait_id; 422 — кривой индекс кластера или не ровно
    один из cluster/point; 409 — портрет сохранён до v0.4 (в points нет
    videoId) — построить заново, ИЛИ рядом с точкой слишком мало треков,
    ЛИБО {detail, job_id} — по этому портрету уже идёт discovery (single-flight).
    """
    row = store.get_portrait(req.portrait_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"портрет {req.portrait_id} не найден")
    portrait = row["portrait"]
    clusters = portrait.get("clusters") or []
    if req.cluster is not None and req.cluster >= len(clusters):
        raise HTTPException(
            status_code=422,
            detail=f"в портрете нет кластера {req.cluster} (всего {len(clusters)})",
        )
    if not discovery.supports_discovery(portrait):
        raise HTTPException(
            status_code=409,
            detail="этот портрет сохранён старой версией — постройте его заново",
        )
    if req.point is not None:
        near = discovery.nearest_points(portrait, req.point.x, req.point.y)
        if len(near) < discovery.POINT_MIN_NEIGHBORS:
            raise HTTPException(status_code=409, detail=discovery.NOT_ENOUGH_NEAR_MSG)
    # owner discovery-джобы — portrait_id: один портрет — одна живая джоба
    existing = jobs.find_active_job("discovery", owner=req.portrait_id)
    if existing:
        return job_conflict_response(existing)
    point = req.point.model_dump() if req.point is not None else None
    params: dict = {"portrait_id": req.portrait_id, "limit": req.limit}
    if point is not None:
        params["point"] = point
    else:
        params["cluster"] = req.cluster
    job_id = jobs.create_job(
        stage="seeding",
        kind="discovery",
        params=params,
        owner=req.portrait_id,
    )
    background_tasks.add_task(
        jobs.run_discovery_job, job_id, req.portrait_id, req.cluster, req.limit, point
    )
    return {"job_id": job_id}
