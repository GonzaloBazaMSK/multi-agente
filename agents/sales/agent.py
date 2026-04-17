"""
Agente de Ventas — LangGraph ReAct agent.
Catálogo completo en system prompt + tools para brief detallado y deep drill-down.
"""
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage
from langgraph.prebuilt import create_react_agent
import structlog

from config.settings import get_settings
from agents.sales.tools import (
    get_course_brief,
    get_course_deep,
    create_payment_link,
    create_or_update_lead,
    create_sales_order,
)
from agents.sales.prompts import build_sales_prompt

logger = structlog.get_logger(__name__)

SALES_TOOLS = [
    get_course_brief,
    get_course_deep,
    create_payment_link,
    create_or_update_lead,
    create_sales_order,
]


async def build_sales_agent(
    country: str = "AR",
    channel: str = "whatsapp",
    page_slug: str = "",
    user_profile: Optional[dict] = None,
):
    """
    Construye el agente de ventas con el sistema prompt y herramientas.
    Si `page_slug` apunta a un curso conocido (+ país), inyecta el `brief_md`
    del curso al system prompt — así el bot puede vender ESE curso con
    contexto completo sin depender de RAG.

    Retorna un agente compilado (LangGraph CompiledGraph).
    """
    settings = get_settings()
    llm = ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        temperature=0.3,
    )

    # --- STEP 1: resolver curso (si aplica) ---
    course = None
    if page_slug:
        try:
            from integrations import courses_cache
            course = await courses_cache.get_course(country.lower(), page_slug)
            if not (course and course.get("brief_md")):
                logger.info("sales_agent_no_brief_for_slug", country=country, slug=page_slug)
                course = None
        except Exception as e:
            logger.warning("sales_agent_brief_load_failed", error=str(e), slug=page_slug)
            course = None

    # --- STEP 2: construir HEADER prioritario con perfil del usuario ---
    # Este bloque va ANTES del prompt maestro para que el LLM lo lea primero
    # y no se le pierda entre 25KB de instrucciones.
    priority_header = _build_priority_profile_header(
        user_profile=user_profile,
        course=course,
    )

    # --- STEP 3: ensamblar system prompt ---
    base_prompt = build_sales_prompt(country=country, channel=channel)
    system_prompt = (priority_header + base_prompt) if priority_header else base_prompt

    # --- STEP 4: inyectar catálogo compacto del país ---
    try:
        from memory import postgres_store
        catalog = await postgres_store.get_catalog_compact(country)
        if catalog:
            system_prompt += f"\n\n---\n\n{catalog}\n\n"
            system_prompt += (
                "👆 El catálogo completo del país está envuelto en las etiquetas "
                f"`<catalogo_{country.upper()}>...</catalogo_{country.upper()}>`. "
                "Ya lo tenés — NO necesitás buscar. Cada fila es un curso: slug + título + categoría + precio.\n"
                "Para vender un curso distinto al actual, usá `get_course_brief(slug)` "
                "para obtener el brief con perfiles, datos técnicos y argumentos de venta.\n"
                "**No mezcles datos entre filas**: cada fila es un curso independiente — el precio de la fila 3 corresponde SOLO al curso de la fila 3.\n"
            )
    except Exception as e:
        logger.warning("catalog_inject_failed", error=str(e))

    # --- STEP 5: append brief del curso activo (SIN re-inyectar perfil — ya está en el priority header) ---
    if course:
        system_prompt += _format_course_context(course, user_profile, has_priority_header=bool(priority_header))

    # --- STEP 6: fallback — perfil suelto SOLO si NO hay priority header Y NO hay curso ---
    # (si hay priority_header, el perfil ya está inyectado al INICIO; no lo dupliques al final)
    if user_profile and not page_slug and not priority_header:
        prof_ctx = _format_user_profile(user_profile)
        if prof_ctx:
            system_prompt += prof_ctx

    agent = create_react_agent(
        model=llm,
        tools=SALES_TOOLS,
        prompt=SystemMessage(content=system_prompt),
    )
    return agent


