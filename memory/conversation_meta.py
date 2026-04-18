"""
CRUD para public.conversation_meta — toda acción humana sobre conversaciones
(asignar, snooze, clasificar, marcar resuelta, tag, queue, pausar bot).

La tabla conversations queda intacta; meta es 1:1 opcional (LEFT JOIN).
"""
from __future__ import annotations

import json
from typing import Optional, Literal

import asyncpg
import structlog

from memory.postgres_store import get_pool

logger = structlog.get_logger(__name__)

ConvStatus = Literal["open", "pending", "resolved"]
LifecycleStage = Literal["new", "hot", "customer", "cold"]
Queue = Literal["sales", "billing", "post-sales"]


# ─── Reads ───────────────────────────────────────────────────────────────────

async def get_meta(conversation_id: str) -> Optional[dict]:
    """Devuelve la meta de una conversación, o None si no existe."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            select cm.*,
                   coalesce(cm.lifecycle_override, cm.lifecycle_auto, 'new') as lifecycle_effective
            from public.conversation_meta cm
            where conversation_id = $1
            """,
            conversation_id,
        )
    if not row:
        return None
    d = dict(row)
    return d


async def ensure_meta(conversation_id: str) -> dict:
    """Crea la meta vacía si no existe y la devuelve. Idempotente."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "select public.ensure_conversation_meta($1::uuid)",
            conversation_id,
        )
        row = await conn.fetchrow(
            """
            select cm.*,
                   coalesce(cm.lifecycle_override, cm.lifecycle_auto, 'new') as lifecycle_effective
            from public.conversation_meta cm
            where conversation_id = $1
            """,
            conversation_id,
        )
    return dict(row)


# Paleta de gradients tailwind para colorear avatares de forma estable por id
_AGENT_COLORS = [
    "from-pink-500 to-fuchsia-600",
    "from-blue-500 to-cyan-600",
    "from-emerald-500 to-teal-600",
    "from-amber-500 to-orange-600",
    "from-violet-500 to-purple-600",
    "from-rose-500 to-red-600",
    "from-sky-500 to-indigo-600",
    "from-lime-500 to-green-600",
]


def _derive_initials(name: str, email: str) -> str:
    n = (name or email or "?").strip()
    parts = [p for p in n.split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (n[:2] or "?").upper()


def _derive_color(seed: str) -> str:
    # Hash estable → mismo color para el mismo agente entre sesiones
    h = sum(ord(c) for c in (seed or ""))
    return _AGENT_COLORS[h % len(_AGENT_COLORS)]


async def list_agents() -> list[dict]:
    """
    Lista agentes humanos del workspace = profiles con role agente/supervisor/admin.
    Initials y color se derivan client-side determinísticamente para que el
    dropdown del inbox no necesite columnas cosméticas en profiles.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            select id::text as id, name, email, role, queues
            from public.profiles
            where role in ('admin', 'supervisor', 'agente')
            order by name nulls last, email
            """
        )
    out = []
    for r in rows:
        d = dict(r)
        out.append({
            "id": d["id"],
            "name": d["name"] or d["email"],
            "email": d["email"],
            "initials": _derive_initials(d.get("name") or "", d.get("email") or ""),
            "color": _derive_color(d["id"]),
            "active": True,
            "role": d["role"],
            "queues": d.get("queues") or [],
        })
    return out


# ─── Writes ──────────────────────────────────────────────────────────────────

async def assign(conversation_id: str, agent_id: Optional[str]) -> None:
    """Asignar (o quitar asignación con agent_id=None) a un agente humano."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("select public.ensure_conversation_meta($1::uuid)", conversation_id)
        if agent_id is None:
            await conn.execute(
                "update public.conversation_meta set assigned_agent_id=null, assigned_at=null where conversation_id=$1",
                conversation_id,
            )
        else:
            await conn.execute(
                """
                update public.conversation_meta
                set assigned_agent_id=$2::uuid, assigned_at=now()
                where conversation_id=$1
                """,
                conversation_id, agent_id,
            )


