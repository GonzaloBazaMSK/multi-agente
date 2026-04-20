"""
Canal WhatsApp vía Twilio Sandbox / API.
Procesa mensajes entrantes y envía respuestas.
Formato de payload: form-urlencoded (diferente a Meta Cloud API).
"""

import datetime

import structlog

from agents.router import route_message
from config.constants import MAX_HISTORY_MESSAGES, Channel, ConversationStatus
from integrations.notifications import notify_handoff
from integrations.twilio_whatsapp import TwilioWhatsAppClient, parse_twilio_webhook
from memory.conversation_store import get_conversation_store
from models.message import Message, MessageRole

logger = structlog.get_logger(__name__)


async def process_twilio_message(form_data: dict) -> None:
    """
    Procesa un mensaje entrante de WhatsApp vía Twilio.
    form_data: dict con los campos del form-urlencoded de Twilio.
    """
    data = parse_twilio_webhook(form_data)
    if not data:
        logger.warning("twilio_parse_failed", raw=str(form_data)[:200])
        return

    phone = data["from"]
    text = data["text"]
    name = data["name"]

    if not text:
        logger.debug("twilio_empty_message", phone=phone)
        return

    logger.info("twilio_message_received", phone=phone, text=text[:50])

    twilio = TwilioWhatsAppClient()

    # Verificar si el bot está desactivado para este número
    store = await get_conversation_store()
    bot_disabled = await store._redis.get(f"bot_disabled_wa:{phone}")
    if bot_disabled:
        # Guardar mensaje y notificar al inbox — agente humano ve el mensaje
        conversation, _ = await store.get_or_create(Channel.WHATSAPP, phone)
        user_msg = Message(role=MessageRole.USER, content=text, metadata={"name": name})
        await store.append_message(conversation, user_msg)
        try:
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
                }
            )
        except Exception:
            pass
        try:
            from utils.notifications import on_inbound_user_message

            await on_inbound_user_message(phone, text, name)
        except Exception:
            pass
        return

    # Detectar país por prefijo
    country = _detect_country(phone)

    # Obtener/crear conversación
    conversation, is_new = await store.get_or_create(
        channel=Channel.WHATSAPP,
        external_id=phone,
        country=country,
    )

    # Actualizar nombre si lo tenemos
    if name and not conversation.user_profile.name:
        conversation.user_profile.name = name
        await store.save(conversation)

    # Si ya está handed off
    if conversation.status == ConversationStatus.HANDED_OFF:
        await twilio.send_text(
            phone, "Tu consulta fue derivada a un asesor. Te contactaremos a la brevedad. 🙏"
        )
        return

    # Guardar mensaje del usuario
    user_msg = Message(role=MessageRole.USER, content=text, metadata={"name": name})
    await store.append_message(conversation, user_msg)

    # Historial para el agente
    history = conversation.messages[-MAX_HISTORY_MESSAGES:-1] if len(conversation.messages) > 1 else []

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

    try:
        from utils.notifications import on_inbound_user_message

        await on_inbound_user_message(phone, text, name)
    except Exception:
        pass

    # Auto-clasificar lead
    try:
        from agents.classifier import classify_conversation

        msgs = [{"role": m.role.value, "content": m.content} for m in conversation.messages[-10:]]
        await classify_conversation(msgs, phone)
    except Exception:
        pass

    if handoff:
        await notify_handoff(
            channel="WhatsApp (Twilio)",
            external_id=phone,
            user_name=name or phone,
            reason=result.get("handoff_reason", ""),
            agent=result["agent_used"],
        )
        conversation.status = ConversationStatus.HANDED_OFF
        await store.save(conversation)

    # Enviar respuesta — Twilio solo soporta texto plano
    # Limpiar tags de botones/lista que son solo para Meta
    clean_text = _clean_tags(response_text)
    if clean_text:
        await twilio.send_chunks(phone, clean_text)


def _clean_tags(text: str) -> str:
    """Elimina tags [BUTTONS:...] y [LIST:...] del texto para envío por Twilio."""
    import re

    text = re.sub(r"\[BUTTONS:\s*.+?\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[LIST:\s*.+?\]", "", text, flags=re.IGNORECASE)
    return text.strip()


def _detect_country(phone: str) -> str:
    """Detecta el país por el prefijo internacional del teléfono."""
    prefixes = {
        "598": "UY",
        "593": "EC",
        "54": "AR",
        "52": "MX",
        "57": "CO",
        "51": "PE",
        "56": "CL",
    }
    p = phone.lstrip("+")
    for prefix, country in sorted(prefixes.items(), key=lambda x: -len(x[0])):
        if p.startswith(prefix):
            return country
    return "AR"


async def send_twilio_reply(phone: str, message: str, agent_name: str = "Agente") -> None:
    """
    Envía un mensaje desde el inbox (agente humano) a un usuario de WhatsApp vía Twilio.
    """
    twilio = TwilioWhatsAppClient()
    clean = _clean_tags(message)
    if clean:
        await twilio.send_chunks(phone, clean)
    logger.info("twilio_human_reply_sent", phone=phone, agent=agent_name)
