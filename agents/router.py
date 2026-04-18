"""
Supervisor multi-agente — LangGraph StateGraph.

Arquitectura:
  entrada → clasificador_intent → [ventas | cobranzas | post_venta | handoff_humano]
                ↑______________________________________|

El clasificador decide con un LLM liviano (gpt-4o-mini) qué agente ejecutar.
Cada agente es un subgrafo compilado que retorna al supervisor.
Si se detecta handoff_requested, el supervisor termina y el caller realiza el handoff.
"""
import re
from typing import Literal
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict, Annotated
from agents.sales.agent import build_sales_agent
from agents.collections.agent import build_collections_agent
from agents.post_sales.agent import build_post_sales_agent
from agents.closer.agent import build_closer_agent
from config.settings import get_settings
from config.constants import AgentType, HANDOFF_KEYWORDS
import structlog

logger = structlog.get_logger(__name__)

_ROUTER_PROMPT_FALLBACK = (
    "Clasificá la intención: ventas, cobranzas, post_venta o humano. "
    "Respondé solo la palabra."
)

# Prompt cacheado al inicio — mismo patrón que los demás agentes.
# Los cambios se aplican al reiniciar el servidor (no hot-reload).
try:
    from agents.routing.router_prompt import ROUTER_SYSTEM_PROMPT as _CACHED_ROUTER_PROMPT
except Exception:
    _CACHED_ROUTER_PROMPT = None


def _load_router_prompt() -> str:
    """Retorna el prompt del router cacheado al inicio del proceso."""
    return _CACHED_ROUTER_PROMPT or _ROUTER_PROMPT_FALLBACK


def _clean_handoff_tags(content: str) -> tuple[str, bool, str]:
    """Extract and clean HANDOFF_REQUIRED tags from agent response.
    Returns: (cleaned_content, has_handoff, motivo)
    """
    has_handoff = "HANDOFF_REQUIRED" in content
    motivo = ""
    if has_handoff:
        match = re.search(r"HANDOFF_REQUIRED\s*:\s*([^\n\r]+)", content)
        if match:
            motivo = match.group(1).strip()
        content = re.sub(r"HANDOFF_REQUIRED(\s*:\s*[^\n\r]*)?", "", content).strip()
    return content, has_handoff, motivo


class SupervisorState(TypedDict):
    messages: Annotated[list, add_messages]
    current_agent: str
    country: str
    channel: str
    conversation_id: str
    phone: str
    email: str          # email del user_profile (widget logueado)
    user_name: str      # nombre del user_profile
    page_slug: str      # slug del curso que el usuario está viendo (widget)
    has_debt: bool      # true si ficha cobranzas cacheada indica deuda vencida
    is_student: bool    # true si tiene cursadas en Zoho Contacts
    handoff_requested: bool
    handoff_reason: str
    link_rebill_enviado: bool
    verificar_pago: bool
    forced_agent: str   # si está seteado, saltea el clasificador LLM


def detect_handoff_keywords(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in HANDOFF_KEYWORDS)


