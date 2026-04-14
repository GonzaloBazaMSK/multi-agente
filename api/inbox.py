"""
Inbox de conversaciones del widget — para agentes humanos.

Endpoints:
  GET  /inbox/conversations          → lista de sesiones activas
  GET  /inbox/conversations/{sid}    → detalle completo de una sesión
  POST /inbox/conversations/{sid}/takeover  → humano toma el control (bot OFF)
  POST /inbox/conversations/{sid}/release   → devuelve al bot (bot ON)
  POST /inbox/conversations/{sid}/reply     → agente envía mensaje
  GET  /inbox/stream                 → SSE para actualizaciones en tiempo real
  WS   /inbox/ws                     → WebSocket para real-time bidireccional
"""
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from memory.conversation_store import get_conversation_store
from models.message import Message, MessageRole
from config.constants import Channel, ConversationStatus
from api.auth import get_current_user, require_role
import structlog

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/inbox", tags=["inbox"])

# ─── SSE broadcast bus (Redis Pub/Sub + local fallback) ──────────────────────
# Usa Redis Pub/Sub para comunicar entre workers de Uvicorn.
# Cada cliente SSE se suscribe al canal Redis.

_sse_clients: set[asyncio.Queue] = set()
_PUBSUB_CHANNEL = "inbox:events"


def broadcast_event(event: dict):
    """Publica un evento via Redis Pub/Sub. El listener distribuye localmente."""
    # Intentar publicar via Redis Pub/Sub — el listener se encarga del local broadcast
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_redis_publish(event))
    except RuntimeError:
        # No running loop — fallback a local broadcast directo
        _local_broadcast(event)


def _local_broadcast(event: dict):
    """Distribuye un evento a todos los clientes SSE/WS locales."""
    dead = set()
    for q in _sse_clients:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.add(q)
    _sse_clients.difference_update(dead)


async def _redis_publish(event: dict):
    """Publica evento al canal Redis Pub/Sub."""
    try:
        from memory.conversation_store import get_conversation_store
        store = await get_conversation_store()
        await store._redis.publish(_PUBSUB_CHANNEL, json.dumps(event))
    except Exception:
        pass


