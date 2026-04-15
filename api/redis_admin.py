"""
Redis Admin — visor y gestor de claves Redis para el panel de administración.
Solo accesible por administradores autenticados.
"""
import json
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from api.auth import require_role
import structlog

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin/redis", tags=["redis-admin"])


async def _redis():
    from memory.conversation_store import get_conversation_store
    store = await get_conversation_store()
    return store._redis


# ── Models ────────────────────────────────────────────────────────────────────

class DeletePatternRequest(BaseModel):
    pattern: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/keys")
async def list_keys(pattern: str = "*", limit: int = 200, user: dict = Depends(require_role("admin"))):
    """Lista claves Redis con tipo, TTL y tamaño."""
    r = await _redis()
    keys = []
    async for key in r.scan_iter(pattern, count=500):
        keys.append(key.decode() if isinstance(key, bytes) else key)
        if len(keys) >= limit:
            break
    keys.sort()

    result = []
    for key in keys:
        try:
            ktype = await r.type(key)
            ktype = ktype.decode() if isinstance(ktype, bytes) else ktype
            ttl = await r.ttl(key)
            size = 0
            preview = ""
            if ktype == "string":
                val = await r.get(key)
                val = val.decode("utf-8", errors="replace") if isinstance(val, bytes) else (val or "")
                size = len(val)
                preview = val[:120] + ("…" if len(val) > 120 else "")
            elif ktype == "list":
                size = await r.llen(key)
                preview = f"{size} elementos"
            elif ktype == "set":
                size = await r.scard(key)
                preview = f"{size} miembros"
            elif ktype == "hash":
                size = await r.hlen(key)
                preview = f"{size} campos"
            result.append({
                "key": key,
                "type": ktype,
                "ttl": ttl,
                "size": size,
                "preview": preview,
            })
        except Exception as e:
            result.append({"key": key, "type": "?", "ttl": -1, "size": 0, "preview": str(e)})

    return {"keys": result, "total": len(result)}


@router.get("/key")
async def get_key(key: str, user: dict = Depends(require_role("admin"))):
    """Obtiene el valor completo de una clave."""
    r = await _redis()
    ktype = await r.type(key)
    ktype = ktype.decode() if isinstance(ktype, bytes) else ktype
    ttl = await r.ttl(key)

    value = None
    if ktype == "string":
        raw = await r.get(key)
        val = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else (raw or "")
        # Try to pretty-print JSON
        try:
            value = json.dumps(json.loads(val), ensure_ascii=False, indent=2)
        except Exception:
            value = val
    elif ktype == "list":
        items = await r.lrange(key, 0, -1)
        value = json.dumps(
            [i.decode("utf-8", errors="replace") if isinstance(i, bytes) else i for i in items],
            ensure_ascii=False, indent=2
        )
    elif ktype == "set":
        members = await r.smembers(key)
        value = json.dumps(
            sorted([m.decode("utf-8", errors="replace") if isinstance(m, bytes) else m for m in members]),
            ensure_ascii=False, indent=2
        )
    elif ktype == "hash":
        fields = await r.hgetall(key)
        value = json.dumps(
            {(k.decode() if isinstance(k, bytes) else k): (v.decode("utf-8", errors="replace") if isinstance(v, bytes) else v)
             for k, v in fields.items()},
            ensure_ascii=False, indent=2
        )
    else:
        value = f"(tipo no soportado: {ktype})"

    return {"key": key, "type": ktype, "ttl": ttl, "value": value}


@router.delete("/key")
async def delete_key(key: str, user: dict = Depends(require_role("admin"))):
    """Elimina una clave."""
    r = await _redis()
    deleted = await r.delete(key)
    logger.info("redis_admin_delete_key", key=key, deleted=deleted)
    return {"deleted": deleted, "key": key}


@router.post("/delete-pattern")
async def delete_by_pattern(req: DeletePatternRequest, user: dict = Depends(require_role("admin"))):
    """Elimina todas las claves que coincidan con el patrón."""
    if not req.pattern or req.pattern.strip() == "*":
        raise HTTPException(status_code=400, detail="Patrón demasiado amplio — especificá un prefijo")
    r = await _redis()
    keys = []
    async for key in r.scan_iter(req.pattern, count=500):
        keys.append(key.decode() if isinstance(key, bytes) else key)
    if keys:
        await r.delete(*keys)
    logger.info("redis_admin_delete_pattern", pattern=req.pattern, count=len(keys))
    return {"deleted": len(keys), "keys": keys}


@router.post("/flush-conversations")
async def flush_conversations(user: dict = Depends(require_role("admin"))):
    """
    Elimina TODAS las conversaciones y datos de sesión.
    Conserva: widget:config, auth tokens, flows, templates.
    """
    r = await _redis()
    patterns = [
        "conv:*",           # Conversaciones
        "idx:widget:*",     # Índice widget
        "idx:whatsapp:*",   # Índice WhatsApp
        "wflow:*",          # Estados del menú widget
        "bot_disabled:*",   # Bot desactivado (widget)
        "bot_disabled_wa:*",# Bot desactivado (WhatsApp)
        "conv_events:*",    # Log de eventos
        "conv_label:*",     # Etiquetas de leads
        "agent_name:*",     # Nombre del agente asignado
        "assigned_agent:*", # Asignación de agente
        "unread:*",         # Conteo de no leídos
        "zoho_cache:*",     # Cache cobranzas Zoho
        "zoho_cursadas:*",  # Cache perfil Zoho Contacts
        "datos_deudor:*",   # Datos deudor cobranzas
        "last_seen:*",      # Última vez visto
        "typing:*",         # Estado "escribiendo"
        "cm_session:*",     # Sesiones internas
    ]
    total = 0
    deleted_by_pattern: dict = {}
    for pattern in patterns:
        keys = []
        async for key in r.scan_iter(pattern, count=500):
            keys.append(key.decode() if isinstance(key, bytes) else key)
        if keys:
            await r.delete(*keys)
            deleted_by_pattern[pattern] = len(keys)
            total += len(keys)

    logger.info("redis_flush_conversations", deleted=total, by_pattern=deleted_by_pattern, by=user.get("username"))
    return {
        "deleted": total,
        "by_pattern": deleted_by_pattern,
        "message": f"✅ {total} claves eliminadas. Redis limpio.",
    }


@router.get("/stats")
async def get_stats(user: dict = Depends(require_role("admin"))):
    """Estadísticas rápidas de Redis."""
    r = await _redis()
    info = await r.info()
    dbsize = await r.dbsize()
    return {
        "dbsize": dbsize,
        "used_memory_human": info.get("used_memory_human", "?"),
        "connected_clients": info.get("connected_clients", 0),
        "uptime_in_seconds": info.get("uptime_in_seconds", 0),
        "redis_version": info.get("redis_version", "?"),
        "keyspace": info.get("db0", {}),
    }
