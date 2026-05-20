"""
Herramientas del agente de post-venta.

Filosofía: el bot responde con info de la FAQ embebida en el prompt; cuando
no puede resolver o el caso requiere acción humana, deriva al portal de
tickets (https://ayuda.msklatam.com/portal/es/newticket).

NO logueamos cosas en Zoho desde acá — el módulo "Cobranzas" no es para
soporte técnico ni campus access ni certificados, y nadie del equipo técnico
monitorea eso. El portal de tickets oficial sí tiene seguimiento real.

Tools activas:
- get_student_info: identifica al alumno en Zoho (Contacts + Sales Orders).
- send_nps_survey: registra puntaje NPS si el alumno lo manda.
"""

import structlog
from langchain_core.tools import tool

from integrations.zoho.collections import ZohoCollections
from integrations.zoho.contacts import ZohoContacts
from integrations.zoho.sales_orders import ZohoSalesOrders
from utils.agent_context import current_user_authenticated, log_to_conv

logger = structlog.get_logger(__name__)


# Mensaje que ve el LLM cuando intenta acceder a info de cuenta sin auth.
# Pensado para que el LLM tenga instrucciones claras de qué decirle al user
# (no traduzca el mensaje técnico).
_UNAUTH_MESSAGE = (
    "⛔ ACCESO DENEGADO — el usuario NO está autenticado en el sitio MSK. "
    "NO podemos darle información de cuenta (cursos, vencimientos, accesos, pagos) "
    "de NADIE, ni siquiera si el usuario dice ser dueño del email.\n\n"
    "Acciones a hacer EN ESTE TURNO:\n"
    "1. NO inventes datos de cuenta. NO digas «no encontré» (suena a que buscaste).\n"
    "2. Pedile al user que inicie sesión en https://msklatam.com para que podamos "
    "acceder a su información personal.\n"
    "3. Si el user dice que NO PUEDE INICIAR SESIÓN, ayudalo con tips de recuperación "
    "(olvidé mi contraseña, probar modo incógnito, limpiar caché, etc.) — SIN dar "
    "datos específicos de la cuenta.\n"
    "4. Si nada funciona, derivá al portal de tickets: https://ayuda.msklatam.com/portal/es/newticket"
)


@tool
async def get_student_info(email: str = "", phone: str = "", contact_id: str = "") -> str:
    """
    Obtiene información del alumno: cursos inscriptos, estado de acceso y pagos.
    Prioridad de búsqueda: contact_id > email > phone.

    🔒 REQUIERE QUE EL USUARIO ESTÉ AUTENTICADO en el sitio MSK. Si está anónimo
    (vino por widget sin sesión activa), la tool rechaza y devuelve un mensaje
    para que el LLM pida login.

    Args:
        email: Email del alumno (forma preferida de identificación)
        phone: Teléfono del alumno (con código de país)
        contact_id: ID del contacto en Zoho (si ya se conoce)
    """
    # 🔒 Guard de autenticación — bloquea acceso a info de cuenta para anónimos.
    # El flag lo setea el endpoint del canal (widget) según la fuente del email.
    if not current_user_authenticated.get():
        await log_to_conv(
            "error",
            {
                "action": "get_student_info_unauth",
                "detail": f"Bloqueado por guard — anónimo intentó acceder a cuenta · email={email or '(s/d)'}",
            },
        )
        return _UNAUTH_MESSAGE

    contacts = ZohoContacts()
    orders = ZohoSalesOrders()

    contact = None
    if contact_id:
        contact = await contacts.get(contact_id)
    if not contact and email:
        contact = await contacts.search_by_email(email)
    if not contact and phone:
        contact = await contacts.search_by_phone(phone)

    if not contact:
        return "No encontré un alumno registrado con esos datos. ¿Puedes verificar el email?"

    name = f"{contact.get('First_Name', '')} {contact.get('Last_Name', '')}".strip()
    c_id = contact.get("id", "")
    sales_orders = await orders.list_by_contact(c_id)

    lines = [f"Alumno: {name}", f"ID Zoho: {c_id}", ""]
    if not sales_orders:
        lines.append("No tiene cursos registrados en el sistema.")
    else:
        lines.append("Cursos:")
        for o in sales_orders:
            lines.append(f"  • {o.get('Curso_Nombre', 'N/A')}")
            lines.append(f"    Estado pago: {o.get('Status', 'N/A')}")
            lines.append(f"    LMS: {o.get('LMS_Platform', 'N/A')}")
            lines.append(f"    Orden ID: {o.get('id', '')}")

    if contact.get("LMS_User_ID"):
        lines.append(f"\nUsuario LMS: {contact['LMS_User_ID']}")

    return "\n".join(lines)


@tool
async def send_nps_survey(
    contact_id: str,
    course_name: str,
    score: int,
    comment: str = "",
) -> str:
    """
    Registra la respuesta de una encuesta NPS en Zoho.

    Args:
        contact_id: ID del contacto
        course_name: Nombre del curso evaluado
        score: Puntuación NPS del 0 al 10
        comment: Comentario adicional del alumno (opcional)
    """
    if not (0 <= score <= 10):
        return "La puntuación debe ser entre 0 y 10."

    nps_type = "Promotor" if score >= 9 else ("Neutral" if score >= 7 else "Detractor")

    collections = ZohoCollections()
    await collections.log_interaction(
        contact_id=contact_id,
        notes=f"NPS recibido. Curso: {course_name}. Score: {score}/10 ({nps_type}). Comentario: {comment or 'Sin comentario'}",
        interaction_type="Encuesta NPS",
    )

    if score >= 9:
        return (
            f"¡Gracias por tu puntuación de {score}/10! Nos alegra que hayas tenido una excelente experiencia. "
            f"¿Te gustaría compartir tu reseña en redes sociales?"
        )
    elif score >= 7:
        return f"Gracias por tu puntuación de {score}/10. ¿Hay algo específico que podríamos mejorar para tu próxima experiencia?"
    else:
        return (
            f"Gracias por ser honesto con tu puntuación de {score}/10. Lamentamos que la experiencia no haya sido la esperada. "
            f"Un asesor se pondrá en contacto para escuchar tu feedback en detalle."
        )