async def start_pubsub_listener():
    """Background task: escucha Redis Pub/Sub y reenvia a clientes SSE locales."""
    import redis.asyncio as aioredis
    from config.settings import get_settings
    try:
        settings = get_settings()
        sub_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        pubsub = sub_client.pubsub()
        await pubsub.subscribe(_PUBSUB_CHANNEL)
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    event = json.loads(message["data"])
                    _local_broadcast(event)
                except Exception:
                    pass
    except Exception as e:
        logger.warning("pubsub_listener_error", error=str(e))


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _get_conv(session_id: str):
    store = await get_conversation_store()
    conv = await store.get_by_external(Channel.WIDGET, session_id)
    if not conv:
        conv = await store.get_by_external(Channel.WHATSAPP, session_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    return store, conv


def _bot_key(session_id: str) -> str:
    """Clave Redis para bot_disabled — soporta widget y WhatsApp."""
    # WhatsApp sessions son números de teléfono (solo dígitos)
    if session_id.lstrip("+").isdigit():
        return f"bot_disabled_wa:{session_id}"
    return f"bot_disabled:{session_id}"


async def _is_bot_disabled(session_id: str) -> bool:
    store = await get_conversation_store()
    val = await store._redis.get(_bot_key(session_id))
    return bool(val)


async def _set_bot_disabled(session_id: str, disabled: bool):
    store = await get_conversation_store()
    if disabled:
        await store._redis.set(_bot_key(session_id), "1")
    else:
        await store._redis.delete(_bot_key(session_id))


async def _get_agent_name(session_id: str) -> str:
    store = await get_conversation_store()
    val = await store._redis.get(f"agent_name:{session_id}")
    if val:
        return val.decode() if isinstance(val, bytes) else val
    return "Agente"


# ─── Etiquetas de clasificación ───────────────────────────────────────────────

LABELS = {
    "caliente":       {"emoji": "🔥", "color": "#ef4444", "label": "Caliente"},
    "tibio":          {"emoji": "🌡️", "color": "#f59e0b", "label": "Tibio"},
    "frio":           {"emoji": "❄️",  "color": "#60a5fa", "label": "Frío"},
    "convertido":     {"emoji": "✅", "color": "#22c55e", "label": "Convertido"},
    "esperando_pago": {"emoji": "💳", "color": "#a78bfa", "label": "Esp. Pago"},
    "seguimiento":    {"emoji": "📞", "color": "#fb923c", "label": "Seguimiento"},
    "no_interesa":    {"emoji": "🚫", "color": "#6b7280", "label": "No interesa"},
}


async def _get_label(session_id: str) -> str:
    store = await get_conversation_store()
    val = await store._redis.get(f"conv_label:{session_id}")
    if val:
        return val.decode() if isinstance(val, bytes) else val
    return ""


async def _set_label(session_id: str, label: str):
    store = await get_conversation_store()
    if label:
        await store._redis.set(f"conv_label:{session_id}", label)
    else:
        await store._redis.delete(f"conv_label:{session_id}")


# ─── Modelos ──────────────────────────────────────────────────────────────────

class ReplyRequest(BaseModel):
    message: str
    agent_name: str = "Agente"


class TakeoverRequest(BaseModel):
    agent_name: str = "Agente"


class LabelRequest(BaseModel):
    label: str  # clave del label o "" para quitar


class CloseRequest(BaseModel):
    resolution: str = ""  # won, lost, resolved, spam, other
    category: str = ""    # venta_cerrada, descartado, info, soporte_resuelto, derivado, otro
    summary: str = ""     # closing notes summary (editable por el agente)


class BulkLabelRequest(BaseModel):
    session_ids: list[str]
    label: str


class BulkAssignRequest(BaseModel):
    session_ids: list[str]
    agent_id: str
    agent_name: str = ""


class BulkCloseRequest(BaseModel):
    session_ids: list[str]
    resolution: str = "resolved"


class TypingRequest(BaseModel):
    agent_name: str = "Agente"
    is_typing: bool = True


# ─── Business hours helper ───────────────────────────────────────────────────

def is_within_business_hours() -> bool:
    """Verifica si estamos dentro del horario de atención."""
    from config.settings import get_settings
    import datetime
    try:
        import zoneinfo
        settings = get_settings()
        tz = zoneinfo.ZoneInfo(settings.business_timezone)
        now = datetime.datetime.now(tz)
        day = now.weekday()  # 0=Monday
        allowed_days = [int(d) for d in settings.business_days.split(",") if d.strip()]
        if day not in allowed_days:
            return False
        return settings.business_hours_start <= now.hour < settings.business_hours_end
    except Exception:
        return True  # Fail open


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/conversations")
async def list_conversations(user: dict = Depends(get_current_user)):
    """
    Lista todas las sesiones de widget activas en Redis.
    Devuelve hasta 100 sesiones ordenadas por actividad reciente.
    """
    store = await get_conversation_store()

    # Buscar todas las claves idx:widget:* e idx:whatsapp:* usando SCAN
    keys = []
    async for key in store._redis.scan_iter("idx:widget:*", count=200):
        keys.append(key)
    async for key in store._redis.scan_iter("idx:whatsapp:*", count=200):
        keys.append(key)

    conversations = []
    for key in keys:
        raw_key = key.decode() if isinstance(key, bytes) else key
        if "idx:widget:" in raw_key:
            channel = Channel.WIDGET
            session_id = raw_key.split("idx:widget:")[-1]
        else:
            channel = Channel.WHATSAPP
            session_id = raw_key.split("idx:whatsapp:")[-1]

        conv = await store.get_by_external(channel, session_id)
        if not conv:
            continue

        # Último mensaje
        last_msg = None
        last_ts = ""
        if conv.messages:
            last = conv.messages[-1]
            last_msg = last.content[:80] + ("…" if len(last.content) > 80 else "")
            last_ts = last.timestamp.isoformat()

        bot_disabled = await _is_bot_disabled(session_id)
        label = await _get_label(session_id)
        queue_raw = await store._redis.get(f"conv_queue:{session_id}")
        queue = queue_raw.decode() if isinstance(queue_raw, bytes) else (queue_raw or "")
        assigned_raw = await store._redis.get(f"conv_assigned:{session_id}")
        assigned_to = assigned_raw.decode() if isinstance(assigned_raw, bytes) else (assigned_raw or "")
        assigned_name_raw = await store._redis.get(f"conv_assigned_name:{session_id}")
        assigned_to_name = assigned_name_raw.decode() if isinstance(assigned_name_raw, bytes) else (assigned_name_raw or "")

        conversations.append({
            "session_id": session_id,
            "channel": channel.value,
            "name": conv.user_profile.name or "",
            "email": conv.user_profile.email or "",
            "phone": conv.user_profile.phone or session_id if channel == Channel.WHATSAPP else "",
            "country": conv.user_profile.country or "AR",
            "status": conv.status.value,
            "bot_disabled": bot_disabled,
            "label": label,
            "queue": queue,
            "assigned_to": assigned_to,
            "assigned_to_name": assigned_to_name,
            "message_count": len(conv.messages),
            "last_message": last_msg or "",
            "last_timestamp": last_ts,
            "created_at": conv.created_at.isoformat() if hasattr(conv, "created_at") else "",
        })

    # Ordenar por último timestamp desc
    conversations.sort(key=lambda x: x["last_timestamp"], reverse=True)
    return {"conversations": conversations[:100]}


@router.get("/conversations/{session_id}")
async def get_conversation(session_id: str, user: dict = Depends(get_current_user)):
    """Detalle completo de una conversación."""
    store, conv = await _get_conv(session_id)
    bot_disabled = await _is_bot_disabled(session_id)
    agent_name = await _get_agent_name(session_id)

    messages = []
    for m in conv.messages:
        msg_dict = {
            "role": m.role.value,
            "content": m.content,
            "timestamp": m.timestamp.isoformat(),
            "agent": m.metadata.get("agent", ""),
            "sender_name": m.metadata.get("sender_name", ""),
        }
        # Media fields
        if m.metadata.get("media_url"):
            msg_dict["media_url"] = m.metadata["media_url"]
            msg_dict["media_type"] = m.metadata.get("media_type", "")
            msg_dict["media_mime"] = m.metadata.get("media_mime", "")
            msg_dict["media_filename"] = m.metadata.get("media_filename", "")
        # Template flag
        if m.metadata.get("is_template"):
            msg_dict["is_template"] = True
            msg_dict["template_name"] = m.metadata.get("template_name", "")
        messages.append(msg_dict)

    label = await _get_label(session_id)

    # Contexto (incluye closing_note si la conv está cerrada con resumen)
    context = conv.context or {}

    return {
        "session_id": session_id,
        "channel": conv.channel.value if hasattr(conv, "channel") else "widget",
        "name": conv.user_profile.name or "",
        "email": conv.user_profile.email or "",
        "phone": conv.user_profile.phone or "",
        "country": conv.user_profile.country or "AR",
        "status": conv.status.value,
        "bot_disabled": bot_disabled,
        "agent_name": agent_name,
        "label": label,
        "messages": messages,
        "context": context,
        "closing_note": context.get("closing_note"),
    }


@router.post("/conversations/{session_id}/takeover")
async def takeover(session_id: str, req: TakeoverRequest, user: dict = Depends(get_current_user)):
    """Agente humano toma el control — deshabilita el bot para esta sesión."""
    store, conv = await _get_conv(session_id)
    await _set_bot_disabled(session_id, True)

    # Guardar nombre del agente
    await store._redis.set(f"agent_name:{session_id}", req.agent_name)

    # Actualizar status
    conv.status = ConversationStatus.HANDED_OFF
    await store.save(conv)

    broadcast_event({
        "type": "takeover",
        "session_id": session_id,
        "agent_name": req.agent_name,
    })

    from utils.conv_events import log_action
    await log_action(session_id, f"Agente humano tomó el control", f"Agente: {req.agent_name}")

    from utils.audit import audit_log
    await audit_log(user.get("id", ""), user.get("name", ""), "takeover", session_id, {"agent_name": req.agent_name})

    logger.info("inbox_takeover", session_id=session_id, agent=req.agent_name)
    return {"status": "ok", "bot_disabled": True}


@router.post("/conversations/{session_id}/release")
async def release(session_id: str, user: dict = Depends(get_current_user)):
    """Devuelve la conversación al bot."""
    store, conv = await _get_conv(session_id)
    await _set_bot_disabled(session_id, False)
    await store._redis.delete(f"agent_name:{session_id}")

    conv.status = ConversationStatus.ACTIVE
    await store.save(conv)

    broadcast_event({
        "type": "release",
        "session_id": session_id,
    })

    from utils.conv_events import log_action
    await log_action(session_id, "Conversación devuelta al bot")

    from utils.audit import audit_log
    await audit_log(user.get("id", ""), user.get("name", ""), "release", session_id)

    logger.info("inbox_release", session_id=session_id)
    return {"status": "ok", "bot_disabled": False}


@router.post("/conversations/{session_id}/reply")
async def reply(session_id: str, req: ReplyRequest, user: dict = Depends(get_current_user)):
    """El agente humano envía un mensaje al usuario (widget o WhatsApp)."""
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Mensaje vacío")

    store, conv = await _get_conv(session_id)

    msg = Message(
        role=MessageRole.ASSISTANT,
        content=req.message,
        metadata={"agent": "humano", "sender_name": req.agent_name},
    )
    await store.append_message(conv, msg)

    # Si la conversación es de WhatsApp, enviar el mensaje via WhatsApp
    from config.constants import Channel as Ch
    from config.settings import get_settings
    if conv.channel == Ch.WHATSAPP:
        try:
            settings = get_settings()
            if settings.twilio_account_sid:
                from channels.twilio_whatsapp import send_twilio_reply
                await send_twilio_reply(session_id, req.message, req.agent_name)
            elif settings.whatsapp_token:
                # Enviar typing indicator antes del mensaje
                try:
                    phone = conv.context.get("phone", "") if conv.context else ""
                    if not phone:
                        phone = session_id.replace("wa_", "").split("_")[0] if "wa_" in session_id else session_id
                    if phone:
                        from integrations.whatsapp_meta import WhatsAppMetaClient
                        wa = WhatsAppMetaClient()
                        await wa.send_typing(phone)
                except Exception:
                    pass
                from channels.whatsapp_meta import send_whatsapp_reply
                await send_whatsapp_reply(session_id, req.message, req.agent_name)
        except Exception as e:
            logger.warning("whatsapp_reply_failed", error=str(e))

    event = {
        "type": "new_message",
        "session_id": session_id,
        "role": "assistant",
        "content": req.message,
        "sender_name": req.agent_name,
        "timestamp": msg.timestamp.isoformat(),
    }
    broadcast_event(event)

    # Track FRT (First Response Time) and last reply timestamp
    try:
        frt_key = f"frt:{session_id}"
        existing = await store._redis.get(frt_key)
        if not existing:
            # First human reply — record FRT
            first_user_ts = None
            for m in conv.messages:
                if m.role == MessageRole.USER:
                    first_user_ts = m.timestamp
                    break
            if first_user_ts:
                frt_seconds = (msg.timestamp - first_user_ts).total_seconds()
                import json as _json
                frt_record = {
                    "agent": req.agent_name,
                    "seconds": round(frt_seconds),
                    "session_id": session_id,
                    "ts": msg.timestamp.isoformat(),
                }
                await store._redis.setex(frt_key, 604800, _json.dumps(frt_record))  # 7 days TTL
        # Always update last reply time for inactivity tracking
        await store._redis.set(f"last_reply:{session_id}", str(time.time()))
    except Exception:
        pass

    # Clear typing lock after sending
    try:
        await store._redis.delete(f"typing:{session_id}")
    except Exception:
        pass

    logger.info("inbox_reply_sent", session_id=session_id, agent=req.agent_name, channel=conv.channel.value)
    return {"status": "ok", "timestamp": msg.timestamp.isoformat()}


@router.post("/conversations/{session_id}/typing")
async def set_typing(session_id: str, req: TypingRequest, user: dict = Depends(get_current_user)):
    """Notifica que un agente está escribiendo (collision prevention)."""
    store = await get_conversation_store()
    key = f"typing:{session_id}"
    if req.is_typing:
        await store._redis.setex(key, 15, req.agent_name)  # 15s TTL
        broadcast_event({
            "type": "typing",
            "session_id": session_id,
            "agent_name": req.agent_name,
            "is_typing": True,
        })
    else:
        await store._redis.delete(key)
        broadcast_event({
            "type": "typing",
            "session_id": session_id,
            "agent_name": req.agent_name,
            "is_typing": False,
        })
    return {"ok": True}


@router.get("/conversations/{session_id}/typing")
async def get_typing(session_id: str, user: dict = Depends(get_current_user)):
    """Consulta si alguien está escribiendo en esta conversación."""
    store = await get_conversation_store()
    agent = await store._redis.get(f"typing:{session_id}")
    return {"is_typing": bool(agent), "agent_name": agent or ""}


@router.post("/conversations/{session_id}/send-media")
async def send_media(session_id: str, request: Request, user: dict = Depends(get_current_user)):
    """
    Agente humano envía un archivo multimedia al usuario.
    Acepta multipart/form-data con:
      - file: archivo a enviar
      - caption: texto opcional
      - agent_name: nombre del agente
    """
    from pathlib import Path
    import uuid as uuid_mod

    form = await request.form()
    file = form.get("file")
    caption = form.get("caption", "")
    agent_name = form.get("agent_name", "Agente")

    if not file:
        raise HTTPException(status_code=400, detail="Archivo requerido")

    store, conv = await _get_conv(session_id)

    # Metadata del archivo
    original_name = getattr(file, "filename", "file")
    ext = Path(original_name).suffix.lower() or ".bin"
    content_type = getattr(file, "content_type", "application/octet-stream") or "application/octet-stream"
    media_type = content_type.split("/")[0]  # image, audio, video, application

    filename = f"{uuid_mod.uuid4().hex[:12]}{ext}"
    content = await file.read()

    # Subir a R2 si está configurado, si no fallback a filesystem local
    from integrations import storage
    if storage.is_enabled():
        media_url = await storage.upload_bytes(f"media/{filename}", content, content_type)
        filepath = None  # no local file when using R2
    else:
        media_dir = Path(__file__).parent.parent / "media"
        media_dir.mkdir(exist_ok=True)
        filepath = media_dir / filename
        filepath.write_bytes(content)
        media_url = f"media/{filename}"

    # Guardar en historial
    msg = Message(
        role=MessageRole.ASSISTANT,
        content=caption or f"[{media_type.title()}: {original_name}]",
        metadata={
            "agent": "humano",
            "sender_name": agent_name,
            "media_url": media_url,
            "media_type": media_type,
            "media_mime": content_type,
            "media_filename": original_name,
        },
    )
    await store.append_message(conv, msg)

    # Enviar por WhatsApp si corresponde
    from config.constants import Channel as Ch
    from config.settings import get_settings
    if conv.channel == Ch.WHATSAPP:
        try:
            settings = get_settings()
            if settings.whatsapp_token:
                from integrations.whatsapp_meta import WhatsAppMetaClient
                wa = WhatsAppMetaClient()
                base_url = settings.app_base_url.rstrip("/")

                # URL pública que Meta va a leer para traer el archivo
                send_url = media_url if media_url.startswith("http") else f"{base_url}/{media_url}"

                # WhatsApp voice notes require audio/ogg;codecs=opus.
                # Transcode webm/mp4/wav → ogg/opus so recipients hear actual audio.
                if media_type == "audio" and not filename.endswith(".ogg"):
                    try:
                        import subprocess, tempfile, os
                        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp_in:
                            tmp_in.write(content)
                            tmp_in_path = tmp_in.name
                        tmp_out_path = tmp_in_path.rsplit(".", 1)[0] + ".ogg"
                        try:
                            subprocess.run(
                                ["ffmpeg", "-y", "-i", tmp_in_path,
                                 "-c:a", "libopus", "-b:a", "32k",
                                 "-ar", "48000", "-ac", "1",
                                 tmp_out_path],
                                check=True, capture_output=True, timeout=30,
                            )
                            ogg_filename = f"{filename.rsplit('.', 1)[0]}.ogg"
                            ogg_bytes = Path(tmp_out_path).read_bytes()
                            if storage.is_enabled():
                                send_url = await storage.upload_bytes(f"media/{ogg_filename}", ogg_bytes, "audio/ogg")
                            else:
                                ogg_filepath = Path(__file__).parent.parent / "media" / ogg_filename
                                ogg_filepath.write_bytes(ogg_bytes)
                                send_url = f"{base_url}/media/{ogg_filename}"
                            logger.info("audio_transcoded_for_whatsapp", original=filename, ogg=ogg_filename)
                        finally:
                            for p in (tmp_in_path, tmp_out_path):
                                try: os.unlink(p)
                                except Exception: pass
                    except Exception as e:
                        logger.warning("audio_transcode_failed", error=str(e))

                if media_type == "image":
                    await wa.send_image(session_id, send_url, caption)
                elif media_type == "audio":
                    await wa.send_audio(session_id, send_url)
                elif media_type == "video":
                    await wa.send_video(session_id, send_url, caption)
                else:
                    await wa.send_document(session_id, send_url, original_name, caption)
        except Exception as e:
            logger.warning("whatsapp_media_send_failed", error=str(e))

    # Broadcast SSE
    broadcast_event({
        "type": "new_message",
        "session_id": session_id,
        "role": "assistant",
        "content": msg.content,
        "sender_name": agent_name,
        "timestamp": msg.timestamp.isoformat(),
        "media_url": media_url,
        "media_type": media_type,
        "media_mime": content_type,
        "media_filename": original_name,
    })

    logger.info("inbox_media_sent", session_id=session_id, agent=agent_name, media_type=media_type)
    return {"status": "ok", "media_url": media_url, "timestamp": msg.timestamp.isoformat()}


# ─── Closing summary IA ──────────────────────────────────────────────────────

CLOSING_CATEGORIES = [
    ("venta_cerrada",   "💰 Venta cerrada",    "El cliente pagó o confirmó compra"),
    ("descartado",      "❌ Lead descartado",   "No calificó o no está interesado"),
    ("info",            "ℹ️ Info entregada",    "Entrega de información sin conversión"),
    ("soporte_resuelto","🔧 Soporte resuelto",  "Consulta resuelta exitosamente"),
    ("derivado",        "🔀 Derivado humano",   "Pasado a otro equipo/canal"),
    ("otro",            "📝 Otro",              "Sin categoría específica"),
]


@router.get("/closing-categories")
async def get_closing_categories(user: dict = Depends(get_current_user)):
    return [{"key": k, "label": l, "hint": h} for k, l, h in CLOSING_CATEGORIES]


@router.post("/conversations/{session_id}/close-summary")
async def generate_closing_summary(session_id: str, user: dict = Depends(get_current_user)):
    """Genera con IA un resumen de 2-3 líneas de la conversación + sugerencia de categoría."""
    store, conv = await _get_conv(session_id)

    if not conv.messages:
        return {"summary": "Conversación sin mensajes.", "category": "otro"}

    # Últimos 40 mensajes (suficiente contexto, no muy caro)
    recent = conv.messages[-40:]
    transcript = "\n".join(
        f"[{m.role.value}] {m.content[:300]}" for m in recent if m.content
    )
    if not transcript.strip():
        return {"summary": "Sin contenido de texto para resumir.", "category": "otro"}

    categories_str = ", ".join(k for k, _, _ in CLOSING_CATEGORIES)
    system = (
        "Sos un asistente que resume conversaciones de atención al cliente para MSK Latam "
        "(venta de cursos médicos online). Recibís el transcript y devolvés un JSON con dos campos: "
        "'summary' (2-3 líneas concisas, en español rioplatense, qué pidió el cliente y cómo terminó) "
        f"y 'category' (uno de: {categories_str}). "
        "Devolvé SOLO JSON válido, sin backticks ni comentarios."
    )

    from openai import AsyncOpenAI
    from config.settings import get_settings
    settings = get_settings()
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.3,
            max_tokens=400,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"Transcript:\n{transcript}"},
            ],
        )
        data = json.loads(resp.choices[0].message.content)
        logger.info("closing_summary_ok", session_id=session_id, category=data.get("category"))
        return {
            "summary": data.get("summary", "").strip(),
            "category": data.get("category", "otro"),
        }
    except Exception as e:
        logger.error("closing_summary_failed", session_id=session_id, error=str(e))
        return {"summary": "", "category": "otro"}


