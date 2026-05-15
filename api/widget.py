"""
API del widget web:
- POST /widget/chat → procesa mensaje y retorna respuesta
- GET  /widget/chat/stream → SSE streaming de respuesta
- GET  /widget/history/{session_id} → historial de conversación
"""

import asyncio
import json
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from channels.widget import generate_greeting_stateless, process_widget_message
from config.constants import Channel
from memory.conversation_store import get_conversation_store

router = APIRouter(prefix="/widget", tags=["widget"])
widget_limiter = Limiter(key_func=get_remote_address)


# ─── Presencia online/offline del visitante ──────────────────────────────────
# El widget manda heartbeats cada 30s. Si no hay heartbeat en 90s → offline.
# Status posibles:
#   - "active": pestaña visible y activa
#   - "hidden": pestaña cambiada / minimizada (browser tab oculta)
#   - "gone":   pagehide / beforeunload (cerró pestaña o navegó fuera)

PRESENCE_TTL_SECONDS = 90  # 3x el intervalo del heartbeat (30s)


def _presence_key(session_id: str) -> str:
    return f"widget_presence:{session_id}"


class PresenceRequest(BaseModel):
    session_id: str
    status: str = "active"  # "active" | "hidden" | "gone"


@router.post("/presence")
async def widget_presence(payload: PresenceRequest) -> dict:
    """Heartbeat del visitante. El widget llama cada 30s + en eventos clave."""
    if not payload.session_id:
        raise HTTPException(status_code=400, detail="missing session_id")
    status = payload.status if payload.status in ("active", "hidden", "gone") else "active"

    from datetime import datetime, timezone

    store = await get_conversation_store()
    r = store._redis
    now_iso = datetime.now(timezone.utc).isoformat()

    if status == "gone":
        # Visitante cerró pestaña / navegó fuera → marcamos como tal pero
        # mantenemos el TTL corto para que se limpie pronto.
        await r.setex(
            _presence_key(payload.session_id),
            PRESENCE_TTL_SECONDS,
            json.dumps({"status": "gone", "last_seen_at": now_iso}),
        )
    else:
        # active / hidden → renueva TTL completo.
        await r.setex(
            _presence_key(payload.session_id),
            PRESENCE_TTL_SECONDS,
            json.dumps({"status": status, "last_seen_at": now_iso}),
        )
    return {"ok": True}


async def get_presence_for(session_ids: list[str]) -> dict[str, dict]:
    """Trae presence (status + last_seen_at) para múltiples session_ids de un toque.
    Usado por inbox_api para inyectar el estado online/offline en el listado."""
    if not session_ids:
        return {}
    store = await get_conversation_store()
    r = store._redis
    keys = [_presence_key(sid) for sid in session_ids]
    raw = await r.mget(keys)
    out: dict[str, dict] = {}
    for sid, val in zip(session_ids, raw):
        if not val:
            out[sid] = {"status": "offline", "last_seen_at": None}
            continue
        try:
            data = json.loads(val if isinstance(val, str) else val.decode("utf-8"))
            # `hidden` lo tratamos como online para el inbox (la pestaña sigue abierta).
            out[sid] = {
                "status": "online" if data.get("status") in ("active", "hidden") else "offline",
                "last_seen_at": data.get("last_seen_at"),
            }
        except Exception:
            out[sid] = {"status": "offline", "last_seen_at": None}
    return out


class PaymentRejectionPayload(BaseModel):
    """
    Payload del evento `msk:paymentRejected` que dispara el frontend del site
    embebedor (msk-front) cuando el gateway (MP/Rebill/Stripe) rechaza un pago
    en el checkout. Se inyecta al contexto del agente para que arranque el
    turno explicando el motivo del rechazo y ofreciendo alternativas.
    """

    reason: str = ""  # razón corta del frontend (ej. "Fondos insuficientes")
    code: str = ""  # código del gateway (ej. "cc_rejected_insufficient_amount")
    message: str = ""  # mensaje crudo que el gateway devolvió al frontend
    gateway: str = ""  # "mercadopago" | "rebill" | "stripe" | ""


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    country: str = "AR"
    user_name: str = ""
    user_email: str = ""
    user_courses: str = ""
    # Datos extra del frontend (msk-front Next.js) — complementan al profile
    # que el backend pueda resolver en Supabase/Zoho. Si llegan, tienen
    # prioridad sobre lo que saque el backend por email. Permite personalizar
    # aunque Supabase no tenga el profile (ej. users recién creados).
    user_profession: str = ""  # ej. "Personal médico", "Residente", "Enfermería"
    user_specialty: str = ""  # ej. "Cardiología", "Alergia e inmunología"
    user_cargo: str = ""  # ej. "Jefe de servicio", "Coordinación"
    page_slug: str = ""  # slug del curso que está mirando el usuario (si aplica)
    initial_greeting: str = ""  # saludo stateless a persistir si la conv se crea recién
    payment_rejection: PaymentRejectionPayload | None = None  # rechazo de pago reciente del checkout


class GreetingRequest(BaseModel):
    user_name: str = ""
    user_email: str = ""
    user_courses: str = ""
    user_profession: str = ""
    user_specialty: str = ""
    user_cargo: str = ""
    page_slug: str = ""
    country: str = "AR"


class ChatResponse(BaseModel):
    session_id: str
    response: str
    agent_used: str
    handoff_requested: bool


