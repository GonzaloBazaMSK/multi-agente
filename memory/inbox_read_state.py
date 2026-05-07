"""
Estado de lectura por agente y conversación.

Concept:
  Una conversación tiene mensajes "no leídos" para un agente si tiene mensajes
  de role='user' posteriores al `last_read_at` de ese agente sobre esa conv.

  Si el agente nunca abrió la conv (no hay row), todos los mensajes del user
  cuentan como no leídos.

API:
  - mark_read(user_id, conv_id): el agente acaba de abrir la conv → upsert
    last_read_at = now().
  - unread_counts_for_user(user_id, conv_ids): para cada conv en la lista,
    retorna {conv_id: count}. Counts solo de mensajes role='user'.
"""

from __future__ import annotations

import asyncpg
import structlog

from memory import postgres_store

logger = structlog.get_logger(__name__)


async def mark_read(user_id: str, conversation_id: str) -> None:
    """Upsert: el agente acaba de abrir la conv. last_read_at = now()."""
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """
                INSERT INTO public.inbox_read_state (user_id, conversation_id, last_read_at)
                VALUES ($1::uuid, $2::uuid, now())
                ON CONFLICT (user_id, conversation_id)
                DO UPDATE SET last_read_at = now()
                """,
                user_id,
                conversation_id,
            )
        except (asyncpg.InvalidTextRepresentationError, ValueError) as e:
            logger.debug("mark_read_invalid_id", user=user_id, conv=conversation_id, error=str(e))


async def unread_counts_for_user(
    user_id: str,
    conversation_ids: list[str],
) -> dict[str, int]:
    """Para cada conv en la lista, devuelve cuántos mensajes role='user' tiene
    posteriores al last_read_at del agente. Si nunca abrió → cuenta TODOS los
    mensajes user de esa conv.

    SOLO cuenta convs con `bot_paused = true` (humano atendiendo). Cuando el
    bot IA está atendiendo solo, no es responsabilidad del agente humano leer
    cada mensaje — el bot maneja el flujo.

    Retorna dict {conv_id_str: count}. Las convs sin mensajes no leídos NO
    aparecen en el dict (se asume 0 implícito).
    """
    if not conversation_ids:
        return {}
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.conversation_id::text AS conv_id, count(*)::int AS cnt
            FROM public.messages m
            JOIN public.conversation_meta cm
                ON cm.conversation_id = m.conversation_id
            LEFT JOIN public.inbox_read_state rs
                ON rs.conversation_id = m.conversation_id
                AND rs.user_id = $1::uuid
            WHERE m.conversation_id = ANY($2::uuid[])
              AND m.role = 'user'
              AND cm.bot_paused = true
              AND (rs.last_read_at IS NULL OR m.created_at > rs.last_read_at)
            GROUP BY m.conversation_id
            """,
            user_id,
            conversation_ids,
        )
    return {r["conv_id"]: r["cnt"] for r in rows}
