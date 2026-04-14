"""
Reports & analytics para supervisores/admin.

Consulta directa a Postgres (Supabase) sobre las tablas:
  - conversations (status, context.closing_note, created_at, updated_at)
  - messages       (role, created_at)

Endpoints:
  GET /admin/reports/overview?days=7       — KPIs top (totales, cerradas, ventas, conversión)
  GET /admin/reports/leaderboard?days=7    — ranking de agentes por cierres/ventas
  GET /admin/reports/categories?days=7     — counts por categoría de closing_note
  GET /admin/reports/timeline?days=14      — cierres por día (bar chart)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
import structlog

from api.auth import require_role
from memory import postgres_store

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin/reports", tags=["reports"])


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=max(1, days))


@router.get("/overview")
async def overview(
    days: int = Query(7, ge=1, le=365),
    user: dict = Depends(require_role("admin", "supervisor")),
):
    """KPIs principales del rango (tarjetas arriba del dashboard)."""
    if not postgres_store.is_enabled():
        raise HTTPException(503, "Postgres no configurado")

    since = _since(days)
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "select count(*) from public.conversations where updated_at >= $1", since,
        )
        closed = await conn.fetchval(
            "select count(*) from public.conversations where status = 'closed' and updated_at >= $1",
            since,
        )
        won = await conn.fetchval(
            """select count(*) from public.conversations
               where status = 'closed' and updated_at >= $1
                 and context->'closing_note'->>'category' = 'venta_cerrada'""",
            since,
        )
        lost = await conn.fetchval(
            """select count(*) from public.conversations
               where status = 'closed' and updated_at >= $1
                 and context->'closing_note'->>'category' = 'descartado'""",
            since,
        )
        # Mensajes totales en el rango (nuestro + cliente)
        msgs = await conn.fetchval(
            "select count(*) from public.messages where created_at >= $1", since,
        )
        # Conversaciones abiertas ahora (no cerradas)
        open_now = await conn.fetchval(
            "select count(*) from public.conversations where status != 'closed'",
        )

    conv_rate = (won / closed * 100.0) if closed else 0.0

    return {
        "days": days,
        "total": total or 0,
        "closed": closed or 0,
        "won": won or 0,
        "lost": lost or 0,
        "open_now": open_now or 0,
        "messages": msgs or 0,
        "conversion_rate": round(conv_rate, 1),
    }


@router.get("/leaderboard")
async def leaderboard(
    days: int = Query(7, ge=1, le=365),
    user: dict = Depends(require_role("admin", "supervisor")),
):
    """Ranking de agentes por total de cierres y ventas cerradas."""
    if not postgres_store.is_enabled():
        raise HTTPException(503, "Postgres no configurado")

    since = _since(days)
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            select
              coalesce(context->'closing_note'->>'closed_by', 'Sin asignar') as agent,
              count(*)                                                       as closed,
              count(*) filter (where context->'closing_note'->>'category' = 'venta_cerrada') as won,
              count(*) filter (where context->'closing_note'->>'category' = 'descartado')   as lost,
              count(*) filter (where context->'closing_note'->>'category' = 'soporte_resuelto') as resolved
            from public.conversations
            where status = 'closed'
              and updated_at >= $1
              and context ? 'closing_note'
            group by 1
            order by closed desc
            limit 50
            """,
            since,
        )
    data = []
    for r in rows:
        closed = r["closed"] or 0
        won = r["won"] or 0
        data.append({
            "agent": r["agent"],
            "closed": closed,
            "won": won,
            "lost": r["lost"] or 0,
            "resolved": r["resolved"] or 0,
            "conversion_rate": round((won / closed * 100.0) if closed else 0.0, 1),
        })
    return {"days": days, "rows": data}


@router.get("/categories")
async def categories(
    days: int = Query(7, ge=1, le=365),
    user: dict = Depends(require_role("admin", "supervisor")),
):
    """Breakdown por categoría de closing_note."""
    if not postgres_store.is_enabled():
        raise HTTPException(503, "Postgres no configurado")

    since = _since(days)
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            select
              coalesce(context->'closing_note'->>'category', 'sin_categoria') as category,
              count(*) as total
            from public.conversations
            where status = 'closed' and updated_at >= $1
            group by 1
            order by total desc
            """,
            since,
        )
    return {"days": days, "rows": [{"category": r["category"], "total": r["total"]} for r in rows]}


@router.get("/timeline")
async def timeline(
    days: int = Query(14, ge=1, le=180),
    user: dict = Depends(require_role("admin", "supervisor")),
):
    """Cierres por día (bar chart). Días sin cierres incluidos con 0."""
    if not postgres_store.is_enabled():
        raise HTTPException(503, "Postgres no configurado")

    since = _since(days)
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            select
              date_trunc('day', updated_at at time zone 'America/Argentina/Buenos_Aires') as day,
              count(*)                                                                    as closed,
              count(*) filter (where context->'closing_note'->>'category' = 'venta_cerrada') as won
            from public.conversations
            where status = 'closed' and updated_at >= $1
            group by 1
            order by 1 asc
            """,
            since,
        )
    # Fill gaps (días sin actividad)
    by_day = {r["day"].date().isoformat(): {"closed": r["closed"], "won": r["won"]} for r in rows}
    out = []
    cur = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    today = datetime.now(timezone.utc).date()
    while cur <= today:
        key = cur.isoformat()
        entry = by_day.get(key, {"closed": 0, "won": 0})
        out.append({"day": key, "closed": entry["closed"], "won": entry["won"]})
        cur += timedelta(days=1)
    return {"days": days, "rows": out}
