"""
Canal WhatsApp directo con Meta Cloud API.
Procesa mensajes entrantes y envía respuestas con soporte de botones interactivos.
"""

import structlog

from agents.router import route_message
from config.constants import MAX_HISTORY_MESSAGES, Channel, ConversationStatus
from integrations.notifications import notify_handoff
from integrations.whatsapp_meta import (
    WhatsAppMetaClient,
    parse_buttons_tag,
    parse_list_tag,
)
from memory.conversation_store import get_conversation_store
from models.message import Message, MessageRole

logger = structlog.get_logger(__name__)


def extract_message_data(payload: dict) -> dict | None:
    """
    Extrae los datos relevantes de un webhook payload de Meta.
    Retorna None si no es un mensaje de texto/botón procesable.
    """
    try:
        entry = payload["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]

        if "messages" not in value:
            return None

        msg = value["messages"][0]
        contact = value["contacts"][0] if "contacts" in value else {}
        metadata = value.get("metadata", {})

        msg_type = msg.get("type", "")
        text = ""

        # Media info
        media_id = ""
        media_mime = ""
        media_filename = ""

        if msg_type == "text":
            text = msg["text"]["body"]
        elif msg_type == "interactive":
            # Respuesta a botón o lista
            interactive = msg["interactive"]
            if interactive["type"] == "button_reply":
                text = interactive["button_reply"]["title"]
            elif interactive["type"] == "list_reply":
                text = interactive["list_reply"]["title"]
        elif msg_type == "button":
            # Template button reply
            text = msg["button"]["text"]
        elif msg_type == "image":
            media_data = msg.get("image", {})
            media_id = media_data.get("id", "")
            media_mime = media_data.get("mime_type", "image/jpeg")
            text = media_data.get("caption", "") or "[Imagen]"
        elif msg_type == "audio":
            media_data = msg.get("audio", {})
            media_id = media_data.get("id", "")
            media_mime = media_data.get("mime_type", "audio/ogg")
            text = "[Audio]"
        elif msg_type == "video":
            media_data = msg.get("video", {})
            media_id = media_data.get("id", "")
            media_mime = media_data.get("mime_type", "video/mp4")
            text = media_data.get("caption", "") or "[Video]"
        elif msg_type == "document":
            media_data = msg.get("document", {})
            media_id = media_data.get("id", "")
            media_mime = media_data.get("mime_type", "application/pdf")
            media_filename = media_data.get("filename", "documento")
            text = media_data.get("caption", "") or f"[Documento: {media_filename}]"
        elif msg_type == "sticker":
            media_data = msg.get("sticker", {})
            media_id = media_data.get("id", "")
            media_mime = media_data.get("mime_type", "image/webp")
            text = "[Sticker]"
        elif msg_type == "location":
            loc = msg.get("location", {})
            text = f"[Ubicacion: {loc.get('latitude', '')}, {loc.get('longitude', '')}]"
        else:
            text = "[Mensaje no soportado]"

        return {
            "from": msg["from"],
            "message_id": msg["id"],
            "text": text,
            "msg_type": msg_type,
            "name": contact.get("profile", {}).get("name", ""),
            "phone_number_id": metadata.get("phone_number_id", ""),
            "timestamp": msg.get("timestamp", ""),
            "media_id": media_id,
            "media_mime": media_mime,
            "media_filename": media_filename,
        }
    except (KeyError, IndexError) as e:
        logger.warning("whatsapp_parse_error", error=str(e))
        return None


async def process_whatsapp_message(payload: dict) -> None:
    """
    Procesa un mensaje entrante de WhatsApp (Meta Cloud API).
    Llama al router de agentes y envía la respuesta.
    """
    data = extract_message_data(payload)
    if not data:
        return

    phone = data["from"]
    text = data["text"]
    name = data["name"]
    message_id = data["message_id"]
    media_id = data.get("media_id", "")
    media_mime = data.get("media_mime", "")
    media_filename = data.get("media_filename", "")

    logger.info("whatsapp_message_received", phone=phone, text=text[:50], msg_type=data.get("msg_type", ""))

    wa = WhatsAppMetaClient()

    # Descargar media si existe
    media_local_path = ""
    if media_id:
        ext_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "audio/ogg": ".ogg",
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
            "audio/aac": ".aac",
            "audio/amr": ".amr",
            "video/mp4": ".mp4",
            "application/pdf": ".pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        }
        # Strip codec suffix: "audio/ogg; codecs=opus" → "audio/ogg"
        base_mime = media_mime.split(";")[0].strip()
        ext = ext_map.get(base_mime, "")
        if not ext and "/" in base_mime:
            ext = "." + base_mime.split("/")[-1]
        try:
            media_local_path = await wa.download_media(media_id, ext) or ""
        except Exception as e:
            logger.warning("media_download_failed", media_id=media_id, error=str(e))

    # ── STT: transcribir audios entrantes ──
    # Si recibimos un audio, lo pasamos por Whisper. El texto reemplaza "[Audio]"
    # para que los agentes IA (router, sales, collections, etc.) puedan entender
    # al cliente y responder al contenido real de la nota de voz.
    if data.get("msg_type") == "audio" and media_local_path:
        try:
            from integrations import stt

            if stt.is_enabled():
                transcription = await stt.transcribe_file(media_local_path, language="es")
                if transcription:
                    text = f"🎤 {transcription}"
                    logger.info("audio_transcribed", phone=phone, chars=len(transcription))
                else:
                    text = "[Audio no transcribible]"
        except Exception as e:
            logger.warning("audio_transcription_failed", error=str(e))

    # Marcar como leído
    try:
        await wa.mark_as_read(message_id)
    except Exception:
        pass

    # Verificar si el bot está desactivado para este número
    store = await get_conversation_store()

    # 24h window — Meta permite mensajes libres durante 24h desde el último msg del usuario
    await store._redis.setex(f"wa_window:{phone}", 86400, "1")

    # Build media metadata
    msg_metadata = {"name": name}
    if media_local_path:
        msg_metadata["media_url"] = media_local_path
        msg_metadata["media_type"] = media_mime.split("/")[0] if "/" in media_mime else "file"
        msg_metadata["media_mime"] = media_mime
        if media_filename:
            msg_metadata["media_filename"] = media_filename

    bot_disabled = await store._redis.get(f"bot_disabled_wa:{phone}")
    if bot_disabled:
        # Guardar mensaje y notificar al inbox
        conversation, _ = await store.get_or_create(Channel.WHATSAPP, phone)
        user_msg = Message(role=MessageRole.USER, content=text, metadata=msg_metadata)
        await store.append_message(conversation, user_msg)
        try:
            import datetime

            from utils.realtime import broadcast_event

            broadcast_event(
                {
                    "type": "new_message",
                    "session_id": phone,
                    "role": "user",
                    "content": text,
                    "sender_name": name or phone,
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                    "channel": "whatsapp",
                    "media_url": media_local_path or None,
                    "media_type": msg_metadata.get("media_type"),
                }
            )
        except Exception:
            pass
        # Notif al agente asignado (si hay) — bot pausado = humano responde
        try:
            from utils.notifications import on_inbound_user_message

            await on_inbound_user_message(phone, text, name)
        except Exception:
            pass
        return

    # Detectar país por prefijo del teléfono
    country = _detect_country(phone)

    # Obtener/crear conversación
    conversation, is_new = await store.get_or_create(
        channel=Channel.WHATSAPP,
        external_id=phone,
        country=country,
    )

    # Bind conversation_id to structlog context for end-to-end tracing
    structlog.contextvars.bind_contextvars(conversation_id=str(conversation.id))

    # Actualizar nombre si lo tenemos
    if name and not conversation.user_profile.name:
        conversation.user_profile.name = name
        await store.save(conversation)

    # Si está handed off sin bot desactivado
    if conversation.status == ConversationStatus.HANDED_OFF:
        await wa.send_text(phone, "Tu consulta fue derivada a un asesor. Te contactaremos a la brevedad. 🙏")
        return

    # Guardar mensaje del usuario (con media si existe)
    user_msg = Message(role=MessageRole.USER, content=text, metadata=msg_metadata)
    await store.append_message(conversation, user_msg)

    # Historial para el agente
    history = conversation.get_history_for_llm(MAX_HISTORY_MESSAGES)

    # Procesar con el router de agentes
    result = await route_message(
        user_message=text,
        history=history,
        country=country,
        channel="whatsapp",
        conversation_id=conversation.id,
        phone=phone,
    )

    response_text = result["response"]
    handoff = result["handoff_requested"]

    # Guardar respuesta del bot
    bot_msg = Message(
        role=MessageRole.ASSISTANT,
        content=response_text,
        metadata={"agent": result["agent_used"]},
    )
    await store.append_message(conversation, bot_msg)

    # Notificar inbox via SSE
    try:
        import datetime

        from utils.realtime import broadcast_event

        broadcast_event(
            {
                "type": "new_message",
                "session_id": phone,
                "role": "user",
                "content": text,
                "sender_name": name or phone,
                "timestamp": user_msg.timestamp.isoformat(),
                "channel": "whatsapp",
                "media_url": media_local_path or None,
                "media_type": msg_metadata.get("media_type"),
            }
        )
        if response_text:
            broadcast_event(
                {
                    "type": "new_message",
                    "session_id": phone,
                    "role": "assistant",
                    "content": response_text,
                    "sender_name": result["agent_used"],
                    "timestamp": bot_msg.timestamp.isoformat(),
                    "channel": "whatsapp",
                }
            )
    except Exception:
        pass

    # Notif al agente asignado (si hay uno humano) — bot conviviendo con
    # takeover, o caso donde la asignación automática ya corrió.
    try:
        from utils.notifications import on_inbound_user_message

        await on_inbound_user_message(phone, text, name)
    except Exception:
        pass

    if handoff:
        await notify_handoff(
            channel="WhatsApp",
            external_id=phone,
            user_name=name or phone,
            reason=result.get("handoff_reason", ""),
            agent=result["agent_used"],
        )
        conversation.status = ConversationStatus.HANDED_OFF
        await store.save(conversation)
        # Auto-assign via round-robin
        try:
            from memory.assignment import auto_assign_round_robin

            await auto_assign_round_robin(phone)
        except Exception:
            pass

    # Enviar respuesta — detectar si tiene botones o lista
    await _send_response(wa, phone, response_text)


async def _send_response(wa: WhatsAppMetaClient, phone: str, text: str) -> None:
    """Envía la respuesta detectando automáticamente botones o listas."""
    if not text:
        return

    # Intentar extraer botones
    clean, buttons = parse_buttons_tag(text)
    if buttons:
        await wa.send_buttons(phone, clean or "¿Qué preferís?", buttons)
        return

    # Intentar extraer lista
    clean, sections = parse_list_tag(text)
    if sections:
        await wa.send_list(phone, clean or "Seleccioná una opción:", "Ver opciones", sections)
        return

    # Texto plano — dividir en chunks si es muy largo (Meta permite max 4096 chars)
    chunks = _split_text(text, max_len=4000)
    for chunk in chunks:
        await wa.send_text(phone, chunk)


def _split_text(text: str, max_len: int = 4000) -> list[str]:
    """Divide texto largo en partes respetando párrafos."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    current = ""
    for paragraph in text.split("\n\n"):
        if len(current) + len(paragraph) + 2 <= max_len:
            current = (current + "\n\n" + paragraph).strip()
        else:
            if current:
                chunks.append(current)
            current = paragraph
    if current:
        chunks.append(current)
    return chunks or [text[:max_len]]


def _detect_country(phone: str) -> str:
    """Detecta el país por el prefijo internacional del teléfono."""
    prefixes = {
        "54": "AR",
        "52": "MX",
        "57": "CO",
        "51": "PE",
        "56": "CL",
        "598": "UY",
        "593": "EC",
    }
    # Eliminar el + inicial si existe
    p = phone.lstrip("+")
    for prefix, country in sorted(prefixes.items(), key=lambda x: -len(x[0])):
        if p.startswith(prefix):
            return country
    return "AR"


async def send_whatsapp_reply(
    phone: str,
    message: str,
    agent_name: str = "Agente",
    media_url: str | None = None,
    media_type: str | None = None,
) -> None:
    """
    Envía un mensaje desde el inbox (agente humano) a un usuario de WhatsApp.
    Llamado desde api/inbox.py cuando el agente responde.
    Soporta texto y multimedia (imagen, audio, video, documento).
    """
    wa = WhatsAppMetaClient()
    if media_url:
        if media_type == "image":
            await wa.send_image(phone, media_url, caption=message)
        elif media_type == "audio":
            await wa.send_audio(phone, media_url)
        elif media_type == "video":
            await wa.send_video(phone, media_url, caption=message)
        else:
            await wa.send_document(phone, media_url, filename="documento", caption=message)
    else:
        await _send_response(wa, phone, message)
    logger.info("whatsapp_human_reply_sent", phone=phone, agent=agent_name, has_media=bool(media_url))
