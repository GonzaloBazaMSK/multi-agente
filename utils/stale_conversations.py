"""
Cron: detectar conversaciones asignadas sin respuesta humana > 2h.

Corre cada 15 min via APScheduler (`utils/scheduler.py`). Para cada conv
detectada, dispara una notif `conv_stale` al agente asignado (si tiene
ese tipo habilitado en `notification_preferences`).

Anti-spam: un lock en Redis `stale_notif:{conv_id}` con TTL 4h. Mientras
ese lock esté vivo, no disparamos otra notif para la misma conv. Así el
agente recibe una sola alerta cada 4h por conv, no una cada 15 min.

Heurística de "sin respuesta":
    - conv con assigned_agent_id != NULL y status IN (open, pending)
    - el ÚLTIMO mensaje de la conv es de rol 'user' (cliente escribió,
      nadie respondió)
    - ese mensaje fue hace > 2h y < 24h (no perseguimos convs viejísimas
      que quizás fueron abandonadas a propósito)
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from memory import postgres_store

logger = structlog.get_logger(__name__)


STALE_THRESHOLD_HOURS = 2
IGNORE_OLDER_THAN_HOURS = 24
DEDUP_TTL_SECONDS = 4 * 3600  # 4h


async def run_stale_conversations_check() -> int:
    """Dispara notifs `conv_stale` a agentes con convs sin respuesta > 2h.

    Devuelve cuántas notifs se enviaron efectivamente (excluyendo las
    que se saltearon por dedup).
    """
    pool = await postgres_store.get_pool()

    async with pool.acquire() as conn:
        # DISTINCT ON nos da el último msg por conversación. Filtramos:
        #   - tiene agente asignado
        #   - status abierto/pendiente
        #   - último msg es del user
        #   - ventana [2h .. 24h] desde que se mandó el último msg
        rows = await conn.fetch(
            """
            with last_msg as (
                select distinct on (conversation_id)
                    conversation_id, role, created_at
                from public.messages
                order by conversation_id, created_at desc
            )
            select
                cm.conversation_id::text as conv_id,
                cm.assigned_agent_id     as agent_id,
                c.user_profile           as user_profile,
                c.external_id            as external_id,
                lm.created_at            as last_msg_at
            from public.conversation_meta cm
            join last_msg lm on lm.conversation_id = cm.conversation_id
            join public.conversations c on c.id = cm.conversation_id
            where cm.assigned_agent_id is not null
              and cm.status in ('open', 'pending')
              and lm.role = 'user'
              and lm.created_at < now() - interval '%s hours'
              and lm.created_at > now() - interval '%s hours'
            """
            % (STALE_THRESHOLD_HOURS, IGNORE_OLDER_THAN_HOURS)
        )

    if not rows:
        logger.debug("stale_check_no_candidates")
        return 0

    # Redis handle para el dedup lock. Si no hay Redis, skipeamos todo
    # (no queremos spamear).
    try:
        from memory.conversation_store import get_conversation_store

        store = await get_conversation_store()
        redis = store._redis
    except Exception as e:
        logger.warning("stale_check_no_redis", error=str(e))
        return 0

    from utils.notifications import notify

    now = datetime.now(timezone.utc)
    notified = 0

    for r in rows:
        conv_id = r["conv_id"]
        agent_id = r["agent_id"]
        last_msg_at = r["last_msg_at"]
        if last_msg_at.tzinfo is None:
            last_msg_at = last_msg_at.replace(tzinfo=timezone.utc)

        # SET NX EX — gana solo si la key no existe. Evita que dos workers
        # ejecuten el mismo job y disparen dos notifs a la vez.
        key = f"stale_notif:{conv_id}"
        try:
            locked = await redis.set(key, "1", nx=True, ex=DEDUP_TTL_SECONDS)
        except Exception as e:
            logger.debug("stale_dedup_lock_failed", conv_id=conv_id, error=str(e))
            continue
        if not locked:
            continue  # ya hay notif reciente para esta conv

        user_profile = r["user_profile"] or {}
        if isinstance(user_profile, str):
            # asyncpg puede devolverlo como str en algunas versiones
            try:
                import json

                user_profile = json.loads(user_profile)
            except Exception:
                user_profile = {}

        client_name = (
            user_profile.get("name")
            or user_profile.get("full_name")
            or r["external_id"]
            or "cliente"
        )
        mins = int((now - last_msg_at).total_seconds() / 60)

        await notify(
            agent_id,
            "conv_stale",
            {
                "conversation_id": conv_id,
                "client_name": client_name,
                "minutes_since_last_msg": mins,
            },
        )
        notified += 1

    logger.info(
        "stale_check_done",
        candidates=len(rows),
        notified=notified,
        skipped_dedup=len(rows) - notified,
    )
    return notified
