"""
Herramientas del Sales Closer — reutiliza las tools de ventas +
una tool nueva para consultar historial del lead.
"""
from langchain_core.tools import tool
from agents.sales.tools import (
    get_course_brief,
    get_course_deep,
    create_payment_link,
    create_or_update_lead,
    create_sales_order,
)
import structlog

logger = structlog.get_logger(__name__)


@tool
async def check_lead_history(phone: str) -> str:
    """
    Consulta el historial de interacciones previas de un lead.
    Retorna un resumen con: último curso de interés, estado del lead, último contacto,
    y cualquier dato relevante del CRM.

    Args:
        phone: Número de teléfono del lead (con código de país)
    """
    from memory.conversation_store import get_conversation_store
    from config.constants import Channel
    import json

    store = await get_conversation_store()
    lines = []

    # Check label
    label = await store._redis.get(f"conv_label:{phone}")
    if label:
        label_names = {
            "caliente": "Caliente (muy interesado)",
            "tibio": "Tibio (interesado pero dudoso)",
            "frio": "Frío (poco interés)",
            "convertido": "Convertido (ya compró)",
            "esperando_pago": "Esperando pago",
            "seguimiento": "En seguimiento",
            "no_interesa": "No le interesa",
        }
        lines.append(f"Estado del lead: {label_names.get(label, label)}")

    # Check retargeting state
    retarget = await store._redis.get(f"retarget:{phone}")
    if retarget:
        try:
            rd = json.loads(retarget)
            lines.append(f"Día de retargeting: {rd.get('day', '?')}")
            lines.append(f"Último template enviado: {rd.get('last_template', 'N/A')}")
            if rd.get("last_sent"):
                lines.append(f"Último envío: {rd['last_sent']}")
        except Exception:
            pass

    # Check Zoho cache
    zoho_cache = await store._redis.get(f"zoho_cache:{phone}")
    if zoho_cache:
        try:
            zd = json.loads(zoho_cache)
            if zd.get("found") and zd.get("record"):
                r = zd["record"]
                if r.get("curso_de_interes"):
                    lines.append(f"Curso de interés (CRM): {r['curso_de_interes']}")
                if r.get("estado"):
                    lines.append(f"Estado CRM: {r['estado']}")
        except Exception:
            pass

    # Check conversation history
    try:
        conv = await store.get_by_external(Channel.WHATSAPP, phone)
        if conv:
            msg_count = len(conv.messages) if conv.messages else 0
            lines.append(f"Mensajes en historial: {msg_count}")
            if conv.user_profile.name:
                lines.append(f"Nombre: {conv.user_profile.name}")
            if conv.user_profile.email:
                lines.append(f"Email: {conv.user_profile.email}")
            # Last few messages for context
            if conv.messages:
                last_msgs = conv.messages[-5:]
                lines.append("\nÚltimos mensajes:")
                for m in last_msgs:
                    role = "Usuario" if m.role.value == "user" else "Bot"
                    text = m.content[:100] + ("..." if len(m.content) > 100 else "")
                    lines.append(f"  {role}: {text}")
    except Exception as e:
        logger.debug("check_lead_history_error", error=str(e))

    if not lines:
        return f"No se encontró historial previo para {phone}."

    return "\n".join(lines)


# All tools available to the closer
CLOSER_TOOLS = [
    get_course_brief,
    get_course_deep,
    create_payment_link,
    create_or_update_lead,
    create_sales_order,
    check_lead_history,
]