async def classify_intent(state: SupervisorState) -> dict:
    """Nodo clasificador — decide qué agente usar."""

    # Si el caller forzó un agente (ej: widget con botones), saltear LLM
    if state.get("forced_agent"):
        forced = state["forced_agent"]
        logger.info("intent_forced", agent=forced)
        return {
            "current_agent": forced,
            "handoff_requested": forced == AgentType.HUMAN.value,
            "handoff_reason": "Derivación forzada por flujo del widget" if forced == AgentType.HUMAN.value else "",
        }

    messages = state["messages"]
    last_user_msg = ""
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            last_user_msg = m.content
            break

    # Detección rápida de handoff por keywords
    if detect_handoff_keywords(last_user_msg):
        logger.info("handoff_keyword_detected")
        return {"current_agent": AgentType.HUMAN.value, "handoff_requested": True, "handoff_reason": "Usuario solicitó hablar con un asesor humano"}

    # LLM classifier con modelo económico
    settings = get_settings()
    classifier = ChatOpenAI(
        model="gpt-4o-mini",
        api_key=settings.openai_api_key,
        temperature=0,
        max_tokens=10,
    )

    # Cargar prompt dinámicamente (cambios del panel admin se aplican sin restart)
    router_system = _load_router_prompt()

    # Últimos 6 mensajes + indicar el agente actual para continuidad
    recent = messages[-8:] if len(messages) > 8 else messages
    current = state.get("current_agent", "")
    agent_hint = ""
    if current and current != AgentType.HUMAN.value:
        agent_label = {"sales": "ventas", "collections": "cobranzas", "post_sales": "post_venta"}.get(current, "")
        if agent_label:
            agent_hint = f"\n\nContexto: la conversación viene siendo atendida por el agente de '{agent_label}'. Mantené ese agente salvo cambio claro de tema."

    # Señales de contexto del usuario (widget): página que está viendo,
    # si es alumno con cursadas, si tiene deuda vencida. Permiten al
    # clasificador desambiguar pre-compra vs cobranzas.
    page_slug = state.get("page_slug", "") or ""
    has_debt = bool(state.get("has_debt", False))
    is_student = bool(state.get("is_student", False))
    email = state.get("email", "") or ""
    signals_hint = (
        "\n\n[SEÑALES]\n"
        f"page_slug: {page_slug or '(ninguno)'}\n"
        f"has_debt: {'true' if has_debt else 'false'}\n"
        f"is_student: {'true' if is_student else 'false'}\n"
        f"identificado: {'sí' if email else 'no (anónimo)'}"
    )

    system_with_hint = SystemMessage(content=router_system + agent_hint + signals_hint)
    prompt_messages = [system_with_hint] + recent

    response = await classifier.ainvoke(prompt_messages)
    intent = response.content.strip().lower()

    agent_map = {
        "ventas": AgentType.SALES.value,
        "cobranzas": AgentType.COLLECTIONS.value,
        "post_venta": AgentType.POST_SALES.value,
        "humano": AgentType.HUMAN.value,
    }
    agent = agent_map.get(intent, AgentType.SALES.value)

    # Closer override: si el lead tiene retargeting activo y la intención es ventas,
    # derivar al closer que está especializado en cerrar
    if agent == AgentType.SALES.value:
        phone = state.get("phone", "")
        if phone:
            try:
                from memory.conversation_store import get_conversation_store
                import json
                store = await get_conversation_store()
                retarget_data = await store._redis.get(f"retarget:{phone}")
                if retarget_data:
                    agent = AgentType.CLOSER.value
                    logger.info("closer_override", phone=phone, reason="retargeting_active")
            except Exception as e:
                logger.warning("retarget_check_failed", phone=phone, error=str(e))

    logger.info("intent_classified", intent=intent, agent=agent)

    # Loguear evento de intent para el inbox
    conversation_id = state.get("conversation_id", "") or state.get("phone", "")
    if conversation_id:
        try:
            from utils.conv_events import log_intent
            await log_intent(
                conversation_id, intent, agent,
                last_user_msg[:80] if last_user_msg else "",
            )
        except Exception:
            pass

    handoff = agent == AgentType.HUMAN.value

    # Persistir queue + needs_human en conversation_meta para que el inbox
    # del back-office los filtre. La queue NO cambia cuando el bot decide
    # "humano" — sólo se levanta el flag needs_human (sigue en su cola
    # original de ventas/cobranzas/post-venta).
    if conversation_id:
        try:
            from memory import conversation_meta as cm
            queue_map = {
                AgentType.SALES.value:       "sales",
                AgentType.CLOSER.value:      "sales",
                AgentType.COLLECTIONS.value: "billing",
                AgentType.POST_SALES.value:  "post-sales",
            }
            queue = queue_map.get(agent)
            if queue:
                await cm.set_queue(conversation_id, queue)
            if handoff:
                await cm.set_needs_human(conversation_id, True)
        except Exception as e:
            logger.warning("queue_persist_failed", error=str(e))

    return {
        "current_agent": agent,
        "handoff_requested": handoff,
        "handoff_reason": "Usuario requiere atención humana" if handoff else "",
    }


def route_after_classify(state: SupervisorState) -> Literal["ventas", "cobranzas", "post_venta", "closer", "__end__"]:
    agent = state.get("current_agent", AgentType.SALES.value)
    if state.get("handoff_requested"):
        return END
    mapping = {
        AgentType.SALES.value: "ventas",
        AgentType.COLLECTIONS.value: "cobranzas",
        AgentType.POST_SALES.value: "post_venta",
        AgentType.CLOSER.value: "closer",
    }
    return mapping.get(agent, "ventas")


