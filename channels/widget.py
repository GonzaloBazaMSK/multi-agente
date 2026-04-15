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
from utils.conv_events import log_event, log_action, log_error

logger = structlog.get_logger(__name__)


# ── Enriquecimiento de contexto ───────────────────────────────────────────────

async def _build_user_context(
    user_email: str,
    store,
    session_id: str,
    user_courses: str = "",
    page_slug: str = "",
    log_events: bool = True,
) -> tuple[list[str], dict]:
    """
    Construye la lista de líneas de contexto del usuario a partir de:
    - Perfil Supabase (customers)
    - Cache Zoho cobranzas (si existe) — si no existe y hay email, la busca y cachea
    - Perfil Zoho Contacts completo (profesión, especialidad, cursadas) — cacheado 24 h
    - Cursos pasados directamente desde el widget
    - Slug de la página actual

    Además:
    - Sincroniza Zoho Contacts → Supabase customers (Zoho pisa como fuente de verdad)
    - Cachea la ficha de cobranzas por email en `datos_deudor:{email}` (TTL 2 h)

    log_events=False para saludos stateless (no ensucia Redis con eventos de
    sesiones que nunca se materializan).

    Retorna (lines, signals):
      lines  = strings listos para el system prompt
      signals = {
        "has_debt": bool,    # ficha cobranzas con saldo vencido > 0
        "is_student": bool,  # tiene cursadas en Zoho Contacts
        "profile_name": str, # nombre resuelto (Supabase o Zoho)
      }
    """
    # Si log_events=False, sustituimos los helpers por no-ops locales
    if log_events:
        _log_event, _log_action, _log_error = log_event, log_action, log_error
    else:
        async def _noop(*_a, **_k): return None
        _log_event = _log_action = _log_error = _noop
    lines: list[str] = []
    signals: dict = {"has_debt": False, "is_student": False, "profile_name": ""}

    if page_slug:
        lines.append(
            f"Página actual del usuario: curso «{page_slug}» — "
            "puede estar interesado en este curso específico."
        )
        await _log_event(session_id, "info", {
            "action": "page_context",
            "detail": f"Usuario en página: {page_slug}",
        })

    if not user_email:
        await _log_event(session_id, "info", {
            "action": "usuario_anonimo",
            "detail": "Sin email — usuario anónimo, sin búsqueda en CRM",
        })
        return lines, signals

    # 1. Perfil Supabase
    sb_profile = None  # conservar para el sync Zoho→Supabase más abajo
    try:
        from integrations.supabase_client import get_customer_profile
        sb_profile = await get_customer_profile(user_email)
        if sb_profile:
            name_found = sb_profile.get("name", "")
            courses_found = sb_profile.get("courses") or []
            profession_found = sb_profile.get("profession", "")
            specialty_found = sb_profile.get("specialty", "")
            if name_found:
                lines.append(f"Nombre del cliente: {name_found}")
                signals["profile_name"] = name_found
            if sb_profile.get("phone"):
                lines.append(f"Teléfono: {sb_profile['phone']}")
            if courses_found:
                lines.append(f"Cursos inscriptos: {', '.join(courses_found)}")
            if profession_found:
                lines.append(f"Profesión: {profession_found}")
            if specialty_found:
                lines.append(f"Especialidad: {specialty_found}")
            if sb_profile.get("interests"):
                lines.append(f"Intereses: {sb_profile['interests']}")
            await _log_event(session_id, "action", {
                "action": "supabase_perfil_encontrado",
                "detail": (
                    f"Nombre: {name_found or '—'} | "
                    f"Profesión: {profession_found or '—'} | "
                    f"Especialidad: {specialty_found or '—'} | "
                    f"Cursos Supabase: {len(courses_found)}"
                ),
            })
        else:
            await _log_event(session_id, "info", {
                "action": "supabase_no_encontrado",
                "detail": f"No hay perfil Supabase para {user_email}",
            })
    except Exception as e:
        logger.debug("supabase_profile_failed", error=str(e))
        await _log_error(session_id, "supabase", str(e)[:150])

    # Cursos pasados directamente por el widget (fast path)
    if user_courses and not any("Cursos inscriptos" in l for l in lines):
        lines.append(f"Cursos inscriptos: {user_courses}")
        await _log_event(session_id, "info", {
            "action": "cursos_desde_widget",
            "detail": f"Cursos recibidos del frontend: {user_courses[:120]}",
        })

    # 2. Ficha de cobranzas Zoho — SIEMPRE que tengamos email.
    #    Cache por email (`datos_deudor:{email}`, TTL 2 h) para que:
    #     - el agente de cobranzas la encuentre sin volver a llamar a Zoho.
    #     - el router pueda decidir has_debt y orientar bien (ventas vs cobranzas).
    try:
        ficha_key = f"datos_deudor:{user_email}"
        cached_ficha = await store._redis.get(ficha_key)
        ficha = None
        if cached_ficha:
            try:
                ficha = _json.loads(cached_ficha)
            except Exception:
                ficha = None

        if ficha is None:
            # Miss → consultar Zoho una vez y cachear. Si no hay registro,
            # guardamos un stub "{}" para evitar N llamadas por turno.
            from integrations.zoho.area_cobranzas import ZohoAreaCobranzas
            zoho_adc = ZohoAreaCobranzas()
            ficha_raw = await zoho_adc.search_by_email(user_email) or {}
            await store._redis.setex(ficha_key, 7200, _json.dumps(ficha_raw))
            ficha = ficha_raw
            await _log_event(session_id, "action", {
                "action": "zoho_cobranzas_buscado",
                "detail": (
                    f"Zoho ADC consultado para {user_email} — "
                    f"encontrado: {'sí' if ficha.get('cobranzaId') else 'no'} | "
                    f"TTL 2 h"
                ),
            })

        if ficha and ficha.get("cobranzaId"):
            saldo = float(ficha.get("saldoPendiente") or 0)
            cuotas_venc = int(ficha.get("cuotasVencidas") or 0)
            has_debt = saldo > 0 or cuotas_venc > 0
            signals["has_debt"] = has_debt
            lines.append(
                f"Estado financiero: cuotas vencidas={cuotas_venc}, "
                f"saldo pendiente={ficha.get('moneda','')} {saldo} "
                f"({'CON deuda' if has_debt else 'AL DÍA'})"
            )
            if not signals["profile_name"] and ficha.get("alumno"):
                signals["profile_name"] = ficha["alumno"]
    except Exception as e:
        logger.debug("zoho_cobranzas_lookup_failed", error=str(e))
        await _log_error(session_id, "zoho_cobranzas", str(e)[:150])

    # 3. Perfil Zoho Contacts (profesión, especialidad, cursadas) — cacheado por email
    zoho_profile_for_sync: dict = {}  # se usa para sincronizar a Supabase al final
    try:
        cursadas_key = f"zoho_cursadas:{user_email}"
        cached_cursadas = await store._redis.get(cursadas_key)

        if cached_cursadas is None:
            await _log_event(session_id, "info", {
                "action": "zoho_contacts_buscando",
                "detail": f"Buscando perfil Zoho Contacts para {user_email}…",
            })
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

                # Guardar datos Zoho para sincronizar a Supabase
                if profesion:
                    zoho_profile_for_sync["profession"] = profesion
                if especialidad:
                    zoho_profile_for_sync["specialty"] = especialidad
                combined_interests = ", ".join(
                    [x for x in (esp_interes, intereses_ad, contenido) if x]
                )
                if combined_interests:
                    zoho_profile_for_sync["interests"] = combined_interests
                # nombre completo (First + Last) si lo tenemos
                full_first = contact.get("First_Name", "")
                full_last = contact.get("Last_Name", "")
                full_name = (f"{full_first} {full_last}").strip()
                if full_name and not signals.get("profile_name"):
                    signals["profile_name"] = full_name
                if full_name:
                    zoho_profile_for_sync["name"] = full_name

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

                await _log_event(session_id, "action", {
                    "action": "zoho_contacts_encontrado",
                    "detail": (
                        f"Profesión: {profesion or '—'} | "
                        f"Especialidad: {especialidad or '—'} | "
                        f"Especialidades interés: {esp_interes or '—'} | "
                        f"Cursadas encontradas: {len(cursadas_list)} | "
                        f"Guardado en Redis (TTL 24h)"
                    ),
                })
            else:
                await _log_event(session_id, "info", {
                    "action": "zoho_contacts_no_encontrado",
                    "detail": f"No hay contacto Zoho para {user_email} — guardando lista vacía en cache",
                })

            await store._redis.setex(cursadas_key, 86400, _json.dumps(cursadas_list))
        else:
            cursadas_list = _json.loads(cached_cursadas)
            await _log_event(session_id, "info", {
                "action": "zoho_contacts_desde_cache",
                "detail": (
                    f"Cache Redis activo para {user_email} — "
                    f"{len(cursadas_list)} cursada(s) en cache (TTL 24h)"
                ),
            })

        if cursadas_list:
            todos = [c["curso"] for c in cursadas_list]
            signals["is_student"] = True
            zoho_profile_for_sync["courses"] = todos
            lines.append(f"Cursos del alumno ({len(todos)} total): {', '.join(todos)}")
            lines.append(f"IMPORTANTE — No recomiendes estos cursos (ya los tiene): {', '.join(todos)}")
    except Exception as e:
        logger.debug("zoho_cursadas_failed", error=str(e))
        await _log_error(session_id, "zoho_contacts", str(e)[:150])

    # 4. Sincronizar Zoho → Supabase (Zoho pisa, es fuente de verdad)
    #    Solo si hay datos que falten o difieran en Supabase. Best-effort,
    #    no bloquea el flujo del agente.
    if zoho_profile_for_sync and log_events:
        try:
            from integrations.supabase_client import (
                create_customer_profile,
                update_customer_profile,
            )

            if sb_profile and sb_profile.get("id"):
                # Armar el diff — solo campos que cambian o están vacíos en Supabase
                updates = {}
                for k, v in zoho_profile_for_sync.items():
                    if not v:
                        continue
                    if sb_profile.get(k) != v:
                        updates[k] = v
                if updates:
                    await update_customer_profile(sb_profile["id"], updates)
                    await _log_event(session_id, "action", {
                        "action": "supabase_sincronizado_zoho",
                        "detail": (
                            f"Supabase actualizado desde Zoho — "
                            f"campos: {', '.join(updates.keys())}"
                        ),
                    })
            else:
                # No existe el customer aún → crearlo con los datos de Zoho
                name_sync = zoho_profile_for_sync.get("name") or signals.get("profile_name") or ""
                courses_sync = zoho_profile_for_sync.get("courses") or []
                created = await create_customer_profile(
                    email=user_email,
                    name=name_sync or "Sin nombre",
                    courses=courses_sync,
                )
                # create_customer_profile no acepta profession/specialty/interests,
                # así que hacemos un patch adicional si hubo datos extra
                extras = {
                    k: v for k, v in zoho_profile_for_sync.items()
                    if k in ("profession", "specialty", "interests") and v
                }
                if created and created.get("id") and extras:
                    await update_customer_profile(created["id"], extras)
                await _log_event(session_id, "action", {
                    "action": "supabase_creado_desde_zoho",
                    "detail": (
                        f"Customer creado en Supabase desde datos Zoho — "
                        f"{user_email} | campos extra: {', '.join(extras.keys()) or '—'}"
                    ),
                })
        except Exception as e:
            logger.debug("zoho_to_supabase_sync_failed", error=str(e))
            await _log_error(session_id, "zoho_supabase_sync", str(e)[:150])

    return lines, signals


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


