"""
Cache Redis del catálogo de cursos.

Patrón:
  1. `get_course(country, slug)` → busca en Redis (TTL 24h)
  2. miss → busca en Postgres → hidrata Redis → devuelve
  3. si tampoco está en PG → None

Key pattern: `course:{country}:{slug}` (JSON encoded)
Invalidación: sync_country borra todas las keys del país (scan + del).

NOTA: guardamos un subset compacto (sin raw JSONB) para keep Redis pequeño.
El raw se lee on-demand desde PG via `get_course_deep()`.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import structlog

from memory import postgres_store
from memory.conversation_store import get_conversation_store

logger = structlog.get_logger(__name__)

COURSE_TTL = 60 * 60 * 24  # 24h
COURSE_KEY_PREFIX = "course:"


def _key(country: str, slug: str) -> str:
    return f"{COURSE_KEY_PREFIX}{country.lower()}:{slug}"


def _compact(row: dict) -> dict:
    """Subset cacheable: todo menos raw (que es voluminoso)."""
    d = dict(row)
    d.pop("raw", None)
    # asyncpg puede devolver datetime → ISO
    for k in ("synced_at", "source_updated_at", "created_at"):
        if k in d and d[k] is not None:
            try:
                d[k] = d[k].isoformat()
            except Exception:
                d[k] = str(d[k])
    # Decimals (numeric) → float
    for k in ("regular_price", "sale_price", "total_price", "price_installments"):
        if k in d and d[k] is not None:
            try:
                d[k] = float(d[k])
            except Exception:
                pass
    return d


async def get_course(country: str, slug: str) -> Optional[dict]:
    """Lookup caliente: Redis → Postgres → None. Hidrata Redis en miss."""
    country = country.lower()
    try:
        store = await get_conversation_store()
        redis = store._redis
    except Exception as e:
        logger.warning("course_cache_redis_unavailable", error=str(e))
        redis = None

    if redis:
        try:
            raw = await redis.get(_key(country, slug))
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.warning("course_cache_read_failed", error=str(e))

    # Miss → PG
    row = await postgres_store.get_course(country, slug)
    if not row:
        return None

    compact = _compact(row)
    if redis:
        try:
            await redis.set(_key(country, slug), json.dumps(compact, default=str), ex=COURSE_TTL)
        except Exception as e:
            logger.warning("course_cache_write_failed", error=str(e))
    return compact


async def get_course_deep(country: str, slug: str) -> Optional[dict]:
    """Lectura profunda (incluye raw JSONB) — siempre desde Postgres."""
    return await postgres_store.get_course(country.lower(), slug)


async def invalidate_country(country: str, slugs: list[str] | None = None) -> int:
    """
    Borra keys de Redis para un país. Si `slugs` está dado, solo esas;
    si no, scan completo por prefijo.
    """
    country = country.lower()
    try:
        store = await get_conversation_store()
        redis = store._redis
    except Exception:
        return 0

    deleted = 0
    try:
        if slugs:
            keys = [_key(country, s) for s in slugs]
            if keys:
                deleted = await redis.delete(*keys)
        else:
            pattern = f"{COURSE_KEY_PREFIX}{country}:*"
            cursor = 0
            while True:
                cursor, batch = await redis.scan(cursor=cursor, match=pattern, count=500)
                if batch:
                    deleted += await redis.delete(*batch)
                if cursor == 0:
                    break
    except Exception as e:
        logger.warning("course_cache_invalidate_error", error=str(e))

    return deleted
