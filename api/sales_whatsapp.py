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
from integrations.stt import transcribe_bytes
from integrations.zoho.leads import ZohoLeads
from memory.conversation_store import get_conversation_store
from utils.agent_context import current_channel

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/sales/whatsapp", tags=["sales-whatsapp"])

# ── Constantes ──────────────────────────────────────────────────────────────
HISTORY_TTL_SECONDS = 3 * 24 * 3600  # 3 días
HISTORY_MAX_TURNS = 20  # últimos 10 turnos user+bot
DEDUP_TTL_SECONDS = 86400  # 24h

# ── Debouncing ──────────────────────────────────────────────────────────────
# Cuando el lead manda varios mensajes en cascada, los acumulamos en un
# bucket Redis y respondemos UNA sola vez con todo junto. El primer request
# de la cascada toma el lock y queda esperando; los siguientes solo pushean
# su msg al bucket y devuelven skip_response (Botmaker no manda nada).
#
#   DEBOUNCE_WAIT_S  = ventana de quiet antes de procesar (se resetea con
#                       cada msg nuevo que entra al bucket).
#   DEBOUNCE_MAX_S   = cap total — si el debouncing dura más, fuerza el
#                       procesamiento (margen restante = timeout Botmaker).
#                       Botmaker timeout = 20s. Dejamos 8s para LLM.
DEBOUNCE_WAIT_S = 2.0
DEBOUNCE_MAX_S = 12.0
DEBOUNCE_LOCK_TTL = 30  # max secs que un request puede tener el lock


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

    model_config = {"extra": "allow"}

    # Identificadores del mensaje
    msgId: str | None = None
    msgTs: int | None = None
    userMessage: str = ""
    audioUrl: str = ""
    imageUrl: str = ""
    phone: str = ""

    # Identificador del Lead Zoho (REQUERIDO)
    leadId: str = ""

    # Texto literal de la HSM que el lead recibió antes de responder.
    # Lo setea el flow de Botmaker en `user.get('hsm_text_sent')` antes de
    # enviar la plantilla. El backend lo inyecta como primer AIMessage cuando
    # no hay historial previo, para que el agente tenga el contexto completo.
    templateText: str = ""

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

    # Datos de la oferta HSM — usados en el fallback sintético cuando Zoho
    # no los retorna (o cuando el lead fetch falla). El Custom Code los envía
    # porque ya los tiene como vars de conversación vía TEMPLATE_VARS.
    curso_nombre_plantilla: str = ""
    Descuento: str = ""
    Cuotas: str = ""
    Precio_cuota: str = ""
    Cup_n_de_descuento: str = ""
    valido_hasta: str = ""

    # Asesor / Owner del lead (para handoff)
    ownerEmail: str = ""
    ownerName: str = ""

    # Datos del anuncio CTWA (Click-to-WhatsApp). Presentes solo cuando el
    # usuario llega desde un anuncio Meta sin lead previo en Zoho.
    # referralHeadline = título del anuncio (identifica el curso).
    # referralSourceId  = ID del anuncio en Meta.
    # referralCtwaClid  = click tracking ID de Meta.
    referralHeadline: str = ""
    referralSourceId: str = ""
    referralCtwaClid: str = ""


class BotmakerResponse(BaseModel):
    """Response que el Custom Code de Botmaker espera."""

    text: str = ""
    context: dict = {}
    skip_response: bool = False


# ── Helpers ─────────────────────────────────────────────────────────────────


def _iso2_from_pais(pais: str, fallback: str = "AR") -> str:
    """Mapea Zoho `Pais` string → ISO-2 (AR/MX/CL/...). Default AR."""
    return _COUNTRY_TO_ISO2.get((pais or "").lower().strip(), fallback)


