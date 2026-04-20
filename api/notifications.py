"""
API de notificaciones in-app.

Endpoints:
    GET    /api/v1/notifications                 → lista paginada (últimas N)
    GET    /api/v1/notifications/unread-count    → solo el número (para badge)
    POST   /api/v1/notifications/{id}/read       → marcar 1 como leída
    POST   /api/v1/notifications/mark-all-read   → bulk
    GET    /api/v1/notifications/preferences     → lee prefs
    PATCH  /api/v1/notifications/preferences     → actualiza prefs
    GET    /api/v1/notifications/stream          → SSE push (pubsub Redis)

Auth: dependiente de `get_current_user` (cookie de sesión). Cada endpoint
filtra por `user_id` del token — un user NO puede ver/modificar notifs
ajenas.

SSE con cookies httpOnly funciona directo — EventSource las manda auto si
`withCredentials: true` del lado frontend. No soportamos ?token= porque
migramos a cookies en el bloque enterprise 4.
"""

from __future__ import annotations

import asyncio
import json as _json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.auth import get_current_user
from utils.notifications import (
    DEFAULT_PREFERENCES,
    get_preferences,
    list_notifications,
    mark_all_read,
    mark_read,
    unread_count,
    update_preferences,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


# ──────────────────────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────────────────────


class PreferencesUpdate(BaseModel):
    conv_assigned: bool | None = None
    new_message_mine: bool | None = None
    conv_stale: bool | None = None
    template_approved: bool | None = None
    sound_enabled: bool | None = None
    email_digest: bool | None = None


# ──────────────────────────────────────────────────────────────────────────
# REST
# ──────────────────────────────────────────────────────────────────────────


@router.get("")
async def list_notifs(
    limit: int = Query(50, ge=1, le=200),
    unread_only: bool = Query(False),
    user: dict = Depends(get_current_user),
):
    items = await list_notifications(user["id"], limit=limit, unread_only=unread_only)
    return {"notifications": items, "count": len(items)}


@router.get("/unread-count")
async def get_unread_count(user: dict = Depends(get_current_user)):
    n = await unread_count(user["id"])
    return {"count": n}


@router.post("/{notif_id}/read")
async def mark_one_read(notif_id: str, user: dict = Depends(get_current_user)):
    ok = await mark_read(user["id"], notif_id)
    if not ok:
        # No lanzamos 404 — puede ser que ya estaba leída. El frontend idempotente.
        return {"ok": False, "already_read_or_missing": True}
    return {"ok": True}


@router.post("/mark-all-read")
async def mark_all(user: dict = Depends(get_current_user)):
    affected = await mark_all_read(user["id"])
    return {"ok": True, "marked": affected}


@router.get("/preferences")
async def read_prefs(user: dict = Depends(get_current_user)):
    prefs = await get_preferences(user["id"])
    # Asegura shape completo (si alguna key falta, rellena con default)
    out = {**DEFAULT_PREFERENCES, **prefs}
    return out


@router.patch("/preferences")
async def update_prefs(
    body: PreferencesUpdate,
    user: dict = Depends(get_current_user),
):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No hay campos para actualizar")
    return await update_preferences(user["id"], updates)


# ──────────────────────────────────────────────────────────────────────────
# SSE stream (Redis pubsub → EventSource)
# ──────────────────────────────────────────────────────────────────────────


@router.get("/stream")
async def notifications_stream(user: dict = Depends(get_current_user)):
    """SSE endpoint. Suscribe al canal Redis `notifications:{user_id}` y
    va emitiendo eventos al browser mientras la conexión esté viva.

    El browser reconecta automáticamente con `retry: 5000` si se corta.
    Cada 25s mandamos un ping (comment SSE) para que proxies intermedios
    no cierren la conexión por idle.
    """
    import redis.asyncio as aioredis

    from config.settings import get_settings

    settings = get_settings()
    user_id = user["id"]
    channel = f"notifications:{user_id}"

    async def event_gen():
        try:
            # Cliente Redis dedicado para este SSE — pubsub mantiene conexión
            # abierta. Usamos decode_responses para no parsear bytes.
            sub_client = aioredis.from_url(settings.redis_url, decode_responses=True)
            pubsub = sub_client.pubsub()
            try:
                await pubsub.subscribe(channel)
            except Exception as e:
                logger.warning(
                    "notifications_sse_subscribe_failed",
                    user_id=user_id,
                    error=str(e),
                )
                yield f"data: {_json.dumps({'event': 'error', 'detail': 'pubsub_unavailable'})}\n\n"
                return

            yield "retry: 5000\n\n"
            yield ": connected\n\n"

            # Loop principal: espera mensajes con timeout para intercalar pings.
            while True:
                try:
                    msg = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=25),
                        timeout=30,
                    )
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                if not msg:
                    yield ": ping\n\n"
                    continue
                if msg.get("type") != "message":
                    continue
                try:
                    payload = _json.loads(msg["data"])
                    yield f"data: {_json.dumps(payload)}\n\n"
                except Exception:
                    pass
        except asyncio.CancelledError:
            # Cliente cerró — normal al desmontar el hook en frontend.
            raise
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
                await sub_client.aclose()
            except Exception:
                pass

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # evita que nginx bufferee
        },
    )


# ──────────────────────────────────────────────────────────────────────────
# Helper para debugging / seeding manual
# ──────────────────────────────────────────────────────────────────────────


class DebugNotifyBody(BaseModel):
    type: str = "conv_assigned"
    data: dict[str, Any] | None = None


@router.post("/debug-notify-me")
async def debug_notify_me(
    body: DebugNotifyBody,
    user: dict = Depends(get_current_user),
):
    """Para smoke test: dispara una notif al user logueado.

    Útil para probar el dropdown sin esperar que pase un evento real.
    No tiene rate limit específico — el limiter global igual se aplica.
    """
    from utils.notifications import notify

    nid = await notify(user["id"], body.type, body.data or {"source": "debug"})
    return {"ok": True, "id": nid}
