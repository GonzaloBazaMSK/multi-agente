"""
Clasificador automático de conversaciones.
Corre después de cada respuesta del agente IA y asigna una etiqueta al lead.
"""

import structlog
from openai import AsyncOpenAI

from config.settings import get_settings

logger = structlog.get_logger(__name__)

LABELS = {
    "caliente": "Muy interesado, pregunta por precio, fechas, formas de pago o quiere inscribirse",
    "tibio": "Interesado pero con dudas, pide más información o no se decide",
    "frio": "Respuestas breves, pasivo, sin preguntas, solo mira",
    "convertido": "El agente confirmó que el pago fue procesado y el usuario tiene acceso al curso",
    "esperando_pago": "Recibió el link de pago pero aún no pagó",
    "seguimiento": "Pidió que lo contacten después, dejó sus datos para seguimiento",
    "no_interesa": "Dijo explícitamente que no le interesa o pidió que no lo contacten",
}

SYSTEM_PROMPT = """Sos un clasificador de leads para una empresa de cursos médicos.
Analizá la conversación y devolvé UNA SOLA palabra que representa el estado del lead.

Opciones:
- caliente: muy interesado, pregunta por precio/fechas/pago o quiere inscribirse
- tibio: interesado pero con dudas, pide más info, no se decide
- frio: pasivo, respuestas breves, sin preguntas, solo mira
- convertido: el agente confirmó explícitamente que el pago fue procesado y el usuario tiene acceso al curso
- esperando_pago: recibió link de pago pero no pagó aún
- seguimiento: pidió que lo contacten después
- no_interesa: dijo que no le interesa

REGLAS CRÍTICAS:
- "convertido" SOLO cuando el AGENTE confirmó pago completado de un NUEVO curso en esta conversación. No alcanza con que el usuario mencione la palabra "pago".
- Si el usuario menciona un pago para reportar un PROBLEMA (acceso, reembolso, error, cuota), NO es "convertido". Usá "seguimiento" o "tibio".
- Si la conversación es de soporte post-venta (alumno con problema en el campus), NO es "convertido".
- Una sola palabra del usuario como "Pago" o "Hice el primer pago" sin confirmación del agente NO es "convertido".

Respondé SOLO con una de esas palabras, nada más."""


async def classify_conversation(messages: list, session_id: str) -> str | None:
    """
    Clasifica la conversación y guarda el label en Redis.
    Retorna el label asignado o None si falla.

    messages: lista de dicts {role, content}
    """
    if not messages or len(messages) < 2:
        return None

    # Solo los últimos 10 mensajes para no gastar tokens
    recent = messages[-10:]
    convo_text = "\n".join(
        f"{'Usuario' if m.get('role') == 'user' else 'Agente'}: {m.get('content', '')[:200]}"
        for m in recent
        if m.get("content")
    )

    try:
        settings = get_settings()
        client = AsyncOpenAI(api_key=settings.openai_api_key)

        response = await client.chat.completions.create(
            model="gpt-4o-mini",  # modelo barato para clasificación
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": convo_text},
            ],
            max_tokens=10,
            temperature=0,
        )

        label = response.choices[0].message.content.strip().lower()

        if label not in LABELS:
            logger.warning("classifier_unknown_label", label=label, session_id=session_id)
            return None

        # Guardar en Redis
        from memory.conversation_store import get_conversation_store

        store = await get_conversation_store()
        await store._redis.set(f"conv_label:{session_id}", label)

        # Broadcast SSE para que el inbox se actualice en tiempo real
        try:
            from utils.realtime import broadcast_event

            broadcast_event({"type": "label_updated", "session_id": session_id, "label": label})
        except Exception:
            pass

        logger.info("conversation_classified", session_id=session_id, label=label)
        return label

    except Exception as e:
        logger.warning("classifier_error", session_id=session_id, error=str(e))
        return None
