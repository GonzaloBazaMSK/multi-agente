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

import httpx
import structlog
from fastapi import APIRouter, HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from agents.sales.agent import build_sales_agent
from config.constants import Channel
from integrations.stt import transcribe_bytes
from integrations.zoho.leads import ZohoLeads
from memory.conversation_store import get_conversation_store
from models.message import Message, MessageRole
from utils.agent_context import current_channel, current_session_id
from utils.conv_events import log_event

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
    # Anonimizar el nombre del asesor si se le escapó al LLM (el prompt
    # pide no nombrarlo pero los LLMs fallan).
    clean = re.sub(r"\b[Vv]ane[ss]+a(\s+Hern[áa]ndez)?\b", "un asesor académico", clean)
    clean = _format_for_whatsapp(clean)

    # `derivarConAsesor` se activa con cualquiera de los dos handoffs (tag
    # textual del LLM) o con el flag mecánico del ContextVar (setea la tool
    # `create_or_update_lead` cuando recibe brand="Master").
    # `asesorEmailOverride` solo se setea para Másters → permite que el
    # Custom Code Botmaker haga `user.set('asesorEmail', ...)` y rutee al
    # asesor de Másters en lugar del owner del lead.
    try:
        from utils.agent_context import masters_handoff_requested

        _flag_masters = masters_handoff_requested.get()
    except Exception:
        _flag_masters = False
    is_masters = ("DERIVAR_MASTERS_VANESA" in tags) or _flag_masters
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


async def _convert_audio_to_mp3(audio_bytes: bytes, src_ext: str = ".ogg") -> bytes:
    """
    Convierte audio (OGG/Opus de WhatsApp, AMR, etc.) a MP3 vía ffmpeg.

    Whisper acepta OGG en la lista de extensiones, pero falla con el OGG/Opus
    específico de WhatsApp en algunos casos ("Invalid file format" desde el
    backend de OpenAI). Convertir a MP3 elimina la ambigüedad de container.
    """
    import asyncio
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False, suffix=src_ext) as src:
        src.write(audio_bytes)
        src_path = src.name
    dst_path = src_path.rsplit(".", 1)[0] + ".mp3"
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", src_path, "-acodec", "libmp3lame", "-ar", "16000", dst_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        if proc.returncode != 0:
            logger.warning(
                "sales_whatsapp_ffmpeg_failed",
                returncode=proc.returncode,
                stderr=stderr.decode("utf-8", errors="ignore")[:500],
            )
            return b""
        with open(dst_path, "rb") as f:
            return f.read()
    finally:
        for p in (src_path, dst_path):
            try:
                os.unlink(p)
            except Exception:
                pass


async def _transcribe_audio_url(audio_url: str) -> str:
    """Descarga el audio de Botmaker/WhatsApp, lo convierte a MP3 con ffmpeg
    y lo transcribe con Whisper. Devuelve string vacío si falla."""
    if not audio_url:
        return ""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(audio_url)
            r.raise_for_status()
            raw_bytes = r.content
        # Extraer extensión de la URL (default .ogg para WhatsApp).
        ext = ".ogg"
        for e in (".ogg", ".oga", ".mp3", ".m4a", ".wav", ".webm", ".amr", ".opus"):
            if e in audio_url.lower():
                ext = e
                break

        # Convertir a MP3 si NO es ya MP3 (WhatsApp manda ogg/opus que falla
        # en Whisper aunque la extensión esté en la whitelist).
        if ext != ".mp3":
            mp3_bytes = await _convert_audio_to_mp3(raw_bytes, src_ext=ext)
            if not mp3_bytes:
                logger.warning("sales_whatsapp_audio_convert_failed", url_hash=hash(audio_url))
                return ""
            audio_bytes = mp3_bytes
            filename = "audio.mp3"
        else:
            audio_bytes = raw_bytes
            filename = "audio.mp3"

        text = await transcribe_bytes(audio_bytes, filename=filename, language="es")
        logger.info(
            "sales_whatsapp_audio_transcribed",
            url_hash=hash(audio_url),
            chars=len(text),
            converted=(ext != ".mp3"),
        )
        return text
    except Exception as e:
        logger.warning("sales_whatsapp_audio_transcribe_failed", error=str(e))
        return ""


