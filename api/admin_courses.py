"""
Admin endpoints para el catálogo de cursos sincronizado desde el WP.

- POST /admin/courses/sync          → sincroniza todos los países habilitados
- POST /admin/courses/sync/{country} → sincroniza un país puntual (AR, MX, …)
- GET  /admin/courses?country=ar    → lista cursos de un país (hot columns)
- GET  /admin/courses/{country}/{slug} → detalle (incluye brief_md)
- GET  /admin/courses/{country}/{slug}/raw → JSON completo original del WP

Protegido con la misma API key (`X-Admin-Key`) que /admin/*.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
import structlog

from api.admin import verify_admin_key
from integrations import msk_courses, courses_cache
from memory import postgres_store

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/courses", tags=["admin-courses"])

# Todos los países habilitados para sync
ENABLED_COUNTRIES: list[str] = [
    "ar", "mx", "cl", "co", "ec", "pe", "es",
    "bo", "cr", "gt", "hn", "ni", "pa", "py", "sv", "uy", "ve",
]


@router.post("/sync")
async def sync_all(
    prune: bool = Query(True, description="Borrar slugs discontinuados"),
    key: str = Depends(verify_admin_key),
):
    """Sincroniza todos los países en `ENABLED_COUNTRIES`."""
    results = []
    for c in ENABLED_COUNTRIES:
        try:
            r = await msk_courses.sync_country(c, prune=prune)
            results.append(r)
        except Exception as e:
            logger.exception("course_sync_failed", country=c)
            results.append({"country": c, "error": str(e)})
    return {"status": "ok", "results": results}


@router.post("/sync/{country}")
async def sync_one(
    country: str,
    prune: bool = Query(True),
    key: str = Depends(verify_admin_key),
):
    country = country.lower()
    if country not in msk_courses.LANG_BY_COUNTRY:
        raise HTTPException(status_code=400, detail=f"Unknown country: {country}")
    try:
        r = await msk_courses.sync_country(country, prune=prune)
        return {"status": "ok", **r}
    except Exception as e:
        logger.exception("course_sync_failed", country=country)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
async def list_courses(
    country: str = Query(..., description="ISO-2 country code"),
    limit: int = Query(200, ge=1, le=1000),
    key: str = Depends(verify_admin_key),
):
    rows = await postgres_store.list_courses(country.lower(), limit=limit)
    return {"country": country.lower(), "count": len(rows), "courses": rows}


@router.get("/{country}/{slug}")
async def get_course(country: str, slug: str, key: str = Depends(verify_admin_key)):
    row = await courses_cache.get_course(country, slug)
    if not row:
        raise HTTPException(status_code=404, detail="course not found")
    return row


@router.get("/{country}/{slug}/raw")
async def get_course_raw(country: str, slug: str, key: str = Depends(verify_admin_key)):
    """Devuelve el JSON crudo del WP tal como se guardó en la última sync."""
    row = await postgres_store.get_course(country, slug)
    if not row:
        raise HTTPException(status_code=404, detail="course not found")
    return row.get("raw") or {}