@router.post("/chat", response_model=ChatResponse)
@widget_limiter.limit("30/minute")
async def chat(request: Request, req: ChatRequest):
    """
    Endpoint principal del widget web.
    El frontend envía el mensaje y recibe la respuesta del bot.
    """
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Mensaje vacío")

    session_id = req.session_id or str(uuid.uuid4())

    result = await process_widget_message(
        session_id=session_id,
        message_text=req.message,
        country=req.country,
        user_name=req.user_name,
        user_email=req.user_email,
        user_courses=req.user_courses,
        page_slug=req.page_slug,
        initial_greeting=req.initial_greeting,
        payment_rejection=(req.payment_rejection.model_dump() if req.payment_rejection else None),
    )

    return ChatResponse(
        session_id=session_id,
        response=result["response"],
        agent_used=result["agent_used"],
        handoff_requested=result["handoff_requested"],
    )


@router.post("/greeting")
@widget_limiter.limit("10/minute")
async def greeting(request: Request, req: GreetingRequest):
    """
    Genera el saludo personalizado SIN crear conversación.
    Se llama al cargar la página; el front lo muestra en el widget.
    La conversación recién se materializa cuando el user envía su primer
    mensaje real (`/widget/chat` con `initial_greeting` en el body).
    """
    data = await generate_greeting_stateless(
        user_name=req.user_name,
        user_email=req.user_email,
        user_courses=req.user_courses,
        user_profession=req.user_profession,
        user_specialty=req.user_specialty,
        user_cargo=req.user_cargo,
        page_slug=req.page_slug,
        country=req.country,
    )
    return data


@router.get("/resume")
async def resume(email: str):
    """
    Busca una conversación previa (activa) del widget para este email.
    Devuelve session_id + messages si existe, o {session_id: null} si no.
    Usado al cargar la página para usuarios logueados, para retomar
    historial sin tener que empezar de cero.
    """
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Email inválido")

    try:
        from memory import postgres_store as pg

        if not pg.is_enabled():
            return {"session_id": None, "messages": []}
        pool = await pg.get_pool()
        async with pool.acquire() as conn:
            # Retoma sólo conversaciones abiertas (active / handed_off)
            # de los últimos 30 días. Cerradas o más viejas arrancan fresh.
            row = await conn.fetchrow(
                """
                SELECT external_id
                FROM public.conversations
                WHERE channel = 'widget'
                  AND status IN ('active', 'handed_off')
                  AND user_profile->>'email' = $1
                  AND updated_at > now() - interval '30 days'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                email,
            )
    except Exception as e:
        return {"session_id": None, "messages": [], "error": str(e)}

    if not row:
        return {"session_id": None, "messages": []}

    session_id = row["external_id"]
    store = await get_conversation_store()
    conversation = await store.get_by_external(Channel.WIDGET, session_id)
    if not conversation:
        return {"session_id": None, "messages": []}

    messages = []
    for m in conversation.messages:
        msg = {
            "role": m.role.value,
            "content": m.content,
            "timestamp": m.timestamp.isoformat(),
            "agent": m.metadata.get("agent", ""),
        }
        if m.metadata.get("media_url"):
            msg["media_url"] = m.metadata["media_url"]
            msg["media_type"] = m.metadata.get("media_type", "")
            msg["media_mime"] = m.metadata.get("media_mime", "")
            msg["media_filename"] = m.metadata.get("media_filename", "")
        messages.append(msg)

    return {
        "session_id": session_id,
        "messages": messages,
        "status": conversation.status.value,
    }


@router.get("/chat/stream")
async def chat_stream(
    session_id: str,
    message: str,
    country: str = "AR",
    user_name: str = "",
    user_email: str = "",
    user_courses: str = "",
    page_slug: str = "",
):
    """
    SSE streaming — el widget recibe la respuesta palabra por palabra.
    Usar este endpoint para experiencia más fluida en el widget.
    """

    async def event_generator():
        # Procesar el mensaje normalmente
        result = await process_widget_message(
            session_id=session_id,
            message_text=message,
            country=country,
            user_name=user_name,
            user_email=user_email,
            user_courses=user_courses,
            page_slug=page_slug,
        )
        response = result["response"]

        # Simular streaming: enviar la respuesta por palabras
        words = response.split(" ")
        for i, word in enumerate(words):
            chunk = word + (" " if i < len(words) - 1 else "")
            data = json.dumps({"chunk": chunk, "done": False})
            yield f"data: {data}\n\n"
            await asyncio.sleep(0.02)

        # Evento final con metadata
        final = json.dumps(
            {
                "chunk": "",
                "done": True,
                "agent_used": result["agent_used"],
                "handoff_requested": result["handoff_requested"],
                "session_id": session_id,
            }
        )
        yield f"data: {final}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/history/{session_id}")
async def get_history(session_id: str):
    """Retorna el historial de mensajes de una sesión del widget."""
    store = await get_conversation_store()
    conversation = await store.get_by_external(Channel.WIDGET, session_id)
    if not conversation:
        return {"messages": [], "session_id": session_id}

    import re

    messages = []
    for m in conversation.messages:
        # Strip PII from content before returning to widget
        content = m.content
        # Redact email addresses
        content = re.sub(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "[email]", content)
        # Redact phone numbers (sequences of 7+ digits, optionally with +, -, spaces, parens)
        content = re.sub(r"[\+]?[\d\s\-\(\)]{7,}", "[phone]", content)

        msg = {
            "role": m.role.value,
            "content": content,
            "timestamp": m.timestamp.isoformat(),
            "agent": m.metadata.get("agent", ""),
        }
        if m.metadata.get("media_url"):
            msg["media_url"] = m.metadata["media_url"]
            msg["media_type"] = m.metadata.get("media_type", "")
            msg["media_mime"] = m.metadata.get("media_mime", "")
            msg["media_filename"] = m.metadata.get("media_filename", "")
        messages.append(msg)
    return {
        "messages": messages,
        "session_id": session_id,
        "status": conversation.status.value,
    }