# Prefijos internacionales → ISO-2. Orden importa: prefijos más largos primero
# para evitar que "+598" (UY) matchee "+59" antes que "+593" (EC).
_PHONE_PREFIXES: list[tuple[str, str]] = [
    ("+598", "UY"),
    ("+595", "PY"),
    ("+593", "EC"),
    ("+591", "BO"),
    ("+507", "PA"),
    ("+505", "NI"),
    ("+504", "HN"),
    ("+503", "SV"),
    ("+502", "GT"),
    ("+549", "AR"),  # Argentina con 9 (móvil)
    ("+521", "MX"),  # México con 1 (móvil)
    ("+54",  "AR"),
    ("+52",  "MX"),
    ("+51",  "PE"),
    ("+57",  "CO"),
    ("+56",  "CL"),
    ("+58",  "VE"),
    ("+34",  "ES"),
    ("+1787", "PR"),
    ("+1809", "DO"),
    ("+1829", "DO"),
    ("+1849", "DO"),
]


def _country_from_phone(phone: str, fallback: str = "AR") -> str:
    """Detecta país ISO-2 a partir del prefijo del número de teléfono.
    Usado para leads CTWA que no tienen Zoho lead asociado."""
    p = (phone or "").strip()
    for prefix, iso2 in _PHONE_PREFIXES:
        if p.startswith(prefix):
            return iso2
    return fallback


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
        "curso_nombre": pick("curso_nombre_plantilla", "curso_nombre_plantilla") or programa_name,
        "curso_slug": _extract_slug(pick("Link_web")),
        "link_checkout": pick("Link_checkout"),
        "link_web": pick("Link_web"),
        "link_temario": pick("Link_temario"),
        "link_certificaciones": pick("Certificaciones"),
        "cursos_consultados": pick("Cursos_consultados"),
        # Datos de la oferta que el lead vio en la HSM
        "descuento": pick("Descuento", "Descuento"),
        "cuotas": pick("Cuotas", "Cuotas"),
        "precio_cuota": pick("Precio_cuota", "Precio_cuota"),
        "cupon": pick("Cup_n_de_descuento", "Cup_n_de_descuento"),
        "valido_hasta": pick("valido_hasta", "valido_hasta"),
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


# ── Debounce helpers ─────────────────────────────────────────────────────────


def _bucket_key(phone: str) -> str:
    return f"wa_bucket:{phone}"


def _bucket_lock_key(phone: str) -> str:
    return f"wa_bucket_lock:{phone}"


async def _bucket_push(phone: str, user_msg: str) -> None:
    """Agrega un mensaje al bucket del lead. El bucket vive como Redis list."""
    if not phone or not user_msg:
        return
    store = await get_conversation_store()
    r = store._redis
    key = _bucket_key(phone)
    pipe = r.pipeline()
    pipe.rpush(key, json.dumps({"msg": user_msg, "ts": _now_ts()}))
    pipe.expire(key, 60)  # auto-expire por si algo se cuelga
    await pipe.execute()


async def _bucket_try_lock(phone: str) -> bool:
    """Intenta tomar el lock del bucket. True = este request procesa la cascada,
    False = otro ya lo tiene y este request solo aportó su msg al bucket."""
    store = await get_conversation_store()
    r = store._redis
    # SET NX EX — atómico.
    return bool(await r.set(_bucket_lock_key(phone), "1", nx=True, ex=DEBOUNCE_LOCK_TTL))


async def _bucket_release(phone: str) -> None:
    store = await get_conversation_store()
    r = store._redis
    try:
        await r.delete(_bucket_lock_key(phone))
    except Exception:
        pass


async def _bucket_drain(phone: str) -> list[str]:
    """Saca todos los mensajes del bucket y borra la lista. Devuelve los msgs
    en orden de llegada."""
    store = await get_conversation_store()
    r = store._redis
    key = _bucket_key(phone)
    pipe = r.pipeline()
    pipe.lrange(key, 0, -1)
    pipe.delete(key)
    raw, _ = await pipe.execute()
    out = []
    for item in raw or []:
        try:
            data = json.loads(item.decode() if isinstance(item, bytes) else item)
            msg = data.get("msg", "")
            if msg:
                out.append(msg)
        except Exception:
            pass
    return out


