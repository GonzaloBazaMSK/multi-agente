"""
Backup diario de Redis a Cloudflare R2 (bucket PRIVADO).

Estrategia: dump por key via protocolo Redis (no requiere acceso al
filesystem del container Redis). Iteramos con SCAN, llamamos DUMP por key
para obtener el formato binario nativo (compatible con RESTORE), guardamos
TTL, serializamos con pickle, comprimimos con gzip y subimos al bucket
privado de R2 (`R2_BACKUPS_BUCKET`).

⚠️ IMPORTANTE — usar bucket PRIVADO, no el público de media:
El backup contiene state sensible (sesiones, prompts del bot, conv labels,
scheduler jobs). El bucket de media es público y su subdominio es
descubrible mirando cualquier asset del widget — si el backup va ahí,
cualquiera con el subdominio puede bajarlo. Por eso el bucket de backups
es separado y SIN public access.

Restauración:
    para cada key del backup → redis.restore(key, ttl_ms, value)
    Lectura del .pkl.gz vía boto3 client (no via URL pública — no hay).

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

import asyncio
import gzip
import pickle
from datetime import UTC, datetime

import structlog

from config.settings import get_settings

logger = structlog.get_logger(__name__)

BACKUP_KEY_PREFIX = "redis-backups"
SCAN_BATCH = 500


def _get_backups_client():
    """Cliente boto3 apuntado a R2 — usado solo para el bucket privado de
    backups. NO compartimos con `integrations.storage._client` porque ese
    se usa para uploads al bucket público y queremos las dependencias
    explícitas para que no se mezclen accidentalmente."""
    import boto3
    from botocore.config import Config

    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.r2_endpoint,
        aws_access_key_id=s.r2_access_key_id,
        aws_secret_access_key=s.r2_secret_access_key,
        config=Config(signature_version="s3v4", region_name="auto"),
    )


def _is_backups_configured() -> bool:
    s = get_settings()
    return bool(
        s.r2_endpoint and s.r2_access_key_id and s.r2_secret_access_key and s.r2_backups_bucket
    )


async def run_redis_backup() -> dict:
    """
    Dump full de Redis → pickle.gz → R2 (bucket privado). Retorna stats.

    Llamado desde APScheduler diariamente (4:00 AR).
    """
    if not _is_backups_configured():
        logger.warning(
            "redis_backup_skipped_no_private_bucket",
            hint="Setear R2_BACKUPS_BUCKET en .env (bucket privado, no el de media)",
        )
        return {"skipped": True, "reason": "r2_backups_bucket_not_configured"}

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

    settings = get_settings()
    try:
        client = _get_backups_client()

        def _put():
            client.put_object(
                Bucket=settings.r2_backups_bucket,
                Key=object_key,
                Body=payload_gz,
                ContentType="application/octet-stream",
            )

        await asyncio.to_thread(_put)
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
        bucket=settings.r2_backups_bucket,
        object_key=object_key,
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


async def restore_redis_from_backup(date_str: str) -> dict:
    """
    Helper de restauración — descarga el .pkl.gz del bucket privado vía boto3
    (con credenciales R2), lo deserializa y aplica cada key con RESTORE.

    NO se llama automático. NO se expone en endpoints público. Uso manual:

        docker exec msk-multiagente-api-1 python -c "
        import asyncio
        from utils.redis_backup import restore_redis_from_backup
        print(asyncio.run(restore_redis_from_backup('2026-05-06')))
        "

    Args:
        date_str: Fecha del backup en formato YYYY-MM-DD.
    """
    if not _is_backups_configured():
        return {"error": "r2_backups_bucket_not_configured"}

    settings = get_settings()
    object_key = f"{BACKUP_KEY_PREFIX}/{date_str}.pkl.gz"

    try:
        client = _get_backups_client()

        def _get():
            return client.get_object(
                Bucket=settings.r2_backups_bucket,
                Key=object_key,
            )["Body"].read()

        payload_gz = await asyncio.to_thread(_get)
    except Exception as e:
        logger.error("redis_restore_download_failed", key=object_key, error=str(e))
        return {"error": f"download_failed: {e}"}

    payload = gzip.decompress(payload_gz)
    snapshot = pickle.loads(payload)

    from memory.conversation_store import get_conversation_store

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
