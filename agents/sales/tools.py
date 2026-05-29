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
from utils.agent_context import log_to_conv, masters_handoff_requested

logger = structlog.get_logger(__name__)


# Respuesta dura cuando el LLM intenta consultar un Máster por brief o deep.
# Los Másters NO se venden por checkout — flujo separado vía asesor humano.
# El bot debe leer este texto, NO inventar pitch del Máster, y derivar.
_MASTER_DERIVATION_RESPONSE = (
    "⛔ STOP — '{slug}' es un MÁSTER PREMIUM. NO se inscribe por el sitio "
    "ni tiene link de checkout. Flujo de inscripción: asesor académico humano.\n\n"
    "Acciones a hacer EN ESTE TURNO:\n"
    "1. NO pitchees el máster, NO listes módulos, NO des precio.\n"
    "2. Decile al user: «Ese es un Máster premium con un proceso de inscripción "
    "distinto. Te derivo a un asesor académico humano para que te lo coordine personalmente.»\n"
    "3. Si tenés alternativa NO-Máster del mismo área (ej: 'Diplomado en X' o "
    "'Curso superior de X'), ofrecela como puente.\n"
    "4. Marcá handoff_requested=true (pedile al user que confirme con su email "
    "para que el asesor lo contacte)."
)


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
    from config.constants import is_master_slug
    from integrations import courses_cache

    await log_to_conv(
        "tool",
        {"action": "tool_get_course_brief", "detail": f"slug={slug} country={country}"},
    )

    if is_master_slug(slug):
        await log_to_conv(
            "action",
            {"action": "master_detectado", "detail": f"slug={slug} — derivación recomendada"},
        )
        return _MASTER_DERIVATION_RESPONSE.format(slug=slug)

    course = await courses_cache.get_course(country.lower(), slug)
    if not course:
        await log_to_conv(
            "error",
            {"action": "curso_no_encontrado", "detail": f"slug={slug} country={country}"},
        )
        return f"No encontré el curso '{slug}' para {country}. Verificá el slug en el catálogo."

    await log_to_conv(
        "action",
        {
            "action": "curso_consultado",
            "detail": f"{course.get('title')} ({country})",
            "slug": slug,
            "title": course.get("title"),
        },
    )

    import re as _re
    brief = course.get("brief_md") or ""
    if brief:
        # Convertir links markdown [texto](URL) → URL plana.
        # Evita que el LLM copie el formato [texto](url) que WhatsApp no renderiza.
        brief = _re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\2', brief)
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
    from config.constants import is_master_slug
    from integrations import courses_cache

    await log_to_conv(
        "tool",
        {
            "action": "tool_get_course_deep",
            "detail": f"slug={slug} country={country} section={section}",
        },
    )

    if is_master_slug(slug):
        await log_to_conv(
            "action",
            {"action": "master_detectado", "detail": f"slug={slug} — derivación recomendada"},
        )
        return _MASTER_DERIVATION_RESPONSE.format(slug=slug)

    course = await courses_cache.get_course_deep(country, slug)
    if not course:
        await log_to_conv(
            "error",
            {"action": "curso_no_encontrado", "detail": f"slug={slug} country={country}"},
        )
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
    await log_to_conv(
        "tool",
        {
            "action": "tool_create_payment_link",
            "detail": f"course={course_name} email={customer_email} {currency} {price}",
            "course": course_name,
            "email": customer_email,
            "amount": price,
            "currency": currency,
            "country": country,
        },
    )

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
            await log_to_conv(
                "error",
                {"action": "payment_link_vacio", "detail": f"course={course_name} email={customer_email}"},
            )
            return "No pude generar el link de pago. Contactá a soporte@msklatam.com para completar la inscripción."

        link_id = result.get("link_id", "")
        logger.info("payment_link_sent", course=course_name, email=customer_email, link_id=link_id)
        await log_to_conv(
            "action",
            {
                "action": "payment_link_generado",
                "detail": f"course={course_name} → {url}",
                "url": url,
                "link_id": link_id,
                "course": course_name,
                "email": customer_email,
                "amount": price,
                "currency": currency,
            },
        )
        return f"Link de pago generado exitosamente:\n{url}"

    except Exception as e:
        logger.error("create_payment_link_failed", error=str(e), course=course_name, email=customer_email)
        await log_to_conv(
            "error",
            {
                "action": "payment_link_failed",
                "detail": f"{type(e).__name__}: {str(e)[:200]}",
                "course": course_name,
                "email": customer_email,
            },
        )
        return (
            "Hubo un error al generar el link de pago. "
            "El usuario puede inscribirse directamente en soporte@msklatam.com "
            "o al WhatsApp de MSK indicando el curso y sus datos."
        )


