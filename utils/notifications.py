"""
Notificaciones in-app — backend helpers.

Flujo:
    evento ──► notify(user_id, type, data)
                 ├──► INSERT INTO public.notifications
                 ├──► publish Redis pubsub `notifications:{user_id}`
                 └──► (futuro) si user offline > 1h → queue email digest

Lado frontend:
    - SSE /api/v1/notifications/stream (filtra por user_id del token)
    - GET /api/v1/notifications (lista inicial)
    - POST /api/v1/notifications/{id}/read
    - POST /api/v1/notifications/mark-all-read
    - GET / PATCH /api/v1/notifications/preferences

Tipos soportados hoy (ver frontend/lib/notifications.ts para iconos/labels):
    - conv_assigned     → "Te asignaron una conversación"
    - new_message_mine  → "Nuevo mensaje de <cliente>"
    - conv_stale        → "Conversación sin respuesta hace 2h"
    - template_approved → "Plantilla HSM aprobada"

Para agregar un tipo nuevo:
    1) sumarlo a VALID_TYPES
    2) (opcional) agregar columna en notification_preferences + index
    3) llamar `await notify(user_id, "new_type", {...})` desde el punto de trigger
    4) frontend: agregar caso al switch en `notification-dropdown.tsx`
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import structlog

from memory.postgres_store import get_pool

logger = structlog.get_logger(__name__)


VALID_TYPES = {
    "conv_assigned",
    "new_message_mine",
    "conv_stale",
    "template_approved",
}

# Canal Redis pubsub — namespaced por user_id. Usamos un canal por user para
# que el SSE de un user NO reciba todas las notificaciones de todos los
# users (escalabilidad + privacidad).
def _pubsub_channel(user_id: str) -> str:
    return f"notifications:{user_id}"


DEFAULT_PREFERENCES = {
    "conv_assigned": True,
    "new_message_mine": True,
    "conv_stale": True,
    "template_approved": True,
    "sound_enabled": False,
    "email_digest": False,
}


# ──────────────────────────────────────────────────────────────────────────
# CREATE
# ──────────────────────────────────────────────────────────────────────────


async def notify(
    user_id: str,
    type: str,
    data: dict[str, Any] | None = None,
) -> str | None:
    """Crea una notificación y la publica por pubsub.

    Devuelve el `id` de la notif creada, o `None` si el user silenció ese
    tipo (chequea `notification_preferences` primero).

    No hace raise en errores — logea y devuelve None. La razón: si Redis o
    Postgres están inestables no queremos bloquear el path crítico (ej.
    asignar una conv). La notif se pierde pero el flujo sigue.
    """
    if type not in VALID_TYPES:
        logger.warning("notify_invalid_type", type=type, user_id=user_id)
        return None

    data = data or {}

    try:
        # Chequeamos prefs — si está desactivado el tipo, no insertamos ni
        # publicamos. Así la UI ni lo ve.
        prefs = await get_preferences(user_id)
        if prefs.get(type) is False:
            return None

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                insert into public.notifications (user_id, type, data)
                values ($1, $2, $3::jsonb)
                returning id, user_id, type, data, created_at, read_at
                """,
                user_id,
                type,
                json.dumps(data),
            )
        notif = _row_to_dict(row)

        # Publish a Redis pubsub para que los SSE conectados la reciban en
        # tiempo real. Si Redis no anda, igualmente la notif quedó en DB y
        # se cargará la próxima vez que hagan GET /notifications.
        try:
            from memory.conversation_store import get_conversation_store

            store = await get_conversation_store()
            await store._redis.publish(
                _pubsub_channel(user_id),
                json.dumps({"event": "new", "notification": notif}),
            )
        except Exception as e:
            logger.debug("notify_pubsub_failed", error=str(e))

        logger.info("notification_created", user_id=user_id, type=type, id=notif["id"])
        return notif["id"]
    except Exception as e:
        logger.error("notify_failed", user_id=user_id, type=type, error=str(e))
        return None


# ──────────────────────────────────────────────────────────────────────────
# READ
# ──────────────────────────────────────────────────────────────────────────