async def run_sales_node(state: SupervisorState) -> dict:
    country = state.get("country", "AR")
    channel = state.get("channel", "whatsapp")
    page_slug = state.get("page_slug", "") or ""
    email = state.get("email", "") or ""

    # Cargar perfil del usuario (cacheado por widget.py durante _build_user_context)
    user_profile: dict | None = None
    if email:
        try:
            from memory.conversation_store import get_conversation_store
            import json as _json
            store = await get_conversation_store()
            raw = await store._redis.get(f"ventas_profile:{email}")
            if raw:
                user_profile = _json.loads(raw)
        except Exception as e:
            logger.debug("sales_profile_cache_miss", error=str(e))

    agent = await build_sales_agent(
        country=country,
        channel=channel,
        page_slug=page_slug,
        user_profile=user_profile,
    )
    result = await agent.ainvoke({"messages": state["messages"]})

    # Detectar si el agente de ventas solicitó handoff en su respuesta
    last_ai = result["messages"][-1] if result["messages"] else None
    handoff = False
    reason = ""
    if last_ai:
        cleaned, handoff, reason = _clean_handoff_tags(str(last_ai.content))
        last_ai.content = cleaned

    return {"messages": result["messages"], "handoff_requested": handoff, "handoff_reason": reason}


async def run_collections_node(state: SupervisorState) -> dict:
    # Intentar cargar ficha del alumno desde Redis. Orden de búsqueda:
    #   1. datos_deudor:{phone}  (legacy WhatsApp)
    #   2. datos_deudor:{email}  (widget logueado — cacheado en widget.py)
    # Si hay email pero no hay ficha, construimos una ficha mínima para
    # que el prompt no caiga en la rama "pedir email al alumno".
    ficha = None
    phone = state.get("phone", "") or ""
    email = state.get("email", "") or ""
    user_name = state.get("user_name", "") or ""

    store = None
    if phone or email:
        try:
            from memory.conversation_store import get_conversation_store
            store = await get_conversation_store()
        except Exception:
            store = None

    import json
    if phone and store is not None:
        try:
            cached = await store._redis.get(f"datos_deudor:{phone}")
            if cached:
                ficha = json.loads(cached)
        except Exception:
            pass

    if ficha is None and email and store is not None:
        try:
            cached = await store._redis.get(f"datos_deudor:{email}")
            if cached:
                data = json.loads(cached)
                # Solo usar si es una ficha real (tiene cobranzaId).
                # Si el stub vacío {} vino del miss de Zoho, lo ignoramos
                # para que la heurística de "ficha mínima" aplique abajo.
                if data and data.get("cobranzaId"):
                    ficha = data
        except Exception:
            pass

    # Ficha mínima: tenemos identificado al usuario (email) aunque no haya
    # datos financieros → el prompt sabe que NO debe pedir el email otra vez.
    if ficha is None and email:
        ficha = {
            "email": email,
            "alumno": user_name or "Alumno",
        }

    agent = build_collections_agent(ficha=ficha)
    result = await agent.ainvoke({"messages": state["messages"]})

    last_ai = result["messages"][-1] if result["messages"] else None
    handoff = False
    reason = ""
    link_rebill_enviado = False
    verificar_pago = False

    if last_ai:
        content = str(last_ai.content)
        link_rebill_enviado = "[LINK_REBILL_ENVIADO]" in content
        verificar_pago = "[VERIFICAR_PAGO]" in content

        # Clean internal tags
        content = content.replace("[LINK_REBILL_ENVIADO]", "")
        content = content.replace("[VERIFICAR_PAGO]", "")
        cleaned, handoff, reason = _clean_handoff_tags(content)
        if handoff and not reason:
            reason = "cobranzas"
        last_ai.content = cleaned

    return {
        "messages": result["messages"],
        "handoff_requested": handoff,
        "handoff_reason": reason,
        "link_rebill_enviado": link_rebill_enviado,
        "verificar_pago": verificar_pago,
    }


async def run_post_sales_node(state: SupervisorState) -> dict:
    country = state.get("country", "AR")
    agent = build_post_sales_agent(country=country)
    result = await agent.ainvoke({"messages": state["messages"]})

    last_ai = result["messages"][-1] if result["messages"] else None
    handoff = False
    reason = ""
    if last_ai:
        cleaned, handoff, reason = _clean_handoff_tags(str(last_ai.content))
        if handoff and not reason:
            reason = "post_venta"
        last_ai.content = cleaned

    return {"messages": result["messages"], "handoff_requested": handoff, "handoff_reason": reason}