def _build_priority_profile_header(
    user_profile: Optional[dict],
    course: Optional[dict],
) -> str:
    """
    Header de MÁXIMA prioridad que se inyecta al INICIO del system prompt.
    Su propósito: que el LLM vea los datos del usuario ANTES de los 25KB de
    reglas/intents, para que no los ignore en el pitch.

    Si no hay datos personales → retorna "" y no se agrega ruido.
    """
    if not user_profile:
        return ""

    name = user_profile.get("name") or user_profile.get("Full_Name") or ""
    prof = user_profile.get("profession") or user_profile.get("Profession") or ""
    spec = user_profile.get("specialty") or user_profile.get("Specialty") or ""
    cargo = user_profile.get("cargo") or ""
    lugar = user_profile.get("lugar_trabajo") or ""
    area = user_profile.get("area_trabajo") or ""
    colegio = user_profile.get("colegio") or ""
    interests = user_profile.get("interests") or ""
    courses_done = user_profile.get("courses") or []

    # Si realmente no hay nada útil, no agregues nada
    if not any([name, prof, spec, cargo, lugar, area, colegio]):
        return ""

    lines = [
        "# 🎯 DATOS DEL USUARIO — USALOS EN CADA RESPUESTA (LEER PRIMERO)",
        "",
        "Este es el perfil del usuario con el que estás hablando AHORA MISMO. ",
        "**NO es contexto opcional — es lo que te permite vender.** Usá estos datos en:",
        "  (a) la LÍNEA DE APERTURA del pitch ('Gonzalo, para vos que sos [cargo] en [área]…')",
        "  (b) la mención de certificaciones jurisdiccionales (nombrá SU colegio, no la lista de 5)",
        "  (c) la adaptación del registro técnico según cargo/profesión",
        "",
        "## Datos del cliente",
    ]

    if name:
        lines.append(f"- **Nombre:** {name}")
    if prof:
        lines.append(f"- **Profesión:** {prof}")
    if spec:
        lines.append(f"- **Especialidad:** {spec}")
    if cargo:
        lines.append(f"- **Cargo:** {cargo}")
    if lugar:
        lines.append(f"- **Lugar de trabajo:** {lugar}")
    if area:
        lines.append(f"- **Área donde trabaja:** {area}")
    if colegio:
        lines.append(f"- **Matrícula activa en:** {colegio}")
    if interests:
        lines.append(f"- **Intereses declarados:** {interests}")
    if courses_done:
        if isinstance(courses_done, list):
            cs = ", ".join(str(c) for c in courses_done[:5])
        else:
            cs = str(courses_done)
        lines.append(f"- **Cursos que ya hizo en MSK:** {cs}")
        lines.extend([
            "",
            "## 🚫 REGLA CRÍTICA — CURSOS QUE YA TIENE (PROHIBIDO RECOMENDAR)",
            "",
            f"El usuario **YA completó** estos cursos: {cs}.",
            "",
            "**PROHIBIDO:**",
            "  - Recomendarlos como opción",
            "  - Incluirlos en listados de cursos sugeridos",
            "  - Mencionarlos como 'te podría interesar'",
            "  - Sugerir 'profundizar' o 'complementar' con un curso que ya hizo",
            "",
            "**Si el usuario está viendo la página de un curso que ya tiene**, no intentes vendérselo de nuevo. ",
            "Reconocé que ya lo hizo y sugerí algo complementario o de nivel superior.",
            "",
            "**Cuando recomiendes cursos del catálogo, filtrá mentalmente** y NO muestres los que ya tiene.",
        ])

    # Regla de confianza: lo que dice el usuario pisa los datos del CRM
    if prof or spec:
        lines.extend([
            "",
            "## ⚠️ REGLA — SI EL USUARIO CONTRADICE ESTOS DATOS, CREELE",
            "",
            "Los datos de arriba vienen del CRM y pueden estar desactualizados.",
            "Si el usuario dice algo distinto (ej: el CRM dice 'Cardiología' pero el ",
            "usuario dice 'soy médico general'), **creele al usuario**. ",
            "Adaptá tu respuesta a lo que ÉL dice, no a lo que dice el CRM.",
            "No le corrijas ni le contradigas — ajustá tu pitch a su realidad actual.",
        ])

    # Directiva crítica sobre colegio con aval jurisdiccional
    if colegio:
        lines.extend([
            "",
            "## ⚠️ REGLA CRÍTICA — Colegio con aval jurisdiccional",
            "",
            f"El usuario tiene matrícula activa en: **{colegio}**.",
            "",
            "Si ese colegio matchea con alguno de los 5 con aval jurisdiccional de MSK:",
            "  - **COLEMEMI** — Colegio de Médicos de Misiones",
            "  - **COLMEDCAT** — Colegio de Médicos de Catamarca",
            "  - **CSMLP** — Consejo Superior Médico de La Pampa",
            "  - **CMSC** — Consejo Médico de Santa Cruz",
            "  - **CMSF1** — Colegio de Médicos de Santa Fe 1ra",
            "",
            "**ENTONCES, cuando hables del curso o de certificaciones, mencionalo PROACTIVAMENTE "
            f"con el NOMBRE ESPECÍFICO del colegio del usuario** (ej: 'te suma el aval de COLEMEMI sin costo'). ",
            "**PROHIBIDO** tirar la lista genérica de los 5 — eso se lee como que ignoraste su matrícula.",
        ])

    # Instrucción de personalización si tenemos perfil + curso
    if course and (cargo or prof or spec or area or lugar):
        title = course.get("title") or ""
        lines.extend([
            "",
            "## ⚠️ APERTURA DEL PITCH — OBLIGATORIA",
            "",
            f"El usuario está viendo el curso **{title}**. Cuando presentes el curso por primera vez, ",
            "**la primera oración debe conectar SU perfil con un beneficio concreto del curso**.",
            "",
            "**PROHIBIDO** arrancar con:",
            "  - '¿A quién está dirigido?' (ya sabés quién es)",
            "  - 'El curso está diseñado para médicos que…' (genérico, no lo personaliza)",
            "  - Cualquier fórmula que ignore el cargo/lugar/área que tenés arriba",
            "",
            "**EN CAMBIO** arrancá con algo como:",
            f"  - '{name or 'Hola'}, para vos que {(('sos ' + (cargo.lower() if cargo else prof.lower())) if (cargo or prof) else 'trabajás en el área')}"
            f"{(' de ' + spec.lower()) if spec else ''}"
            f"{(', en ' + lugar) if lugar else ''}, este curso te sirve especialmente porque…'",
        ])

    lines.extend([
        "",
        "---",
        "",
    ])
    return "\n".join(lines)


