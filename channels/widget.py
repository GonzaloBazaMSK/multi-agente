"""
Procesador de mensajes del widget web embebible.

Flujo de una sesión:
  1. __widget_init__  → saludo personalizado + botones principales
  2. Selección de menú → routing por botones (sin LLM clasificador)
  3. Mensajes libres  → agentes IA vía supervisor LangGraph
"""

import datetime
import json as _json

import structlog

from agents.router import route_message
from agents.routing.widget_flow import (
    MAIN_BUTTONS,
    fmt_buttons,
)
from agents.routing.widget_flow import (
    init_state as wflow_init,
)
from agents.routing.widget_flow import (
    process_step as wflow_step,
)
from config.constants import MAX_HISTORY_MESSAGES, AgentType, Channel, ConversationStatus
from integrations.notifications import notify_handoff
from memory.conversation_store import get_conversation_store
from models.message import Message, MessageRole
from utils.conv_events import log_action, log_error, log_event

logger = structlog.get_logger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _resolve_course_title(page_slug: str, country: str) -> str:
    """
    Resuelve el título real del curso a partir del slug de la página.
    Lee de Redis/Postgres vía courses_cache — no pega al WP.
    Devuelve "" si no encuentra (el prompt cae al fallback genérico).
    """
    if not page_slug or not country:
        return ""
    try:
        from integrations.courses_cache import get_course

        course = await get_course(country, page_slug)
        if course:
            return course.get("title") or ""
    except Exception as e:
        logger.warning("resolve_course_title_failed", slug=page_slug, country=country, error=str(e))
    return ""


def _has_profile_signal(ctx_lines: list[str]) -> bool:
    """
    True si las líneas de contexto contienen señales reales del perfil del
    usuario (profesión/especialidad/cargo). Si False = usuario anónimo o
    identificado pero sin perfil — en ese caso NO debemos inyectar la lista
    de "perfiles dirigidos del curso" al system prompt, porque el LLM la
    usaba para alucinar ("Como residente de…", "Como médico jefe…") aunque
    no tuviéramos esos datos.
    """
    if not ctx_lines:
        return False
    for line in ctx_lines:
        if (
            line.startswith("Profesión:")
            or line.startswith("Especialidad:")
            or line.startswith("Cargo:")
        ):
            return True
    return False