async def set_status(conversation_id: str, status: ConvStatus) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("select public.ensure_conversation_meta($1::uuid)", conversation_id)
        await conn.execute(
            "update public.conversation_meta set status=$2 where conversation_id=$1",
            conversation_id, status,
        )


async def classify(conversation_id: str, lifecycle: LifecycleStage) -> None:
    """Override manual del lifecycle (humano > bot)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("select public.ensure_conversation_meta($1::uuid)", conversation_id)
        await conn.execute(
            """
            update public.conversation_meta
            set lifecycle_override=$2, lifecycle_overridden_at=now()
            where conversation_id=$1
            """,
            conversation_id, lifecycle,
        )


async def set_lifecycle_auto(conversation_id: str, lifecycle: LifecycleStage) -> None:
    """El bot setea esto en cada turno. NO pisa el override humano."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("select public.ensure_conversation_meta($1::uuid)", conversation_id)
        await conn.execute(
            "update public.conversation_meta set lifecycle_auto=$2 where conversation_id=$1",
            conversation_id, lifecycle,
        )


async def set_queue(conversation_id: str, queue: Queue) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("select public.ensure_conversation_meta($1::uuid)", conversation_id)
        await conn.execute(
            "update public.conversation_meta set queue=$2 where conversation_id=$1",
            conversation_id, queue,
        )


async def set_bot_paused(conversation_id: str, paused: bool) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("select public.ensure_conversation_meta($1::uuid)", conversation_id)
        await conn.execute(
            """
            update public.conversation_meta
            set bot_paused=$2,
                bot_paused_at=case when $2 then now() else null end
            where conversation_id=$1
            """,
            conversation_id, paused,
        )


async def set_needs_human(conversation_id: str, needs: bool) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("select public.ensure_conversation_meta($1::uuid)", conversation_id)
        await conn.execute(
            "update public.conversation_meta set needs_human=$2 where conversation_id=$1",
            conversation_id, needs,
        )


async def add_tags(conversation_id: str, tags: list[str]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("select public.ensure_conversation_meta($1::uuid)", conversation_id)
        await conn.execute(
            """
            update public.conversation_meta
            set tags = (select array(select distinct unnest(tags || $2::text[])))
            where conversation_id=$1
            """,
            conversation_id, tags,
        )


async def remove_tag(conversation_id: str, tag: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "update public.conversation_meta set tags = array_remove(tags, $2) where conversation_id=$1",
            conversation_id, tag,
        )


# ─── Bulk ────────────────────────────────────────────────────────────────────

async def bulk_assign(conversation_ids: list[str], agent_id: Optional[str]) -> int:
    """Reasignar múltiples conversaciones a un agente. Devuelve filas afectadas."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Asegurar meta para todas
        await conn.executemany(
            "select public.ensure_conversation_meta($1::uuid)",
            [(cid,) for cid in conversation_ids],
        )
        if agent_id is None:
            r = await conn.execute(
                "update public.conversation_meta set assigned_agent_id=null, assigned_at=null where conversation_id = any($1::uuid[])",
                conversation_ids,
            )
        else:
            r = await conn.execute(
                """
                update public.conversation_meta
                set assigned_agent_id=$2::uuid, assigned_at=now()
                where conversation_id = any($1::uuid[])
                """,
                conversation_ids, agent_id,
            )
    try:
        return int(r.split()[-1])
    except Exception:
        return 0


async def bulk_set_status(conversation_ids: list[str], status: ConvStatus) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            "select public.ensure_conversation_meta($1::uuid)",
            [(cid,) for cid in conversation_ids],
        )
        r = await conn.execute(
            "update public.conversation_meta set status=$2 where conversation_id = any($1::uuid[])",
            conversation_ids, status,
        )
    try:
        return int(r.split()[-1])
    except Exception:
        return 0


# ─── Snooze removed ──────────────────────────────────────────────────────────
# La feature de snooze fue removida — no agregaba valor real al flujo y
# requería un cron de wake-up. Si volvés a necesitar postergar conversaciones,
# usá `set_status(conv_id, "pending")` y filtrá por status en el inbox.
# Migración 006 dropea las columnas snoozed_until/snoozed_at.
