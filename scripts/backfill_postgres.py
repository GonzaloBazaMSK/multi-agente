"""
Backfill one-time: lee todas las conversaciones existentes en Redis (`conv:*`)
y las escribe en Postgres vía postgres_store.save_conversation.

Uso:
    docker exec multiagente-api-1 python scripts/backfill_postgres.py

Es idempotente: si se corre dos veces, los ON CONFLICT evitan duplicados.
"""
import asyncio
import sys
from pathlib import Path

# Permitir ejecutar desde cualquier ubicación
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import redis.asyncio as aioredis
import structlog

from config.settings import get_settings
from memory import postgres_store
from models.conversation import Conversation

logger = structlog.get_logger(__name__)


async def backfill():
    settings = get_settings()

    if not postgres_store.is_enabled():
        print("❌ DATABASE_URL no está configurado. Abortando.")
        return

    # Asegurar schema
    await postgres_store.ensure_schema()
    print("✓ Schema verificado en Postgres")

    # Abrir Redis
    redis = aioredis.from_url(settings.redis_url, decode_responses=False)

    total = 0
    errors = 0
    total_messages = 0

    # Scan de todas las keys conv:*
    async for key in redis.scan_iter(match="conv:*", count=100):
        key_str = key.decode() if isinstance(key, bytes) else key
        try:
            raw = await redis.get(key)
            if not raw:
                continue
            conv = Conversation.model_validate_json(raw)
            await postgres_store.save_conversation(conv)
            total += 1
            total_messages += len(conv.messages)
            if total % 10 == 0:
                print(f"  ... {total} conversaciones migradas")
        except Exception as e:
            errors += 1
            print(f"  ✗ error en {key_str}: {e}")

    print()
    print(f"✓ Backfill completado:")
    print(f"  • conversaciones migradas: {total}")
    print(f"  • mensajes insertados:     {total_messages}")
    print(f"  • errores:                 {errors}")

    await redis.close()
    await postgres_store.close_pool()


if __name__ == "__main__":
    asyncio.run(backfill())
