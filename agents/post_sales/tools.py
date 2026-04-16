"""
Herramientas del agente de post-venta.
El alta en LMS la gestiona Zoho automáticamente al crear el contrato.
Aquí solo consultamos estado y gestionamos soporte.
"""
from langchain_core.tools import tool
from integrations.zoho.contacts import ZohoContacts
from integrations.zoho.sales_orders import ZohoSalesOrders
from integrations.zoho.collections import ZohoCollections
import structlog

logger = structlog.get_logger(__name__)


@tool
async def get_student_info(email: str = "", phone: str = "", contact_id: str = "") -> str:
    """
    Obtiene información del alumno: cursos inscriptos, estado de acceso y pagos.
    Prioridad de búsqueda: contact_id > email > phone.

    Args:
        email: Email del alumno (forma preferida de identificación)
        phone: Teléfono del alumno (con código de país)
        contact_id: ID del contacto en Zoho (si ya se conoce)
    """
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
        return "No encontré un alumno registrado con esos datos. ¿Podés verificar el email?"

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
async def request_campus_access(
    contact_id: str,
    order_id: str,
    course_name: str,
    issue_description: str,
) -> str:
    """
    Registra un problema de acceso al campus y escala para revisión.
    El acceso lo gestiona Zoho automáticamente; si falló, se crea un ticket.

    Args:
        contact_id: ID del contacto en Zoho
        order_id: ID de la orden del curso
        course_name: Nombre del curso
        issue_description: Descripción del problema de acceso
    """
    collections = ZohoCollections()
    await collections.log_interaction(
        contact_id=contact_id,
        notes=f"Problema de acceso al campus. Curso: {course_name}. Descripción: {issue_description}",
        interaction_type="Soporte - Acceso campus",
    )
    return (
        f"Registré el problema de acceso para {course_name}.\n"
        f"El equipo técnico revisará tu caso en las próximas 2-4 horas hábiles.\n"
        f"Número de ticket: SOPORTE-{order_id[:8].upper()}\n\n"
        f"Mientras tanto, podés intentar:\n"
        f"• Limpiar caché del navegador\n"
        f"• Usar el link de ingreso que llegó al email al momento de la inscripción\n"
        f"• Probar con otro navegador (Chrome recomendado)"
    )


@tool
async def request_certificate(
    contact_id: str,
    order_id: str,
    course_name: str,
    student_full_name: str,
    student_dni: str = "",
) -> str:
    """
    Registra una solicitud de certificado de aprobación.

    Args:
        contact_id: ID del contacto
        order_id: ID de la orden del curso
        course_name: Nombre del curso
        student_full_name: Nombre completo tal como debe figurar en el certificado
        student_dni: DNI / documento del alumno (opcional, según país)
    """
    collections = ZohoCollections()
    await collections.log_interaction(
        contact_id=contact_id,
        notes=(
            f"Solicitud de certificado. Curso: {course_name}. "
            f"Nombre en certificado: {student_full_name}. DNI: {student_dni or 'No proporcionado'}"
        ),
        interaction_type="Solicitud de certificado",
    )
    return (
        f"Solicitud de certificado registrada correctamente.\n\n"
        f"📋 Detalles:\n"
        f"• Curso: {course_name}\n"
        f"• Nombre en certificado: {student_full_name}\n"
        f"• Referencia: CERT-{order_id[:8].upper()}\n\n"
        f"El certificado será emitido y enviado a tu email registrado dentro de 3 a 5 días hábiles."
    )


@tool
async def log_technical_issue(
    contact_id: str,
    issue_type: str,
    description: str,
    course_name: str = "",
) -> str:
    """
    Registra un problema técnico (videos, descargas, plataforma) y escala si no tiene solución rápida.

    Args:
        contact_id: ID del contacto
        issue_type: Tipo de problema ('video', 'descarga', 'login', 'plataforma', 'otro')
        description: Descripción detallada del problema
        course_name: Nombre del curso afectado (opcional)
    """
    collections = ZohoCollections()
    await collections.log_interaction(
        contact_id=contact_id,
        notes=f"Soporte técnico. Tipo: {issue_type}. Curso: {course_name}. Descripción: {description}",
        interaction_type="Soporte técnico",
    )

    quick_fixes = {
        "video": (
            "• Verificá tu conexión a internet (se necesitan al menos 10 Mbps)\n"
            "• Desactivá el VPN si lo tenés activo\n"
            "• Probá con Chrome o Firefox actualizados\n"
            "• Limpiar caché: Ctrl+Shift+Delete"
        ),
        "descarga": (
            "• Verificá que no tenés bloqueador de descargas activo\n"
            "• Intentá hacer click derecho → Guardar enlace como\n"
            "• Probá con otro navegador"
        ),
        "login": (
            "• Usá el link de recuperación de contraseña en la pantalla de login\n"
            "• Verificá que estás usando el email con el que te registraste\n"
            "• Revisá la carpeta de spam por el email de bienvenida"
        ),
    }

    tip = quick_fixes.get(issue_type, "• Intentá limpiar la caché del navegador y volver a intentar.")

    return (
        f"Registré tu problema técnico de tipo '{issue_type}'.\n\n"
        f"Algunas cosas que podés probar:\n{tip}\n\n"
        f"Si el problema persiste, nuestro equipo técnico lo revisará. "
        f"Ticket de soporte: TEC-{contact_id[:6].upper()}"
    )


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
        return (
            f"Gracias por tu puntuación de {score}/10. ¿Hay algo específico que podríamos mejorar para tu próxima experiencia?"
        )
    else:
        return (
            f"Gracias por ser honesto con tu puntuación de {score}/10. Lamentamos que la experiencia no haya sido la esperada. "
            f"Un asesor se pondrá en contacto para escuchar tu feedback en detalle."
        )
