"""
Herramientas del agente de ventas:
- get_course_brief: brief completo de un curso para venderlo
- get_course_deep: sección puntual del JSON original
- create_payment_link: genera link de pago
- create_lead: crea lead en Zoho
- create_sales_order: crea orden de venta en Zoho

Nota: el catálogo compacto (título + categoría + precio de todos los cursos)
se inyecta directo en el system prompt — el agente NO necesita buscar.
"""

import structlog
from langchain_core.tools import tool

from integrations.payments.rebill import RebillClient
from integrations.zoho.leads import ZohoLeads
from integrations.zoho.sales_orders import ZohoSalesOrders

logger = structlog.get_logger(__name__)


@tool
async def get_course_brief(slug: str, country: str = "AR") -> str:
    """
    Devuelve el brief completo de un curso — perfiles objetivo, datos técnicos,
    objetivos, certificaciones, precio. Usalo cuando querés vender un curso
    distinto al que el usuario está viendo, o para comparar cursos.

    Args:
        slug: Slug del curso (lo ves en el catálogo del system prompt)
        country: Código de país del usuario (AR, MX, CO, PE, CL, UY, etc.)
    """
    from integrations import courses_cache

    course = await courses_cache.get_course(country.lower(), slug)
    if not course:
        return f"No encontré el curso '{slug}' para {country}. Verificá el slug en el catálogo."

    brief = course.get("brief_md") or ""
    if brief:
        return brief

    # Fallback mínimo si no tiene brief
    return (
        f"Título: {course.get('title')}\n"
        f"Categoría: {course.get('categoria')}\n"
        f"Precio: {course.get('currency')} {course.get('total_price')}\n"
        f"Slug: {slug}"
    )


@tool
async def get_course_deep(slug: str, country: str = "AR", section: str = "summary") -> str:
    """
    Lee una sección específica del JSON original de un curso sincronizado desde el WP.
    Usar cuando el usuario pregunta por información puntual que NO está en el brief
    inyectado en el system prompt (ej: "contame más del módulo 3", "quiénes son los
    docentes", "qué avales tiene para mi provincia").

    Args:
        slug: Slug del curso (ej: 'cardiologia-amir'). Si el usuario está viendo
              un curso en la web, ya sabés cuál es — usá ese slug.
        country: Código ISO-2 de país (AR, MX, CO, PE, CL, UY, ES, etc.)
        section: Sección a leer. Valores válidos:
            - 'modules' → plan de estudios con los temas de cada módulo
            - 'teaching_team' → equipo docente completo
            - 'institutions' → instituciones avalantes detalladas
            - 'certificacion_relacionada' → certificaciones adicionales disponibles
            - 'learning' → qué vas a aprender
            - 'habilities' → habilidades que desarrolla
            - 'formacion_dirigida' → a quién está dirigido
            - 'perfiles_dirigidos' → perfiles objetivo con dolor y beneficio
            - 'objetivos' → objetivos de aprendizaje
            - 'prices' → precio y cuotas
            - 'summary' → resumen corto (default)
    """
    from integrations import courses_cache

    course = await courses_cache.get_course_deep(country, slug)
    if not course:
        return f"No encontré el curso '{slug}' para el país {country}. Verificá el slug y el país."

    raw = course.get("raw") or {}
    sections = raw.get("sections") or {}
    kb_ai = raw.get("kb_ai") or {}
    section = (section or "").lower().strip()

    # Importar helper local para limpieza HTML
    from integrations.msk_courses import html_to_text

    def _dump_list(items: list, formatter) -> str:
        out = [formatter(x) for x in items]
        out = [x for x in out if x]
        return "\n".join(out) if out else "(sin datos)"

    if section == "modules":
        mods = (sections.get("study_plan") or {}).get("modules") or []
        if not mods:
            return "(sin plan de estudios)"
        out = []
        for i, m in enumerate(mods, 1):
            out.append(f"### Módulo {i} — {m.get('title', '').strip()}")
            out.append(html_to_text(m.get("content", "")))
            out.append("")
        return "\n".join(out).strip()

    if section == "teaching_team":
        team = sections.get("teaching_team") or []
        return _dump_list(
            team,
            lambda t: (
                f"- {t.get('name', '')}"
                + (f" ({t.get('description', '')})" if t.get("description") else "")
                + (f" — {t.get('specialty', '')}" if t.get("specialty") else "")
            ),
        )

    if section == "institutions":
        insts = sections.get("institutions") or []
        return _dump_list(
            insts, lambda i: f"- **{i.get('title', '')}** — {html_to_text(i.get('description', ''))}"
        )

    if section == "certificacion_relacionada":
        certs = raw.get("certificacion_relacionada") or []
        import html as _html

        return _dump_list(
            certs,
            lambda c: (
                f"- {_html.unescape(c.get('title', ''))}"
                + (f" ({c.get('currency', '')} {c.get('total_price', '')})" if c.get("total_price") else "")
            ),
        )

    if section == "learning":
        return _dump_list(
            sections.get("learning") or [], lambda l: f"- {html_to_text(l.get('msk_learning_content', ''))}"
        )

    if section == "habilities":
        habs = sections.get("habilities") or []
        names = [h.get("name", "") for h in habs if h.get("name")]
        return ", ".join(names) if names else "(sin datos)"

    if section == "formacion_dirigida":
        return _dump_list(
            sections.get("formacion_dirigida") or [], lambda d: f"- {html_to_text(d.get('step', ''))}"
        )

    if section == "perfiles_dirigidos":
        perfiles = kb_ai.get("perfiles_dirigidos") or []
        out = []
        for p in perfiles:
            out.append(f"### {p.get('perfil', '').strip()}")
            out.append(f"**Dolor:** {html_to_text(p.get('problema_actual__necesidad', ''))}")
            out.append(f"**Qué obtiene:** {html_to_text(p.get('que_obtiene', ''))}")
            out.append("")
        return "\n".join(out).strip() or "(sin datos)"

    if section in ("objetivos", "objetivos_de_aprendizaje"):
        return html_to_text(kb_ai.get("objetivos_de_aprendizaje", "")) or "(sin datos)"

    if section == "prices":
        prices = raw.get("prices") or {}
        lines = [
            f"Moneda: {prices.get('currency', '')}",
            f"Precio total: {prices.get('total_price', '')}",
            f"Cuotas máximas: {prices.get('max_installments', '')}",
            f"Valor cuota: {prices.get('price_installments', '')}",
            f"Gratis: {prices.get('is_free', False)}",
        ]
        return "\n".join(lines)

    # Default: resumen + URL
    return (
        f"Título: {course.get('title')}\n"
        f"Categoría: {course.get('categoria')}\n"
        f"Cedente: {course.get('cedente')}\n"
        f"Duración: {course.get('duration_hours')}h — {course.get('modules_count')} módulos\n"
        f"Precio: {course.get('currency')} {course.get('total_price')} "
        f"({course.get('max_installments')} pagos de {course.get('price_installments')})\n"
        f"URL: {course.get('url')}\n"
    )