# ── Saludo stateless (no crea conversación) ──────────────────────────────────

def _load_greeting_prompt() -> str:
    try:
        from pathlib import Path
        import importlib.util
        path = Path(__file__).parent.parent / "agents" / "routing" / "greeting_prompt.py"
        spec = importlib.util.spec_from_file_location("greeting_prompt_dyn", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.GREETING_SYSTEM_PROMPT
    except Exception:
        return (
            "Sos el asistente de MSK Latam. "
            "Generá un saludo breve y personalizado, sin mencionar cursos."
        )


async def generate_greeting_stateless(
    user_name: str = "",
    user_email: str = "",
    user_courses: str = "",
    page_slug: str = "",
) -> dict:
    """
    Genera el saludo personalizado SIN crear conversación ni loggear eventos.
    Usado al cargar la página para mostrar el saludo en el UI del widget
    antes de que el usuario interactúe. Solo cuando el usuario envíe su
    primer mensaje real se materializa la conversación.

    Returns:
        {greeting: str, buttons: list[str], context_lines: int}
    """
    store = await get_conversation_store()
    # Session id efímero solo como etiqueta técnica; log_events=False hace
    # que no se escriban eventos a Redis (evita conv_events fantasmas por
    # cada visita anónima a la página).
    ephemeral_sid = "greeting-ephemeral"

    ctx_lines, _signals = await _build_user_context(
        user_email, store, ephemeral_sid, user_courses, page_slug,
        log_events=False,
    )
    ctx = "\n".join(ctx_lines) if ctx_lines else ""

    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import SystemMessage as LcSystem, HumanMessage as LcHuman
        from config.settings import get_settings

        system_txt = _load_greeting_prompt()
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
        logger.warning("greeting_stateless_failed", error=str(e))
        nombre = user_name.split()[0] if user_name else ""
        greeting = (
            f"¡Hola{' ' + nombre if nombre else ''}! 😊 "
            "Soy tu asistente virtual de MSK. "
            "Estoy aquí para guiarte y brindarte la información que necesites."
        )

    greeting_with_buttons = fmt_buttons(greeting, MAIN_BUTTONS)
    return {
        "greeting": greeting_with_buttons,
        "buttons": list(MAIN_BUTTONS),
        "context_lines": len(ctx_lines),
        "is_personalized": bool(user_email),
    }


# ── Handler principal ─────────────────────────────────────────────────────────

async def process_widget_message(
    session_id: str,
    message_text: str,
    country: str = "AR",
    user_name: str = "",
    user_email: str = "",
    user_courses: str = "",
    page_slug: str = "",
    initial_greeting: str = "",
) -> dict:
    """
    Procesa un mensaje del widget web.

    initial_greeting: si la conversación es nueva y se pasa, se persiste
    como primer bot msg antes del user msg. El front lo envía solo en el
    primer mensaje real del usuario (así el saludo stateless queda en
    histórico una vez que la conversación se materializa).

    Returns:
        {response: str, agent_used: str, handoff_requested: bool, session_id: str}
    """
    store = await get_conversation_store()
    conversation, is_new = await store.get_or_create(
        channel=Channel.WIDGET,
        external_id=session_id,
        country=country,
    )

    # Si acabamos de crear la conv y el front envió el saludo que mostró,
    # lo persistimos como primer bot msg para que quede en el historial
    # y en el contexto del agente.
    if is_new and initial_greeting and message_text != "__widget_init__":
        try:
            await _save_bot_msg(store, conversation, initial_greeting, "bienvenida")
            # Inicializar estado del menú en Redis para que los botones del saludo funcionen
            await wflow_init(store._redis, session_id)
            await log_event(session_id, "action", {
                "action": "saludo_persistido",
                "detail": "Saludo stateless persistido como primer mensaje bot (conv materializada)",
            })
        except Exception as e:
            logger.warning("persist_initial_greeting_failed", error=str(e))

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
    # B4: anónimo que se loguea mid-conversación → pegamos el email al
    # user_profile existente y lo loggeamos. El próximo turno el agente
    # reconstruye contexto (Supabase + Zoho) y ya personaliza respuestas.
    if user_email and not conversation.user_profile.email:
        conversation.user_profile.email = user_email
        if user_name:
            conversation.user_profile.name = user_name
        await log_event(session_id, "action", {
            "action": "email_capturado",
            "detail": (
                f"Usuario anónimo se identificó: {user_email}"
                + (f" ({user_name})" if user_name else "")
                + " — el próximo turno reconstruye contexto Supabase/Zoho."
            ),
        })
    elif user_name and not conversation.user_profile.name:
        conversation.user_profile.name = user_name

    # ─────────────────────────────────────────────────────────────────────────
    # INIT: saludo personalizado + botones del menú
    # ─────────────────────────────────────────────────────────────────────────
    if message_text == "__widget_init__":
        await log_event(session_id, "info" if user_email else "error", {
            "action": "widget_init",
            "detail": (
                f"Email frontend: {user_email or '❌ NO RECIBIDO (anónimo)'} | "
                f"Nombre: {user_name or '—'} | "
                f"Cursos: {user_courses[:50] if user_courses else '—'} | "
                f"Página: {page_slug or '—'} | "
                f"Sesión nueva: {is_new}"
            ),
        })

        # Enriquecer contexto para personalizar el saludo
        ctx_lines, _signals = await _build_user_context(
            user_email, store, session_id, user_courses, page_slug
        )
        ctx = "\n".join(ctx_lines) if ctx_lines else ""

        # Generar saludo con IA
        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import SystemMessage as LcSystem, HumanMessage as LcHuman
            from config.settings import get_settings

            # Cargar prompt dinámicamente (cambios del panel admin aplicados sin restart)
            def _load_greeting_prompt() -> str:
                try:
                    from pathlib import Path
                    import importlib.util
                    path = Path(__file__).parent.parent / "agents" / "routing" / "greeting_prompt.py"
                    spec = importlib.util.spec_from_file_location("greeting_prompt_dyn", path)
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    return mod.GREETING_SYSTEM_PROMPT
                except Exception:
                    return (
                        "Sos el asistente de MSK Latam. "
                        "Generá un saludo breve y personalizado, sin mencionar cursos."
                    )

            system_txt = _load_greeting_prompt()
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
                f"¡Hola{' ' + nombre if nombre else ''}! 😊 "
                "Soy tu asistente virtual de MSK. "
                "Estoy aquí para guiarte y brindarte la información que necesites."
            )

        # Agregar botones del menú principal
        greeting_with_buttons = fmt_buttons(greeting, MAIN_BUTTONS)

        await log_event(session_id, "action", {
            "action": "saludo_generado",
            "detail": (
                f"{'Personalizado (IA)' if not isinstance(greeting, str) or 'asesor' not in greeting.lower() else 'Fallback'} | "
                f"Contexto: {len(ctx_lines)} líneas | "
                f"Botones: {', '.join(MAIN_BUTTONS)}"
            ),
        })

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
        await log_event(session_id, "intent", {
            "action": "menu_respuesta",
            "detail": f"Respuesta de menú: «{menu_text[:80]}»",
            "agent": "menu",
        })
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
            await log_event(session_id, "action", {
                "action": "email_capturado",
                "detail": f"Email ingresado por usuario anónimo: {collected_email} → derivando a {forced_agent}",
            })
        if forced_agent:
            await log_event(session_id, "intent", {
                "action": "agente_forzado_por_menu",
                "detail": f"Botón seleccionado → agente: {forced_agent}",
                "agent": forced_agent,
            })

    # ─────────────────────────────────────────────────────────────────────────
    # ENRIQUECIMIENTO de contexto (siempre antes de llamar al agente)
    # ─────────────────────────────────────────────────────────────────────────
    user_context_lines, user_signals = await _build_user_context(
        user_email, store, session_id, user_courses, page_slug
    )
    if user_context_lines:
        await log_event(session_id, "info", {
            "action": "contexto_usuario_listo",
            "detail": f"{len(user_context_lines)} líneas de contexto inyectadas al agente:\n" +
                      "\n".join(f"  • {l}" for l in user_context_lines[:8]),
        })

    logger.info(
        "widget_message_received",
        session_id=session_id,
        country=country,
        is_new=is_new,
        forced_agent=forced_agent,
    )
    await log_event(session_id, "info", {
        "action": "mensaje_recibido",
        "detail": (
            f"«{message_text[:80]}{'…' if len(message_text) > 80 else ''}» | "
            f"Email: {user_email or '(anónimo)'} | "
            f"Agente forzado: {forced_agent or 'no (clasificará IA)'}"
        ),
    })

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
        email=user_email or "",
        user_name=user_name or user_signals.get("profile_name", "") or "",
        page_slug=page_slug or "",
        has_debt=bool(user_signals.get("has_debt")),
        is_student=bool(user_signals.get("is_student")),
        skip_flow=True,         # Drawflow desactivado para widget (usamos widget_flow)
        forced_agent=forced_agent,
    )

    response_text = result["response"]
    handoff = result["handoff_requested"]
    handoff_reason = result["handoff_reason"]
    agent_used = result["agent_used"]

    await log_event(session_id, "intent", {
        "action": "agente_respondio",
        "detail": (
            f"Agente: {agent_used} | "
            f"Derivación humano: {'sí — ' + handoff_reason[:60] if handoff else 'no'} | "
            f"Respuesta: «{response_text[:100]}{'…' if len(response_text) > 100 else ''}»"
        ),
        "agent": agent_used,
    })

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

    # Nota: la ficha de cobranzas ya se cachea por email en _build_user_context
    # (datos_deudor:{email}, TTL 2 h), por lo que no duplicamos la búsqueda acá.

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