async def _bucket_size(phone: str) -> int:
    store = await get_conversation_store()
    r = store._redis
    try:
        return int(await r.llen(_bucket_key(phone)))
    except Exception:
        return 0


def _now_ts() -> float:
    import time as _t

    return _t.time()


async def _debounce_wait(phone: str) -> None:
    """Espera hasta que pasen DEBOUNCE_WAIT_S sin que entren mensajes nuevos
    al bucket. Cap total = DEBOUNCE_MAX_S."""
    import asyncio
    import time as _t

    start = _t.monotonic()
    last_size = await _bucket_size(phone)
    while True:
        if _t.monotonic() - start >= DEBOUNCE_MAX_S:
            logger.info("debounce_cap_reached", phone=phone, secs=DEBOUNCE_MAX_S)
            return
        await asyncio.sleep(DEBOUNCE_WAIT_S)
        size = await _bucket_size(phone)
        if size == last_size:
            return  # nada nuevo durante la ventana → cerramos
        last_size = size


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

    Stateless puro: NO persiste conv ni mensajes en Postgres ni alimenta el
    inbox del multi-agente. WhatsApp vive 100% en Botmaker. Lo único que
    persiste es:
    - `sales_wa_history:{phone}` en Redis (memoria del bot, TTL 3 días).
    - `dedup:{msgId}` en Redis (anti-loop, TTL 24h).
    Esto refleja el comportamiento del workflow n8n original.
    """
    logger.info(
        "sales_whatsapp_webhook_received",
        msg_id=payload.msgId,
        phone=payload.phone,
        lead_id=payload.leadId,
        user_msg_len=len(payload.userMessage),
        has_template_text=bool(payload.templateText),
    )

    # 0. Dedup por msgId (anti-bucle si Botmaker reintenta el webhook).
    if await _is_duplicate_msgid(payload.msgId or ""):
        logger.info("sales_whatsapp_duplicate_msgid", msg_id=payload.msgId)
        return BotmakerResponse(skip_response=True)

    # 1. Validaciones mínimas.
    if not payload.phone:
        raise HTTPException(status_code=400, detail="missing phone")

    # ContextVar de canal — las tools del agente lo leen para ajustar comportamiento.
    # `current_session_id` queda vacío a propósito → `log_to_conv` es no-op.
    current_channel.set("whatsapp")

    # ──────────── Procesamiento de audio / imagen ────────────
    user_msg = payload.userMessage or ""

    if payload.audioUrl or user_msg == "__audio__":
        transcribed = await _transcribe_audio_url(payload.audioUrl) if payload.audioUrl else ""
        if transcribed:
            user_msg = transcribed
            logger.info("sales_whatsapp_audio_transcribed", phone=payload.phone, chars=len(transcribed))
        else:
            logger.warning("sales_whatsapp_audio_transcription_failed", phone=payload.phone)
            return BotmakerResponse(
                text="Recibí tu audio pero no logré transcribirlo. ¿Me lo escribís en texto, por favor? Así te ayudo mejor 🙏",
                context={},
            )

    elif payload.imageUrl or user_msg == "__image__":
        description = await _describe_image_url(payload.imageUrl) if payload.imageUrl else ""
        if description:
            user_msg = description
            logger.info("sales_whatsapp_image_described", phone=payload.phone, chars=len(description))
        else:
            logger.warning("sales_whatsapp_image_description_failed", phone=payload.phone)
            return BotmakerResponse(
                text="Recibí tu imagen pero no logré procesarla. ¿Me contás por texto qué necesitás? 🙏",
                context={},
            )

    if not user_msg:
        return BotmakerResponse(skip_response=True)

    # ──────────── Debouncing ────────────
    # Acumulamos en bucket Redis. El primer request de la cascada toma el lock
    # y queda esperando (DEBOUNCE_WAIT_S sin nuevos msgs, cap DEBOUNCE_MAX_S).
    # Los siguientes solo aportan su msg y devuelven skip_response.
    await _bucket_push(payload.phone, user_msg)
    got_lock = await _bucket_try_lock(payload.phone)
    if not got_lock:
        logger.info("sales_whatsapp_debounce_yielded", phone=payload.phone, msg_id=payload.msgId)
        return BotmakerResponse(skip_response=True)

    try:
        await _debounce_wait(payload.phone)
        # Drain — combina todos los msgs del bucket en un único turno.
        msgs = await _bucket_drain(payload.phone)
        if not msgs:
            # Edge case: el bucket quedó vacío entre el wait y el drain
            # (otro proceso lo drenó). Usamos el msg original.
            msgs = [user_msg]
        # Si llegó 1 solo, usar tal cual. Si llegaron varios, unir con saltos
        # para que el LLM vea claramente que son mensajes separados del lead.
        if len(msgs) == 1:
            user_msg = msgs[0]
        else:
            user_msg = "\n".join(msgs)
            logger.info(
                "sales_whatsapp_debounce_combined",
                phone=payload.phone,
                msgs_count=len(msgs),
                combined_chars=len(user_msg),
            )

        return await _process_message_and_respond(payload, user_msg)
    finally:
        await _bucket_release(payload.phone)


async def _process_message_and_respond(payload: BotmakerPayload, user_msg: str) -> BotmakerResponse:
    """Procesa el mensaje (Zoho fetch + agente + tags) y devuelve la response
    para Botmaker. Esta función asume que user_msg ya está combinado/debounceado."""
    import time as _time

    # ──────────── Fetch lead Zoho ────────────
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

    # ──────────── Detección CTWA ────────────
    # Condición: no hay lead Zoho (leadId vacío) pero sí datos del anuncio Meta.
    # En este caso el bot sigue un script de recolección de datos antes de pitchear.
    is_ctwa = not payload.leadId and bool(payload.referralHeadline)
    if is_ctwa:
        ctwa_country = _country_from_phone(payload.phone)
        country = ctwa_country
        user_profile["country"] = ctwa_country
        user_profile["curso_nombre"] = payload.referralHeadline
        user_profile["ctwa"] = True
        user_profile["ctwa_ad_id"] = payload.referralSourceId
        user_profile["ctwa_ad_name"] = payload.referralHeadline
        user_profile["ctwa_lead_id_social"] = payload.referralCtwaClid
        # Guardar datos del anuncio en Redis para que el tool los use al crear el lead.
        try:
            store = await get_conversation_store()
            await store._redis.set(
                f"ctwa_data:{payload.phone}",
                json.dumps({
                    "headline": payload.referralHeadline,
                    "source_id": payload.referralSourceId,
                    "ctwa_clid": payload.referralCtwaClid,
                    "country": ctwa_country,
                }, ensure_ascii=False),
                ex=7 * 24 * 3600,
            )
        except Exception as e:
            logger.warning("sales_whatsapp_ctwa_redis_save_failed", error=str(e))
        logger.info(
            "sales_whatsapp_ctwa_detected",
            phone=payload.phone,
            headline=payload.referralHeadline,
            source_id=payload.referralSourceId,
            country=ctwa_country,
        )

    # ──────────── Build + invocar agente ────────────
    try:
        agent = await build_sales_agent(
            country=country,
            channel="whatsapp",
            page_slug=page_slug,
            user_profile=user_profile,
        )
    except Exception as e:
        logger.error("sales_whatsapp_build_agent_failed", error=str(e), country=country, slug=page_slug)
        return BotmakerResponse(
            text="Tuve un problema técnico al procesar tu consulta. Te paso con un asesor académico.",
            context={"derivarConAsesor": True},
        )

    history = await _load_history(payload.phone)

    # Primer turno: inyectar contexto de la HSM como AIMessage para que el agente
    # sepa qué vio el lead antes de responder.
    #   - CTWA: no hay HSM previa → no inyectar nada. El agente arranca directo
    #     con el script de recolección de datos.
    #   - Si Botmaker mandó el texto real (templateText) → usarlo tal cual.
    #   - Si no (caso más común hoy) → construir contexto sintético con los datos
    #     del lead: curso, nombre, datos de oferta si existen.
    if not history and not is_ctwa:
        template_context = payload.templateText
        if template_context:
            # templateText puede venir como JSON (callService serializa TEMPLATE_VARS)
            # o como texto plano (implementaciones futuras con texto real de Botmaker).
            if template_context.startswith("{"):
                try:
                    hsm_data = json.loads(template_context)
                    # Convertir el dict de vars a un string legible para el LLM.
                    # Solo incluir campos con valor; el formato es clave: valor.
                    lines = [f"{k}: {v}" for k, v in hsm_data.items() if v and v != ""]
                    template_context = "Datos de la campaña enviada al lead:\n" + "\n".join(lines)
                except Exception:
                    pass  # Si falla el parse, usar el string tal cual
        else:
            # Fallback sintético cuando Botmaker no envió hsm_text_sent.
            # Usa los datos de Zoho / payload individuales ya parseados en user_profile.
            parts = []
            nombre = user_profile.get("first_name") or user_profile.get("full_name") or ""
            curso = user_profile.get("curso_nombre") or ""
            descuento = user_profile.get("descuento") or ""
            cuotas = user_profile.get("cuotas") or ""
            precio_cuota = user_profile.get("precio_cuota") or ""
            cupon = user_profile.get("cupon") or ""
            valido_hasta = user_profile.get("valido_hasta") or ""
            if nombre:
                parts.append(f"Hola {nombre}!")
            if curso:
                parts.append(f"Te contactamos por el {curso}.")
            if descuento:
                parts.append(f"Descuento ofrecido: {descuento}%.")
            if cuotas and precio_cuota:
                parts.append(f"{cuotas} cuotas de {precio_cuota}.")
            if cupon:
                parts.append(f"Cupón disponible: {cupon}.")
            if valido_hasta:
                parts.append(f"Válido hasta: {valido_hasta}.")
            if parts:
                template_context = " ".join(parts)
        if template_context:
            history = [AIMessage(content=template_context)]
            logger.info(
                "sales_whatsapp_template_context_injected",
                phone=payload.phone,
                synthetic=not bool(payload.templateText),
                chars=len(template_context),
                has_offer_data=bool(user_profile.get("descuento") or user_profile.get("cuotas")),
                preview=template_context[:120],
            )

    messages_in = history + [HumanMessage(content=user_msg)]

    invoke_start = _time.perf_counter()
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
    invoke_ms = round((_time.perf_counter() - invoke_start) * 1000)

    clean_text, ctx = _parse_tags(raw_response)

    # Historial Redis (memoria conversacional rápida del agente).
    try:
        await _append_history(payload.phone, user_msg, clean_text)
    except Exception as e:
        logger.debug("sales_whatsapp_history_save_failed", error=str(e))

    tags_active = [k for k in ("derivarConAsesor", "cierreEnviado", "objecionPrecio", "cargarTicket") if ctx.get(k)]
    logger.info(
        "sales_whatsapp_response_ready",
        phone=payload.phone,
        lead_id=payload.leadId,
        country=country,
        slug=page_slug,
        tags=tags_active,
        motivo_derivacion=ctx.get("motivoDerivacion") or "",
        text_len=len(clean_text),
        invoke_ms=invoke_ms,
    )

    return BotmakerResponse(text=clean_text, context=ctx, skip_response=False)


@router.get("/health")
async def health() -> dict:
    """Health check del endpoint (Botmaker puede usarlo para validar URL)."""
    return {"status": "ok", "service": "sales-whatsapp"}