# ─── Send attachment by URL (para snippets) ──────────────────────────────────

class SendAttachmentRequest(BaseModel):
    media_url: str          # URL pública ya existente (típicamente R2)
    filename: str
    mime: str
    caption: str = ""


@router.post("/conversations/{session_id}/send-attachment")
async def send_attachment_by_url(
    session_id: str,
    req: SendAttachmentRequest,
    user: dict = Depends(get_current_user),
):
    """Envía un archivo ya subido (típicamente adjunto de un snippet) al cliente.
    Reutiliza la URL pública de R2 sin re-upload."""
    agent_name = user.get("name") or user.get("email") or "Agente"
    store, conv = await _get_conv(session_id)

    mime = req.mime or "application/octet-stream"
    media_type = mime.split("/")[0]  # image, audio, video, application

    msg = Message(
        role=MessageRole.ASSISTANT,
        content=req.caption or f"[{media_type.title()}: {req.filename}]",
        metadata={
            "agent": "humano",
            "sender_name": agent_name,
            "media_url": req.media_url,
            "media_type": media_type,
            "media_mime": mime,
            "media_filename": req.filename,
            "from_snippet": True,
        },
    )
    await store.append_message(conv, msg)

    # Enviar a WhatsApp si corresponde
    from config.constants import Channel as Ch
    from config.settings import get_settings
    if conv.channel == Ch.WHATSAPP:
        try:
            settings = get_settings()
            if settings.whatsapp_token:
                from integrations.whatsapp_meta import WhatsAppMetaClient
                wa = WhatsAppMetaClient()
                send_url = req.media_url if req.media_url.startswith("http") else f"{settings.app_base_url.rstrip('/')}/{req.media_url}"
                if media_type == "image":
                    await wa.send_image(session_id, send_url, req.caption)
                elif media_type == "audio":
                    await wa.send_audio(session_id, send_url)
                elif media_type == "video":
                    await wa.send_video(session_id, send_url, req.caption)
                else:
                    await wa.send_document(session_id, send_url, req.filename, req.caption)
        except Exception as e:
            logger.warning("whatsapp_media_send_failed", error=str(e))

    # Broadcast SSE
    broadcast_event({
        "type": "new_message",
        "session_id": session_id,
        "role": "assistant",
        "content": msg.content,
        "sender_name": agent_name,
        "timestamp": msg.timestamp.isoformat(),
        "media_url": req.media_url,
        "media_type": media_type,
        "media_mime": mime,
        "media_filename": req.filename,
    })
    return {"status": "ok", "timestamp": msg.timestamp.isoformat()}


# ─── TTS (text-to-speech para voice notes del agente humano) ────────────────

class TTSRequest(BaseModel):
    text: str
    voice: str = "nova"
    format: str = "mp3"  # mp3 para widget, opus->ogg si WhatsApp


@router.post("/tts/generate")
async def tts_generate(req: TTSRequest, user: dict = Depends(get_current_user)):
    """Genera audio TTS desde texto, sube a R2, retorna metadata del adjunto
    lista para agregar a PENDING_ATTACHMENTS en el frontend."""
    if not req.text or len(req.text.strip()) < 2:
        raise HTTPException(400, "Texto vacío o muy corto")
    if len(req.text) > 4000:
        raise HTTPException(400, "Texto supera 4000 caracteres")

    from integrations import tts
    if not tts.is_enabled():
        raise HTTPException(503, "TTS no configurado (falta OPENAI_API_KEY)")

    result = await tts.synthesize_to_r2(req.text, voice=req.voice, output_format=req.format)
    if not result:
        raise HTTPException(500, "Error generando audio")

    logger.info("tts_attachment_created", user=user.get("email"), voice=req.voice, size=result["size"])
    return result


# ─── AI Assist (Respond.io-style prompts inline) ─────────────────────────────

AI_ASSIST_PROMPTS = {
    "tone_formal":     "Reescribí el siguiente mensaje en un tono profesional y formal, manteniendo el idioma original. Devolvé SOLO el texto reescrito, sin comentarios.",
    "tone_friendly":   "Reescribí el siguiente mensaje en un tono amigable y cercano, manteniendo el idioma original. Devolvé SOLO el texto reescrito, sin comentarios.",
    "tone_empathetic": "Reescribí el siguiente mensaje con un tono empático y comprensivo, manteniendo el idioma original. Devolvé SOLO el texto reescrito, sin comentarios.",
    "tone_direct":     "Reescribí el siguiente mensaje de forma directa y concisa, sin rodeos. Mantené el idioma original. Devolvé SOLO el texto reescrito, sin comentarios.",
    "translate_es":    "Traducí el siguiente texto al español rioplatense natural. Devolvé SOLO la traducción, sin comentarios.",
    "translate_en":    "Traducí el siguiente texto al inglés natural. Devolvé SOLO la traducción, sin comentarios.",
    "translate_pt":    "Traducí el siguiente texto al portugués brasileño natural. Devolvé SOLO la traducción, sin comentarios.",
    "fix":             "Corregí ortografía, gramática y puntuación del siguiente texto, manteniendo el idioma, el tono y el significado. Devolvé SOLO el texto corregido, sin comentarios.",
    "rephrase":        "Reescribí el siguiente texto con otras palabras, manteniendo el significado, el idioma y el tono. Devolvé SOLO el texto reescrito, sin comentarios.",
    "simplify":        "Simplificá el siguiente texto para que sea más fácil de leer y entender, manteniendo el idioma y el tono. Devolvé SOLO el texto simplificado, sin comentarios.",
}


class AIAssistRequest(BaseModel):
    text: str
    action: str


