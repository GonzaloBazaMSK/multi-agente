"""
Procesador de mensajes del widget web embebible.

Flujo de una sesión:
  1. __widget_init__  → saludo personalizado + botones principales
  2. Selección de menú → routing por botones (sin LLM clasificador)
  3. Mensajes libres  → agentes IA vía supervisor LangGraph
"""
import json as _json
import datetime
import structlog
from typing import Optional

from models.message import Message, MessageRole
from memory.conversation_store import get_conversation_store
from agents.router import route_message
from integrations.notifications import notify_handoff
from config.constants import Channel, ConversationStatus, MAX_HISTORY_MESSAGES
from agents.routing.widget_flow import (
    init_state as wflow_init,
    process_step as wflow_step,
    fmt_buttons,
    MAIN_BUTTONS,
)

logger = structlog.get_logger(__name__)


# ── Enriquecimiento de contexto ───────────────────────────────────────────────

async def _build_user_context(
    user_email: str,
    store,
    session_id: str,
    user_courses: str = "",
    page_slug: str = "",
) -> list[str]:
    """
    Construye la lista de líneas de contexto del usuario a partir de:
    - Perfil Supabase (customers)
    - Cache Zoho cobranzas (si existe)
    - Perfil Zoho Contacts completo (profesión, especialidad, cursadas) — cacheado 24 h
    - Cursos pasados directamente desde el widget
    - Slug de la página actual

    Retorna una lista de strings listos para inyectar en el system prompt.
    """
    lines: list[str] = []

    if page_slug:
        lines.append(
            f"Página actual del usuario: curso «{page_slug}» — "
            "puede estar interesado en este curso específico."
        )

    if not user_email:
        return lines

    # 1. Perfil Supabase
    try:
        from integrations.supabase_client import get_customer_profile
        profile = await get_customer_profile(user_email)
        if profile:
            if profile.get("name"):
                lines.append(f"Nombre del cliente: {profile['name']}")
            if profile.get("phone"):
                lines.append(f"Teléfono: {profile['phone']}")
            courses = profile.get("courses") or []
            if courses:
                lines.append(f"Cursos inscriptos: {', '.join(courses)}")
            if profile.get("profession"):
                lines.append(f"Profesión: {profile['profession']}")
            if profile.get("specialty"):
                lines.append(f"Especialidad: {profile['specialty']}")
            if profile.get("interests"):
                lines.append(f"Intereses: {profile['interests']}")
    except Exception as e:
        logger.debug("supabase_profile_failed", error=str(e))

    # Cursos pasados directamente por el widget (fast path)
    if user_courses and not any("Cursos inscriptos" in l for l in lines):
        lines.append(f"Cursos inscriptos: {user_courses}")

    # 2. Cache Zoho cobranzas (si fue buscado antes)
    try:
        cached_zoho = await store._redis.get(f"zoho_cache:{session_id}")
        if cached_zoho:
            zoho_data = _json.loads(cached_zoho)
            if zoho_data.get("found") and zoho_data.get("record"):
                r = zoho_data["record"]
                if r.get("curso_de_interes"):
                    lines.append(f"Curso de interés (CRM): {r['curso_de_interes']}")
                if r.get("estado_pago"):
                    lines.append(f"Estado de pago (CRM): {r['estado_pago']}")
    except Exception:
        pass

    # 3. Perfil Zoho Contacts (profesión, especialidad, cursadas) — cacheado por email
    try:
        cursadas_key = f"zoho_cursadas:{user_email}"
        cached_cursadas = await store._redis.get(cursadas_key)

        if cached_cursadas is None:
            from integrations.zoho.contacts import ZohoContacts
            zc = ZohoContacts()
            contact = await zc.search_by_email_with_full_profile(user_email)
            cursadas_list: list[dict] = []
            if contact:
                def _lst(v):
                    if isinstance(v, list):
                        return ", ".join(str(x) for x in v if x and str(x) != "null")
                    return str(v) if v else ""

                profesion = (
                    contact.get("Profesi_n", "")
                    or contact.get("Profesión", "")
                    or contact.get("Profesion", "")
                )
                especialidad = contact.get("Especialidad", "")
                esp_interes = _lst(contact.get("Especialidad_interes"))
                intereses_ad = _lst(contact.get("Intereses_adicionales"))
                contenido = _lst(contact.get("Contenido_Interes"))

                if profesion and not any("Profesión" in l for l in lines):
                    lines.append(f"Profesión: {profesion}")
                if especialidad and not any("Especialidad:" in l for l in lines):
                    lines.append(f"Especialidad: {especialidad}")
                if esp_interes:
                    lines.append(f"Especialidades de interés: {esp_interes}")
                if intereses_ad:
                    lines.append(f"Intereses adicionales: {intereses_ad}")
                if contenido:
                    lines.append(f"Contenido de interés: {contenido}")

                def _curso_name(entry):
                    for fld in ("Curso", "Nombre_de_curso", "Nombre_del_curso"):
                        v = entry.get(fld)
                        if isinstance(v, dict):
                            return v.get("name", "")
                        if isinstance(v, str) and v.strip():
                            return v.strip()
                    return ""

                for item in (contact.get("Formulario_de_cursada") or []):
                    nombre = _curso_name(item)
                    if nombre:
                        cursadas_list.append({
                            "curso": nombre,
                            "finalizo": item.get("Finalizo"),
                            "estado_ov": item.get("Estado_de_OV", ""),
                            "fecha_fin": item.get("Fecha_finalizaci_n") or item.get("Fecha_finalización", ""),
                            "fecha_enrol": item.get("Enrollamiento", ""),
                        })

            await store._redis.setex(cursadas_key, 86400, _json.dumps(cursadas_list))
        else:
            cursadas_list = _json.loads(cached_cursadas)

        if cursadas_list:
            todos = [c["curso"] for c in cursadas_list]
            lines.append(f"Cursos del alumno ({len(todos)} total): {', '.join(todos)}")
            lines.append(f"IMPORTANTE — No recomiendes estos cursos (ya los tiene): {', '.join(todos)}")
    except Exception as e:
        logger.debug("zoho_cursadas_failed", error=str(e))

    return lines


