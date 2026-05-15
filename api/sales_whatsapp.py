"""
API endpoint para el bot de ventas vía WhatsApp/Botmaker.

Flujo:
    Lead Zoho ─HSM→ Botmaker ─reply→ Custom Code (Botmaker) ─POST→ este endpoint
                                                                         │
                       ┌─────────────────────────────────────────────────┘
                       ▼
    1. Fetch lead Zoho por leadId → ficha completa (nombre, país, curso, etc.)
    2. Armar user_profile + resolver page_slug del curso
    3. build_sales_agent(country, channel="whatsapp", page_slug, user_profile)
    4. Cargar historial Redis (sales_wa_history:{phone}, TTL 3 días)
    5. agent.ainvoke({"messages": history + [user_msg]})
    6. Parsear tags [DERIVAR_HUMANO], [CIERRE_ENVIADO], [OBJECION_PRECIO]
    7. Guardar nuevo turno en historial
    8. Devolver {text, context: {...}, skip_response: false} compatible con
       el Custom Code Botmaker.

NOTA: NO incluye debounce/bolsa de mensajes. Cada mensaje del lead se procesa
individualmente. Si el lead manda 3 mensajes seguidos, el bot va a responder
3 veces. Si esto se vuelve un problema, se puede agregar debounce siguiendo
el patrón de Redis list + asyncio.sleep que está en n8n (FM35XYfQ6lYQEUV3).
"""

from __future__ import annotations

import json
import re

import structlog
from fastapi import APIRouter, HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from agents.sales.agent import build_sales_agent
from integrations.zoho.leads import ZohoLeads
from memory.conversation_store import get_conversation_store

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/sales/whatsapp", tags=["sales-whatsapp"])

# ── Constantes ──────────────────────────────────────────────────────────────
HISTORY_TTL_SECONDS = 3 * 24 * 3600  # 3 días
HISTORY_MAX_TURNS = 20  # últimos 10 turnos user+bot
DEDUP_TTL_SECONDS = 86400  # 24h


# Mapa país (Zoho lo guarda como string) → ISO-2.
_COUNTRY_TO_ISO2 = {
    "argentina": "AR", "ar": "AR",
    "bolivia": "BO", "bo": "BO",
    "chile": "CL", "cl": "CL",
    "colombia": "CO", "co": "CO",
    "costa rica": "CR", "cr": "CR",
    "ecuador": "EC", "ec": "EC",
    "españa": "ES", "espana": "ES", "es": "ES",
    "guatemala": "GT", "gt": "GT",
    "honduras": "HN", "hn": "HN",
    "méxico": "MX", "mexico": "MX", "mx": "MX",
    "nicaragua": "NI", "ni": "NI",
    "panamá": "PA", "panama": "PA", "pa": "PA",
    "perú": "PE", "peru": "PE", "pe": "PE",
    "paraguay": "PY", "py": "PY",
    "el salvador": "SV", "sv": "SV",
    "uruguay": "UY", "uy": "UY",
    "venezuela": "VE", "ve": "VE",
}


# ── Schemas ─────────────────────────────────────────────────────────────────


class BotmakerPayload(BaseModel):
    """Payload que envía el Custom Code de Botmaker."""

    # Identificadores del mensaje
    msgId: str | None = None
    msgTs: int | None = None
    userMessage: str = ""
    audioUrl: str = ""
    imageUrl: str = ""
    phone: str = ""

    # Identificador del Lead Zoho (REQUERIDO)
    leadId: str = ""

    # Datos opcionales del lead (Botmaker los pasa como fallback —
    # el backend igual re-fetcha de Zoho con leadId).
    First_Name: str = ""
    Last_Name: str = ""
    Full_Name: str = ""
    Email: str = ""
    Pais: str = ""
    Profesion: str = ""
    Especialidad: str = ""
    Lugar_de_trabajo: str = ""

    # Asesor / Owner del lead (para handoff)
    ownerEmail: str = ""
    ownerName: str = ""


class BotmakerResponse(BaseModel):
    """Response que el Custom Code de Botmaker espera."""

    text: str = ""
    context: dict = {}
    skip_response: bool = False


# ── Helpers ─────────────────────────────────────────────────────────────────


