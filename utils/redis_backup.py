"""
Backup diario de Redis a Cloudflare R2.

Estrategia: dump por key via protocolo Redis (no requiere acceso al
filesystem del container Redis). Iteramos con SCAN, llamamos DUMP por key
para obtener el formato binario nativo (compatible con RESTORE), guardamos
TTL, serializamos con pickle, comprimimos con gzip y subimos a R2.

Restauración:
    para cada key del backup → redis.restore(key, ttl_ms, value)

Pros:
- Sin docker exec ni mount de volúmenes (más limpio en operación).
- Funciona desde el mismo container que ya tiene credenciales R2.
- Captura TTL exacto y valores binarios (lists, hashes, sets, zsets, strings).

Contras:
- No es un .rdb estándar — la restauración requiere este script. Es el
  trade-off por no necesitar acceso al filesystem.
- Para >100k keys puede tardar varios segundos (SCAN no bloquea pero hace
  ida y vuelta por key). Hoy tenemos <10k keys → tarda <30s.

Rotación: subimos con key `redis-backups/YYYY-MM-DD.pkl.gz`. Sobrescribir
el del día evita acumular si el job corre 2x. Para retención más larga,
configurar lifecycle rules en el bucket R2 (ej: delete after 30 days).
"""

from __future__ import annotations

import gzip
import pickle
from datetime import UTC, datetime

import structlog

from integrations import storage

logger = structlog.get_logger(__name__)

BACKUP_KEY_PREFIX = "redis-backups"
SCAN_BATCH = 500


async def run_redis_backup() -> dict:
    """
    Dump full de Redis → pickle.gz → R2. Retorna stats.

    Llamado desde APScheduler diariamente (4:00 AR).
    """
    if not storage.is_enabled():
        logger.warning("redis_backup_skipped_no_r2")
        return {"skipped": True, "reason": "r2_not_configured"}

    from memory.conversation_store import get_conversation_store

    store = await get_conversation_store()
    redis = store._redis

    started = datetime.now(UTC)
    snapshot: dict[str, dict] = {}
    failed = 0

    # SCAN no bloquea Redis (vs KEYS *). Iteramos en lotes de 500.
    async for raw_key in redis.scan_iter(count=SCAN_BATCH):
        key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
        try:
            # DUMP devuelve el binario serializado en formato RDB de esa key.
            # Es lo más fiel posible al estado, restaurable con RESTORE.
            value = await redis.dump(raw_key)
            if value is None:
                # La key expiró entre el SCAN y el DUMP — skip
                continue
            ttl_ms = await redis.pttl(raw_key)
            # PTTL devuelve -1 si no tiene TTL, -2 si la key no existe ya.
            # Para el dump usamos 0 (sin TTL) si es -1, y skip si es -2.
            if ttl_ms == -2:
                continue
            snapshot[key] = {
                "value": value,
                "ttl_ms": max(ttl_ms, 0),  # 0 = no TTL al restore
            }
        except Exception as e:
            failed += 1
            logger.debug("redis_backup_key_failed", key=key, error=str(e))

    if not snapshot:
        logger.warning("redis_backup_empty")
        return {"skipped": True, "reason": "empty_redis"}

    # pickle protocol 5 + gzip nivel 6 — buena compresión, no demasiado CPU.
    payload = pickle.dumps(snapshot, protocol=5)
    payload_gz = gzip.compress(payload, compresslevel=6)

    today = started.strftime("%Y-%m-%d")
    object_key = f"{BACKUP_KEY_PREFIX}/{today}.pkl.gz"

    try:
        url = await storage.upload_bytes(
            object_key, payload_gz, "application/octet-stream"
        )
    except Exception as e:
        logger.error("redis_backup_upload_failed", error=str(e))
        return {"error": str(e), "keys": len(snapshot)}

    elapsed = (datetime.now(UTC) - started).total_seconds()
    logger.info(
        "redis_backup_done",
        keys=len(snapshot),
        failed=failed,
        size_bytes=len(payload_gz),
        size_mb=round(len(payload_gz) / 1024 / 1024, 2),
        elapsed_s=round(elapsed, 1),
        url=url,
    )

    # Marca timestamp en Redis para que un watchdog futuro pueda alertar
    # si el backup no corrió.
    try:
        await redis.set("redis_backup:last_run", started.isoformat())
    except Exception:
        pass

    return {
        "keys": len(snapshot),
        "failed": failed,
        "size_bytes": len(payload_gz),
        "elapsed_s": round(elapsed, 1),
        "url": url,
    }


async def restore_redis_from_backup(backup_url: str) -> dict:
    """
    Helper de restauración — descarga el .pkl.gz, lo deserializa y aplica
    cada key con RESTORE. NO se llama automático — solo manualmente vía CLI
    desde la consola de admin si pasa algo crítico con Redis.

    NO se llama desde el scheduler. NO se expone en endpoints público.
    """
    import httpx

    from memory.conversation_store import get_conversation_store

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(backup_url)
        resp.raise_for_status()
        payload_gz = resp.content

    payload = gzip.decompress(payload_gz)
    snapshot = pickle.loads(payload)

    store = await get_conversation_store()
    redis = store._redis
    restored = 0
    skipped = 0

    for key, entry in snapshot.items():
        try:
            await redis.restore(
                key.encode() if isinstance(key, str) else key,
                entry["ttl_ms"],
                entry["value"],
                replace=True,
            )
            restored += 1
        except Exception as e:
            skipped += 1
            logger.warning("redis_restore_failed", key=key, error=str(e))

    return {"restored": restored, "skipped": skipped, "total": len(snapshot)}