@tool
async def create_payment_link(
    course_name: str,
    price: float,
    currency: str,
    country: str,
    customer_email: str,
    customer_name: str,
) -> str:
    """
    Genera un link de pago (Rebill) para la inscripción al curso.
    El link se genera con los pagos mensuales habilitados según el país del usuario.

    Args:
        course_name: Nombre del curso (ej: "Curso Superior de Cardiología AMIR")
        price: Precio TOTAL del curso (no el del pago mensual — el total)
        currency: Moneda (ARS, MXN, COP, CLP, PEN, UYU)
        country: País del usuario (AR, MX, CO, CL, PE, UY)
        customer_email: Email del cliente
        customer_name: Nombre completo del cliente
    """
    try:
        client = RebillClient()
        result = await client.create_payment_link(
            title=course_name,
            amount=price,
            currency=currency,
            country=country,
            customer_email=customer_email,
            customer_name=customer_name,
            is_single_use=True,
        )
        url = result.get("checkout_url", "")
        if not url:
            return "No pude generar el link de pago. Contactá a soporte@msklatam.com para completar la inscripción."

        link_id = result.get("link_id", "")
        logger.info("payment_link_sent", course=course_name, email=customer_email, link_id=link_id)
        return f"Link de pago generado exitosamente:\n{url}"

    except Exception as e:
        logger.error("create_payment_link_failed", error=str(e), course=course_name, email=customer_email)
        return (
            "Hubo un error al generar el link de pago. "
            "El usuario puede inscribirse directamente en soporte@msklatam.com "
            "o al WhatsApp de MSK indicando el curso y sus datos."
        )


@tool
async def create_or_update_lead(
    name: str,
    phone: str,
    email: str,
    country: str,
    course_name: str,
    channel: str = "WhatsApp",
    notes: str = "",
) -> str:
    """
    Crea o actualiza un Lead en Zoho CRM.
    Llamar cuando el usuario muestra interés o antes de generar el link de pago.

    Args:
        name: Nombre completo
        phone: Teléfono con código de país
        email: Email
        country: País (Argentina, México, etc.)
        course_name: Nombre del curso de interés
        channel: Canal de origen (WhatsApp, Widget Web)
        notes: Notas adicionales
    """
    leads = ZohoLeads()
    existing = await leads.search_by_phone(phone) if phone else None

    data = {
        "name": name,
        "phone": phone,
        "email": email,
        "country": country,
        "curso_de_interes": course_name,
        "canal_origen": channel,
        "notas": notes,
    }

    if existing:
        await leads.update(
            existing["id"],
            {
                "Curso_de_Interes": course_name,
                "Notas_Bot": notes,
            },
        )
        return f"Lead actualizado en Zoho. ID: {existing['id']}"
    else:
        result = await leads.create(data)
        return f"Lead creado en Zoho. ID: {result['id']}"


@tool
async def create_sales_order(
    contact_id: str,
    course_name: str,
    price: float,
    currency: str,
    country: str,
    payment_link: str,
    payment_provider: str,
    notes: str = "",
) -> str:
    """
    Crea una Sales Order en Zoho CRM para registrar la inscripción y el link de pago.
    Llamar después de generar el link de pago.

    Args:
        contact_id: ID del contacto en Zoho
        course_name: Nombre del curso
        price: Precio
        currency: Moneda
        country: País
        payment_link: URL del link de pago generado
        payment_provider: 'MercadoPago' o 'Rebill'
        notes: Notas adicionales
    """
    orders = ZohoSalesOrders()
    result = await orders.create(
        {
            "contact_id": contact_id,
            "curso_nombre": course_name,
            "precio": price,
            "moneda": currency,
            "payment_link": payment_link,
            "payment_provider": payment_provider,
            "pais": country,
            "notas": notes,
        }
    )
    return f"Orden de venta creada en Zoho. ID: {result['id']}"