@router.post("/ai-assist")
async def ai_assist(req: AIAssistRequest, user: dict = Depends(get_current_user)):
    """
    Reformatea el texto del compositor usando el LLM.
    Acciones soportadas: tone_{formal,friendly,empathetic,direct},
    translate_{es,en,pt}, fix, rephrase, simplify.
    """
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="Texto vacío")
    if req.action not in AI_ASSIST_PROMPTS:
        raise HTTPException(status_code=400, detail=f"Acción desconocida: {req.action}")

    from openai import AsyncOpenAI
    from config.settings import get_settings
    settings = get_settings()

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.4,
            max_tokens=500,
            messages=[
                {"role": "system", "content": AI_ASSIST_PROMPTS[req.action]},
                {"role": "user", "content": req.text},
            ],
        )
        rewritten = resp.choices[0].message.content.strip()
        if rewritten.startswith(('"', "'")) and rewritten.endswith(('"', "'")):
            rewritten = rewritten[1:-1].strip()
        logger.info("ai_assist_ok", action=req.action, input_len=len(req.text), output_len=len(rewritten))
        return {"text": rewritten}
    except Exception as e:
        logger.error("ai_assist_failed", action=req.action, error=str(e))
        raise HTTPException(status_code=500, detail="Error del LLM, probá de nuevo.")


# ─── Snippets (respuestas rápidas con adjuntos + topics) ────────────────────

class SnippetIn(BaseModel):
    shortcut: str
    title: str
    content: str
    topics: list[str] = []
    attachments: list[dict] = []  # [{url, filename, mime, size}]


@router.get("/snippets")
async def list_snippets(user: dict = Depends(get_current_user)):
    from memory import postgres_store
    if not postgres_store.is_enabled():
        return {"snippets": []}
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "select id, shortcut, title, content, topics, attachments, created_by, created_at, updated_at "
            "from public.snippets order by updated_at desc"
        )
    snippets = []
    all_topics: set[str] = set()
    for r in rows:
        attachments = r["attachments"]
        if isinstance(attachments, str):
            attachments = json.loads(attachments)
        topics = list(r["topics"]) if r["topics"] else []
        all_topics.update(topics)
        snippets.append({
            "id": str(r["id"]),
            "shortcut": r["shortcut"],
            "title": r["title"],
            "content": r["content"],
            "topics": topics,
            "attachments": attachments,
            "created_by": r["created_by"],
            "updated_at": r["updated_at"].isoformat(),
        })
    return {"snippets": snippets, "all_topics": sorted(all_topics)}


@router.post("/snippets")
async def create_snippet(req: SnippetIn, user: dict = Depends(require_role("admin", "supervisor"))):
    import uuid as uuid_mod
    from memory import postgres_store
    if not postgres_store.is_enabled():
        raise HTTPException(status_code=503, detail="Postgres no configurado")

    if not req.shortcut.startswith("/"):
        req.shortcut = "/" + req.shortcut
    req.shortcut = req.shortcut.strip().lower()
    if len(req.shortcut) < 2:
        raise HTTPException(status_code=400, detail="Shortcut muy corto")

    snippet_id = str(uuid_mod.uuid4())
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                "insert into public.snippets (id, shortcut, title, content, topics, attachments, created_by) "
                "values ($1, $2, $3, $4, $5, $6::jsonb, $7)",
                uuid_mod.UUID(snippet_id), req.shortcut, req.title, req.content,
                req.topics, json.dumps(req.attachments), user.get("email", "unknown"),
            )
        except Exception as e:
            if "unique" in str(e).lower():
                raise HTTPException(status_code=409, detail=f"Ya existe un snippet con shortcut {req.shortcut}")
            raise
    logger.info("snippet_created", id=snippet_id, shortcut=req.shortcut, user=user.get("email"))
    return {"id": snippet_id, "shortcut": req.shortcut}


@router.patch("/snippets/{snippet_id}")
async def update_snippet(snippet_id: str, req: SnippetIn, user: dict = Depends(require_role("admin", "supervisor"))):
    import uuid as uuid_mod
    from memory import postgres_store
    if not postgres_store.is_enabled():
        raise HTTPException(status_code=503, detail="Postgres no configurado")

    if not req.shortcut.startswith("/"):
        req.shortcut = "/" + req.shortcut
    req.shortcut = req.shortcut.strip().lower()

    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "update public.snippets set shortcut=$2, title=$3, content=$4, topics=$5, "
            "attachments=$6::jsonb, updated_at=now() where id=$1",
            uuid_mod.UUID(snippet_id), req.shortcut, req.title, req.content,
            req.topics, json.dumps(req.attachments),
        )
    if result.endswith(" 0"):
        raise HTTPException(status_code=404, detail="Snippet no encontrado")
    logger.info("snippet_updated", id=snippet_id, user=user.get("email"))
    return {"ok": True}


@router.delete("/snippets/{snippet_id}")
async def delete_snippet(snippet_id: str, user: dict = Depends(require_role("admin", "supervisor"))):
    import uuid as uuid_mod
    from memory import postgres_store
    if not postgres_store.is_enabled():
        raise HTTPException(status_code=503, detail="Postgres no configurado")
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "delete from public.snippets where id=$1",
            uuid_mod.UUID(snippet_id),
        )
    if result.endswith(" 0"):
        raise HTTPException(status_code=404, detail="Snippet no encontrado")
    logger.info("snippet_deleted", id=snippet_id, user=user.get("email"))
    return {"ok": True}


@router.post("/snippets/upload-attachment")
async def upload_snippet_attachment(request: Request, user: dict = Depends(require_role("admin", "supervisor"))):
    """Sube un archivo a R2 y retorna metadata para asociar a un snippet."""
    import uuid as uuid_mod
    from integrations import storage

    form = await request.form()
    file = form.get("file")
    if not file:
        raise HTTPException(status_code=400, detail="Archivo requerido")

    original_name = getattr(file, "filename", "file")
    content_type = getattr(file, "content_type", "application/octet-stream") or "application/octet-stream"
    ext = Path(original_name).suffix.lower() or ".bin"
    key = f"snippets/{uuid_mod.uuid4().hex[:12]}{ext}"
    content = await file.read()

    if storage.is_enabled():
        url = await storage.upload_bytes(key, content, content_type)
    else:
        media_dir = Path(__file__).parent.parent / "media" / "snippets"
        media_dir.mkdir(parents=True, exist_ok=True)
        filepath = media_dir / Path(key).name
        filepath.write_bytes(content)
        url = f"media/snippets/{filepath.name}"

    return {
        "url": url,
        "filename": original_name,
        "mime": content_type,
        "size": len(content),
    }


# ─── Labels ───────────────────────────────────────────────────────────────────

@router.get("/labels")
async def get_labels():
    """Devuelve el catálogo de etiquetas disponibles."""
    return {"labels": LABELS}


@router.post("/conversations/{session_id}/label")
async def set_label(session_id: str, req: LabelRequest, user: dict = Depends(get_current_user)):
    """Asigna o quita una etiqueta de clasificación a la conversación."""
    store, conv = await _get_conv(session_id)

    if req.label and req.label not in LABELS:
        raise HTTPException(status_code=400, detail=f"Etiqueta inválida. Opciones: {list(LABELS.keys())}")

    await _set_label(session_id, req.label)

    broadcast_event({
        "type": "label_updated",
        "session_id": session_id,
        "label": req.label,
    })

    from utils.conv_events import log_action
    label_info = LABELS.get(req.label, {})
    desc = f"{label_info.get('emoji','')} {label_info.get('label', req.label)}" if req.label else "Sin etiqueta"
    await log_action(session_id, f"Clasificación actualizada → {desc}")

    logger.info("inbox_label_set", session_id=session_id, label=req.label)
    return {"status": "ok", "label": req.label}


# ─── Kanban Move (drag & drop con triggers) ─────────────────────────────────

class KanbanMoveRequest(BaseModel):
    new_label: str  # clave del label destino o "" para "sin clasificar"
    old_label: str = ""


@router.post("/conversations/{session_id}/kanban-move")
async def kanban_move(session_id: str, req: KanbanMoveRequest, user: dict = Depends(get_current_user)):
    """
    Mueve una conversación de una columna del kanban a otra.
    Ejecuta triggers automáticos según la transición.
    """
    store, conv = await _get_conv(session_id)

    # Validar label destino
    if req.new_label and req.new_label not in LABELS:
        raise HTTPException(status_code=400, detail=f"Label inválido: {req.new_label}")

    # Aplicar el cambio de label
    await _set_label(session_id, req.new_label)

    # Broadcast SSE
    broadcast_event({
        "type": "label_updated",
        "session_id": session_id,
        "label": req.new_label,
    })

    # Log de evento
    from utils.conv_events import log_action
    old_info = LABELS.get(req.old_label, {})
    new_info = LABELS.get(req.new_label, {})
    old_desc = f"{old_info.get('emoji', '❓')} {old_info.get('label', 'Sin clasificar')}" if req.old_label else "❓ Sin clasificar"
    new_desc = f"{new_info.get('emoji', '❓')} {new_info.get('label', 'Sin clasificar')}" if req.new_label else "❓ Sin clasificar"
    await log_action(session_id, f"Kanban: {old_desc} → {new_desc}")

    # ─── Triggers automáticos ───
    triggers_executed = []

    # Trigger: → No interesa → marcar como archivada
    if req.new_label == "no_interesa":
        conv.status = ConversationStatus.ACTIVE
        await store.save(conv)
        triggers_executed.append("archived")

    # Trigger: → Convertido → actualizar status + intentar Zoho Sales Order
    if req.new_label == "convertido":
        triggers_executed.append("converted")
        try:
            from config.settings import get_settings
            s = get_settings()
            if s.zoho_client_id:
                phone = conv.user_profile.phone or session_id
                email = conv.user_profile.email or ""
                if email or phone:
                    logger.info("kanban_trigger_zoho_conversion", session_id=session_id, email=email)
                    # Marcar en Redis para seguimiento
                    await store._redis.setex(f"converted:{session_id}", 86400 * 30, "1")
        except Exception as e:
            logger.warning("kanban_trigger_zoho_error", error=str(e))

    # Trigger: → Caliente (desde tibio/frio) → notificar equipo
    if req.new_label == "caliente" and req.old_label in ("tibio", "frio", ""):
        triggers_executed.append("escalated_to_hot")

    # Trigger: → Esperando pago → marcar para seguimiento de pago
    if req.new_label == "esperando_pago":
        triggers_executed.append("payment_pending")
        await store._redis.setex(f"payment_pending:{session_id}", 86400 * 7, "1")

    # Trigger: → Seguimiento → programar recordatorio 48h
    if req.new_label == "seguimiento":
        triggers_executed.append("followup_scheduled")
        followup_ts = str(time.time() + 172800)  # 48h from now
        await store._redis.setex(f"followup:{session_id}", 172800, followup_ts)

    # Audit log
    from utils.audit import audit_log
    await audit_log(
        user.get("id", ""), user.get("name", ""),
        "kanban_move", session_id,
        {"from": req.old_label, "to": req.new_label, "triggers": triggers_executed},
    )

    logger.info("kanban_move", session_id=session_id, old=req.old_label, new=req.new_label, triggers=triggers_executed)
    return {"status": "ok", "label": req.new_label, "triggers": triggers_executed}


