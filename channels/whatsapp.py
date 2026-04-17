"""
Procesador de mensajes entrantes de WhatsApp vía Botmaker.
Extrae el texto, detecta el país, gestiona la conversación y envía la respuesta.
"""
from models.message import Message, MessageRole
from models.conversation import Conversation
from memory.conversation_store import get_conversation_store
from agents.router import route_message
from integrations.botmaker import BotmakerClient
from integrations.notifications import notify_handoff
from config.constants import Channel, COUNTRY_PHONE_PREFIXES, Country, ConversationStatus, MAX_HISTORY_MESSAGES
import structlog

logger = structlog.get_logger(__name__)


def detect_country_from_phone(phone: str) -> str:
    """Detecta el país a partir del número de teléfono (código internacional)."""
    # Limpiar el número
    clean = phone.lstrip("+").replace(" ", "").replace("-", "")
    for prefix, country in COUNTRY_PHONE_PREFIXES.items():
        if clean.startswith(prefix):
            return country.value
    return Country.ARGENTINA.value  # Default: Argentina


async def process_whatsapp_message(payload: dict) -> None:
    """
    Procesa un mensaje entrante de Botmaker.
    payload: el body del webhook de Botmaker
    """
    botmaker = BotmakerClient()

    # Extraer datos del payload de Botmaker
    # Estructura: https://go.botmaker.com/api/docs#webhooks
    chat_id = (
        payload.get("chatId")
        or payload.get("chatPlatformId")
        or payload.get("id", "")
    )
    message_text = (
        payload.get("text")
        or payload.get("message", {}).get("text", "")
        or ""
    ).strip()

    if not chat_id or not message_text:
        logger.warning("botmaker_empty_message", payload_keys=list(payload.keys()))
        return

    phone = payload.get("customerPhone", payload.get("phone", chat_id))
    customer_name = payload.get("customerName", payload.get("name", ""))

    # Detectar país
    country = detect_country_from_phone(phone)

    # Obtener/crear conversación
    store = await get_conversation_store()
    conversation, is_new = await store.get_or_create(
        channel=Channel.WHATSAPP,
        external_id=chat_id,
        country=country,
    )

    # Bind conversation_id to structlog context for end-to-end tracing
    structlog.contextvars.bind_contextvars(conversation_id=str(conversation.id))

    # Si la conversación está handed off, no responder (el humano tiene el control)
    if conversation.status == ConversationStatus.HANDED_OFF:
        logger.info("conversation_handed_off_skipping", chat_id=chat_id)
        return

    # Actualizar nombre si se tiene
    if customer_name and not conversation.user_profile.name:
        conversation.user_profile.name = customer_name
        conversation.user_profile.phone = phone

    # Guardar mensaje del usuario
    user_msg = Message(role=MessageRole.USER, content=message_text)
    await store.append_message(conversation, user_msg)

    # Obtener historial para el LLM
    history = conversation.get_history_for_llm(MAX_HISTORY_MESSAGES)
    # El último mensaje ya está en history; pasarlo como user_message aparte
    history_without_last = history[:-1] if history else []

    logger.info(
        "whatsapp_message_received",
        chat_id=chat_id,
        country=country,
        is_new=is_new,
        message_len=len(message_text),
    )

    # Procesar con el supervisor multi-agente
    result = await route_message(
        user_message=message_text,
        history=history_without_last,
        country=country,
        channel="whatsapp",
        conversation_id=conversation.id,
    )

    response_text = result["response"]
    handoff = result["handoff_requested"]
    handoff_reason = result["handoff_reason"]

    # Guardar respuesta del bot
    bot_msg = Message(
        role=MessageRole.ASSISTANT,
        content=response_text,
        metadata={"agent": result["agent_used"]},
    )
    await store.append_message(conversation, bot_msg)

    # Enviar respuesta por WhatsApp
    if response_text and not handoff:
        await botmaker.send_message(chat_id, response_text)
    elif handoff:
        # Mensaje de transición al usuario
        await botmaker.send_message(
            chat_id,
            "En un momento te atiendo un asesor. ¡Gracias por tu paciencia! 🙏",
        )
        # Transferir en Botmaker
        await botmaker.handoff_to_human(chat_id, reason=handoff_reason)
        # Notificar al equipo
        await notify_handoff(
            channel="WhatsApp",
            external_id=chat_id,
            user_name=customer_name or phone,
            reason=handoff_reason,
            agent=result["agent_used"],
        )
        # Marcar conversación como handed off
        conversation.status = ConversationStatus.HANDED_OFF
        await store.save(conversation)

    logger.info(
        "whatsapp_message_processed",
        chat_id=chat_id,
        agent=result["agent_used"],
        handoff=handoff,
    )
