"""
Store de conversaciones con arquitectura cache + durable.

Redis       → cache caliente (fast reads, TTL, pub/sub, estructuras auxiliares)
Postgres    → source of truth durable (Supabase, sin TTL)

Flujo:
  save()    → escribe Redis (sync) + Postgres (async background, no bloqueante)
  get()     → intenta Redis; si miss y Postgres está habilitado, lee Postgres
              y rehidrata Redis para futuros reads.
  delete()  → borra en ambos.

Si Postgres está desactivado (DATABASE_URL vacío), el módulo se comporta como
antes: solo Redis.
"""
from __future__ import annotations

import asyncio

import redis.asyncio as aioredis
import structlog

from memory import postgres_store
from models.conversation import Conversation, UserProfile
from models.message import Message
from config.settings import get_settings
from config.constants import CONVERSATION_TTL, Channel

logger = structlog.get_logger(__name__)


class ConversationStore:
    _pg_consecutive_failures: int = 0
    _PG_FAILURE_THRESHOLD: int = 5

    def __init__(self, redis_client: aioredis.Redis):
        self._redis = redis_client

    def _key(self, conversation_id: str) -> str:
        return f"conv:{conversation_id}"

    def _index_key(self, channel: str, external_id: str) -> str:
        return f"idx:{channel}:{external_id}"

    # ─── Lectura ──────────────────────────────────────────────────────────

    async def get(self, conversation_id: str) -> Conversation | None:
        raw = await self._redis.get(self._key(conversation_id))
        if raw:
            try:
                return Conversation.model_validate_json(raw)
            except Exception:
                logger.warning("corrupt_conversation_in_redis", conversation_id=conversation_id)

        # Fallback Postgres
        if postgres_store.is_enabled():
            try:
                conv = await postgres_store.get_conversation(conversation_id)
                if conv:
                    await self._write_redis(conv)  # rehidrata cache
                    logger.info("conv_rehydrated_from_pg", conversation_id=conversation_id)
                    return conv
            except Exception as e:
                logger.warning("postgres_read_failed", conversation_id=conversation_id, error=str(e))
        return None

    async def get_by_external(self, channel: Channel, external_id: str) -> Conversation | None:
        conv_id = await self._redis.get(self._index_key(channel.value, external_id))
        if conv_id:
            return await self.get(conv_id.decode())

        # Fallback Postgres
        if postgres_store.is_enabled():
            try:
                conv = await postgres_store.get_by_external(channel, external_id)
                if conv:
                    await self._write_redis(conv)
                    logger.info("conv_rehydrated_from_pg_by_external", channel=channel.value, external_id=external_id)
                    return conv
            except Exception as e:
                logger.warning("postgres_read_failed", error=str(e))
        return None

    # ─── Escritura ────────────────────────────────────────────────────────

    async def _write_redis(self, conversation: Conversation) -> None:
        pipe = self._redis.pipeline()
        pipe.setex(
            self._key(conversation.id),
            CONVERSATION_TTL,
            conversation.model_dump_json(),
        )
        pipe.setex(
            self._index_key(conversation.channel.value, conversation.external_id),
            CONVERSATION_TTL,
            conversation.id,
        )
        await pipe.execute()

    async def save(self, conversation: Conversation) -> None:
        # Redis primero (bloqueante — si falla, la app lo ve)
        await self._write_redis(conversation)

        # Postgres en background (best effort, no bloquea el request)
        if postgres_store.is_enabled():
            asyncio.create_task(self._save_to_postgres(conversation))

    async def _save_to_postgres(self, conversation: Conversation) -> None:
        """Write to Postgres with one automatic retry on failure."""
        max_retries = 1
        for attempt in range(max_retries + 1):
            try:
                await postgres_store.save_conversation(conversation)
                if self._pg_consecutive_failures > 0:
                    logger.info("postgres_write_recovered",
                                conversation_id=conversation.id,
                                after_failures=self._pg_consecutive_failures)
                ConversationStore._pg_consecutive_failures = 0
                return
            except Exception as e:
                ConversationStore._pg_consecutive_failures += 1
                if attempt < max_retries:
                    logger.warning("postgres_write_retrying",
                                   conversation_id=conversation.id,
                                   error=str(e), attempt=attempt)
                    await asyncio.sleep(1.0)
                else:
                    level = "critical" if self._pg_consecutive_failures >= self._PG_FAILURE_THRESHOLD else "error"
                    getattr(logger, level)(
                        "postgres_write_failed",
                        conversation_id=conversation.id,
                        error=str(e),
                        consecutive_failures=self._pg_consecutive_failures,
                    )

    async def get_or_create(
        self,
        channel: Channel,
        external_id: str,
        country: str = "AR",
    ) -> tuple[Conversation, bool]:
        """Returns (conversation, is_new)."""
        existing = await self.get_by_external(channel, external_id)
        if existing:
            return existing, False

        conversation = Conversation(
            channel=channel,
            external_id=external_id,
            user_profile=UserProfile(country=country),
        )
        await self.save(conversation)
        logger.info("conversation_created", id=conversation.id, channel=channel, external_id=external_id)
        return conversation, True

    async def append_message(self, conversation: Conversation, message: Message) -> Conversation:
        conversation.add_message(message)
        await self.save(conversation)
        return conversation

    async def delete(self, conversation_id: str, channel: str, external_id: str) -> None:
        pipe = self._redis.pipeline()
        pipe.delete(self._key(conversation_id))
        pipe.delete(self._index_key(channel, external_id))
        await pipe.execute()

        if postgres_store.is_enabled():
            try:
                pool = await postgres_store.get_pool()
                async with pool.acquire() as conn:
                    await conn.execute(
                        "delete from public.conversations where id = $1",
                        __import__("uuid").UUID(conversation_id),
                    )
            except Exception as e:
                logger.warning("postgres_delete_failed", conversation_id=conversation_id, error=str(e))


_store: ConversationStore | None = None


async def get_conversation_store() -> ConversationStore:
    global _store
    if _store is None:
        settings = get_settings()
        client = aioredis.from_url(settings.redis_url, decode_responses=False)
        _store = ConversationStore(client)
    return _store