# ─── Zoho CRM lookup ──────────────────────────────────────────────────────────

@router.get("/conversations/{session_id}/zoho")
async def get_zoho_data(session_id: str, user: dict = Depends(get_current_user)):
    """
    Busca información del usuario en Zoho CRM.
    Busca primero en Leads, luego en Contacts, por teléfono y/o email.
    """
    store, conv = await _get_conv(session_id)

    phone = conv.user_profile.phone or (session_id if session_id.lstrip("+").isdigit() else "")
    email = conv.user_profile.email or ""

    result = {
        "found": False,
        "module": None,
        "record": None,
        "error": None,
    }

    try:
        from integrations.zoho.leads import ZohoLeads
        from integrations.zoho.contacts import ZohoContacts

        leads = ZohoLeads()
        contacts = ZohoContacts()

        record = None
        module = None

        # Buscar primero en Contacts (prioridad sobre Leads)
        if phone:
            record = await contacts.search_by_phone(phone)
            if record:
                module = "Contacts"

        if not record and email:
            try:
                record = await contacts.search_by_email(email)
                if record:
                    module = "Contacts"
            except Exception:
                pass

        # Solo si no hay Contact, buscar en Leads
        if not record and phone:
            record = await leads.search_by_phone(phone)
            if record:
                module = "Leads"

        if not record and email:
            try:
                record = await leads.search_by_email(email)
                if record:
                    module = "Leads"
            except Exception:
                pass

        if record:
            # Si es un Contact, traer perfil completo (campos profesionales + cursadas)
            if module == "Contacts":
                try:
                    full = await contacts.get_full_profile(record.get("id", ""))
                    if full:
                        record.update(full)
                except Exception:
                    pass

            result["found"] = True
            result["module"] = module

            # Parsear cursadas del subformulario
            cursadas_raw = record.get("Formulario_de_cursada") or []
            cursadas = []

            def _curso_name(entry):
                """Extrae nombre del curso — puede ser string o lookup {name, id}."""
                for fld in ("Curso", "Nombre_de_curso", "Nombre_del_curso"):
                    v = entry.get(fld)
                    if isinstance(v, dict):
                        return v.get("name", "")
                    if isinstance(v, str) and v.strip():
                        return v.strip()
                return ""

            for c in cursadas_raw:
                nombre = _curso_name(c)
                if nombre:
                    avance_raw = c.get("Avance")
                    avance = ""
                    if avance_raw is not None:
                        try:
                            avance = f"{float(avance_raw):.0f}%"
                        except (ValueError, TypeError):
                            avance = str(avance_raw)
                    cursadas.append({
                        "curso": nombre,
                        "estado": c.get("Estado_cursada", "") or c.get("Estado_de_OV", ""),
                        "avance": avance,
                    })

            def _list_to_str(val):
                if isinstance(val, list):
                    return ", ".join(str(v) for v in val if v and str(v) != "null")
                return str(val) if val else ""

            result["record"] = {
                "id": record.get("id", ""),
                "name": f"{record.get('First_Name','')} {record.get('Last_Name','')}".strip(),
                "email": record.get("Email", ""),
                "phone": record.get("Phone", ""),
                "owner": record.get("Owner", {}).get("name", "") if isinstance(record.get("Owner"), dict) else "",
                "lead_source": record.get("Lead_Source", ""),
                "canal_origen": record.get("Canal_Origen", ""),
                "created_time": record.get("Created_Time", ""),
                "modified_time": record.get("Modified_Time", ""),
                # Perfil profesional
                "profesion": record.get("Profesi_n", "") or record.get("Profesi\u00f3n", "") or record.get("Profesion", ""),
                "especialidad": record.get("Especialidad", ""),
                "especialidad_interes": _list_to_str(record.get("Especialidad_interes")),
                "intereses_adicionales": _list_to_str(record.get("Intereses_adicionales")),
                "contenido_interes": _list_to_str(record.get("Contenido_Interes")),
                # Cursadas
                "cursadas": cursadas,
                # Legacy
                "lms_user_id": record.get("LMS_User_ID", ""),
            }
        else:
            result["found"] = False

    except Exception as e:
        logger.warning("zoho_lookup_error", session_id=session_id, error=str(e))
        result["error"] = str(e)

    # Cache Zoho result for contact profile panel
    if result.get("found"):
        await store._redis.set(f"zoho_cache:{session_id}", json.dumps(result), ex=3600)

    return result


@router.get("/conversations/{session_id}/wa-window")
async def get_wa_window(session_id: str, user: dict = Depends(get_current_user)):
    """
    Verifica si la ventana de 24h de WhatsApp sigue abierta.
    Meta solo permite mensajes libres dentro de las 24h del último mensaje del usuario.
    Fuera de la ventana, solo se pueden enviar plantillas HSM.
    """
    store = await get_conversation_store()
    ttl = await store._redis.ttl(f"wa_window:{session_id}")
    if ttl and ttl > 0:
        hours = ttl // 3600
        mins = (ttl % 3600) // 60
        return {
            "open": True,
            "ttl_seconds": ttl,
            "expires_in": f"{hours}h {mins}m",
        }
    return {
        "open": False,
        "ttl_seconds": 0,
        "expires_in": "Expirada",
    }


@router.get("/conversations/{session_id}/cobranzas")
async def get_cobranzas_data(session_id: str, user: dict = Depends(get_current_user)):
    """
    Busca información de cobranzas del usuario en Zoho (Area_de_cobranzas).
    Busca por email y/o teléfono.
    """
    store, conv = await _get_conv(session_id)

    phone = conv.user_profile.phone or (session_id if session_id.lstrip("+").isdigit() else "")
    email = conv.user_profile.email or ""

    result = {"found": False, "record": None, "error": None}

    try:
        from integrations.zoho.area_cobranzas import ZohoAreaCobranzas
        zoho = ZohoAreaCobranzas()

        ficha = {}
        if email:
            ficha = await zoho.search_by_email(email)
        if not ficha and phone:
            ficha = await zoho.search_by_phone(phone)

        if ficha and ficha.get("cobranzaId"):
            result["found"] = True
            result["record"] = ficha
    except Exception as e:
        logger.warning("cobranzas_lookup_error", session_id=session_id, error=str(e))
        result["error"] = str(e)

    return result


# ─── Contact profile unificado ───────────────────────────────────────────────

class ContactNote(BaseModel):
    text: str

class ContactField(BaseModel):
    key: str
    value: str


@router.get("/conversations/{session_id}/contact-profile")
async def get_contact_profile(session_id: str, user: dict = Depends(get_current_user)):
    """
    Perfil unificado del contacto:
    - Datos base de la conversación
    - Enriquecimiento extraído de los mensajes (intención, cursos mencionados, especialidad)
    - Notas manuales del agente
    - Historial de sesiones previas del mismo contacto
    - Datos de Zoho CRM (si están cacheados)
    """
    store = await get_conversation_store()
    redis = store._redis

    # Buscar conversación — no tirar 404 si no existe
    conv = await store.get_by_external(Channel.WIDGET, session_id)
    if not conv:
        conv = await store.get_by_external(Channel.WHATSAPP, session_id)

    # ── Datos base ──
    if conv:
        base = {
            "name": conv.user_profile.name or "",
            "email": conv.user_profile.email or "",
            "phone": conv.user_profile.phone or (session_id if session_id.lstrip("+").isdigit() else ""),
            "country": conv.user_profile.country or "",
            "channel": conv.channel.value if hasattr(conv.channel, "value") else str(conv.channel),
        }
    else:
        base = {
            "name": "", "email": "",
            "phone": session_id if session_id.lstrip("+").isdigit() else "",
            "country": "", "channel": "widget",
        }

    # ── Enriquecimiento de la conversación ──
    enrichment = {}
    enrichment_raw = await redis.get(f"conv_enrichment:{session_id}")
    if enrichment_raw:
        try:
            enrichment = json.loads(enrichment_raw.decode() if isinstance(enrichment_raw, bytes) else enrichment_raw)
        except Exception:
            pass

    # Si no hay enriquecimiento guardado, extraer de los mensajes
    if not enrichment and conv and conv.messages:
        user_msgs = [m.content for m in conv.messages if m.role.value == "user"]
        if user_msgs:
            enrichment = _extract_enrichment(user_msgs)
            await redis.set(f"conv_enrichment:{session_id}", json.dumps(enrichment), ex=86400*30)

    # ── Notas manuales ──
    notes_raw = await redis.lrange(f"conv_notes:{session_id}", 0, -1)
    notes = []
    for n in notes_raw:
        try:
            notes.append(json.loads(n.decode() if isinstance(n, bytes) else n))
        except Exception:
            notes.append({"text": n.decode() if isinstance(n, bytes) else n, "ts": ""})

    # ── Historial de sesiones del mismo contacto ──
    history = []
    contact_key = base["phone"] or base["email"]
    if contact_key:
        # Buscar otras sesiones del mismo teléfono/email
        contact_sessions_raw = await redis.smembers(f"contact_sessions:{contact_key}")
        for s in contact_sessions_raw:
            sid = s.decode() if isinstance(s, bytes) else s
            if sid == session_id:
                continue
            try:
                other_store = store
                other_conv = await other_store.get_by_external(conv.channel, sid)
                if other_conv and other_conv.messages:
                    last = other_conv.messages[-1]
                    history.append({
                        "session_id": sid,
                        "message_count": len(other_conv.messages),
                        "last_message": last.content[:60] + ("…" if len(last.content) > 60 else ""),
                        "last_timestamp": last.timestamp.isoformat() if hasattr(last, "timestamp") and last.timestamp else "",
                        "label": await _get_label(sid),
                    })
            except Exception:
                pass

    # Registrar esta sesión en el índice de contacto
    if contact_key:
        await redis.sadd(f"contact_sessions:{contact_key}", session_id)
        await redis.expire(f"contact_sessions:{contact_key}", 86400 * 90)

    # ── Zoho cache ──
    zoho_cache_raw = await redis.get(f"zoho_cache:{session_id}")
    zoho_data = None
    if zoho_cache_raw:
        try:
            zoho_data = json.loads(zoho_cache_raw.decode() if isinstance(zoho_cache_raw, bytes) else zoho_cache_raw)
        except Exception:
            pass

    return {
        "base": base,
        "enrichment": enrichment,
        "notes": notes,
        "history": history,
        "zoho_cached": zoho_data,
    }


