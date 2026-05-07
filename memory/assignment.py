"""
Round-robin auto-assignment de conversaciones a agentes humanos.

Trabaja a nivel Redis (claves `conv_assigned:{session_id}`,
`agent_available:{user_id}`, `conv_queue:{session_id}`), no a nivel
Postgres (`conversation_meta.assigned_agent_id`). Es intencional: la
asignación automática pasa cuando el humano toma una conv desde el
widget/WhatsApp — la fuente es el session_id (hash/phone) no el UUID.

Cuando el inbox nuevo (UI Next.js) asigna explícitamente, usa
`conversation_meta.assign(conversation_id, agent_id)` que escribe en
Postgres. Son dos caminos complementarios; `broadcast_event` publica
desde ambos al mismo canal SSE.
"""

from __future__ import annotations

import structlog

from memory.conversation_store import get_conversation_store
from utils.realtime import broadcast_event

logger = structlog.get_logger(__name__)


async def auto_assign_round_robin(
    session_id: str,
    queue: str = "",
    conversation_id: str | None = None,
) -> dict | None:
    """Asigna la conversación al agente disponible con menos carga.

    Matching:
      - Agentes con `queues` vacío (admins/supervisores típicamente) =
        wildcard, reciben de cualquier cola.
      - Agentes con colas definidas = match EXACTO contra la cola de la
        conv (ej. `cobranzas_AR` no cubre `cobranzas_MX`).

    Args:
        session_id: Hash del widget o teléfono WhatsApp (Redis fast-path).
        queue: Cola específica (ej. "ventas_AR"). Si vacía, se resuelve
            desde `conv_queue:{session_id}` en Redis.
        conversation_id: UUID de la conversation (Postgres). Necesario para
            persistir la asignación en `conversation_meta.assigned_agent_id`.
            Si no se pasa, el inbox UI no verá la asignación (solo Redis).

    Returns:
        `{id, name, role, has_queue_match, is_online}` del agente asignado,
        o `None` si nadie disponible. Ya publicó `conv_assigned` al bus
        cuando retorna dict.
    """
    try:
        store = await get_conversation_store()
        r = store._redis

        # Resolver queue desde Redis si no vino explícita
        if not queue:
            try:
                _q = await r.get(f"conv_queue:{session_id}")
                queue = (_q.decode() if isinstance(_q, bytes) else _q) or ""
            except Exception:
                queue = ""

        from integrations.supabase_client import list_profiles

        profiles = await list_profiles()

        # Política B: asignar al agente con menos carga de la queue, esté
        # online o no. Cuando se conecte verá las conversaciones asignadas.
        # NO filtramos por agent_available — eso evita que las convs queden
        # huérfanas en horarios sin agentes activos.
        candidates: list[dict] = []
        for p in profiles:
            role = p.get("role")
            if role not in ("agente", "supervisor", "admin"):
                continue
            user_id = p.get("id") or p.get("email", "")
            agent_queues = p.get("queues") or []
            # Match estricto contra la queue de la conv (cobranzas_AR no cubre
            # cobranzas_MX). queues vacío = wildcard (ven todo, típico de admin).
            if queue and agent_queues and queue not in agent_queues:
                continue
            # Para preferir online cuando hay, leer el flag de Redis.
            status = await r.get(f"agent_available:{user_id}")
            status_str = (status.decode() if isinstance(status, bytes) else status) if status else ""
            is_online = status_str == "available"
            candidates.append(
                {
                    "id": user_id,
                    "name": p.get("name") or p.get("email", ""),
                    "role": role,
                    "has_queue_match": bool(agent_queues and queue in agent_queues),
                    "is_online": is_online,
                }
            )

        if not candidates:
            logger.warning(
                "auto_assign_no_agents_for_queue",
                session_id=session_id,
                queue=queue,
            )
            return None

        # Calcular carga de cada candidato (cantidad de convs ya asignadas).
        load: dict[str, int] = {}
        for agent in candidates:
            count = 0
            async for _k in r.scan_iter("conv_assigned:*", count=500):
                val = await r.get(_k)
                if val and (val.decode() if isinstance(val, bytes) else val) == agent["id"]:
                    count += 1
            load[agent["id"]] = count

        # Orden de preferencia (cada criterio es desempate del anterior):
        #   1. Match exacto de queue antes que wildcard.
        #   2. Online antes que offline.
        #   3. Menos carga primero.
        candidates.sort(
            key=lambda a: (
                0 if a["has_queue_match"] else 1,
                0 if a["is_online"] else 1,
                load.get(a["id"], 0),
            )
        )
        chosen = candidates[0]

        # Persistir asignación (30 días TTL en Redis para fast-path).
        await r.set(f"conv_assigned:{session_id}", chosen["id"], ex=86400 * 30)
        await r.set(f"conv_assigned_name:{session_id}", chosen["name"], ex=86400 * 30)

        # Persistir también en conversation_meta.assigned_agent_id (Postgres) —
        # sin esto el inbox UI muestra "Sin asignar" porque lee de la DB, no
        # del Redis fast-path. Necesita el conversation_id (UUID), NO el
        # session_id del widget (que es un sid distinto del conv_id).
        if conversation_id:
            try:
                from memory import conversation_meta as cm

                await cm.assign(conversation_id, chosen["id"])
            except Exception as _e:
                logger.warning(
                    "assign_pg_persist_failed",
                    conversation_id=conversation_id,
                    error=str(_e),
                )

        broadcast_event(
            {
                "type": "conv_assigned",
                "session_id": session_id,
                "agent_id": chosen["id"],
                "agent_name": chosen["name"],
                "queue": queue,
            }
        )
        logger.info(
            "auto_assigned",
            session_id=session_id,
            agent=chosen["name"],
            queue=queue,
            online=chosen["is_online"],
        )
        return chosen
    except Exception as e:
        logger.warning("auto_assign_failed", error=str(e))
        return None