def _map_profesion_to_zoho(text: str) -> str:
    """Mapea texto libre de profesión a los valores válidos de la picklist Zoho."""
    t = (text or "").lower().strip()
    if not t:
        return "Otra profesión"

    # Residente — va antes de médico porque "residente de cardiología" es Residente
    if "residente" in t:
        return "Residente"

    # Estudiante
    if any(w in t for w in ["estudiante", "alumno", "alumna", "cursando", "estudiando"]):
        return "Estudiante"

    # Auxiliar de enfermería — va antes de enfermería general
    if any(w in t for w in ["auxiliar de enferm", "aux. enferm", "aux enferm"]):
        return "Auxiliar de enfermería"

    # Personal de enfermería
    if any(w in t for w in ["enfermero", "enfermera", "enfermería", "enfermeria", "lic. en enf", "licenciado en enf", "licenciada en enf"]):
        return "Personal de enfermería"

    # Tecnología Médica
    if any(w in t for w in ["tecnólogo", "tecnologo", "tecnología médica", "tecnologia medica", "técnico en imagen", "tecnico en imagen", "radiólogo técnico", "radiologo tecnico"]):
        return "Tecnología Médica"

    # Técnico universitario
    if any(w in t for w in ["técnico universitario", "tecnico universitario", "técnico superior", "tecnico superior"]):
        return "Técnico universitario"

    # Licenciado de la salud (profesiones no médicas del área salud)
    if any(w in t for w in [
        "nutricionista", "nutrición", "nutricion",
        "kinesiólogo", "kinesióloga", "kinesiologo", "kinesiologia", "kinesióloga",
        "fisioterapeuta", "fisioterapia",
        "fonoaudiólogo", "fonoaudióloga", "fonoaudiologo", "fonoaudiologia",
        "psicólogo", "psicóloga", "psicologo", "psicologia",
        "terapista", "terapia ocupacional",
        "bioquímico", "bioquimica",
        "farmacéutico", "farmaceutico", "farmacia",
        "odontólogo", "odontóloga", "odontologo", "odontologia", "dentista",
        "optometrista", "óptico",
        "obstétrica", "obstétrico", "obstetricia",
        "trabajador social", "trabajo social",
        "lic.", "licenciado", "licenciada",
    ]):
        return "Licenciado de la salud"

    # Personal médico (amplio — cualquier especialidad médica)
    if any(w in t for w in [
        "médico", "medico", "médica", "medica",
        "doctor", "doctora", "dr.", "dra.",
        "cirujano", "cirujana",
        "cardiólogo", "cardióloga", "cardiologo",
        "neurólogo", "neuróloga", "neurologo",
        "pediatra",
        "clínico", "clínica", "clinico",
        "internista", "medicina interna",
        "ginecólogo", "ginecóloga", "ginecologo", "ginecología",
        "dermatólogo", "dermatologo", "dermatología",
        "psiquiatra", "psiquiatría",
        "traumatólogo", "traumatologo", "traumatología",
        "urólogo", "urologo", "urología",
        "oftalmólogo", "oftalmologo", "oftalmología",
        "otorrinolaringólogo", "otorrino",
        "anestesiólogo", "anestesista", "anestesiología",
        "radiólogo", "radiologo", "radiología",
        "oncólogo", "oncologo", "oncología",
        "gastroenterólogo", "gastroenterologo",
        "nefrólogo", "nefrologo", "nefrología",
        "reumatólogo", "reumatologo", "reumatología",
        "endocrinólogo", "endocrinologo",
        "infectólogo", "infectologo",
        "hematólogo", "hematologo",
        "neumólogo", "neumologo", "neumología",
        "hepatólogo", "hepatologo",
        "obstetra", "tocólogo",
        "médico general", "medico general",
        "médico de familia", "medicina familiar",
        "emergentólogo", "emergenciólogo", "urgenciólogo",
        "intensivista", "terapia intensiva", "uci",
        "flebólogo", "flebologa",
    ]):
        return "Personal médico"

    # Fuerza pública
    if any(w in t for w in ["policía", "policia", "militar", "gendarmería", "gendarmeria", "bombero", "fuerza pública", "fuerza publica"]):
        return "Fuerza pública"

    return "Otra profesión"