async def list_notifications(
    user_id: str,
    limit: int = 50,
    unread_only: bool = False,
) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if unread_only:
            rows = await conn.fetch(
                """
                select id, user_id, type, data, created_at, read_at
                from public.notifications
                where user_id = $1 and read_at is null
                order by created_at desc
                limit $2
                """,
                user_id,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                select id, user_id, type, data, created_at, read_at
                from public.notifications
                where user_id = $1
                order by created_at desc
                limit $2
                """,
                user_id,
                limit,
            )
    return [_row_to_dict(r) for r in rows]


async def unread_count(user_id: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """
            select count(*)
            from public.notifications
            where user_id = $1 and read_at is null
            """,
            user_id,
        )
    return int(count or 0)


# ──────────────────────────────────────────────────────────────────────────
# UPDATE (mark read)
# ──────────────────────────────────────────────────────────────────────────


async def mark_read(user_id: str, notif_id: str) -> bool:
    """Marca UNA notif como leída. Devuelve True si se actualizó (existe +
    es del user), False si ya estaba leída o no existe. Previene que un user
    marque notifs de otro vía el WHERE user_id.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            update public.notifications
            set read_at = now()
            where id = $1 and user_id = $2 and read_at is null
            """,
            notif_id,
            user_id,
        )
    # asyncpg devuelve "UPDATE N" donde N es cantidad afectada
    affected = int(result.split()[-1]) if result else 0
    return affected > 0


async def mark_all_read(user_id: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            update public.notifications
            set read_at = now()
            where user_id = $1 and read_at is null
            """,
            user_id,
        )
    affected = int(result.split()[-1]) if result else 0
    return affected


# ──────────────────────────────────────────────────────────────────────────
# PREFERENCES
# ──────────────────────────────────────────────────────────────────────────


async def get_preferences(user_id: str) -> dict[str, Any]:
    """Devuelve prefs del user, creando la fila con defaults si no existe."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "select * from public.notification_preferences where user_id = $1",
            user_id,
        )
    if row is None:
        return DEFAULT_PREFERENCES.copy()
    return {k: v for k, v in dict(row).items() if k not in ("user_id", "updated_at")}


async def update_preferences(
    user_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Upsert de preferencias. Solo acepta keys conocidas — el resto se
    ignora silenciosamente. Devuelve el estado final completo.
    """
    valid_keys = set(DEFAULT_PREFERENCES.keys())
    clean = {k: bool(v) for k, v in updates.items() if k in valid_keys}
    if not clean:
        return await get_preferences(user_id)

    # Asegura que la fila existe (insert with defaults); después update.
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                insert into public.notification_preferences (user_id)
                values ($1)
                on conflict (user_id) do nothing
                """,
                user_id,
            )
            set_clauses = []
            params: list[Any] = [user_id]
            for i, (k, v) in enumerate(clean.items(), start=2):
                set_clauses.append(f"{k} = ${i}")
                params.append(v)
            set_clauses.append("updated_at = now()")
            await conn.execute(
                f"update public.notification_preferences "
                f"set {', '.join(set_clauses)} where user_id = $1",
                *params,
            )
    return await get_preferences(user_id)


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────
# Triggers — helpers llamados desde puntos concretos del flujo
# ──────────────────────────────────────────────────────────────────────────


async def on_inbound_user_message(
    session_id: str,
    content_preview: str,
    sender_name: str | None = None,
) -> None:
    """Cuando entra un mensaje inbound (usuario → bot/consola), chequea si
    hay un agente humano asignado a la conv; si sí, le dispara una notif
    `new_message_mine`. Silent fail — esto corre en el path crítico del
    webhook de WhatsApp/widget y no puede tirarlo abajo.

    Si no hay assigned_agent_id (bot todavía atendiendo), no hace nada —
    al agente no le interesa cada vez que el bot recibe un mensaje.
    """
    try:
        from memory import conversation_meta as cm

        meta = await cm.get(session_id)
        if not meta:
            return
        agent_id = meta.get("assigned_agent_id")
        if not agent_id:
            return
        await notify(
            agent_id,
            "new_message_mine",
            {
                "conversation_id": session_id,
                "client_name": sender_name or "cliente",
                "preview": (content_preview or "")[:120],
            },
        )
    except Exception as e:
        logger.debug("notify_inbound_msg_failed", session_id=session_id, error=str(e))


def _row_to_dict(row) -> dict[str, Any]:
    if row is None:
        return {}
    d = dict(row)
    # asyncpg devuelve jsonb como str/dict según versión — normalizamos.
    data = d.get("data")
    if isinstance(data, str):
        try:
            d["data"] = json.loads(data)
        except Exception:
            d["data"] = {}
    elif data is None:
        d["data"] = {}
    for k in ("id", "user_id"):
        if k in d and d[k] is not None:
            d[k] = str(d[k])
    for k in ("created_at", "read_at"):
        if k in d and isinstance(d[k], datetime):
            d[k] = d[k].isoformat()
    return d
