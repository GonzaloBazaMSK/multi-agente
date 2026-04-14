"""
Cloudflare R2 object storage (S3-compatible).

Subida de media (audios, imágenes, docs) al bucket público de R2.
Las URLs retornadas son públicas vía el subdominio pub-*.r2.dev,
listas para servir al widget, al inbox y a WhatsApp (Meta pull).

Fallback a filesystem local si R2 no está configurado (desarrollo).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import boto3
from botocore.config import Config
import structlog

from config.settings import get_settings

logger = structlog.get_logger(__name__)

_client = None


def _get_client():
    """Lazy singleton del cliente S3 apuntado a R2."""
    global _client
    if _client is None:
        s = get_settings()
        _client = boto3.client(
            "s3",
            endpoint_url=s.r2_endpoint,
            aws_access_key_id=s.r2_access_key_id,
            aws_secret_access_key=s.r2_secret_access_key,
            config=Config(signature_version="s3v4", region_name="auto"),
        )
    return _client


def is_enabled() -> bool:
    """True si R2 está configurado (endpoint + credenciales + bucket + public URL)."""
    s = get_settings()
    return bool(
        s.r2_endpoint
        and s.r2_access_key_id
        and s.r2_secret_access_key
        and s.r2_bucket
        and s.r2_public_url
    )


async def upload_bytes(key: str, data: bytes, content_type: str) -> str:
    """Sube `data` al bucket bajo `key`. Retorna la URL pública."""
    s = get_settings()

    def _put():
        _get_client().put_object(
            Bucket=s.r2_bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl="public, max-age=31536000, immutable",
        )

    await asyncio.to_thread(_put)
    public_url = f"{s.r2_public_url.rstrip('/')}/{key.lstrip('/')}"
    logger.info("r2_upload_ok", key=key, size=len(data), url=public_url)
    return public_url


async def upload_file(key: str, filepath: str | Path, content_type: str) -> str:
    """Conveniencia: lee un archivo y lo sube a R2."""
    p = Path(filepath)
    data = p.read_bytes()
    return await upload_bytes(key, data, content_type)


async def delete_object(key: str) -> None:
    """Borra un objeto del bucket (best-effort)."""
    s = get_settings()

    def _del():
        _get_client().delete_object(Bucket=s.r2_bucket, Key=key)

    try:
        await asyncio.to_thread(_del)
        logger.info("r2_delete_ok", key=key)
    except Exception as e:
        logger.warning("r2_delete_failed", key=key, error=str(e))


def public_url_for(key: str) -> Optional[str]:
    """Construye la URL pública para un key, sin subir nada."""
    s = get_settings()
    if not s.r2_public_url:
        return None
    return f"{s.r2_public_url.rstrip('/')}/{key.lstrip('/')}"