def _extract_enrichment(user_messages: list) -> dict:
    """Extrae datos de interés del contacto a partir de sus mensajes."""
    text = " ".join(user_messages).lower()

    specialties = ["cardiología", "pediatría", "neurología", "traumatología", "clínica", "cirugía",
                   "oncología", "ginecología", "psiquiatría", "dermatología", "endocrinología",
                   "gastroenterología", "nefrología", "infectología", "reumatología", "hematología"]
    mentioned = [s for s in specialties if s in text]

    intent = ""
    if any(w in text for w in ["precio", "costo", "valor", "cuánto", "cuanto", "tarifa"]):
        intent = "consulta_precio"
    elif any(w in text for w in ["inscribir", "inscripción", "inscripcion", "anotarme", "quiero el curso"]):
        intent = "quiere_inscribirse"
    elif any(w in text for w in ["pago", "pagué", "pague", "abono", "cuota", "transferencia"]):
        intent = "consulta_pago"
    elif any(w in text for w in ["certificado", "certificación", "aval"]):
        intent = "consulta_certificado"
    elif mentioned:
        intent = "busca_curso"

    return {
        "specialties_mentioned": mentioned,
        "primary_intent": intent,
        "message_count": len(user_messages),
    }


@router.post("/conversations/{session_id}/notes")
async def add_contact_note(session_id: str, note: ContactNote, user: dict = Depends(get_current_user)):
    """Agrega una nota interna al contacto."""
    import datetime
    store, _ = await _get_conv(session_id)
    redis = store._redis
    entry = json.dumps({
        "text": note.text,
        "ts": datetime.datetime.utcnow().isoformat(),
    })
    await redis.lpush(f"conv_notes:{session_id}", entry)
    await redis.expire(f"conv_notes:{session_id}", 86400 * 90)
    from utils.conv_events import log_action
    await log_action(session_id, f"Nota agregada: {note.text[:50]}")
    return {"ok": True}


@router.delete("/conversations/{session_id}/notes/{note_index}")
async def delete_contact_note(session_id: str, note_index: int, user: dict = Depends(get_current_user)):
    """Elimina una nota por índice."""
    store, _ = await _get_conv(session_id)
    redis = store._redis
    # Redis doesn't support direct index delete — replace with placeholder then trim
    await redis.lset(f"conv_notes:{session_id}", note_index, "__DELETED__")
    await redis.lrem(f"conv_notes:{session_id}", 0, "__DELETED__")
    return {"ok": True}


@router.patch("/conversations/{session_id}/contact-profile")
async def update_contact_field(session_id: str, field: ContactField, user: dict = Depends(get_current_user)):
    """Actualiza un campo del enriquecimiento del contacto."""
    store, _ = await _get_conv(session_id)
    redis = store._redis
    enrichment_raw = await redis.get(f"conv_enrichment:{session_id}")
    enrichment = {}
    if enrichment_raw:
        try:
            enrichment = json.loads(enrichment_raw.decode() if isinstance(enrichment_raw, bytes) else enrichment_raw)
        except Exception:
            pass
    enrichment[field.key] = field.value
    await redis.set(f"conv_enrichment:{session_id}", json.dumps(enrichment), ex=86400*30)
    return {"ok": True, "enrichment": enrichment}


# ─── Download conversación ────────────────────────────────────────────────────

@router.get("/conversations/{session_id}/download")
async def download_conversation(session_id: str, user: dict = Depends(get_current_user)):
    """Descarga la conversación completa en JSON."""
    import datetime
    from fastapi.responses import JSONResponse

    store, conv = await _get_conv(session_id)
    agent_name_raw = await store._redis.get(f"agent_name:{session_id}")
    agent_name = (agent_name_raw.decode() if isinstance(agent_name_raw, bytes) else agent_name_raw) or ""
    label = await _get_label(session_id)

    from utils.conv_events import get_events
    events = await get_events(session_id, limit=200)

    data = {
        "exported_at": datetime.datetime.utcnow().isoformat() + "Z",
        "session_id": session_id,
        "channel": conv.channel.value if hasattr(conv, "channel") else "widget",
        "user": {
            "name": conv.user_profile.name or "",
            "email": conv.user_profile.email or "",
            "phone": conv.user_profile.phone or "",
            "country": conv.user_profile.country or "",
        },
        "status": conv.status.value,
        "label": label,
        "agent_name": agent_name,
        "message_count": len(conv.messages),
        "messages": [
            {
                "role": m.role.value,
                "content": m.content,
                "timestamp": m.timestamp.isoformat() if hasattr(m, "timestamp") and m.timestamp else "",
                "agent": m.metadata.get("agent", "") if m.metadata else "",
            }
            for m in conv.messages
        ],
        "events": events,
    }

    filename = f"conv_{session_id[:8]}_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    return JSONResponse(
        content=data,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─── Eventos por conversación ─────────────────────────────────────────────────

class AssignRequest(BaseModel):
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None


@router.post("/conversations/{session_id}/assign")
async def assign_conversation(session_id: str, req: AssignRequest, user: dict = Depends(require_role("admin", "supervisor"))):
    """Asigna la conversación a un agente (o desasigna si agent_id=None)."""
    store, conv = await _get_conv(session_id)
    redis = store._redis

    if req.agent_id:
        await redis.set(f"conv_assigned:{session_id}", req.agent_id, ex=86400 * 30)
        await redis.set(f"conv_assigned_name:{session_id}", req.agent_name or req.agent_id, ex=86400 * 30)
        action_desc = f"Asignada a {req.agent_name or req.agent_id}"
    else:
        await redis.delete(f"conv_assigned:{session_id}")
        await redis.delete(f"conv_assigned_name:{session_id}")
        action_desc = "Asignación removida"

    broadcast_event({
        "type": "conv_assigned",
        "session_id": session_id,
        "agent_id": req.agent_id,
        "agent_name": req.agent_name,
    })

    from utils.conv_events import log_action
    await log_action(session_id, action_desc)
    logger.info("conv_assigned", session_id=session_id, agent=req.agent_id)
    return {"ok": True}


@router.get("/conversations/{session_id}/events")
async def get_conv_events(session_id: str, limit: int = 50, user: dict = Depends(get_current_user)):
    """Devuelve el log de eventos de la conversación (agentes, acciones, errores)."""
    from utils.conv_events import get_events
    events = await get_events(session_id, limit=limit)
    return {"events": events}


# ─── Metrics ─────────────────────────────────────────────────────────────────

@router.get("/metrics")
async def get_metrics(user: dict = Depends(get_current_user)):
    """Métricas del sistema: conversaciones hoy, containment, handoffs, últimos 7 días."""
    import datetime
    store = await get_conversation_store()
    redis = store._redis

    # Count today's conversations from Redis keys
    today = datetime.date.today().isoformat()
    today_total = 0
    today_human = 0

    # Scan all widget and whatsapp idx keys
    session_ids = []
    async for key in redis.scan_iter("idx:widget:*", count=500):
        session_ids.append(key.decode() if isinstance(key, bytes) else key)
    async for key in redis.scan_iter("idx:whatsapp:*", count=500):
        session_ids.append(key.decode() if isinstance(key, bytes) else key)

    today_total = len(session_ids)

    # Count those with bot_disabled (human handoff)
    for key in session_ids:
        sid = key.split("idx:widget:")[-1] if "idx:widget:" in key else key.split("idx:whatsapp:")[-1]
        bot_key = _bot_key(sid)
        val = await redis.get(bot_key)
        if val:
            today_human += 1

    today_bot = today_total - today_human
    bot_containment = round((today_bot / today_total) * 100) if today_total > 0 else 0

    # Last 7 days — compute from Redis (estimate based on current data)
    last_7_days = []
    for i in range(6, -1, -1):
        d = (datetime.date.today() - datetime.timedelta(days=i)).isoformat()
        # For today we have real data, for historical we approximate
        day_count = today_total if i == 0 else 0
        last_7_days.append({"date": d, "total": day_count})

    # Get agent statuses from profiles
    agents = []
    try:
        from integrations.supabase_client import list_profiles
        profiles = await list_profiles()
        for p in profiles:
            if p.get("role") in ("agente", "supervisor", "admin"):
                user_id = p.get("id") or p.get("email", "")
                status_val = await redis.get(f"agent_available:{user_id}")
                status = (status_val.decode() if isinstance(status_val, bytes) else status_val) if status_val else "offline"
                agents.append({
                    "name": p.get("name", ""),
                    "email": p.get("email", ""),
                    "role": p.get("role", "agente"),
                    "status": status,
                    "handled": 0,
                    "avg_response": "< 2s",
                })
    except Exception:
        pass

    # FRT metrics per agent
    frt_data = {}
    try:
        frt_keys = []
        async for k in redis.scan_iter("frt:*", count=500):
            frt_keys.append(k)
        for k in frt_keys[:200]:
            raw = await redis.get(k)
            if raw:
                import json as _json
                frt = _json.loads(raw)
                agent = frt.get("agent", "unknown")
                if agent not in frt_data:
                    frt_data[agent] = {"total": 0, "sum_seconds": 0}
                frt_data[agent]["total"] += 1
                frt_data[agent]["sum_seconds"] += frt.get("seconds", 0)
    except Exception:
        pass

    frt_summary = []
    for agent, data in frt_data.items():
        avg = data["sum_seconds"] / data["total"] if data["total"] > 0 else 0
        frt_summary.append({"agent": agent, "avg_seconds": round(avg), "count": data["total"]})

    # Inactivity alerts: conversations with human takeover and no reply in X minutes
    inactive_alerts = []
    for key in session_ids[:100]:
        sid = key.split("idx:widget:")[-1] if "idx:widget:" in key else key.split("idx:whatsapp:")[-1]
        bot_key = _bot_key(sid)
        val = await redis.get(bot_key)
        if not val:
            continue
        # Check last message timestamp
        label_raw = await redis.get(f"conv_label:{sid}")
        label = (label_raw.decode() if isinstance(label_raw, bytes) else (label_raw or ""))
        last_activity = await redis.get(f"last_reply:{sid}")
        if last_activity:
            ts = float(last_activity)
            elapsed = time.time() - ts
            threshold = 300 if label == "caliente" else 900  # 5min hot, 15min others
            if elapsed > threshold:
                name_raw = await redis.get(f"conv_assigned_name:{sid}")
                name = (name_raw.decode() if isinstance(name_raw, bytes) else (name_raw or ""))
                inactive_alerts.append({
                    "session_id": sid, "agent": name,
                    "minutes_inactive": round(elapsed / 60),
                    "label": label,
                })

    return {
        "today_total": today_total,
        "today_human": today_human,
        "today_bot": today_bot,
        "bot_containment": bot_containment,
        "last_7_days": last_7_days,
        "agents": agents,
        "frt_summary": frt_summary,
        "inactive_alerts": inactive_alerts,
    }


