"""
Lifecycle stages — pipeline visual estilo CRM.

Cada conversación puede estar en un stage (New Lead → Hot → Payment → Customer).
Los stages son editables por admin/supervisor. Los agentes mueven convs
entre stages con drag-and-drop.
"""
from __future__ import annotations

from uuid import UUID, uuid4
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import structlog

from api.auth import get_current_user, require_role
from memory import postgres_store

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/lifecycle", tags=["lifecycle"])


DEFAULT_STAGES = [
    {"key": "new_lead",      "label": "New Lead",     "emoji": "🌱", "color": "#60a5fa", "position": 0, "is_lost": False, "is_won": False},
    {"key": "contacted",     "label": "Contactado",   "emoji": "👋", "color": "#6366f1", "position": 1, "is_lost": False, "is_won": False},
    {"key": "hot_lead",      "label": "Hot Lead",     "emoji": "🔥", "color": "#f59e0b", "position": 2, "is_lost": False, "is_won": False},
    {"key": "payment",       "label": "Payment",      "emoji": "💳", "color": "#a78bfa", "position": 3, "is_lost": False, "is_won": False},
    {"key": "customer",      "label": "Customer",     "emoji": "⭐", "color": "#10b981", "position": 4, "is_lost": False, "is_won": True},
    {"key": "cold_lead",     "label": "Cold Lead",    "emoji": "❄️", "color": "#6b7280", "position": 5, "is_lost": True, "is_won": False},
    {"key": "lost",          "label": "Perdido",      "emoji": "✕",  "color": "#ef4444", "position": 6, "is_lost": True, "is_won": False},
]


async def ensure_default_stages() -> None:
    """Sembra los stages default si la tabla está vacía."""
    if not postgres_store.is_enabled():
        return
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval("select count(*) from public.lifecycle_stages")
        if count and count > 0:
            return
        for s in DEFAULT_STAGES:
            await conn.execute(
                """insert into public.lifecycle_stages
                   (id, key, label, emoji, color, position, is_lost, is_won)
                   values ($1, $2, $3, $4, $5, $6, $7, $8)
                   on conflict (key) do nothing""",
                uuid4(), s["key"], s["label"], s["emoji"], s["color"],
                s["position"], s["is_lost"], s["is_won"],
            )
        logger.info("lifecycle_stages_seeded")


class StageIn(BaseModel):
    key: str
    label: str
    emoji: str = "📍"
    color: str = "#6366f1"
    position: int = 0
    is_lost: bool = False
    is_won: bool = False


class MoveIn(BaseModel):
    stage_key: str


@router.get("/stages")
async def list_stages(user: dict = Depends(get_current_user)):
    """Devuelve todos los stages ordenados por position + counts por stage."""
    if not postgres_store.is_enabled():
        return {"stages": []}
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        stages = await conn.fetch(
            "select id, key, label, emoji, color, position, is_lost, is_won "
            "from public.lifecycle_stages order by position, label"
        )
        counts = await conn.fetch(
            "select stage_key, count(*)::int as n from public.conversation_stage group by stage_key"
        )
    counts_map = {r["stage_key"]: r["n"] for r in counts}
    return {
        "stages": [
            {
                "id": str(s["id"]), "key": s["key"], "label": s["label"],
                "emoji": s["emoji"], "color": s["color"], "position": s["position"],
                "is_lost": s["is_lost"], "is_won": s["is_won"],
                "count": counts_map.get(s["key"], 0),
            }
            for s in stages
        ]
    }


@router.get("/by-stage/{stage_key}")
async def list_by_stage(stage_key: str, user: dict = Depends(get_current_user)):
    """Conversaciones en un stage, con metadata básica para el Kanban."""
    if not postgres_store.is_enabled():
        raise HTTPException(503, "Postgres no configurado")
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            select c.id, c.external_id, c.channel, c.status,
                   c.user_profile->>'name' as name,
                   c.user_profile->>'phone' as phone,
                   c.user_profile->>'email' as email,
                   c.updated_at, cs.changed_at
            from public.conversation_stage cs
            join public.conversations c on c.id = cs.conversation_id
            where cs.stage_key = $1
            order by cs.changed_at desc
            limit 200
            """,
            stage_key,
        )
    return {
        "stage_key": stage_key,
        "conversations": [
            {
                "id": str(r["id"]),
                "external_id": r["external_id"],
                "channel": r["channel"],
                "status": r["status"],
                "name": r["name"] or "",
                "phone": r["phone"] or "",
                "email": r["email"] or "",
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                "stage_changed_at": r["changed_at"].isoformat() if r["changed_at"] else None,
            }
            for r in rows
        ],
    }


@router.post("/move/{conversation_id}")
async def move_to_stage(
    conversation_id: str,
    req: MoveIn,
    user: dict = Depends(get_current_user),
):
    """Mueve una conversación a un stage (upsert)."""
    if not postgres_store.is_enabled():
        raise HTTPException(503, "Postgres no configurado")
    try:
        conv_uuid = UUID(conversation_id)
    except ValueError:
        raise HTTPException(400, "ID de conversación inválido")

    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        # Verify stage exists
        exists = await conn.fetchval(
            "select 1 from public.lifecycle_stages where key = $1", req.stage_key
        )
        if not exists:
            raise HTTPException(404, f"Stage '{req.stage_key}' no existe")
        await conn.execute(
            """insert into public.conversation_stage (conversation_id, stage_key, changed_by, changed_at)
               values ($1, $2, $3, now())
               on conflict (conversation_id) do update set
                 stage_key = excluded.stage_key,
                 changed_by = excluded.changed_by,
                 changed_at = now()""",
            conv_uuid, req.stage_key, user.get("email", ""),
        )
    logger.info("lifecycle_move", conversation_id=conversation_id, stage=req.stage_key, user=user.get("email"))
    return {"ok": True, "stage_key": req.stage_key}


@router.post("/stages")
async def create_stage(req: StageIn, user: dict = Depends(require_role("admin"))):
    if not postgres_store.is_enabled():
        raise HTTPException(503, "Postgres no configurado")
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """insert into public.lifecycle_stages
                   (id, key, label, emoji, color, position, is_lost, is_won)
                   values ($1, $2, $3, $4, $5, $6, $7, $8)""",
                uuid4(), req.key, req.label, req.emoji, req.color,
                req.position, req.is_lost, req.is_won,
            )
        except Exception as e:
            if "unique" in str(e).lower():
                raise HTTPException(409, f"Ya existe un stage con key '{req.key}'")
            raise
    return {"ok": True}


@router.delete("/stages/{key}")
async def delete_stage(key: str, user: dict = Depends(require_role("admin"))):
    if not postgres_store.is_enabled():
        raise HTTPException(503, "Postgres no configurado")
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("delete from public.lifecycle_stages where key = $1", key)
    return {"ok": True}