async def _resolve_course_mini_brief(page_slug: str, country: str) -> dict:
    """
    Devuelve un mini-brief del curso para inyectar en el saludo personalizado:
      { title, short_desc, perfiles: [list of short profile strings] }
    Si no está en catálogo, devuelve {} y el saludo cae al template genérico.
    """
    if not page_slug or not country:
        return {}
    try:
        from integrations.courses_cache import get_course_deep

        course = await get_course_deep(country, page_slug)
        if not course:
            return {}
        title = course.get("title") or ""
        raw = course.get("raw") or course  # get_course_deep devuelve JSONB crudo
        # Descripción corta (excerpt) — campo "description_short" en el schema MSK.
        short_desc = (
            course.get("description_short")
            or (raw.get("description_short") if isinstance(raw, dict) else "")
            or ""
        )
        # Perfiles dirigidos — el gold para conectar profesión × curso.
        perfiles: list[str] = []
        try:
            kb = (raw.get("kb_ai") or {}) if isinstance(raw, dict) else {}
            pdir = kb.get("perfiles_dirigidos") or []
            for p in pdir[:5]:
                if not isinstance(p, dict):
                    continue
                nombre = (p.get("perfil") or p.get("nombre") or "").strip()
                dolor = (p.get("dolor") or p.get("problema") or "").strip()
                gain = (p.get("obtiene") or p.get("beneficio") or p.get("que_obtiene") or "").strip()
                if nombre:
                    trozo = nombre
                    if dolor:
                        trozo += f" — dolor: {dolor[:140]}"
                    if gain:
                        trozo += f" — obtiene: {gain[:140]}"
                    perfiles.append(trozo)
        except Exception:
            pass
        return {"title": title, "short_desc": short_desc[:400], "perfiles": perfiles}
    except Exception as e:
        logger.warning("resolve_course_mini_brief_failed", slug=page_slug, country=country, error=str(e))
        return {}


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

        async def _noop(*_a, **_k):
            return None

        _log_event = _log_action = _log_error = _noop
    lines: list[str] = []
    signals: dict = {"has_debt": False, "is_student": False, "profile_name": ""}

    if page_slug:
        lines.append(
            f"Página actual del usuario: curso «{page_slug}» — "
            "puede estar interesado en este curso específico."
        )
        await _log_event(
            session_id,
            "info",
            {
                "action": "page_context",
                "detail": f"Usuario en página: {page_slug}",
            },
        )

    if not user_email:
        await _log_event(
            session_id,
            "info",
            {
                "action": "usuario_anonimo",
                "detail": "Sin email — usuario anónimo, sin búsqueda en CRM",
            },
        )
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
            await _log_event(
                session_id,
                "action",
                {
                    "action": "supabase_perfil_encontrado",
                    "detail": (
                        f"Nombre: {name_found or '—'} | "
                        f"Profesión: {profession_found or '—'} | "
                        f"Especialidad: {specialty_found or '—'} | "
                        f"Cursos Supabase: {len(courses_found)}"
                    ),
                },
            )
        else:
            await _log_event(
                session_id,
                "info",
                {
                    "action": "supabase_no_encontrado",
                    "detail": f"No hay perfil Supabase para {user_email}",
                },
            )
    except Exception as e:
        logger.debug("supabase_profile_failed", error=str(e))
        await _log_error(session_id, "supabase", str(e)[:150])

    # Cursos pasados directamente por el widget (fast path)
    if user_courses and not any("Cursos inscriptos" in l for l in lines):
        lines.append(f"Cursos inscriptos: {user_courses}")
        await _log_event(
            session_id,
            "info",
            {
                "action": "cursos_desde_widget",
                "detail": f"Cursos recibidos del frontend: {user_courses[:120]}",
            },
        )

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
            await _log_event(
                session_id,
                "action",
                {
                    "action": "zoho_cobranzas_buscado",
                    "detail": (
                        f"Zoho ADC consultado para {user_email} — "
                        f"encontrado: {'sí' if ficha.get('cobranzaId') else 'no'} | "
                        f"TTL 2 h"
                    ),
                },
            )

        if ficha and ficha.get("cobranzaId"):
            saldo = float(ficha.get("saldoPendiente") or 0)
            cuotas_venc = int(ficha.get("cuotasVencidas") or 0)
            has_debt = saldo > 0 or cuotas_venc > 0
            signals["has_debt"] = has_debt
            lines.append(
                f"Estado financiero: cuotas vencidas={cuotas_venc}, "
                f"saldo pendiente={ficha.get('moneda', '')} {saldo} "
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
            await _log_event(
                session_id,
                "info",
                {
                    "action": "zoho_contacts_buscando",
                    "detail": f"Buscando perfil Zoho Contacts para {user_email}…",
                },
            )
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
                # Campos nuevos — para cargo/lugar/colegio (registro técnico +
                # matching de avales AR jurisdiccionales).
                cargo = contact.get("Cargo", "") or ""
                lugar_trabajo = contact.get("Lugar_de_trabajo", "") or ""
                # Typo del API de Zoho — "tabaja" sin "r". Probamos el typo primero
                # y caemos al nombre correcto si algún día lo arreglan.
                area_trabajo = (
                    contact.get("rea_donde_tabaja", "") or contact.get("rea_donde_trabaja", "") or ""
                )
                pertenece_colegio = bool(contact.get("Pertenece_a_un_colegio", False))
                colegio_nombre = _lst(contact.get("Colegio_Sociedad_o_Federaci_n"))

                if profesion and not any("Profesión" in l for l in lines):
                    lines.append(f"Profesión: {profesion}")
                if especialidad and not any("Especialidad:" in l for l in lines):
                    lines.append(f"Especialidad: {especialidad}")
                if cargo:
                    lines.append(f"Cargo: {cargo}")
                if lugar_trabajo:
                    lines.append(f"Lugar de trabajo: {lugar_trabajo}")
                if area_trabajo:
                    lines.append(f"Área donde trabaja: {area_trabajo}")
                if pertenece_colegio and colegio_nombre:
                    lines.append(
                        f"Matrícula activa en colegio/sociedad: {colegio_nombre} "
                        "(aplicable para certificaciones jurisdiccionales AR si el curso las ofrece)"
                    )
                elif pertenece_colegio:
                    lines.append("Pertenece a un colegio/sociedad/federación (nombre no especificado)")
                if esp_interes:
                    lines.append(f"Especialidades de interés: {esp_interes}")
                if intereses_ad:
                    lines.append(f"Intereses adicionales: {intereses_ad}")
                if contenido:
                    lines.append(f"Contenido de interés: {contenido}")

                # Guardar datos Zoho para sincronizar a Supabase + cache ventas
                if profesion:
                    zoho_profile_for_sync["profession"] = profesion
                if especialidad:
                    zoho_profile_for_sync["specialty"] = especialidad
                # Campos extendidos (NO van a Supabase — solo a cache ventas)
                # para que el agente pueda usar Cargo/Lugar/Área/Colegio en el pitch.
                if cargo:
                    zoho_profile_for_sync["cargo"] = cargo
                if lugar_trabajo:
                    zoho_profile_for_sync["lugar_trabajo"] = lugar_trabajo
                if area_trabajo:
                    zoho_profile_for_sync["area_trabajo"] = area_trabajo
                if pertenece_colegio and colegio_nombre:
                    zoho_profile_for_sync["colegio"] = colegio_nombre
                combined_interests = ", ".join([x for x in (esp_interes, intereses_ad, contenido) if x])
                if combined_interests:
                    zoho_profile_for_sync["interests"] = combined_interests
                # nombre completo (First + Last) si lo tenemos
                full_first = contact.get("First_Name", "")
                full_last = contact.get("Last_Name", "")
                full_name = (f"{full_first} {full_last}").strip()
                # Fallback a Full_Name si First/Last vienen vacíos pero
                # Full_Name no (pasa con users cargados por formularios que
                # sólo tienen el campo compuesto).
                if not full_name:
                    full_name = contact.get("Full_Name", "").strip()
                if full_name and not signals.get("profile_name"):
                    signals["profile_name"] = full_name
                    # También inyectar al ctx para que el LLM salude por nombre.
                    # Antes solo se agregaba en el branch de Supabase (línea
                    # ~188), pero si Supabase no tiene el profile y Zoho sí,
                    # el nombre se perdía para el greeting.
                    if not any(l.startswith("Nombre del cliente:") for l in lines):
                        lines.insert(0, f"Nombre del cliente: {full_name}")
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

                for item in contact.get("Formulario_de_cursada") or []:
                    nombre = _curso_name(item)
                    if nombre:
                        cursadas_list.append(
                            {
                                "curso": nombre,
                                "finalizo": item.get("Finalizo"),
                                "estado_ov": item.get("Estado_de_OV", ""),
                                "fecha_fin": item.get("Fecha_finalizaci_n")
                                or item.get("Fecha_finalización", ""),
                                "fecha_enrol": item.get("Enrollamiento", ""),
                            }
                        )

                await _log_event(
                    session_id,
                    "action",
                    {
                        "action": "zoho_contacts_encontrado",
                        "detail": (
                            f"Profesión: {profesion or '—'} | "
                            f"Especialidad: {especialidad or '—'} | "
                            f"Especialidades interés: {esp_interes or '—'} | "
                            f"Cursadas encontradas: {len(cursadas_list)} | "
                            f"Guardado en Redis (TTL 24h)"
                        ),
                    },
                )
            else:
                await _log_event(
                    session_id,
                    "info",
                    {
                        "action": "zoho_contacts_no_encontrado",
                        "detail": f"No hay contacto Zoho para {user_email} — guardando lista vacía en cache",
                    },
                )

            # Guardar cursadas + perfil completo juntos en la misma entrada de
            # cache. Antes solo cacheabamos `cursadas_list`, lo que provocaba
            # que en el HIT (ej. /widget/chat tras /widget/greeting) se perdieran
            # profession/specialty/cargo/lugar/area/colegio — y el agente de
            # ventas recibía un `ventas_profile` sin esos campos.
            cache_payload = {
                "cursadas": cursadas_list,
                "profile": dict(zoho_profile_for_sync),  # snapshot
            }
            # TTL adaptativo: si el profile viene VACÍO (Zoho no encontró el
            # contacto, o falló la request, o el user es muy nuevo), cacheamos
            # solo 5 min para dar chance a un retry pronto. Si hay datos reales
            # cacheamos 24h. Antes cacheábamos TODO a 24h y un "profile vacío"
            # se quedaba pegado por un día entero aunque después aparecieran
            # los datos en Zoho.
            is_empty = not zoho_profile_for_sync and not cursadas_list
            ttl = 300 if is_empty else 86400
            await store._redis.setex(cursadas_key, ttl, _json.dumps(cache_payload))
        else:
            try:
                cached_obj = _json.loads(cached_cursadas)
            except Exception:
                cached_obj = None
            # Compat: formato viejo era una lista; nuevo es {"cursadas":[], "profile":{}}
            if isinstance(cached_obj, list):
                cursadas_list = cached_obj
                cached_profile = {}
            elif isinstance(cached_obj, dict):
                cursadas_list = cached_obj.get("cursadas") or []
                cached_profile = cached_obj.get("profile") or {}
            else:
                cursadas_list = []
                cached_profile = {}

            # Rehidratar zoho_profile_for_sync desde el cache para que los
            # campos de perfil lleguen a ventas_profile incluso en cache-HIT.
            for k, v in cached_profile.items():
                if v and not zoho_profile_for_sync.get(k):
                    zoho_profile_for_sync[k] = v

            await _log_event(
                session_id,
                "info",
                {
                    "action": "zoho_contacts_desde_cache",
                    "detail": (
                        f"Cache Redis activo para {user_email} — "
                        f"{len(cursadas_list)} cursada(s), "
                        f"perfil rehidratado con {len(cached_profile)} campo(s) (TTL 24h)"
                    ),
                },
            )

        if cursadas_list:
            todos = [c["curso"] for c in cursadas_list]
            signals["is_student"] = True
            zoho_profile_for_sync["courses"] = todos
            lines.append(f"Cursos del alumno ({len(todos)} total): {', '.join(todos)}")
            lines.append(f"IMPORTANTE — No recomiendes estos cursos (ya los tiene): {', '.join(todos)}")

        # Si venimos de cache-HIT y tenemos perfil rehidratado pero las `lines`
        # de contexto están incompletas, agregarlas desde el cached_profile
        # para que el bloque [CONTEXTO DEL CLIENTE IDENTIFICADO] esté completo
        # también en las llamadas subsiguientes.
        if cached_cursadas is not None:
            cp = zoho_profile_for_sync  # ya rehidratado
            if cp.get("profession") and not any("Profesión:" in l for l in lines):
                lines.append(f"Profesión: {cp['profession']}")
            if cp.get("specialty") and not any("Especialidad:" in l for l in lines):
                lines.append(f"Especialidad: {cp['specialty']}")
            if cp.get("cargo") and not any("Cargo:" in l for l in lines):
                lines.append(f"Cargo: {cp['cargo']}")
            if cp.get("lugar_trabajo") and not any("Lugar de trabajo:" in l for l in lines):
                lines.append(f"Lugar de trabajo: {cp['lugar_trabajo']}")
            if cp.get("area_trabajo") and not any("Área donde trabaja:" in l for l in lines):
                lines.append(f"Área donde trabaja: {cp['area_trabajo']}")
            if cp.get("colegio") and not any("Matrícula activa" in l for l in lines):
                lines.append(
                    f"Matrícula activa en colegio/sociedad: {cp['colegio']} "
                    "(aplicable para certificaciones jurisdiccionales AR si el curso las ofrece)"
                )
            if cp.get("interests") and not any(
                "Especialidades de interés" in l
                or "Intereses adicionales" in l
                or "Contenido de interés" in l
                for l in lines
            ):
                lines.append(f"Intereses: {cp['interests']}")
    except Exception as e:
        logger.debug("zoho_cursadas_failed", error=str(e))
        await _log_error(session_id, "zoho_contacts", str(e)[:150])

    # 4. Sincronizar Zoho → Supabase (Zoho pisa, es fuente de verdad)
    #    Solo si hay datos que falten o difieran en Supabase. Best-effort,
    #    no bloquea el flujo del agente.
    #    Corre siempre (incluso en greeting) para que el primer contacto
    #    ya tenga los datos completos en Supabase.
    if zoho_profile_for_sync:
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
                    await _log_event(
                        session_id,
                        "action",
                        {
                            "action": "supabase_sincronizado_zoho",
                            "detail": (
                                f"Supabase actualizado desde Zoho — campos: {', '.join(updates.keys())}"
                            ),
                        },
                    )
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
                    k: v
                    for k, v in zoho_profile_for_sync.items()
                    if k in ("profession", "specialty", "interests") and v
                }
                if created and created.get("id") and extras:
                    await update_customer_profile(created["id"], extras)
                await _log_event(
                    session_id,
                    "action",
                    {
                        "action": "supabase_creado_desde_zoho",
                        "detail": (
                            f"Customer creado en Supabase desde datos Zoho — "
                            f"{user_email} | campos extra: {', '.join(extras.keys()) or '—'}"
                        ),
                    },
                )
        except Exception as e:
            logger.debug("zoho_to_supabase_sync_failed", error=str(e))
            await _log_error(session_id, "zoho_supabase_sync", str(e)[:150])

    # Cachear perfil de ventas (profession/specialty/name/interests/courses)
    # para que `run_sales_node` lo inyecte al system prompt del agente sin
    # hacer round-trip a Zoho/Supabase en cada turno.
    try:
        if user_email and (zoho_profile_for_sync or signals.get("profile_name")):
            sales_profile = {
                "email": user_email,
                "name": zoho_profile_for_sync.get("name") or signals.get("profile_name") or "",
                "profession": zoho_profile_for_sync.get("profession", ""),
                "specialty": zoho_profile_for_sync.get("specialty", ""),
                "interests": zoho_profile_for_sync.get("interests", ""),
                "courses": zoho_profile_for_sync.get("courses", []),
                # Campos extendidos para personalización de ventas
                "cargo": zoho_profile_for_sync.get("cargo", ""),
                "lugar_trabajo": zoho_profile_for_sync.get("lugar_trabajo", ""),
                "area_trabajo": zoho_profile_for_sync.get("area_trabajo", ""),
                "colegio": zoho_profile_for_sync.get("colegio", ""),
            }
            await store._redis.setex(
                f"ventas_profile:{user_email}",
                6 * 3600,  # TTL 6h
                _json.dumps(sales_profile),
            )
    except Exception as e:
        logger.debug("ventas_profile_cache_failed", error=str(e))

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
        from utils.realtime import broadcast_event

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
        import importlib.util
        from pathlib import Path

        path = Path(__file__).parent.parent / "agents" / "routing" / "greeting_prompt.py"
        spec = importlib.util.spec_from_file_location("greeting_prompt_dyn", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.GREETING_SYSTEM_PROMPT
    except Exception:
        return "Sos el asistente de MSK Latam. Generá un saludo breve y personalizado, sin mencionar cursos."


async def generate_greeting_stateless(
    user_name: str = "",
    user_email: str = "",
    user_courses: str = "",
    user_profession: str = "",
    user_specialty: str = "",
    user_cargo: str = "",
    page_slug: str = "",
    country: str = "AR",
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
        user_email,
        store,
        ephemeral_sid,
        user_courses,
        page_slug,
        log_events=False,
    )

    # Si el frontend mandó user_name pero el CRM no devolvió nombre, usarlo como fallback
    if user_name and not _signals.get("profile_name"):
        ctx_lines.insert(0, f"Nombre del cliente: {user_name}")

    # Fallbacks del frontend para profesión/especialidad/cargo. El widget del
    # msk-front (Next.js) ya tiene esos datos en el hook useProfile() — si
    # llegan por el body pero el CRM no los resolvió, los inyectamos al
    # contexto. Prioridad: CRM > frontend > vacío.
    if user_profession and "Profesión" not in "\n".join(ctx_lines):
        ctx_lines.append(f"Profesión: {user_profession}")
    if user_specialty and "Especialidad" not in "\n".join(ctx_lines):
        ctx_lines.append(f"Especialidad: {user_specialty}")
    if user_cargo and "Cargo" not in "\n".join(ctx_lines):
        ctx_lines.append(f"Cargo: {user_cargo}")

    ctx = "\n".join(ctx_lines) if ctx_lines else ""

    try:
        from langchain_core.messages import HumanMessage as LcHuman
        from langchain_core.messages import SystemMessage as LcSystem
        from langchain_openai import ChatOpenAI

        from config.settings import get_settings

        system_txt = _load_greeting_prompt()
        # Inyectar guia de tono segun pais (tuteo LATAM neutro, rioplatense
        # para AR/UY sin voseo, neutro formal para ES/resto). Ver
        # `agents/routing/greeting_prompt.tone_block_for_country`.
        try:
            from agents.routing.greeting_prompt import tone_block_for_country

            system_txt += f"\n\n{tone_block_for_country(country)}"
        except Exception:
            pass
        if ctx:
            system_txt += f"\n\nDatos del cliente:\n{ctx}"
        if page_slug:
            brief = await _resolve_course_mini_brief(page_slug, country)
            if brief.get("title"):
                block = (
                    f"\n\nEl usuario está viendo la página del curso **{brief['title']}** "
                    f"(slug: {page_slug}). Mencionalo por su nombre en el saludo — "
                    "NO uses el slug crudo, usá el título."
                )
                if brief.get("short_desc"):
                    block += f"\n\nDescripción corta del curso:\n{brief['short_desc']}"
                # Solo inyectar la lista de perfiles dirigidos cuando TENEMOS
                # señal real del perfil del usuario (profesión/especialidad).
                # Sin esto, el LLM alucinaba ("Como residente de…") sobre un
                # usuario anónimo eligiendo un perfil al azar de la lista.
                if brief.get("perfiles") and _has_profile_signal(ctx_lines):
                    block += (
                        "\n\nPerfiles dirigidos del curso (usá el que matchee con la "
                        "profesión/especialidad del usuario para conectar el saludo):\n- "
                        + "\n- ".join(brief["perfiles"])
                    )
                elif brief.get("perfiles"):
                    # Anónimo o sin perfil — NO listamos perfiles. Refuerzo
                    # explícito para que el LLM no invente que el usuario es
                    # de un perfil específico.
                    block += (
                        "\n\nNO conocemos la profesión/especialidad del usuario. "
                        "PROHIBIDO asumir que es residente, médico, enfermería, "
                        "estudiante o cualquier perfil específico. Hacé un saludo "
                        "neutro mencionando solo el curso por su nombre."
                    )
                system_txt += block
            # Si no tenemos el título real, NO mencionamos "ese curso"
            # porque genera saludos genéricos confusos.

        llm = ChatOpenAI(
            model="gpt-4o-mini",
            api_key=get_settings().openai_api_key,
            temperature=0.4,  # bajado de 0.7: reglas estrictas + temperatura alta = alucina (inventa "curso de Cardiología")
            max_tokens=160,
        )
        resp = await llm.ainvoke(
            [
                LcSystem(content=system_txt),
                LcHuman(content="Generá el saludo."),
            ]
        )
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
    payment_rejection: dict | None = None,
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

    # Bind conversation_id to structlog context for end-to-end tracing
    structlog.contextvars.bind_contextvars(conversation_id=str(conversation.id))

    # Si acabamos de crear la conv y el front envió el saludo que mostró,
    # lo persistimos como primer bot msg para que quede en el historial
    # y en el contexto del agente.
    if is_new and initial_greeting and message_text != "__widget_init__":
        try:
            await _save_bot_msg(store, conversation, initial_greeting, "bienvenida")
            # Inicializar estado del menú en Redis para que los botones del saludo funcionen
            await wflow_init(store._redis, session_id)
            await log_event(
                session_id,
                "action",
                {
                    "action": "saludo_persistido",
                    "detail": "Saludo stateless persistido como primer mensaje bot (conv materializada)",
                },
            )
        except Exception as e:
            logger.warning("persist_initial_greeting_failed", error=str(e))

    # ── Bot desactivado por agente humano ─────────────────────────────────────
    bot_disabled = await store._redis.get(f"bot_disabled:{session_id}")
    if bot_disabled:
        user_msg = Message(role=MessageRole.USER, content=message_text)
        await store.append_message(conversation, user_msg)
        await _broadcast(
            {
                "type": "new_message",
                "session_id": session_id,
                "role": "user",
                "content": message_text,
                "sender_name": user_name or "Usuario",
                "timestamp": datetime.datetime.utcnow().isoformat(),
            }
        )
        return {
            "response": "",
            "agent_used": "humano",
            "handoff_requested": False,
            "session_id": session_id,
            "bot_disabled": True,
        }

    # ── Conversación ya derivada a humano ─────────────────────────────────────
    # Doble gate: (1) status persistido en la conversación (fuente de verdad)
    # (2) flag rápido en Redis `conv_handoff:{sid}` — se setea sincrónico al
    # momento del handoff y sobrevive si el load del status quedó stale
    # (race con la persistencia async a PG).
    _handoff_flag = await store._redis.get(f"conv_handoff:{session_id}")
    if conversation.status == ConversationStatus.HANDED_OFF or _handoff_flag:
        # Log para debugging — así vemos si el gate se dispara pero la conv no
        # estaba en HANDED_OFF (indicaría race condition entre save() y load).
        if conversation.status != ConversationStatus.HANDED_OFF and _handoff_flag:
            logger.warning(
                "handoff_gate_via_redis_flag",
                session_id=session_id,
                conv_status=str(conversation.status),
            )
        # Persistir mensaje del usuario igual — así el humano lo ve en el inbox.
        try:
            user_msg = Message(role=MessageRole.USER, content=message_text)
            await store.append_message(conversation, user_msg)
            await _broadcast(
                {
                    "type": "new_message",
                    "session_id": session_id,
                    "role": "user",
                    "content": message_text,
                    "sender_name": user_name or "Usuario",
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                }
            )
        except Exception as _e:
            logger.debug("append_user_msg_on_handoff_failed", error=str(_e))
        return {
            "response": "",  # no respondemos — el humano atiende
            "agent_used": "humano",
            "handoff_requested": True,
            "session_id": session_id,
            "bot_disabled": True,
        }

    # ── Actualizar perfil del usuario ─────────────────────────────────────────
    # B4: anónimo que se loguea mid-conversación → pegamos el email al
    # user_profile existente y lo loggeamos. El próximo turno el agente
    # reconstruye contexto (Supabase + Zoho) y ya personaliza respuestas.
    if user_email and not conversation.user_profile.email:
        conversation.user_profile.email = user_email
        if user_name:
            conversation.user_profile.name = user_name
        await log_event(
            session_id,
            "action",
            {
                "action": "email_capturado",
                "detail": (
                    f"Usuario anónimo se identificó: {user_email}"
                    + (f" ({user_name})" if user_name else "")
                    + " — el próximo turno reconstruye contexto Supabase/Zoho."
                ),
            },
        )
    elif user_name and not conversation.user_profile.name:
        conversation.user_profile.name = user_name

    # ─────────────────────────────────────────────────────────────────────────
    # INIT: saludo personalizado + botones del menú
    # ─────────────────────────────────────────────────────────────────────────
    if message_text == "__widget_init__":
        await log_event(
            session_id,
            "info" if user_email else "error",
            {
                "action": "widget_init",
                "detail": (
                    f"Email frontend: {user_email or '❌ NO RECIBIDO (anónimo)'} | "
                    f"Nombre: {user_name or '—'} | "
                    f"Cursos: {user_courses[:50] if user_courses else '—'} | "
                    f"Página: {page_slug or '—'} | "
                    f"Sesión nueva: {is_new}"
                ),
            },
        )

        # Enriquecer contexto para personalizar el saludo
        ctx_lines, _signals = await _build_user_context(
            user_email, store, session_id, user_courses, page_slug
        )
        ctx = "\n".join(ctx_lines) if ctx_lines else ""

        # Sincronizar nombre del CRM al perfil de la conversación
        _init_dirty = False
        _init_name = _signals.get("profile_name", "")
        if _init_name and not conversation.user_profile.name:
            conversation.user_profile.name = _init_name
            _init_dirty = True
        for _cl in ctx_lines:
            if _cl.startswith("Teléfono:") and not conversation.user_profile.phone:
                conversation.user_profile.phone = _cl.split(":", 1)[1].strip()
                _init_dirty = True
        if _init_dirty:
            await store.save(conversation)

        # Generar saludo con IA
        try:
            from langchain_core.messages import HumanMessage as LcHuman
            from langchain_core.messages import SystemMessage as LcSystem
            from langchain_openai import ChatOpenAI

            from config.settings import get_settings

            # Cargar prompt dinámicamente (cambios del panel admin aplicados sin restart)
            def _load_greeting_prompt() -> str:
                try:
                    import importlib.util
                    from pathlib import Path

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
                brief = await _resolve_course_mini_brief(page_slug, country)
                if brief.get("title"):
                    block = (
                        f"\n\nEl usuario está viendo la página del curso **{brief['title']}** "
                        f"(slug: {page_slug}). Mencionalo por su nombre en el saludo — "
                        "NO uses el slug crudo, usá el título."
                    )
                    if brief.get("short_desc"):
                        block += f"\n\nDescripción corta del curso:\n{brief['short_desc']}"
                    # Solo listar perfiles cuando hay perfil real del usuario.
                    # Ver `_has_profile_signal()` y comentario en
                    # `generate_greeting_stateless` — sin este gate el LLM
                    # alucinaba "Como residente de…" sobre usuarios anónimos.
                    if brief.get("perfiles") and _has_profile_signal(ctx_lines):
                        block += (
                            "\n\nPerfiles dirigidos del curso (usá el que matchee con la "
                            "profesión/especialidad del usuario para conectar el saludo):\n- "
                            + "\n- ".join(brief["perfiles"])
                        )
                    elif brief.get("perfiles"):
                        block += (
                            "\n\nNO conocemos la profesión/especialidad del usuario. "
                            "PROHIBIDO asumir que es residente, médico, enfermería, "
                            "estudiante o cualquier perfil específico. Hacé un saludo "
                            "neutro mencionando solo el curso por su nombre."
                        )
                    system_txt += block
                # Si no tenemos el título real, NO mencionamos "ese curso"
                # porque el slug crudo queda feo y "ese curso" es genérico.
                # El saludo será genérico sin mención del curso.

            llm = ChatOpenAI(
                model="gpt-4o-mini",
                api_key=get_settings().openai_api_key,
                temperature=0.7,
                max_tokens=160,
            )
            resp = await llm.ainvoke(
                [
                    LcSystem(content=system_txt),
                    LcHuman(content="Generá el saludo."),
                ]
            )
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

        await log_event(
            session_id,
            "action",
            {
                "action": "saludo_generado",
                "detail": (
                    f"{'Personalizado (IA)' if not isinstance(greeting, str) or 'asesor' not in greeting.lower() else 'Fallback'} | "
                    f"Contexto: {len(ctx_lines)} líneas | "
                    f"Botones: {', '.join(MAIN_BUTTONS)}"
                ),
            },
        )

        # Inicializar estado del menú en Redis
        await wflow_init(store._redis, session_id)

        # Guardar y emitir
        bot_msg = await _save_bot_msg(store, conversation, greeting_with_buttons, "bienvenida")
        await _broadcast(
            {
                "type": "new_message",
                "session_id": session_id,
                "role": "assistant",
                "content": greeting_with_buttons,
                "sender_name": "bienvenida",
                "timestamp": bot_msg.timestamp.isoformat(),
            }
        )
        return {
            "response": greeting_with_buttons,
            "agent_used": "bienvenida",
            "handoff_requested": False,
            "session_id": session_id,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # MENÚ: verificar si estamos en la máquina de estados
    # ─────────────────────────────────────────────────────────────────────────
    wflow_result = await wflow_step(store._redis, session_id, message_text, user_email)

    # Guardar siempre el mensaje del usuario (menú o no)
    user_msg = Message(role=MessageRole.USER, content=message_text)
    await store.append_message(conversation, user_msg)
    await _broadcast(
        {
            "type": "new_message",
            "session_id": session_id,
            "role": "user",
            "content": message_text,
            "sender_name": user_name or "Usuario",
            "timestamp": user_msg.timestamp.isoformat(),
        }
    )

    # ── Respuesta directa del menú (sin agente IA) ────────────────────────────
    if wflow_result is not None and not wflow_result.get("needs_routing"):
        menu_text = wflow_result["response"]
        await log_event(
            session_id,
            "intent",
            {
                "action": "menu_respuesta",
                "detail": f"Respuesta de menú: «{menu_text[:80]}»",
                "agent": "menu",
            },
        )
        bot_msg = await _save_bot_msg(store, conversation, menu_text, "menu")
        await _broadcast(
            {
                "type": "new_message",
                "session_id": session_id,
                "role": "assistant",
                "content": menu_text,
                "sender_name": "menu",
                "timestamp": bot_msg.timestamp.isoformat(),
            }
        )
        return {
            "response": menu_text,
            "agent_used": "menu",
            "handoff_requested": False,
            "session_id": session_id,
        }

    # ── Si el menú colectó un email anónimo, actualizarlo ────────────────────
    forced_agent: str | None = None
    collected_email: str | None = None
    if wflow_result and wflow_result.get("needs_routing"):
        forced_agent = wflow_result.get("forced_agent")
        collected_email = wflow_result.get("collected_email")
    # Si no hay forced_agent del menú, usar el agente persistido en la conv
    # para mantener continuidad (evita que cobranzas/post_venta se pierda).
    elif conversation.current_agent and conversation.current_agent != AgentType.SALES:
        _agent_label_map = {
            AgentType.COLLECTIONS: "cobranzas",
            AgentType.POST_SALES: "post_venta",
        }
        forced_agent = _agent_label_map.get(conversation.current_agent)
    if collected_email:
        user_email = collected_email
        conversation.user_profile.email = collected_email
        await store.save(conversation)
        logger.info("widget_email_collected", session=session_id, email=collected_email)
        await log_event(
            session_id,
            "action",
            {
                "action": "email_capturado",
                "detail": f"Email ingresado por usuario anónimo: {collected_email} → derivando a {forced_agent}",
            },
        )
    if forced_agent:
        await log_event(
            session_id,
            "intent",
            {
                "action": "agente_forzado_por_menu",
                "detail": f"Agente forzado → {forced_agent}",
                "agent": forced_agent,
            },
        )

    # ─────────────────────────────────────────────────────────────────────────
    # ENRIQUECIMIENTO de contexto (siempre antes de llamar al agente)
    # ─────────────────────────────────────────────────────────────────────────
    user_context_lines, user_signals = await _build_user_context(
        user_email, store, session_id, user_courses, page_slug
    )

    # ── Sincronizar datos del CRM al perfil de la conversación ──────────────
    # _build_user_context resuelve el nombre, teléfono y cursos desde
    # Zoho/Supabase pero esos datos solo se usaban para el LLM context.
    # Ahora los persistimos en conversation.user_profile para que el inbox
    # muestre datos completos del contacto.
    _profile_dirty = False
    crm_name = user_signals.get("profile_name", "")
    if crm_name and not conversation.user_profile.name:
        conversation.user_profile.name = crm_name
        _profile_dirty = True
    # Extraer teléfono y profesión de las líneas de contexto
    for _ctx_line in user_context_lines:
        if _ctx_line.startswith("Teléfono:") and not conversation.user_profile.phone:
            conversation.user_profile.phone = _ctx_line.split(":", 1)[1].strip()
            _profile_dirty = True
    if _profile_dirty:
        await store.save(conversation)

    if user_context_lines:
        await log_event(
            session_id,
            "info",
            {
                "action": "contexto_usuario_listo",
                "detail": f"{len(user_context_lines)} líneas de contexto inyectadas al agente:\n"
                + "\n".join(f"  • {l}" for l in user_context_lines[:8]),
            },
        )

    logger.info(
        "widget_message_received",
        session_id=session_id,
        country=country,
        is_new=is_new,
        forced_agent=forced_agent,
    )
    await log_event(
        session_id,
        "info",
        {
            "action": "mensaje_recibido",
            "detail": (
                f"«{message_text[:80]}{'…' if len(message_text) > 80 else ''}» | "
                f"Email: {user_email or '(anónimo)'} | "
                f"Agente forzado: {forced_agent or 'no (clasificará IA)'}"
            ),
        },
    )

    # ── Historial para el LLM ─────────────────────────────────────────────────
    history = conversation.get_history_for_llm(MAX_HISTORY_MESSAGES)
    history_without_last = history[:-1] if history else []

    if user_context_lines:
        history_without_last = [
            {"role": "system", "content": _build_context_block(user_context_lines)}
        ] + history_without_last

    # ── Inyectar contexto de rechazo de pago (si llegó del checkout) ─────────
    # El widget pasa el payload del evento `msk:paymentRejected` en el body.
    # Se inyecta como system msg ALTO en la pila para que el agente arranque
    # el turno explicando el motivo del rechazo (ver
    # `integrations.payment_rejections.build_context_block`).
    if payment_rejection:
        try:
            from integrations.payment_rejections import build_context_block as _build_pr_block

            pr_block = _build_pr_block(payment_rejection)
            if pr_block:
                history_without_last = [
                    {"role": "system", "content": pr_block}
                ] + history_without_last
                await log_event(
                    session_id,
                    "action",
                    {
                        "action": "rechazo_pago_recibido",
                        "detail": (
                            f"Rechazo inyectado al contexto — "
                            f"code={payment_rejection.get('code', '—')} | "
                            f"gateway={payment_rejection.get('gateway', '—')} | "
                            f"reason={(payment_rejection.get('reason') or payment_rejection.get('message') or '')[:80]}"
                        ),
                    },
                )
        except Exception as _e:
            logger.warning("payment_rejection_inject_failed", error=str(_e))

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
        skip_flow=True,  # Drawflow desactivado para widget (usamos widget_flow)
        forced_agent=forced_agent,
    )

    response_text = result["response"]
    handoff = result["handoff_requested"]
    handoff_reason = result["handoff_reason"]
    agent_used = result["agent_used"]

    await log_event(
        session_id,
        "intent",
        {
            "action": "agente_respondio",
            "detail": (
                f"Agente: {agent_used} | "
                f"Derivación humano: {'sí — ' + handoff_reason[:60] if handoff else 'no'} | "
                f"Respuesta: «{response_text[:100]}{'…' if len(response_text) > 100 else ''}»"
            ),
            "agent": agent_used,
        },
    )

    # Nota: antes se anteponía un "notice" cosmético ("🎓 Agente de Cursos",
    # "💳 Área de Cobranzas y Pagos", "👤 Soporte para Alumnos") al primer
    # mensaje tras selección de menú. Se removió — queda raro y no aporta.
    # El bot ya se presenta con el tono adecuado en la respuesta misma.

    # Nota: la ficha de cobranzas ya se cachea por email en _build_user_context
    # (datos_deudor:{email}, TTL 2 h), por lo que no duplicamos la búsqueda acá.

    # ── Persistir agente actual en la conversación ─────────────────────────────
    # Sin esto, el router pierde el agente en el segundo mensaje y defaultea a
    # "sales" — rompe cobranzas y post_venta.
    _agent_type_map = {
        "sales": AgentType.SALES,
        "collections": AgentType.COLLECTIONS,
        "post_sales": AgentType.POST_SALES,
        "closer": AgentType.SALES,  # closer vuelve a ventas
    }
    new_agent_type = _agent_type_map.get(agent_used)
    if new_agent_type and conversation.current_agent != new_agent_type:
        conversation.current_agent = new_agent_type
        await store.save(conversation)

    # ── Guardar respuesta del bot ─────────────────────────────────────────────
    bot_msg = await _save_bot_msg(store, conversation, response_text, agent_used)

    await _broadcast(
        {
            "type": "new_message",
            "session_id": session_id,
            "role": "assistant",
            "content": response_text,
            "sender_name": agent_used,
            "timestamp": bot_msg.timestamp.isoformat(),
        }
    )

    # ── Auto-clasificar lead ──────────────────────────────────────────────────
    try:
        from agents.classifier import classify_conversation

        msgs = [{"role": m.role.value, "content": m.content} for m in conversation.messages[-10:]]
        await classify_conversation(msgs, session_id)
    except Exception:
        pass

    # ── Handoff ───────────────────────────────────────────────────────────────
    if handoff:
        # 1) Flag defensivo sincrónico en Redis — independiente del save async
        # de la conversación. El gate de entrada lo chequea primero así no hay
        # race con la persistencia a Postgres.
        try:
            await store._redis.setex(f"conv_handoff:{session_id}", 86400 * 30, "1")
        except Exception as _e:
            logger.debug("handoff_flag_set_failed", error=str(_e))

        # 2) Notificar
        await notify_handoff(
            channel="Widget Web",
            external_id=session_id,
            user_name=user_name or session_id,
            reason=handoff_reason,
            agent=agent_used,
        )

        # 3) Persistir status en la conversación (fuente de verdad)
        conversation.status = ConversationStatus.HANDED_OFF
        await store.save(conversation)

        # 4) Asignar a un humano de la cola correcta (cobranzas_AR, ventas_MX, etc.)
        try:
            queue_key = f"conv_queue:{conversation.id}"
            _q = await store._redis.get(queue_key)
            queue_val = (_q.decode() if isinstance(_q, bytes) else _q) if _q else ""
            # Fallback: construir la cola desde el agente + país si no está cacheada
            if not queue_val:
                _agent_map = {
                    "sales": "ventas",
                    "collections": "cobranzas",
                    "post_sales": "post_venta",
                    "closer": "ventas",
                }
                _prefix = _agent_map.get(agent_used, "ventas")
                queue_val = f"{_prefix}_{(country or 'XX').upper()}"
            from memory.assignment import auto_assign_round_robin

            await auto_assign_round_robin(session_id, queue=queue_val)
        except Exception:
            pass

    return {
        "response": response_text,
        "agent_used": agent_used,
        "handoff_requested": handoff,
        "session_id": session_id,
    }