@tool
async def create_or_update_lead(
    name: str,
    phone: str,
    email: str,
    country: str,
    course_name: str,
    channel: str = "WhatsApp",
    notes: str = "",
    brand: str = "",
    lead_status: str = "Atención BOT IA",
    lead_source: str = "Widget",
    ad_account: str = "Widget",
    ad_id: str = "",
    ad_name: str = "",
    tipo_de_lead: str = "",
    lead_id_social: str = "",
    profesion: str = "",
    especialidad: str = "",
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
        brand: Marca del curso. Para los 6 Másters premium (Urgencias, Cuidados
            Paliativos, Imagen Clínica/Ecografía, Rehabilitación/Fisioterapia,
            Salud Familiar, Medicina Estética) pasar brand="Master". Para
            cursos normales dejar vacío.
        lead_status: Estado del lead en Zoho. Para leads CTWA usar "No habilitado"
            (evita que Zoho dispare otra plantilla). Default "Atención BOT IA".
        lead_source: Fuente del lead. "Facebook" para CTWA, "Widget" default.
        ad_account: Cuenta de anuncio. "Facebook" para CTWA, "Widget" default.
        ad_id: ID del anuncio Meta (referralSourceId). Solo para CTWA.
        ad_name: Nombre/headline del anuncio Meta (referralHeadline). Solo para CTWA.
        tipo_de_lead: "Paid" para leads de campañas pagas. Vacío por defecto.
        lead_id_social: ID de click CTWA (referralCtwaClid). Solo para CTWA.
        profesion: Profesión del usuario en texto libre (ej. "médico cardiólogo").
            Se mapea automáticamente a la picklist de Zoho.
        especialidad: Especialidad o área del usuario (ej. "Cardiología"). Texto libre.
    """
    # Log de entrada — visibilidad cuándo el LLM dispara la tool.
    logger.info(
        "create_or_update_lead_called",
        name=name,
        phone=phone,
        email=email,
        country=country,
        course=course_name,
        channel=channel,
        has_notes=bool(notes),
    )
    await log_to_conv(
        "tool",
        {
            "action": "tool_create_or_update_lead",
            "detail": f"name={name} email={email} phone={phone} course={course_name}"
            + (f" brand={brand}" if brand else ""),
            "name": name,
            "email": email,
            "phone": phone,
            "country": country,
            "course": course_name,
            "channel": channel,
            "brand": brand,
        },
    )

    # Si el lead es de Másters, marcar el ContextVar para que el endpoint
    # del canal (widget/whatsapp) dispare el handoff al asesor académico.
    # Esto reemplaza la dependencia del tag textual `[DERIVAR_MASTERS_VANESA]`
    # que el LLM emite de forma inconsistente.
    if (brand or "").strip().lower() == "master":
        try:
            masters_handoff_requested.set(True)
        except Exception:
            pass

    try:
        leads = ZohoLeads()
        # 🔑 Match por EMAIL (no por teléfono).
        # Razón: cada email distinto debe crear un lead nuevo aunque el
        # teléfono coincida con uno viejo (mismo dispositivo, distinta persona).
        # Search por phone generaba falsos UPDATE y perdíamos los nuevos emails.
        existing = await leads.search_by_email(email) if email else None

        profesion_zoho = _map_profesion_to_zoho(profesion) if profesion else ""

        data = {
            "name": name,
            "phone": phone,
            "email": email,
            "country": country,
            "curso_de_interes": course_name,  # Va a `Description` en el create (ZohoLeads.create)
            "canal_origen": channel,
            "notas": notes,
            "brand": brand,  # "Master" para Másters, "" para cursos normales
            "lead_status": lead_status,
            "lead_source": lead_source,
            "ad_account": ad_account,
            "ad_id": ad_id,
            "ad_name": ad_name,
            "tipo_de_lead": tipo_de_lead,
            "lead_id_social": lead_id_social,
            "profesion": profesion_zoho,
            "profesion_raw": profesion,  # texto libre para Notas_Bot
            "especialidad": especialidad,
        }

        if existing:
            logger.info(
                "create_or_update_lead_path_update",
                existing_lead_id=existing.get("id"),
                email=email,
                match_by="email",
            )
            # Decisión: PISAR TODOS los campos del lead — datos de contacto,
            # Lead_Source / Ad_Account / Lead_Status / país. El último contacto
            # gana. Se pierde la atribución original pero queda 100% sincronizado
            # con la conversación más reciente.
            # Partir el name en First_Name / Last_Name (mismo split que `ZohoLeads.create`).
            _parts = (name or "").strip().split(" ", 1)
            _first = _parts[0] if _parts else ""
            _last = _parts[1] if len(_parts) > 1 else (_first or "Sin nombre")
            # Armar notas combinando texto libre de profesión + notas del bot
            _notas_parts = []
            if profesion:
                _notas_parts.append(f"Profesión declarada: {profesion}")
            if notes:
                _notas_parts.append(notes)
            _notas_combined = " | ".join(_notas_parts) if _notas_parts else notes

            update_payload = {
                "First_Name": _first,
                "Last_Name": _last,
                "Phone": phone or "",
                "Email": email or "",
                "Pais": ZohoLeads._normalize_pais(country or "Argentina"),
                "Lead_Source": lead_source,
                "Lead_Status": lead_status,
                "Ad_Account": ad_account,
                "Brand": brand or "",
                "Description": course_name,
                "Notas_Bot": _notas_combined,
            }
            if profesion_zoho:
                update_payload["Profesion"] = profesion_zoho
            if especialidad:
                update_payload["Especialidad"] = especialidad
            if ad_id:
                update_payload["Ad_ID"] = ad_id
            if ad_name:
                update_payload["Ad_Name"] = ad_name
            if tipo_de_lead:
                update_payload["Tipo_de_lead"] = tipo_de_lead
            if lead_id_social:
                update_payload["Lead_ID_social"] = lead_id_social
            await leads.update(existing["id"], update_payload)
            await log_to_conv(
                "action",
                {
                    "action": "lead_actualizado_zoho",
                    "detail": f"ID: {existing['id']} (match por email) — curso: {course_name}",
                    "lead_id": existing["id"],
                    "match_by": "email",
                    "email": email,
                    "course": course_name,
                },
            )
            return f"Lead actualizado en Zoho. ID: {existing['id']}"
        else:
            logger.info(
                "create_or_update_lead_path_create",
                email=email,
                phone=phone,
                reason="email_not_found_in_zoho",
            )
            result = await leads.create(data)
            await log_to_conv(
                "action",
                {
                    "action": "lead_creado_zoho",
                    "detail": f"Nuevo lead {result['id']} — {name} ({email}) — curso: {course_name}",
                    "lead_id": result["id"],
                    "name": name,
                    "email": email,
                    "phone": phone,
                    "country": country,
                    "course": course_name,
                },
            )
            return f"Lead creado en Zoho. ID: {result['id']}"
    except Exception as e:
        logger.error(
            "create_or_update_lead_failed",
            error=str(e),
            error_type=type(e).__name__,
            email=email,
            phone=phone,
            course=course_name,
        )
        await log_to_conv(
            "error",
            {
                "action": "lead_zoho_failed",
                "detail": f"{type(e).__name__}: {str(e)[:200]}",
                "email": email,
                "phone": phone,
                "course": course_name,
            },
        )
        # Devuelve string descriptivo al LLM (NO re-raise, así el agente
        # puede manejar el fallo en lugar de morir).
        return (
            f"Error al registrar el lead en Zoho ({type(e).__name__}): {str(e)[:200]}. "
            "Continuá la conversación normalmente — un asesor humano podrá registrarlo después."
        )


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
    await log_to_conv(
        "tool",
        {
            "action": "tool_create_sales_order",
            "detail": f"contact_id={contact_id} course={course_name} {currency} {price}",
            "contact_id": contact_id,
            "course": course_name,
            "amount": price,
            "currency": currency,
            "country": country,
            "payment_provider": payment_provider,
        },
    )
    try:
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
        await log_to_conv(
            "action",
            {
                "action": "sales_order_creada",
                "detail": f"Order {result['id']} — {course_name} ({currency} {price})",
                "order_id": result["id"],
                "course": course_name,
                "amount": price,
                "currency": currency,
                "payment_provider": payment_provider,
            },
        )
        return f"Orden de venta creada en Zoho. ID: {result['id']}"
    except Exception as e:
        logger.error("create_sales_order_failed", error=str(e), course=course_name, contact_id=contact_id)
        await log_to_conv(
            "error",
            {
                "action": "sales_order_failed",
                "detail": f"{type(e).__name__}: {str(e)[:200]}",
                "course": course_name,
                "contact_id": contact_id,
            },
        )
        return f"Error al crear la orden de venta en Zoho: {str(e)[:200]}"