async def run_closer_node(state: SupervisorState) -> dict:
    """Nodo del Sales Closer — agente especializado en cerrar ventas con leads retargeteados."""
    country = state.get("country", "AR")
    channel = state.get("channel", "whatsapp")
    phone = state.get("phone", "")

    # Build lead context from Redis/CRM data
    lead_context = ""
    if phone:
        try:
            from memory.conversation_store import get_conversation_store
            import json
            store = await get_conversation_store()

            context_lines = []

            # Retargeting state
            retarget = await store._redis.get(f"retarget:{phone}")
            if retarget:
                rd = json.loads(retarget)
                context_lines.append(f"Día de seguimiento: {rd.get('day', '?')}")
                if rd.get("last_template"):
                    context_lines.append(f"Último template enviado: {rd['last_template']}")

            # Lead label
            label = await store._redis.get(f"conv_label:{phone}")
            if label:
                context_lines.append(f"Clasificación: {label}")

            # Zoho cache
            zoho = await store._redis.get(f"zoho_cache:{phone}")
            if zoho:
                zd = json.loads(zoho)
                if zd.get("found") and zd.get("record"):
                    r = zd["record"]
                    if r.get("curso_de_interes"):
                        context_lines.append(f"Curso de interés: {r['curso_de_interes']}")

            lead_context = "\n".join(context_lines)
        except Exception as e:
            logger.debug("closer_context_build_error", error=str(e))

    agent = build_closer_agent(
        country=country,
        channel=channel,
        lead_context=lead_context,
    )
    result = await agent.ainvoke({"messages": state["messages"]})

    last_ai = result["messages"][-1] if result["messages"] else None
    handoff = False
    reason = ""
    if last_ai:
        cleaned, handoff, reason = _clean_handoff_tags(str(last_ai.content))
        if handoff and not reason:
            reason = "closer"
        last_ai.content = cleaned

    return {"messages": result["messages"], "handoff_requested": handoff, "handoff_reason": reason}


def build_supervisor() -> StateGraph:
    """Construye y compila el grafo supervisor multi-agente."""
    graph = StateGraph(SupervisorState)

    graph.add_node("clasificador", classify_intent)
    graph.add_node("ventas", run_sales_node)
    graph.add_node("cobranzas", run_collections_node)
    graph.add_node("post_venta", run_post_sales_node)
    graph.add_node("closer", run_closer_node)

    graph.add_edge(START, "clasificador")
    graph.add_conditional_edges(
        "clasificador",
        route_after_classify,
        {
            "ventas": "ventas",
            "cobranzas": "cobranzas",
            "post_venta": "post_venta",
            "closer": "closer",
            END: END,
        },
    )
    graph.add_edge("ventas", END)
    graph.add_edge("cobranzas", END)
    graph.add_edge("post_venta", END)
    graph.add_edge("closer", END)

    return graph.compile()


_cached_supervisor = None


def _get_supervisor():
    global _cached_supervisor
    if _cached_supervisor is None:
        _cached_supervisor = build_supervisor()
    return _cached_supervisor