def _format_course_context(
    course: dict,
    user_profile: Optional[dict] = None,
    has_priority_header: bool = False,
) -> str:
    """
    Arma el bloque del curso + instrucción de venta contextualizada.

    Si `has_priority_header=True` → el perfil del usuario YA está inyectado al
    inicio del system prompt, así que acá NO lo repetimos (evita duplicación
    que confunde al LLM). Solo se incluye el perfil dentro del course context
    cuando NO hay priority header (ej: llamada sin user_profile pero con curso).
    """
    brief = course.get("brief_md") or ""
    slug = course.get("slug") or ""
    title = course.get("title") or ""
    country_code = (course.get("country") or "").upper()

    profile_line = ""
    if user_profile and not has_priority_header:
        # Solo inyectamos perfil acá si no se inyectó arriba.
        prof = user_profile.get("profession") or user_profile.get("Profession") or ""
        spec = user_profile.get("specialty") or user_profile.get("Specialty") or ""
        name = user_profile.get("name") or user_profile.get("Full_Name") or ""
        cargo = user_profile.get("cargo") or ""
        lugar = user_profile.get("lugar_trabajo") or ""
        area = user_profile.get("area_trabajo") or ""
        colegio = user_profile.get("colegio") or ""
        interests = user_profile.get("interests") or ""
        courses_done = user_profile.get("courses") or []

        bits = []
        if name:
            bits.append(f"se llama **{name}**")
        if prof:
            bits.append(f"profesión: **{prof}**")
        if spec:
            bits.append(f"especialidad: **{spec}**")
        if cargo:
            bits.append(f"cargo: **{cargo}**")
        if lugar:
            bits.append(f"lugar de trabajo: **{lugar}**")
        if area:
            bits.append(f"área donde trabaja: **{area}**")
        if bits:
            profile_line = "- El usuario " + ", ".join(bits) + ".\n"

        if colegio:
            profile_line += (
                f"- **Matrícula activa en: {colegio}**. "
                "Si matchea con uno de los 5 colegios argentinos con aval "
                "jurisdiccional (COLEMEMI-Misiones, COLMEDCAT-Catamarca, "
                "CSMLP-La Pampa, CMSC-Santa Cruz, CMSF1-Santa Fe), **mencionalo "
                "proactivamente al hablar de certificaciones o del curso** "
                "diciendo el nombre del colegio del usuario (NO la lista de 5).\n"
            )

        if interests:
            profile_line += f"- Intereses declarados: {interests}.\n"

        if courses_done:
            if isinstance(courses_done, list):
                cs = ", ".join(str(c) for c in courses_done[:5])
            else:
                cs = str(courses_done)
            profile_line += f"- Cursos que ya realizó en MSK: {cs}.\n"

    # Si hay priority header, NO repetimos la cabecera "El usuario entró al
    # chat desde la página del curso X" porque el priority header ya lo dice.
    # Solo dejamos el brief + la instrucción de venta.
    intro = (
        ""
        if has_priority_header
        else f"El usuario entró al chat desde la página del curso **{title}** (slug: `{slug}`).\nAsumí que la consulta es sobre ESTE curso salvo que diga lo contrario.\n\n"
    )

    profile_block = f"{profile_line}" if profile_line else ""

    return f"""

---

## CURSO ACTIVO — BRIEF COMPLETO

{intro}### Brief del curso **{title}** (slug: `{slug}`) — fuente primaria, datos oficiales

{brief}

---

## INSTRUCCIÓN DE VENTA CONTEXTUAL

{profile_block}- **Hablá de este curso por nombre** — ya sabés cuál es, no preguntes "qué curso te interesa".
- **Elegí el perfil objetivo que mejor encaje** con el usuario (ver "Perfiles objetivo — dolor y beneficio"):
  usá el **dolor** del perfil para mostrar empatía y el **beneficio** para construir el pitch.
- **Primera respuesta sobre este curso**: 4-5 líneas + gancho + pregunta bifurcada. **Sin precio, sin volcar módulos, sin subheaders.** (ver sección 2.1)
- **Precio y cuotas**: solo cuando el usuario lo pregunta o da señal de compra — nunca de entrada.
- Si el usuario quiere profundizar en un módulo, el equipo docente o las certificaciones,
  usá la tool `get_course_deep(slug="{slug}", country="{country_code}", section="…")`
  con secciones como `modules`, `teaching_team`, `institutions`, `certificacion_relacionada`.
- **No repitas el brief literal** — conversá, referite a los puntos que apliquen.
- Si el usuario intenta inscribirse, seguí el flujo de inscripción normal (nombre + email → link).
"""