def _iso2_from_pais(pais: str, fallback: str = "AR") -> str:
    """Mapea Zoho `Pais` string → ISO-2 (AR/MX/CL/...). Default AR."""
    return _COUNTRY_TO_ISO2.get((pais or "").lower().strip(), fallback)


def _extract_slug(link_web: str) -> str:
    """Extrae el slug de la URL `Link_web` del lead (ej. msklatam.com/curso/medicina-interna)."""
    m = re.search(r"curso/([^/?#]+)", link_web or "", re.I)
    return m.group(1) if m else ""


def _build_user_profile(lead: dict, fallback_payload: BotmakerPayload) -> dict:
    """
    Arma el user_profile para `build_sales_agent` a partir del lead Zoho.
    Si el lead no se pudo fetchear, usa los campos del payload Botmaker como
    fallback.
    """
    lead = lead or {}

    def pick(key: str, fallback_attr: str = "") -> str:
        v = lead.get(key)
        if v:
            return str(v)
        return getattr(fallback_payload, fallback_attr, "") if fallback_attr else ""

    programa = lead.get("Programa") or {}
    if isinstance(programa, dict):
        programa_name = programa.get("name", "")
    else:
        programa_name = str(programa)

    owner = lead.get("Owner") or {}
    owner_email = (owner.get("email") if isinstance(owner, dict) else "") or fallback_payload.ownerEmail
    owner_name = (owner.get("name") if isinstance(owner, dict) else "") or fallback_payload.ownerName

    colegios = lead.get("Colegio_Sociedad_o_Federaci_n") or []
    if isinstance(colegios, list):
        colegios_names = [c.get("name", "") if isinstance(c, dict) else str(c) for c in colegios]
    else:
        colegios_names = []

    return {
        "lead_id": pick("id"),
        "first_name": pick("First_Name", "First_Name"),
        "last_name": pick("Last_Name", "Last_Name"),
        "full_name": pick("Full_Name", "Full_Name"),
        "email": pick("Email", "Email"),
        "phone": pick("Phone") or fallback_payload.phone,
        "country": _iso2_from_pais(pick("Pais", "Pais")),
        "country_name": pick("Pais", "Pais"),
        "city": pick("City"),
        "state": pick("State"),
        "profession": pick("Profesion", "Profesion"),
        "specialty": pick("Especialidad", "Especialidad"),
        "lugar_trabajo": pick("Lugar_de_trabajo", "Lugar_de_trabajo"),
        "lead_source": pick("Lead_Source"),
        "lead_status": pick("Lead_Status"),
        "scoring_venta": lead.get("Scoring_venta"),
        "colegios": colegios_names,
        "owner_email": owner_email,
        "owner_name": owner_name,
        "curso_nombre": pick("curso_nombre_plantilla") or programa_name,
        "curso_slug": _extract_slug(pick("Link_web")),
        "link_checkout": pick("Link_checkout"),
        "link_web": pick("Link_web"),
        "link_temario": pick("Link_temario"),
        "link_certificaciones": pick("Certificaciones"),
        "cursos_consultados": pick("Cursos_consultados"),
    }


# Tags que el agente puede emitir al final del mensaje (para tracking +
# routing en Botmaker).
#
#   [DERIVAR_HUMANO]         → handoff genérico al asesor académico asignado
#                              (owner del lead en Zoho).
#   [DERIVAR_MASTERS_VANESA] → handoff específico a Vanesa Hernández
#                              (vanessahernandez@msklatam.com) para Másters
#                              que NO se venden por sitio. El Custom Code
#                              Botmaker setea asesorEmail antes del handoff.
#   [CIERRE_ENVIADO]         → bot mandó el link de checkout en este turno.
#   [OBJECION_PRECIO]        → bot ofreció cupón BOT15/BOT20 por objeción de precio.
#   [CARGAR_TICKET]          → bot dirigió al lead al portal de tickets
#                              (bajas, anulaciones, reclamos, refunds).
#                              NO genera handoff a humano — la baja la
#                              tramita el cliente en el portal.
_TAGS_PATTERN = re.compile(
    r"\[(DERIVAR_HUMANO|DERIVAR_MASTERS_VANESA|CIERRE_ENVIADO|OBJECION_PRECIO|CARGAR_TICKET)\]"
)

