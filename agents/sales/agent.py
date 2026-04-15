"""
Agente de Ventas — LangGraph ReAct agent con RAG sobre cursos médicos.
Capacidades: buscar cursos, responder dudas, generar links de pago, registrar en Zoho.
"""
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage
from langgraph.prebuilt import create_react_agent
import structlog

from config.settings import get_settings
from agents.sales.tools import (
    search_courses,
    get_course_details,
    get_course_deep,
    create_payment_link,
    create_or_update_lead,
    create_sales_order,
)
from agents.sales.prompts import build_sales_prompt

logger = structlog.get_logger(__name__)

SALES_TOOLS = [
    search_courses,
    get_course_details,
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

    system_prompt = build_sales_prompt(country=country, channel=channel)

    # Inyectar contexto del curso que está viendo el usuario
    if page_slug:
        try:
            from integrations import courses_cache
            course = await courses_cache.get_course(country.lower(), page_slug)
            if course and course.get("brief_md"):
                system_prompt += _format_course_context(course, user_profile)
            else:
                logger.info("sales_agent_no_brief_for_slug", country=country, slug=page_slug)
        except Exception as e:
            logger.warning("sales_agent_brief_load_failed", error=str(e), slug=page_slug)

    # Perfil del usuario (Zoho) — para personalizar el pitch
    if user_profile and not page_slug:
        prof_ctx = _format_user_profile(user_profile)
        if prof_ctx:
            system_prompt += prof_ctx

    agent = create_react_agent(
        model=llm,
        tools=SALES_TOOLS,
        prompt=SystemMessage(content=system_prompt),
    )
    return agent


def _format_course_context(course: dict, user_profile: Optional[dict] = None) -> str:
    """Arma el bloque del curso + instrucción de venta contextualizada."""
    brief = course.get("brief_md") or ""
    slug = course.get("slug") or ""
    title = course.get("title") or ""

    profile_line = ""
    if user_profile:
        prof = user_profile.get("profession") or user_profile.get("Profession") or ""
        spec = user_profile.get("specialty") or user_profile.get("Specialty") or ""
        name = user_profile.get("name") or user_profile.get("Full_Name") or ""
        bits = []
        if name:
            bits.append(f"se llama **{name}**")
        if prof:
            bits.append(f"profesión: **{prof}**")
        if spec:
            bits.append(f"especialidad: **{spec}**")
        if bits:
            profile_line = "- El usuario " + ", ".join(bits) + ".\n"

    return f"""

---

## CURSO QUE EL USUARIO ESTÁ VIENDO AHORA

El usuario entró al chat desde la página del curso **{title}** (slug: `{slug}`).
Asumí que la consulta es sobre ESTE curso salvo que diga lo contrario.

### Brief del curso (úsalo como fuente primaria — los datos son oficiales y actualizados)

{brief}

---

## INSTRUCCIÓN DE VENTA CONTEXTUAL

{profile_line}- **Hablá de este curso por nombre** — ya sabés cuál es, no preguntes "qué curso te interesa".
- **Elegí el perfil objetivo que mejor encaje** con el usuario (ver "Perfiles objetivo — dolor y beneficio"):
  usá el **dolor** del perfil para mostrar empatía y el **beneficio** para construir el pitch.
- **Precio y cuotas**: ya los tenés en el brief — mostralos con seguridad cuando el usuario pregunte.
- Si el usuario quiere profundizar en un módulo, el equipo docente o las certificaciones,
  usá la tool `get_course_deep(slug="{slug}", country="{course.get('country', '').upper()}", section="…")`
  con secciones como `modules`, `teaching_team`, `institutions`, `certificacion_relacionada`.
- **No repitas el brief literal** — conversá, referite a los puntos que apliquen.
- Si el usuario intenta inscribirse, seguí el flujo de inscripción normal (nombre + email → link).
"""


def _format_user_profile(user_profile: dict) -> str:
    """Contexto liviano del usuario cuando NO hay page_slug (navegación genérica)."""
    prof = user_profile.get("profession") or user_profile.get("Profession") or ""
    spec = user_profile.get("specialty") or user_profile.get("Specialty") or ""
    interests = user_profile.get("interests") or user_profile.get("Interests") or ""
    courses = user_profile.get("courses") or user_profile.get("Courses") or []

    bits = []
    if prof:
        bits.append(f"Profesión: {prof}")
    if spec:
        bits.append(f"Especialidad: {spec}")
    if interests:
        bits.append(f"Intereses: {interests}")
    if courses:
        if isinstance(courses, list):
            courses = ", ".join(str(c) for c in courses[:5])
        bits.append(f"Ya cursó: {courses}")

    if not bits:
        return ""

    return "\n\n## PERFIL DEL USUARIO LOGUEADO\n" + "\n".join(f"- {b}" for b in bits)