async def route_message(
    user_message: str,
    history: list[dict],
    country: str = "AR",
    channel: str = "whatsapp",
    conversation_id: str = "",
    phone: str = "",
    email: str = "",
    user_name: str = "",
    page_slug: str = "",
    has_debt: bool = False,
    is_student: bool = False,
    skip_flow: bool = False,
    forced_agent: str | None = None,
) -> dict:
    """
    Punto de entrada principal para procesar un mensaje del usuario.

    Args:
        user_message: Texto del último mensaje del usuario
        history: Historial [{role: user/assistant, content: ...}]
        country: Código de país
        channel: whatsapp | widget
        conversation_id: ID de conversación para logs

    Returns:
        {response: str, agent_used: str, handoff_requested: bool, handoff_reason: str}
    """
    # Bind conversation_id to structlog context for end-to-end tracing
    if conversation_id:
        structlog.contextvars.bind_contextvars(conversation_id=str(conversation_id))

    # El flow-builder Drawflow y `agents/flow_runner.py` se eliminaron: el
    # runner nunca se invocaba en producción (0 sesiones con `flow_state:*` en
    # Redis) y el widget real usa `agents.routing.widget_flow` (máquina de
    # estados hardcoded). El parámetro `skip_flow` quedó como no-op por
    # compatibilidad con los callers existentes.
    _param_forced_agent = forced_agent  # puede ser None si no viene del widget
    _flow_forced_agent: str | None = None

    supervisor = _get_supervisor()

    # Construir messages para LangGraph
    lc_messages = []
    for m in history:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            lc_messages.append(SystemMessage(content=content))
        elif role == "user":
            lc_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            lc_messages.append(AIMessage(content=content))

    lc_messages.append(HumanMessage(content=user_message))

    # Resolver el agente efectivo: parámetro tiene prioridad sobre el flow_runner
    _agent_name_map = {
        "ventas": AgentType.SALES.value,
        "cobranzas": AgentType.COLLECTIONS.value,
        "post_venta": AgentType.POST_SALES.value,
        "closer": AgentType.CLOSER.value,
        "sales": AgentType.SALES.value,
        "collections": AgentType.COLLECTIONS.value,
        "post_sales": AgentType.POST_SALES.value,
    }
    _effective_forced = _param_forced_agent or _flow_forced_agent
    _initial_agent = _agent_name_map.get(_effective_forced, AgentType.SALES.value) if _effective_forced else AgentType.SALES.value

    initial_state: SupervisorState = {
        "messages": lc_messages,
        "current_agent": _initial_agent,
        "country": country,
        "channel": channel,
        "conversation_id": conversation_id,
        "phone": phone,
        "email": email,
        "user_name": user_name,
        "page_slug": page_slug,
        "has_debt": has_debt,
        "is_student": is_student,
        "handoff_requested": False,
        "handoff_reason": "",
        "link_rebill_enviado": False,
        "verificar_pago": False,
        "forced_agent": _effective_forced or "",
    }

    from utils.circuit_breaker import openai_breaker
    if not openai_breaker.can_execute():
        logger.warning("openai_circuit_open")
        return {
            "response": "Estamos experimentando dificultades técnicas. Por favor intentá de nuevo en unos minutos.",
            "agent_used": "system",
            "handoff_requested": False,
            "handoff_reason": "",
            "link_rebill_enviado": False,
            "verificar_pago": False,
            "phone": phone,
        }

    try:
        final_state = await supervisor.ainvoke(initial_state)
        openai_breaker.record_success()
    except Exception as e:
        openai_breaker.record_failure()
        logger.error("openai_invoke_error", error=str(e))
        return {
            "response": "Tuve un problema procesando tu mensaje. Intentá de nuevo en un momento.",
            "agent_used": "system",
            "handoff_requested": False,
            "handoff_reason": "",
            "link_rebill_enviado": False,
            "verificar_pago": False,
            "phone": phone,
        }

    # Extraer respuesta del último mensaje del asistente
    response_text = ""
    for m in reversed(final_state["messages"]):
        if isinstance(m, AIMessage) and m.content:
            response_text = m.content
            break

    # Si es handoff directo (sin pasar por agente), generar mensaje para el usuario
    if not response_text and final_state.get("handoff_requested"):
        response_text = (
            "Te voy a conectar con un asesor para que pueda ayudarte personalmente. "
            "Un momento, por favor 🙏"
        )

    # ── Cleanup centralizado de tags internos ────────────────────────────────
    handoff_reason = final_state.get("handoff_reason", "") or ""
    response_text, _has_handoff, _motivo = _clean_handoff_tags(response_text)
    if _motivo:
        handoff_reason = _motivo
    # Borrar tags internas que nunca deben llegar al usuario
    for _tag in ("[LINK_REBILL_ENVIADO]", "[VERIFICAR_PAGO]"):
        response_text = response_text.replace(_tag, "")
    # Compactar saltos múltiples
    response_text = re.sub(r"\n{3,}", "\n\n", response_text).strip()

    agent_used = final_state.get("current_agent", AgentType.SALES.value)

    # Save queue for inbox filtering
    try:
        queue_key = f"conv_queue:{conversation_id or 'unknown'}"
        _country = country or "XX"
        _agent_queue_map = {
            AgentType.SALES.value: "ventas",
            AgentType.COLLECTIONS.value: "cobranzas",
            AgentType.POST_SALES.value: "post_venta",
            AgentType.CLOSER.value: "ventas",  # closer feeds into ventas queue
        }
        _queue_prefix = _agent_queue_map.get(agent_used, "ventas")
        queue_val = f"{_queue_prefix}_{_country}"  # e.g., "ventas_AR"
        from memory.conversation_store import get_conversation_store as _gcs
        _store = await _gcs()
        await _store._redis.set(queue_key, queue_val, ex=86400*30)
    except Exception as e:
        logger.warning("queue_persist_failed", conversation_id=conversation_id, error=str(e))

    return {
        "response": response_text,
        "agent_used": agent_used,
        "handoff_requested": final_state.get("handoff_requested", False),
        "handoff_reason": handoff_reason,
        "link_rebill_enviado": final_state.get("link_rebill_enviado", False),
        "verificar_pago": final_state.get("verificar_pago", False),
        "phone": phone,
    }
