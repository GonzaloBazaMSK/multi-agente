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

# Prefijos protegidos — no se pueden borrar vía delete-pattern ni delete-key
# para evitar eliminar accidentalmente sesiones de admin o config del widget.
PROTECTED_PREFIXES = ("session:", "flow:", "widget:config")


def _is_protected_key(key: str) -> bool:
    """Retorna True si la clave pertenece a un prefijo protegido."""
    return any(key.startswith(p) for p in PROTECTED_PREFIXES)


def _pattern_hits_protected(pattern: str) -> bool:
    """Retorna True si el patrón de glob podría borrar claves protegidas."""
    for prefix in PROTECTED_PREFIXES:
        # "session:*" matchea "session:", "*" matchea todo
        if pattern == "*" or prefix.startswith(pattern.rstrip("*")):
            return True
    return False


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
    """Elimina una clave (excepto claves protegidas: session:*, flow:*, widget:config)."""
    if _is_protected_key(key):
        raise HTTPException(status_code=403, detail=f"Clave protegida — no se puede eliminar: {key}")
    r = await _redis()
    deleted = await r.delete(key)
    logger.info("redis_admin_delete_key", key=key, deleted=deleted, by=user.get("username"))
    return {"deleted": deleted, "key": key}


@router.post("/delete-pattern")
async def delete_by_pattern(req: DeletePatternRequest, user: dict = Depends(require_role("admin"))):
    """Elimina claves que coincidan con el patrón (excepto claves protegidas)."""
    if not req.pattern or req.pattern.strip() == "*":
        raise HTTPException(status_code=400, detail="Patrón demasiado amplio — especificá un prefijo")
    if _pattern_hits_protected(req.pattern):
        raise HTTPException(status_code=403, detail=f"El patrón '{req.pattern}' afecta claves protegidas (session:*, flow:*, widget:config)")
    r = await _redis()
    keys = []
    async for key in r.scan_iter(req.pattern, count=500):
        k = key.decode() if isinstance(key, bytes) else key
        if not _is_protected_key(k):
            keys.append(k)
    if keys:
        await r.delete(*keys)
    logger.info("redis_admin_delete_pattern", pattern=req.pattern, count=len(keys), by=user.get("username"))
    return {"deleted": len(keys), "keys": keys}