def _build_context_block(lines: list[str]) -> str:
    return (
        "[CONTEXTO DEL CLIENTE IDENTIFICADO]\n"
        + "\n".join(lines)
        + "\n\nUsá estos datos para personalizar la respuesta:\n"
        + "- Saludá al cliente por su nombre si es el primer mensaje.\n"
        + "- Si pregunta por cursos, sugerí cursos NUEVOS basándote en su perfil, "
        + "pero NUNCA recomiendes los que ya tiene (marcados como 'No recomiendes').\n"
        + "- No repitas literalmente estos datos, usálos de forma natural."
    )


async def _broadcast(event: dict) -> None:
    try:
        from api.inbox import broadcast_event
        broadcast_event(event)
    except Exception:
        pass


async def _save_bot_msg(store, conversation, text: str, agent: str = "bot") -> Message:
    msg = Message(
        role=MessageRole.ASSISTANT,
        content=text,
        metadata={"agent": agent},
    )
    await store.append_message(conversation, msg)
    return msg


# ── Handler principal ─────────────────────────────────────────────────────────

async def process_widget_message(
    session_id: str,
    message_text: str,
    country: str = "AR",
    user_name: str = "",
    user_email: str = "",
    user_courses: str = "",
    page_slug: str = "",
) -> dict:
    """
    Procesa un mensaje del widget web.

    Returns:
        {response: str, agent_used: str, handoff_requested: bool, session_id: str}
    """
    store = await get_conversation_store()
    conversation, is_new = await store.get_or_create(
        channel=Channel.WIDGET,
        external_id=session_id,
        country=country,
    )

    # ── Bot desactivado por agente humano ─────────────────────────────────────
    bot_disabled = await store._redis.get(f"bot_disabled:{session_id}")
    if bot_disabled:
        user_msg = Message(role=MessageRole.USER, content=message_text)
        await store.append_message(conversation, user_msg)
        await _broadcast({
            "type": "new_message",
            "session_id": session_id,
            "role": "user",
            "content": message_text,
            "sender_name": user_name or "Usuario",
            "timestamp": datetime.datetime.utcnow().isoformat(),
        })
        return {
            "response": "",
            "agent_used": "humano",
            "handoff_requested": False,
            "session_id": session_id,
            "bot_disabled": True,
        }

    # ── Conversación ya derivada a humano ─────────────────────────────────────
    if conversation.status == ConversationStatus.HANDED_OFF:
        return {
            "response": "Tu consulta fue derivada a un asesor. Te contactaremos a la brevedad.",
            "agent_used": "humano",
            "handoff_requested": True,
            "session_id": session_id,
        }

    # ── Actualizar perfil del usuario ─────────────────────────────────────────
    if user_name and not conversation.user_profile.name:
        conversation.user_profile.name = user_name
    if user_email and not conversation.user_profile.email:
        conversation.user_profile.email = user_email

    # ─────────────────────────────────────────────────────────────────────────
    # INIT: saludo personalizado + botones del menú
    # ─────────────────────────────────────────────────────────────────────────
    if message_text == "__widget_init__":
        # Enriquecer contexto para personalizar el saludo
        ctx_lines = await _build_user_context(
            user_email, store, session_id, user_courses, page_slug
        )
        ctx = "\n".join(ctx_lines) if ctx_lines else ""

        # Generar saludo con IA
        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import SystemMessage as LcSystem, HumanMessage as LcHuman
            from config.settings import get_settings

            system_txt = (
                "Sos el asistente de MSK Latam, plataforma de capacitación médica. "
                "El usuario acaba de abrir el chat. "
                "Generá UN saludo breve (2-3 oraciones), cálido y personalizado. "
                "Reglas ESTRICTAS:\n"
                "- Si sabés el nombre, usá solo el primero.\n"
                "- Podés mencionar su profesión o especialidad si la tenés.\n"
                "- NUNCA menciones nombres de cursos — ni los exactos ni paráfrasis.\n"
                "- NUNCA inventes información que no esté en los datos.\n"
                "- Invitalo a consultar sobre cursos o a explorar el catálogo.\n"
                "- Solo el saludo, sin explicaciones."
            )
            if ctx:
                system_txt += f"\n\nDatos del cliente:\n{ctx}"
            if page_slug:
                system_txt += (
                    f"\n\nEl usuario está viendo la página del curso «{page_slug}». "
                    "Podés mencionarlo como 'veo que estás explorando ese curso'."
                )

            llm = ChatOpenAI(
                model="gpt-4o-mini",
                api_key=get_settings().openai_api_key,
                temperature=0.7,
                max_tokens=120,
            )
            resp = await llm.ainvoke([
                LcSystem(content=system_txt),
                LcHuman(content="Generá el saludo."),
            ])
            greeting = resp.content.strip()
        except Exception as e:
            logger.warning("widget_init_greeting_failed", error=str(e))
            nombre = user_name.split()[0] if user_name else ""
            greeting = (
                f"¡Hola{' ' + nombre if nombre else ''}! 👋 "
                "Soy tu asesor de MSK Latam. "
                "¿En qué te puedo ayudar hoy?"
            )

        # Agregar botones del menú principal
        greeting_with_buttons = fmt_buttons(greeting, MAIN_BUTTONS)

        # Inicializar estado del menú en Redis
        await wflow_init(store._redis, session_id)

        # Guardar y emitir
        bot_msg = await _save_bot_msg(store, conversation, greeting_with_buttons, "bienvenida")
        await _broadcast({
            "type": "new_message",
            "session_id": session_id,
            "role": "assistant",
            "content": greeting_with_buttons,
            "sender_name": "bienvenida",
            "timestamp": bot_msg.timestamp.isoformat(),
        })
        return {
            "response": greeting_with_buttons,
            "agent_used": "bienvenida",
            "handoff_requested": False,
            "session_id": session_id,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # MENÚ: verificar si estamos en la máquina de estados
    # ─────────────────────────────────────────────────────────────────────────
    wflow_result = await wflow_step(
        store._redis, session_id, message_text, user_email
    )

    # Guardar siempre el mensaje del usuario (menú o no)
    user_msg = Message(role=MessageRole.USER, content=message_text)
    await store.append_message(conversation, user_msg)
    await _broadcast({
        "type": "new_message",
        "session_id": session_id,
        "role": "user",
        "content": message_text,
        "sender_name": user_name or "Usuario",
        "timestamp": user_msg.timestamp.isoformat(),
    })

    # ── Respuesta directa del menú (sin agente IA) ────────────────────────────
    if wflow_result is not None and not wflow_result.get("needs_routing"):
        menu_text = wflow_result["response"]
        bot_msg = await _save_bot_msg(store, conversation, menu_text, "menu")
        await _broadcast({
            "type": "new_message",
            "session_id": session_id,
            "role": "assistant",
            "content": menu_text,
            "sender_name": "menu",
            "timestamp": bot_msg.timestamp.isoformat(),
        })
        return {
            "response": menu_text,
            "agent_used": "menu",
            "handoff_requested": False,
            "session_id": session_id,
        }

    # ── Si el menú colectó un email anónimo, actualizarlo ────────────────────
    forced_agent: Optional[str] = None
    if wflow_result and wflow_result.get("needs_routing"):
        forced_agent = wflow_result.get("forced_agent")
        collected_email = wflow_result.get("collected_email")
        if collected_email:
            user_email = collected_email
            conversation.user_profile.email = collected_email
            await store.save(conversation)
            logger.info("widget_email_collected", session=session_id, email=collected_email)

    # ─────────────────────────────────────────────────────────────────────────
    # ENRIQUECIMIENTO de contexto (siempre antes de llamar al agente)
    # ─────────────────────────────────────────────────────────────────────────
    user_context_lines = await _build_user_context(
        user_email, store, session_id, user_courses, page_slug
    )

    logger.info(
        "widget_message_received",
        session_id=session_id,
        country=country,
        is_new=is_new,
        forced_agent=forced_agent,
    )

    # ── Historial para el LLM ─────────────────────────────────────────────────
    history = conversation.get_history_for_llm(MAX_HISTORY_MESSAGES)
    history_without_last = history[:-1] if history else []

    if user_context_lines:
        history_without_last = [
            {"role": "system", "content": _build_context_block(user_context_lines)}
        ] + history_without_last

    # ─────────────────────────────────────────────────────────────────────────
    # AGENTE IA — procesar con el supervisor
    # ─────────────────────────────────────────────────────────────────────────
    result = await route_message(
        user_message=message_text,
        history=history_without_last,
        country=country,
        channel="widget",
        conversation_id=conversation.id,
        phone=conversation.user_profile.phone or "",
        skip_flow=True,         # Drawflow desactivado para widget (usamos widget_flow)
        forced_agent=forced_agent,
    )

    response_text = result["response"]
    handoff = result["handoff_requested"]
    handoff_reason = result["handoff_reason"]
    agent_used = result["agent_used"]

    # ── Handoff notice si viene de selección de menú ──────────────────────────
    if forced_agent and response_text:
        notices = {
            "ventas": "🎓 *Agente de Cursos*\n\n",
            "cobranzas": "💳 *Área de Cobranzas y Pagos*\n\n",
            "post_venta": "👤 *Soporte para Alumnos*\n\n",
        }
        notice = notices.get(forced_agent, "")
        if notice:
            response_text = notice + response_text

    # ── Cache ficha cobranzas para próximas interacciones ─────────────────────
    if agent_used == "cobranzas" and user_email and not handoff:
        try:
            from integrations.zoho.area_cobranzas import ZohoAreaCobranzas
            zoho = ZohoAreaCobranzas()
            ficha = await zoho.search_by_email(user_email)
            if ficha and ficha.get("cobranzaId"):
                await store._redis.setex(
                    f"datos_deudor:{session_id}", 7200, _json.dumps(ficha)
                )
        except Exception:
            pass

    # ── Guardar respuesta del bot ─────────────────────────────────────────────
    bot_msg = await _save_bot_msg(store, conversation, response_text, agent_used)

    await _broadcast({
        "type": "new_message",
        "session_id": session_id,
        "role": "assistant",
        "content": response_text,
        "sender_name": agent_used,
        "timestamp": bot_msg.timestamp.isoformat(),
    })

    # ── Auto-clasificar lead ──────────────────────────────────────────────────
    try:
        from agents.classifier import classify_conversation
        msgs = [{"role": m.role.value, "content": m.content} for m in conversation.messages[-10:]]
        await classify_conversation(msgs, session_id)
    except Exception:
        pass

    # ── Handoff ───────────────────────────────────────────────────────────────
    if handoff:
        await notify_handoff(
            channel="Widget Web",
            external_id=session_id,
            user_name=user_name or session_id,
            reason=handoff_reason,
            agent=agent_used,
        )
        conversation.status = ConversationStatus.HANDED_OFF
        await store.save(conversation)
        try:
            from api.inbox import auto_assign_round_robin
            await auto_assign_round_robin(session_id)
        except Exception:
            pass

    return {
        "response": response_text,
        "agent_used": agent_used,
        "handoff_requested": handoff,
        "session_id": session_id,
    }