def _format_user_profile(user_profile: dict) -> str:
    """Contexto liviano del usuario cuando NO hay page_slug (navegación genérica)."""
    prof = user_profile.get("profession") or user_profile.get("Profession") or ""
    spec = user_profile.get("specialty") or user_profile.get("Specialty") or ""
    cargo = user_profile.get("cargo") or ""
    lugar = user_profile.get("lugar_trabajo") or ""
    area = user_profile.get("area_trabajo") or ""
    colegio = user_profile.get("colegio") or ""
    interests = user_profile.get("interests") or user_profile.get("Interests") or ""
    courses = user_profile.get("courses") or user_profile.get("Courses") or []

    bits = []
    if prof:
        bits.append(f"Profesión: {prof}")
    if spec:
        bits.append(f"Especialidad: {spec}")
    if cargo:
        bits.append(f"Cargo: {cargo}")
    if lugar:
        bits.append(f"Lugar de trabajo: {lugar}")
    if area:
        bits.append(f"Área donde trabaja: {area}")
    if colegio:
        bits.append(
            f"Matrícula activa en: {colegio} — **si matchea con uno de los 5 "
            "colegios AR con aval jurisdiccional (COLEMEMI-Misiones, "
            "COLMEDCAT-Catamarca, CSMLP-La Pampa, CMSC-Santa Cruz, "
            "CMSF1-Santa Fe), mencionalo proactivamente al hablar de "
            "certificaciones** diciendo el nombre del colegio del usuario."
        )
    if interests:
        bits.append(f"Intereses: {interests}")
    if courses:
        if isinstance(courses, list):
            courses = ", ".join(str(c) for c in courses[:5])
        bits.append(f"Ya cursó: {courses}")

    if not bits:
        return ""

    return "\n\n## PERFIL DEL USUARIO LOGUEADO\n" + "\n".join(f"- {b}" for b in bits)