# WhatsApp usa **UN** asterisco para negrita (`*texto*`). Si el LLM emite
# `**texto**` (markdown estándar), WhatsApp lo muestra LITERAL con los
# asteriscos. Convertimos cualquier `**...**` a `*...*` para asegurar
# que la negrita se renderice bien.
_WA_BOLD_PATTERN = re.compile(r"\*\*([^*\n]+?)\*\*")


def _format_for_whatsapp(text: str) -> str:
    """Adapta el texto del LLM al formato que WhatsApp interpreta correctamente."""
    if not text:
        return ""
    # Doble asterisco → single asterisco (negrita WhatsApp).
    text = _WA_BOLD_PATTERN.sub(r"*\1*", text)
    return text


def _parse_tags(ai_text: str) -> tuple[str, dict]:
    """
    Extrae tags de tracking del output del agente, adapta el formato para
    WhatsApp y devuelve el texto limpio + un dict de contexto compatible
    con el Custom Code de Botmaker.
    """
    tags = set(_TAGS_PATTERN.findall(ai_text or ""))
    clean = _TAGS_PATTERN.sub("", ai_text or "").strip()
    clean = _format_for_whatsapp(clean)

    # `derivarConAsesor` se activa con cualquiera de los dos handoffs.
    # `asesorEmailOverride` solo se setea para Másters → permite que el
    # Custom Code Botmaker haga `user.set('asesorEmail', ...)` y rutee a
    # Vanesa en lugar del owner del lead.
    is_masters = "DERIVAR_MASTERS_VANESA" in tags
    is_generic = "DERIVAR_HUMANO" in tags
    return clean, {
        "derivarConAsesor": is_masters or is_generic,
        "asesorEmailOverride": "vanessahernandez@msklatam.com" if is_masters else "",
        "motivoDerivacion": "masters" if is_masters else ("generico" if is_generic else ""),
        "cierreEnviado": "CIERRE_ENVIADO" in tags,
        "objecionPrecio": "OBJECION_PRECIO" in tags,
        "cargarTicket": "CARGAR_TICKET" in tags,
    }


# ── Historial Redis ──────────────────────────────────────────────────────────
# Key: sales_wa_history:{phone} → list de JSON {"role": "user"|"assistant", "content": "..."}


def _history_key(phone: str) -> str:
    return f"sales_wa_history:{phone}"


async def _load_history(phone: str) -> list:
    if not phone:
        return []
    store = await get_conversation_store()
    r = store._redis
    raw = await r.lrange(_history_key(phone), -HISTORY_MAX_TURNS, -1)
    msgs = []
    for item in raw:
        try:
            data = json.loads(item)
            if data.get("role") == "user":
                msgs.append(HumanMessage(content=data["content"]))
            elif data.get("role") == "assistant":
                msgs.append(AIMessage(content=data["content"]))
        except Exception:
            pass
    return msgs


async def _append_history(phone: str, user_msg: str, bot_msg: str) -> None:
    if not phone:
        return
    store = await get_conversation_store()
    r = store._redis
    key = _history_key(phone)
    pipe = r.pipeline()
    pipe.rpush(key, json.dumps({"role": "user", "content": user_msg}))
    pipe.rpush(key, json.dumps({"role": "assistant", "content": bot_msg}))
    pipe.ltrim(key, -HISTORY_MAX_TURNS, -1)
    pipe.expire(key, HISTORY_TTL_SECONDS)
    await pipe.execute()


async def _is_duplicate_msgid(msg_id: str) -> bool:
    """Anti-doble-procesamiento por msgId. TTL 24h."""
    if not msg_id:
        return False
    store = await get_conversation_store()
    r = store._redis
    # SET NX: gana solo si la key no existía.
    ok = await r.set(f"sales_wa_seen:{msg_id}", "1", nx=True, ex=DEDUP_TTL_SECONDS)
    return not ok  # si NO se pudo setear, es porque ya estaba → duplicado


# ── Endpoint principal ───────────────────────────────────────────────────────