@router.post("/flush-conversations")
async def flush_conversations(user: dict = Depends(require_role("admin"))):
    """
    Elimina TODAS las conversaciones y datos de sesión.
    Conserva: widget:config, auth tokens, flows, templates.
    """
    r = await _redis()
    patterns = [
        "conv:*",                # Conversaciones
        "idx:widget:*",          # Índice widget
        "idx:whatsapp:*",        # Índice WhatsApp
        "wflow:*",               # Estados del menú widget
        "bot_disabled:*",        # Bot desactivado (widget)
        "bot_disabled_wa:*",     # Bot desactivado (WhatsApp)
        "conv_events:*",         # Log de eventos
        "conv_label:*",          # Etiquetas de leads
        "conv_queue:*",          # Cola de conversación
        "conv_assigned:*",       # Asignación
        "conv_assigned_name:*",  # Nombre asignado
        "conv_enrichment:*",     # Enriquecimiento de perfil
        "conv_notes:*",          # Notas internas
        "closing_note:*",        # Nota de cierre
        "frt:*",                 # First response time
        "last_reply:*",          # Última respuesta
        "agent_name:*",          # Nombre del agente asignado
        "assigned_agent:*",      # Asignación de agente
        "unread:*",              # Conteo de no leídos
        "zoho_cache:*",          # Cache cobranzas Zoho
        "zoho_cursadas:*",       # Cache perfil Zoho Contacts
        "datos_deudor:*",        # Datos deudor cobranzas
        "last_seen:*",           # Última vez visto
        "typing:*",              # Estado "escribiendo"
        "cm_session:*",          # Sesiones internas
        "customer_session:*",    # Sesiones de cliente
        "contact_sessions:*",    # Sesiones por contacto
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


@router.post("/nuclear-reset")
async def nuclear_reset(user: dict = Depends(require_role("admin"))):
    """
    RESET TOTAL: Redis (conversaciones + caches) + Supabase (customers + auth users).
    Conserva: widget:config, flow:*, session:* (admin auth), perfiles de agentes (profiles).
    """
    from integrations import supabase_client as sb

    # 1) Redis — patrones amplios (conversación + caches + sesiones de cliente)
    r = await _redis()
    patterns = [
        "conv:*", "idx:widget:*", "idx:whatsapp:*", "wflow:*",
        "bot_disabled:*", "bot_disabled_wa:*",
        "conv_events:*", "conv_label:*", "conv_queue:*",
        "conv_assigned:*", "conv_assigned_name:*", "conv_enrichment:*",
        "conv_notes:*", "closing_note:*", "frt:*", "last_reply:*",
        "agent_name:*", "assigned_agent:*", "unread:*",
        "zoho_cache:*", "zoho_cursadas:*", "datos_deudor:*",
        "last_seen:*", "typing:*", "cm_session:*",
        "customer_session:*", "contact_sessions:*",
    ]
    redis_total = 0
    redis_by_pattern: dict = {}
    for pattern in patterns:
        keys = []
        async for key in r.scan_iter(pattern, count=500):
            keys.append(key.decode() if isinstance(key, bytes) else key)
        if keys:
            await r.delete(*keys)
            redis_by_pattern[pattern] = len(keys)
            redis_total += len(keys)

    # 2) Postgres — conversations + messages + conversation_stage (cascade)
    pg_conversations = 0
    pg_messages = 0
    pg_error = None
    try:
        from memory import postgres_store as pg
        if pg.is_enabled():
            pool = await pg.get_pool()
            async with pool.acquire() as conn:
                # messages y conversation_stage tienen ON DELETE CASCADE,
                # pero los borramos explícitamente para contar.
                pg_messages = int(await conn.fetchval(
                    "WITH d AS (DELETE FROM public.messages RETURNING 1) SELECT COUNT(*) FROM d"
                ) or 0)
                await conn.execute("DELETE FROM public.conversation_stage")
                pg_conversations = int(await conn.fetchval(
                    "WITH d AS (DELETE FROM public.conversations RETURNING 1) SELECT COUNT(*) FROM d"
                ) or 0)
    except Exception as e:
        pg_error = str(e)
        logger.error("nuclear_reset_postgres_failed", error=pg_error)

    # 3) Supabase — customers table
    customers_deleted = 0
    customers_error = None
    try:
        customers_deleted = await sb.delete_all_customers()
    except Exception as e:
        customers_error = str(e)
        logger.error("nuclear_reset_customers_failed", error=customers_error)

    # 4) Supabase — auth users (preservar perfiles de agentes)
    auth_deleted = 0
    auth_error = None
    try:
        profiles = await sb.list_profiles()
        keep_emails = [p.get("email") for p in profiles if p.get("email")]
        auth_deleted = await sb.delete_all_customer_auth_users(keep_emails=keep_emails)
    except Exception as e:
        auth_error = str(e)
        logger.error("nuclear_reset_auth_failed", error=auth_error)

    logger.info(
        "nuclear_reset",
        redis_deleted=redis_total,
        pg_conversations=pg_conversations,
        pg_messages=pg_messages,
        customers_deleted=customers_deleted,
        auth_deleted=auth_deleted,
        by=user.get("username"),
    )
    return {
        "redis": {"deleted": redis_total, "by_pattern": redis_by_pattern},
        "postgres": {
            "conversations_deleted": pg_conversations,
            "messages_deleted": pg_messages,
            "error": pg_error,
        },
        "supabase": {
            "customers_deleted": customers_deleted,
            "customers_error": customers_error,
            "auth_users_deleted": auth_deleted,
            "auth_error": auth_error,
        },
        "message": (
            f"☢️ Reset nuclear completado — "
            f"Redis: {redis_total} | "
            f"Postgres: {pg_conversations} conv + {pg_messages} msg | "
            f"Supabase: {customers_deleted} customers + {auth_deleted} auth"
        ),
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