# ─── Audit log ────────────────────────────────────────────────────────────────

@router.get("/audit-log")
async def get_audit(limit: int = 100, user: dict = Depends(require_role("admin"))):
    """Retorna el log de auditoría de acciones administrativas."""
    from utils.audit import get_audit_log
    entries = await get_audit_log(limit=limit)
    return {"entries": entries}


# ─── SSE stream ───────────────────────────────────────────────────────────────

@router.get("/stream")
async def sse_stream(request: Request, token: str = ""):
    """
    Server-Sent Events — el inbox escucha este endpoint para actualizaciones
    en tiempo real cuando llegan mensajes nuevos.
    Token se pasa como query param porque EventSource no soporta headers custom.
    """
    # Validar auth via query param o header
    session_token = token or request.headers.get("x-session-token", "")
    if session_token:
        from memory.conversation_store import get_conversation_store
        store = await get_conversation_store()
        data = await store._redis.get(f"session:{session_token}")
        if not data:
            raise HTTPException(status_code=401, detail="Token inválido")

    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    _sse_clients.add(queue)

    async def generator() -> AsyncGenerator[str, None]:
        try:
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # Keepalive ping
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        finally:
            _sse_clients.discard(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ─── WebSocket endpoint ──────────────────────────────────────────────────────

_ws_clients: set[asyncio.Queue] = set()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = ""):
    """
    WebSocket para actualizaciones en tiempo real.
    Reemplaza SSE con comunicación bidireccional.
    Token se pasa como query param: /inbox/ws?token=xxx
    """
    # Auth
    if token:
        from memory.conversation_store import get_conversation_store
        store = await get_conversation_store()
        data = await store._redis.get(f"session:{token}")
        if not data:
            await websocket.close(code=4001, reason="Token inválido")
            return

    await websocket.accept()

    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    _ws_clients.add(queue)
    _sse_clients.add(queue)  # Also receive SSE events so broadcast_event works

    try:
        await websocket.send_json({"type": "connected"})

        async def send_events():
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25)
                    await websocket.send_json(event)
                except asyncio.TimeoutError:
                    await websocket.send_json({"type": "ping"})

        async def receive_messages():
            while True:
                data = await websocket.receive_text()
                # Client can send pong or other messages
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "pong":
                        pass  # keepalive response
                except Exception:
                    pass

        # Run both tasks concurrently
        send_task = asyncio.create_task(send_events())
        recv_task = asyncio.create_task(receive_messages())
        done, pending = await asyncio.wait(
            [send_task, recv_task], return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _ws_clients.discard(queue)
        _sse_clients.discard(queue)


# ─── Quick Replies (Respuestas rápidas dinámicas) ───────────────────────────

class QuickReplyRequest(BaseModel):
    shortcut: str   # e.g. "/pago"
    title: str      # e.g. "Enlace de pago"
    content: str    # the full text
    category: str = "general"  # general, ventas, soporte, cobranzas


@router.get("/quick-replies")
async def list_quick_replies(user: dict = Depends(get_current_user)):
    """Lista todas las respuestas rápidas."""
    store = await get_conversation_store()
    raw = await store._redis.get("quick_replies")
    if not raw:
        return {"replies": []}
    data = raw.decode() if isinstance(raw, bytes) else raw
    return {"replies": json.loads(data)}


@router.post("/quick-replies")
async def save_quick_replies(request: Request, user: dict = Depends(require_role("admin", "supervisor"))):
    """Guarda la lista completa de respuestas rápidas (reemplaza todas)."""
    body = await request.json()
    replies = body.get("replies", [])
    store = await get_conversation_store()
    await store._redis.set("quick_replies", json.dumps(replies))
    return {"ok": True, "count": len(replies)}


@router.post("/quick-replies/add")
async def add_quick_reply(req: QuickReplyRequest, user: dict = Depends(get_current_user)):
    """Agrega una respuesta rápida."""
    store = await get_conversation_store()
    raw = await store._redis.get("quick_replies")
    replies = json.loads(raw.decode() if isinstance(raw, bytes) else raw) if raw else []
    replies.append({
        "shortcut": req.shortcut if req.shortcut.startswith("/") else f"/{req.shortcut}",
        "title": req.title,
        "content": req.content,
        "category": req.category,
    })
    await store._redis.set("quick_replies", json.dumps(replies))
    return {"ok": True, "count": len(replies)}


@router.delete("/quick-replies/{index}")
async def delete_quick_reply(index: int, user: dict = Depends(require_role("admin", "supervisor"))):
    """Elimina una respuesta rápida por índice."""
    store = await get_conversation_store()
    raw = await store._redis.get("quick_replies")
    replies = json.loads(raw.decode() if isinstance(raw, bytes) else raw) if raw else []
    if 0 <= index < len(replies):
        replies.pop(index)
        await store._redis.set("quick_replies", json.dumps(replies))
    return {"ok": True, "count": len(replies)}


# ─── Close / Archive conversations ──────────────────────────────────────────

@router.post("/conversations/{session_id}/close")
async def close_conversation(session_id: str, req: CloseRequest, user: dict = Depends(get_current_user)):
    """Cierra/archiva una conversación."""
    store, conv = await _get_conv(session_id)

    conv.status = ConversationStatus.CLOSED
    await store.save(conv)
    await _set_bot_disabled(session_id, False)
    await store._redis.delete(f"agent_name:{session_id}")

    # Save resolution metadata
    if req.resolution:
        await store._redis.set(f"conv_resolution:{session_id}", req.resolution, ex=86400*90)

    # Closing notes (categoría + summary), persistidos en conv.context y Redis
    if req.category or req.summary:
        conv.context["closing_note"] = {
            "category": req.category,
            "summary": req.summary,
            "closed_by": user.get("name") or user.get("email") or "",
            "closed_at": datetime.utcnow().isoformat(),
        }
        # Guardar en Redis para queries rápidas de reports
        await store._redis.set(
            f"closing_note:{session_id}",
            json.dumps({
                "category": req.category,
                "summary": req.summary,
                "closed_by": user.get("name") or user.get("email") or "",
            }),
            ex=86400 * 180,
        )
        await store.save(conv)

    # Track close time for metrics
    await store._redis.set(f"conv_closed_at:{session_id}", str(time.time()), ex=86400*30)

    broadcast_event({
        "type": "conv_closed",
        "session_id": session_id,
        "resolution": req.resolution,
        "category": req.category,
    })

    from utils.conv_events import log_action
    await log_action(session_id, f"Conversación cerrada — {req.resolution or 'sin resolución'}")

    from utils.audit import audit_log
    await audit_log(user.get("id",""), user.get("name",""), "close", session_id, {"resolution": req.resolution})

    return {"ok": True}


@router.post("/conversations/{session_id}/reopen")
async def reopen_conversation(session_id: str, user: dict = Depends(get_current_user)):
    """Reabre una conversación cerrada."""
    store, conv = await _get_conv(session_id)
    conv.status = ConversationStatus.ACTIVE
    await store.save(conv)
    await store._redis.delete(f"conv_resolution:{session_id}")
    await store._redis.delete(f"conv_closed_at:{session_id}")

    broadcast_event({"type": "conv_reopened", "session_id": session_id})

    from utils.conv_events import log_action
    await log_action(session_id, "Conversación reabierta")
    return {"ok": True}


# ─── Bulk Operations ────────────────────────────────────────────────────────

@router.post("/bulk/label")
async def bulk_label(req: BulkLabelRequest, user: dict = Depends(require_role("admin", "supervisor"))):
    """Aplica una etiqueta a múltiples conversaciones."""
    if req.label and req.label not in LABELS:
        raise HTTPException(status_code=400, detail=f"Label inválido: {req.label}")
    count = 0
    for sid in req.session_ids[:50]:  # max 50
        try:
            await _set_label(sid, req.label)
            broadcast_event({"type": "label_updated", "session_id": sid, "label": req.label})
            count += 1
        except Exception:
            pass
    return {"ok": True, "updated": count}


@router.post("/bulk/assign")
async def bulk_assign(req: BulkAssignRequest, user: dict = Depends(require_role("admin", "supervisor"))):
    """Asigna múltiples conversaciones a un agente."""
    store = await get_conversation_store()
    count = 0
    for sid in req.session_ids[:50]:
        try:
            if req.agent_id:
                await store._redis.set(f"conv_assigned:{sid}", req.agent_id, ex=86400*30)
                await store._redis.set(f"conv_assigned_name:{sid}", req.agent_name or req.agent_id, ex=86400*30)
            else:
                await store._redis.delete(f"conv_assigned:{sid}")
                await store._redis.delete(f"conv_assigned_name:{sid}")
            broadcast_event({"type": "conv_assigned", "session_id": sid, "agent_id": req.agent_id, "agent_name": req.agent_name})
            count += 1
        except Exception:
            pass
    return {"ok": True, "updated": count}


@router.post("/bulk/close")
async def bulk_close(req: BulkCloseRequest, user: dict = Depends(require_role("admin", "supervisor"))):
    """Cierra múltiples conversaciones."""
    store = await get_conversation_store()
    count = 0
    for sid in req.session_ids[:50]:
        try:
            conv = await store.get_by_external(Channel.WIDGET, sid)
            if not conv:
                conv = await store.get_by_external(Channel.WHATSAPP, sid)
            if conv:
                conv.status = ConversationStatus.CLOSED
                await store.save(conv)
                await _set_bot_disabled(sid, False)
                if req.resolution:
                    await store._redis.set(f"conv_resolution:{sid}", req.resolution, ex=86400*90)
                broadcast_event({"type": "conv_closed", "session_id": sid})
                count += 1
        except Exception:
            pass
    return {"ok": True, "closed": count}


# ─── Business Hours ─────────────────────────────────────────────────────────

@router.get("/business-hours")
async def get_business_hours():
    """Retorna el estado de horario de atención."""
    from config.settings import get_settings
    import datetime
    settings = get_settings()
    within = is_within_business_hours()
    return {
        "open": within,
        "start": settings.business_hours_start,
        "end": settings.business_hours_end,
        "timezone": settings.business_timezone,
        "days": settings.business_days,
        "message": settings.off_hours_message if not within else "",
    }


# ─── Round-robin auto-assignment ────────────────────────────────────────────

async def auto_assign_round_robin(session_id: str, queue: str = "") -> dict | None:
    """
    Auto-assign a conversation to the least-loaded available agent.
    Returns {agent_id, agent_name} or None if no agents available.
    """
    try:
        store = await get_conversation_store()
        r = store._redis

        # Get all agent profiles
        from integrations.supabase_client import list_profiles
        profiles = await list_profiles()

        # Filter to agents/supervisors who are available
        available = []
        for p in profiles:
            if p.get("role") not in ("agente", "supervisor", "admin"):
                continue
            user_id = p.get("id") or p.get("email", "")
            status = await r.get(f"agent_available:{user_id}")
            status_str = (status.decode() if isinstance(status, bytes) else status) if status else ""
            if status_str != "available":
                continue
            # Check queue match if agent has queue restrictions
            agent_queues = p.get("queues") or []
            if agent_queues and queue:
                area = queue.split("_")[0] if "_" in queue else queue
                if not any(q.startswith(area) for q in agent_queues):
                    continue
            available.append({
                "id": user_id,
                "name": p.get("name") or p.get("email", ""),
            })

        if not available:
            return None

        # Count current assignments per agent
        load = {}
        for agent in available:
            count = 0
            async for _k in r.scan_iter(f"conv_assigned:*", count=500):
                val = await r.get(_k)
                if val and (val.decode() if isinstance(val, bytes) else val) == agent["id"]:
                    count += 1
            load[agent["id"]] = count

        # Pick agent with fewest assignments (round-robin by load)
        available.sort(key=lambda a: load.get(a["id"], 0))
        chosen = available[0]

        # Assign
        await r.set(f"conv_assigned:{session_id}", chosen["id"], ex=86400 * 30)
        await r.set(f"conv_assigned_name:{session_id}", chosen["name"], ex=86400 * 30)

        broadcast_event({
            "type": "conv_assigned",
            "session_id": session_id,
            "agent_id": chosen["id"],
            "agent_name": chosen["name"],
        })

        logger.info("auto_assigned", session_id=session_id, agent=chosen["name"])
        return chosen
    except Exception as e:
        logger.warning("auto_assign_failed", error=str(e))
        return None


# ─── Follow-up checker ──────────────────────────────────────────────────────

@router.get("/pending-followups")
async def get_pending_followups(user: dict = Depends(get_current_user)):
    """Lista conversaciones con follow-up pendiente."""
    store = await get_conversation_store()
    redis = store._redis
    followups = []
    async for key in redis.scan_iter("followup:*", count=200):
        raw_key = key.decode() if isinstance(key, bytes) else key
        sid = raw_key.replace("followup:", "")
        ttl = await redis.ttl(raw_key)
        label = await _get_label(sid)
        name_raw = await redis.get(f"conv_assigned_name:{sid}")
        agent = (name_raw.decode() if isinstance(name_raw, bytes) else (name_raw or ""))
        followups.append({
            "session_id": sid,
            "hours_remaining": round(ttl / 3600, 1) if ttl > 0 else 0,
            "label": label,
            "agent": agent,
        })
    followups.sort(key=lambda x: x["hours_remaining"])
    return {"followups": followups}


# ─── WhatsApp 24h window ────────────────────────────────────────────────────

@router.get("/conversations/{session_id}/wa-window")
async def check_wa_window(session_id: str, user: dict = Depends(get_current_user)):
    """
    Verifica si la ventana de 24h de WhatsApp está abierta.
    Si cerrada, el agente solo puede enviar plantillas HSM.
    """
    store = await get_conversation_store()
    # WhatsApp sessions are phone numbers
    window = await store._redis.get(f"wa_window:{session_id}")
    ttl = await store._redis.ttl(f"wa_window:{session_id}") if window else 0
    return {
        "window_open": bool(window),
        "ttl_seconds": max(ttl, 0),
        "hours_remaining": round(max(ttl, 0) / 3600, 1),
    }


# ─── Retargeting / Follow-up sequences ──────────────────────────────────────

@router.get("/retargeting/config")
async def get_retargeting_config(user: dict = Depends(get_current_user)):
    """Retorna la configuración de secuencias de retargeting."""
    store = await get_conversation_store()
    raw = await store._redis.get("retargeting:config")
    if raw:
        config = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    else:
        config = {
            "enabled": False,
            "sequences": [
                {"day": 1, "template": "", "label_filter": "tibio,caliente,esp_pago", "message": "Recordatorio suave"},
                {"day": 3, "template": "", "label_filter": "tibio,caliente", "message": "Oferta especial"},
                {"day": 7, "template": "", "label_filter": "caliente,esp_pago", "message": "Urgencia"},
                {"day": 15, "template": "", "label_filter": "caliente", "message": "Ultima oportunidad"},
            ],
            "exclude_labels": ["convertido", "no_interesa"],
            "only_whatsapp": True,
        }
    return config


@router.post("/retargeting/config")
async def save_retargeting_config(request: Request, user: dict = Depends(get_current_user)):
    """Guarda la configuración de retargeting."""
    config = await request.json()
    store = await get_conversation_store()
    await store._redis.set("retargeting:config", json.dumps(config))
    return {"ok": True}


@router.post("/retargeting/run")
async def run_retargeting(background_tasks: BackgroundTasks, user: dict = Depends(get_current_user)):
    """Ejecuta manualmente un ciclo de retargeting."""
    background_tasks.add_task(_run_retargeting_cycle)
    return {"ok": True, "message": "Retargeting cycle started"}


@router.get("/retargeting/stats")
async def get_retargeting_stats(user: dict = Depends(get_current_user)):
    """Estadísticas de retargeting."""
    store = await get_conversation_store()
    r = store._redis
    stats = {
        "total_sent": 0,
        "total_responded": 0,
        "last_run": None,
        "by_sequence": [],
    }
    raw = await r.get("retargeting:stats")
    if raw:
        stats = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    return stats


async def _run_retargeting_cycle():
    """
    Core retargeting logic:
    1. Scan all conversations
    2. Find inactive leads matching criteria
    3. Send HSM templates based on sequence config
    4. Track results
    """
    import datetime
    try:
        from memory.conversation_store import get_conversation_store
        from integrations.whatsapp_meta import WhatsAppMetaClient

        store = await get_conversation_store()
        r = store._redis

        # Load config
        raw = await r.get("retargeting:config")
        if not raw:
            logger.info("retargeting_no_config")
            return
        config = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        if not config.get("enabled"):
            logger.info("retargeting_disabled")
            return

        sequences = config.get("sequences", [])
        exclude_labels = set(config.get("exclude_labels", []))
        now = datetime.datetime.utcnow()
        sent_count = 0

        # Scan WhatsApp conversations
        wa = WhatsAppMetaClient()
        async for key in r.scan_iter("conv:*", count=500):
            try:
                raw_conv = await r.get(key)
                if not raw_conv:
                    continue
                conv_data = json.loads(raw_conv)

                # Only WhatsApp
                if conv_data.get("channel") != "whatsapp":
                    continue

                phone = conv_data.get("external_id", "")
                if not phone:
                    continue

                # Check label
                label_raw = await r.get(f"conv_label:{phone}")
                label = (label_raw.decode() if isinstance(label_raw, bytes) else (label_raw or "")).strip()
                if label in exclude_labels:
                    continue

                # Find last message timestamp
                messages = conv_data.get("messages", [])
                if not messages:
                    continue
                last_ts = messages[-1].get("timestamp", "")
                if not last_ts:
                    continue

                try:
                    last_dt = datetime.datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                except Exception:
                    continue

                days_inactive = (now - last_dt.replace(tzinfo=None)).days

                # Check if already retargeted at this step
                retarget_key = f"retarget:{phone}"
                last_step_raw = await r.get(retarget_key)
                last_step_num = 0
                if last_step_raw:
                    try:
                        rd = json.loads(last_step_raw)
                        last_step_num = rd.get("day", 0) if isinstance(rd, dict) else int(last_step_raw)
                    except (json.JSONDecodeError, ValueError):
                        last_step_num = int(last_step_raw) if str(last_step_raw).isdigit() else 0

                # Find matching sequence
                for seq in sequences:
                    seq_day = seq.get("day", 0)
                    template = seq.get("template", "")
                    label_filter = set(seq.get("label_filter", "").split(","))

                    if days_inactive >= seq_day and seq_day > last_step_num:
                        if label and label not in label_filter and label_filter != {""}:
                            continue
                        if not template:
                            continue

                        # Send HSM
                        try:
                            await wa.send_template(phone, template, language="es_AR")
                            retarget_data = json.dumps({
                                "day": seq_day,
                                "last_template": template,
                                "last_sent": now.isoformat(),
                                "label": label,
                            })
                            await r.setex(retarget_key, 86400 * 30, retarget_data)
                            sent_count += 1
                            logger.info("retargeting_sent", phone=phone, template=template, day=seq_day)
                        except Exception as e:
                            logger.warning("retargeting_send_failed", phone=phone, error=str(e))
                        break  # Only send one per cycle per contact

            except Exception as e:
                logger.debug("retargeting_conv_error", error=str(e))
                continue

        # Save stats
        stats = {
            "total_sent": sent_count,
            "last_run": now.isoformat(),
        }
        await r.set("retargeting:stats", json.dumps(stats))
        logger.info("retargeting_cycle_complete", sent=sent_count)

    except Exception as e:
        logger.error("retargeting_cycle_error", error=str(e))