@router.post("/webhook", response_model=BotmakerResponse)
async def sales_whatsapp_webhook(payload: BotmakerPayload) -> BotmakerResponse:
    """
    Webhook que Botmaker invoca cuando un lead responde a la HSM de ventas.

    Devuelve `{text, context, skip_response}` compatible con el Custom Code
    de Botmaker — el código ya espera ese shape.
    """
    logger.info(
        "sales_whatsapp_webhook_received",
        msg_id=payload.msgId,
        phone=payload.phone,
        lead_id=payload.leadId,
        user_msg_len=len(payload.userMessage),
    )

    # 0. Dedup por msgId (anti-bucle si Botmaker reintenta el webhook).
    if await _is_duplicate_msgid(payload.msgId or ""):
        logger.info("sales_whatsapp_duplicate_msgid", msg_id=payload.msgId)
        return BotmakerResponse(skip_response=True)

    # 1. Validaciones mínimas.
    if not payload.userMessage or payload.userMessage in ("__audio__", "__image__"):
        # Audio/imagen: por ahora respondemos con disclaimer. Más adelante
        # podemos sumar transcripción/OCR como en el flow n8n.
        return BotmakerResponse(
            text="Recibí tu mensaje. Por ahora prefiero responderte por texto — escribime tu consulta y te ayudo. 🙏",
            context={},
        )

    if not payload.phone:
        raise HTTPException(status_code=400, detail="missing phone")

    # 2. Fetch lead Zoho (best-effort: si falla, seguimos con fallback del payload).
    lead: dict | None = None
    if payload.leadId:
        try:
            zl = ZohoLeads()
            lead = await zl.get(payload.leadId)
            if not lead:
                logger.warning("sales_whatsapp_lead_not_found", lead_id=payload.leadId)
        except Exception as e:
            logger.warning("sales_whatsapp_zoho_fetch_failed", lead_id=payload.leadId, error=str(e))

    user_profile = _build_user_profile(lead or {}, payload)
    country = user_profile["country"]
    page_slug = user_profile["curso_slug"]

    # 3. Construir agente (system prompt completo + catálogo + brief del curso).
    try:
        agent = await build_sales_agent(
            country=country,
            channel="whatsapp",
            page_slug=page_slug,
            user_profile=user_profile,
        )
    except Exception as e:
        logger.error("sales_whatsapp_build_agent_failed", error=str(e), country=country, slug=page_slug)
        # Fallback: devolvemos un mensaje genérico para que Botmaker derive.
        return BotmakerResponse(
            text="Tuve un problema técnico al procesar tu consulta. Te paso con un asesor académico.",
            context={"derivarConAsesor": True},
        )

    # 4. Historial.
    history = await _load_history(payload.phone)
    messages_in = history + [HumanMessage(content=payload.userMessage)]

    # 5. Invocar agente.
    try:
        result = await agent.ainvoke({"messages": messages_in})
        bot_msg = result["messages"][-1]
        raw_response = bot_msg.content if hasattr(bot_msg, "content") else str(bot_msg)
    except Exception as e:
        logger.error("sales_whatsapp_agent_invoke_failed", error=str(e), phone=payload.phone)
        return BotmakerResponse(
            text="Disculpame, tuve un problema procesando tu mensaje. Probá escribirme de nuevo en un minuto.",
            context={},
        )

    # 6. Parsear tags y limpiar texto.
    clean_text, ctx = _parse_tags(raw_response)

    # 7. Guardar en historial (best-effort).
    try:
        await _append_history(payload.phone, payload.userMessage, clean_text)
    except Exception as e:
        logger.debug("sales_whatsapp_history_save_failed", error=str(e))

    logger.info(
        "sales_whatsapp_response_ready",
        phone=payload.phone,
        lead_id=payload.leadId,
        country=country,
        slug=page_slug,
        derivar=ctx.get("derivarConAsesor"),
        cierre=ctx.get("cierreEnviado"),
        objecion=ctx.get("objecionPrecio"),
        text_len=len(clean_text),
    )

    return BotmakerResponse(text=clean_text, context=ctx, skip_response=False)


@router.get("/health")
async def health() -> dict:
    """Health check del endpoint (Botmaker puede usarlo para validar URL)."""
    return {"status": "ok", "service": "sales-whatsapp"}