async def _describe_image_url(image_url: str) -> str:
    """
    Descarga la imagen de Botmaker/WhatsApp y la describe con GPT-4o vision.
    Devuelve un string con la descripción + clasificación de la imagen, lista
    para inyectar como userMessage al agente sales.

    Casos típicos en ventas: captura de error de pago en checkout, foto de
    matrícula/título, screenshot de conversación, comprobante. El bot recibe
    la descripción y decide qué responder.

    Devuelve string vacío si falla (sin romper el flow).
    """
    if not image_url:
        return ""
    try:
        import base64

        from openai import AsyncOpenAI

        from config.settings import get_settings

        # 1. Descargar imagen.
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(image_url)
            r.raise_for_status()
            img_bytes = r.content
            content_type = r.headers.get("content-type", "image/jpeg")

        # 2. Encode base64 para enviar a GPT-4o vision.
        b64 = base64.b64encode(img_bytes).decode("ascii")
        data_url = f"data:{content_type};base64,{b64}"

        # 3. Pedir descripción al modelo.
        settings = get_settings()
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres un clasificador de imágenes para un bot de ventas de cursos médicos. "
                        "Describí brevemente (1-2 oraciones) qué ves en la imagen. "
                        "Si es un screenshot de error de pago / checkout fallido, mencionalo explícitamente. "
                        "Si es una matrícula / título / certificado profesional, indicá perfil. "
                        "Si es un meme / selfie / foto sin relación con el tema, decí 'imagen sin relación con consulta'."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describí esta imagen brevemente:"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            max_tokens=200,
            temperature=0.2,
        )
        desc = (resp.choices[0].message.content or "").strip()
        logger.info("sales_whatsapp_image_described", url_hash=hash(image_url), chars=len(desc))
        # Prefijar para que el agente sales entienda el contexto.
        return f"[El lead envió una imagen. Descripción: {desc}]"
    except Exception as e:
        logger.warning("sales_whatsapp_image_analysis_failed", error=str(e))
        return ""


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

    Persistencia:
    - Crea/obtiene conversation en Postgres (channel=WHATSAPP, external_id=phone).
    - Guarda user + bot messages en la conv (para que aparezca en el inbox).
    - Loggea eventos a `conv_events:{conv.id}` para la pestaña "Log de eventos".
    """
    import time as _time

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
    if not payload.phone:
        raise HTTPException(status_code=400, detail="missing phone")

    # 2. Crear/obtener conversation Postgres — necesario para que aparezca
    #    en el inbox + tener el conv_id para el log de eventos.
    #    external_id = phone (mismo patrón que channels/whatsapp.py).
    iso2_country = _iso2_from_pais(payload.Pais or "")
    store = await get_conversation_store()
    conversation, is_new = await store.get_or_create(
        channel=Channel.WHATSAPP,
        external_id=payload.phone,
        country=iso2_country,
    )
    conv_id = str(conversation.id)

    # Bindea structlog ctx con conv_id para que cualquier logger.info() del request
    # incluya este campo automáticamente.
    structlog.contextvars.bind_contextvars(conversation_id=conv_id)

    # ContextVars para que las tools del agente puedan emitir log_events
    # con el session_id correcto sin recibirlo como argumento.
    current_session_id.set(conv_id)
    current_channel.set("whatsapp")

    # Actualizar profile básico de la conv con el nombre/email del payload.
    profile_dirty = False
    if payload.Full_Name and not conversation.user_profile.name:
        conversation.user_profile.name = payload.Full_Name
        profile_dirty = True
    if payload.Email and not conversation.user_profile.email:
        conversation.user_profile.email = payload.Email
        profile_dirty = True
    if not conversation.user_profile.phone:
        conversation.user_profile.phone = payload.phone
        profile_dirty = True
    if profile_dirty:
        await store.save(conversation)

    if is_new:
        await log_event(
            conv_id,
            "info",
            {
                "action": "conv_iniciada",
                "detail": f"Nueva conversación WhatsApp desde Botmaker · lead Zoho {payload.leadId or '(s/d)'}",
                "channel": "whatsapp",
            },
        )

    # ──────────── Procesamiento de audio / imagen ────────────
    user_msg = payload.userMessage or ""

    if payload.audioUrl or user_msg == "__audio__":
        await log_event(conv_id, "info", {"action": "audio_recibido", "detail": "Voice note recibida — transcribiendo con Whisper"})
        transcribed = await _transcribe_audio_url(payload.audioUrl) if payload.audioUrl else ""
        if transcribed:
            user_msg = transcribed
            await log_event(
                conv_id,
                "action",
                {
                    "action": "audio_transcripto",
                    "detail": f"«{transcribed[:120]}{'…' if len(transcribed) > 120 else ''}»",
                    "chars": len(transcribed),
                },
            )
        else:
            await log_event(conv_id, "error", {"source": "stt", "error": "Transcripción Whisper falló"})
            return BotmakerResponse(
                text="Recibí tu audio pero no logré transcribirlo. ¿Me lo escribís en texto, por favor? Así te ayudo mejor 🙏",
                context={},
            )

    elif payload.imageUrl or user_msg == "__image__":
        await log_event(conv_id, "info", {"action": "imagen_recibida", "detail": "Imagen recibida — analizando con GPT-4o vision"})
        description = await _describe_image_url(payload.imageUrl) if payload.imageUrl else ""
        if description:
            user_msg = description
            await log_event(
                conv_id,
                "action",
                {
                    "action": "imagen_descripta",
                    "detail": description[:200],
                },
            )
        else:
            await log_event(conv_id, "error", {"source": "vision", "error": "Análisis GPT-4o vision falló"})
            return BotmakerResponse(
                text="Recibí tu imagen pero no logré procesarla. ¿Me contás por texto qué necesitás? 🙏",
                context={},
            )

    if not user_msg:
        return BotmakerResponse(skip_response=True)

    # Loggear el msg del usuario que se va a procesar.
    await log_event(
        conv_id,
        "info",
        {
            "action": "msg_recibido",
            "detail": f"«{user_msg[:120]}{'…' if len(user_msg) > 120 else ''}»",
            "msg": user_msg[:300],
        },
    )
    # Persistir user msg en la conversación.
    try:
        user_message = Message(role=MessageRole.USER, content=user_msg)
        await store.append_message(conversation, user_message)
    except Exception as e:
        logger.warning("sales_whatsapp_user_msg_persist_failed", error=str(e))

    # ──────────── Fetch lead Zoho ────────────
    lead: dict | None = None
    if payload.leadId:
        try:
            zl = ZohoLeads()
            lead = await zl.get(payload.leadId)
            if lead:
                await log_event(
                    conv_id,
                    "action",
                    {
                        "action": "zoho_lead_fetched",
                        "detail": f"Lead {payload.leadId} traído de Zoho · curso={lead.get('curso_nombre_plantilla', '—')}",
                        "lead_id": payload.leadId,
                    },
                )
            else:
                logger.warning("sales_whatsapp_lead_not_found", lead_id=payload.leadId)
                await log_event(conv_id, "error", {"source": "zoho", "error": f"Lead {payload.leadId} no encontrado en Zoho"})
        except Exception as e:
            logger.warning("sales_whatsapp_zoho_fetch_failed", lead_id=payload.leadId, error=str(e))
            await log_event(conv_id, "error", {"source": "zoho", "error": f"Fetch lead falló: {str(e)[:200]}"})

    user_profile = _build_user_profile(lead or {}, payload)
    country = user_profile["country"]
    page_slug = user_profile["curso_slug"]

    # ──────────── Build + invocar agente ────────────
    await log_event(
        conv_id,
        "intent",
        {
            "intent": "sales",
            "agent": "sales",
            "msg": f"País={country} · curso={page_slug or '(sin slug)'}",
        },
    )

    try:
        agent = await build_sales_agent(
            country=country,
            channel="whatsapp",
            page_slug=page_slug,
            user_profile=user_profile,
        )
    except Exception as e:
        logger.error("sales_whatsapp_build_agent_failed", error=str(e), country=country, slug=page_slug)
        await log_event(conv_id, "error", {"source": "build_sales_agent", "error": str(e)[:300]})
        return BotmakerResponse(
            text="Tuve un problema técnico al procesar tu consulta. Te paso con un asesor académico.",
            context={"derivarConAsesor": True},
        )

    history = await _load_history(payload.phone)
    messages_in = history + [HumanMessage(content=user_msg)]

    invoke_start = _time.perf_counter()
    try:
        result = await agent.ainvoke({"messages": messages_in})
        bot_msg = result["messages"][-1]
        raw_response = bot_msg.content if hasattr(bot_msg, "content") else str(bot_msg)
    except Exception as e:
        logger.error("sales_whatsapp_agent_invoke_failed", error=str(e), phone=payload.phone)
        await log_event(conv_id, "error", {"source": "agent_invoke", "error": str(e)[:300]})
        return BotmakerResponse(
            text="Disculpame, tuve un problema procesando tu mensaje. Probá escribirme de nuevo en un minuto.",
            context={},
        )
    invoke_ms = round((_time.perf_counter() - invoke_start) * 1000)

    clean_text, ctx = _parse_tags(raw_response)

    # Loggear tags emitidos (si los hay) — el agente firmó la respuesta.
    tags_active = [k for k in ("derivarConAsesor", "cierreEnviado", "objecionPrecio", "cargarTicket") if ctx.get(k)]
    await log_event(
        conv_id,
        "action",
        {
            "action": "agente_respondio",
            "detail": f"sales · {invoke_ms}ms · {len(clean_text)} chars · tags={','.join(tags_active) or '—'}",
            "duration_ms": invoke_ms,
            "tags": tags_active,
            "response_preview": clean_text[:200],
        },
    )

    # Eventos específicos por tag — para que sea súper visible en el log.
    if ctx.get("cierreEnviado"):
        await log_event(conv_id, "action", {"action": "cierre_enviado", "detail": "Bot mandó link de checkout"})
    if ctx.get("objecionPrecio"):
        await log_event(conv_id, "action", {"action": "cupon_ofrecido", "detail": "Bot ofreció cupón por objeción de precio (BOT15/BOT20)"})
    if ctx.get("derivarConAsesor"):
        motivo = ctx.get("motivoDerivacion") or "generico"
        override_email = ctx.get("asesorEmailOverride") or ""
        await log_event(
            conv_id,
            "action",
            {
                "action": "derivacion_solicitada",
                "detail": f"Motivo: {motivo}" + (f" → asesor: {override_email}" if override_email else ""),
                "motivo": motivo,
                "asesor_email": override_email,
            },
        )
    if ctx.get("cargarTicket"):
        await log_event(conv_id, "action", {"action": "ticket_portal_sugerido", "detail": "Bot dirigió al lead al portal de tickets"})

    # Persistir bot msg en la conversación.
    try:
        bot_message = Message(role=MessageRole.ASSISTANT, content=clean_text, metadata={"agent": "sales"})
        await store.append_message(conversation, bot_message)
    except Exception as e:
        logger.warning("sales_whatsapp_bot_msg_persist_failed", error=str(e))

    # Historial Redis (memoria conversacional rápida del agente).
    try:
        await _append_history(payload.phone, user_msg, clean_text)
    except Exception as e:
        logger.debug("sales_whatsapp_history_save_failed", error=str(e))

    logger.info(
        "sales_whatsapp_response_ready",
        phone=payload.phone,
        lead_id=payload.leadId,
        conv_id=conv_id,
        country=country,
        slug=page_slug,
        derivar=ctx.get("derivarConAsesor"),
        cierre=ctx.get("cierreEnviado"),
        objecion=ctx.get("objecionPrecio"),
        text_len=len(clean_text),
        invoke_ms=invoke_ms,
    )

    return BotmakerResponse(text=clean_text, context=ctx, skip_response=False)


@router.get("/health")
async def health() -> dict:
    """Health check del endpoint (Botmaker puede usarlo para validar URL)."""
    return {"status": "ok", "service": "sales-whatsapp"}
