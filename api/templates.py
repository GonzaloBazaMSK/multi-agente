"""
Endpoint para envío de plantillas de WhatsApp:
  POST /templates/send      → desde Zoho CRM (Deluge)
  GET  /templates/hsm       → lista plantillas aprobadas de Meta (para inbox)
  POST /templates/send-hsm  → envía plantilla desde el inbox humano
"""
import json
from fastapi import APIRouter, Request, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from api.auth import get_current_user
from api.admin import verify_admin_key
import structlog

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/templates", tags=["templates"])

# Mapeo país → número de WhatsApp que envía (mismo que Botmaker)
CHANNEL_NUMBERS = {
    "Argentina": "5491152170771",
    "México":    "5215590586200",
    "Chile":     "56224875300",
    "Ecuador":   "593993957801",
    "Uruguay":   "5491152170771",
    "Colombia":  "5753161349",
    "Peru":      "5753161349",  # fallback MP
}

COUNTRY_ISO = {
    "Argentina": "arg",
    "México":    "mx",
    "Chile":     "cl",
    "Ecuador":   "ec",
    "Uruguay":   "arg",
    "Colombia":  "mp",
    "Peru":      "mp",
}


@router.post("/send")
async def send_template(request: Request, background_tasks: BackgroundTasks, key: str = Depends(verify_admin_key)):
    """
    Recibe datos de Zoho CRM y envía una plantilla de WhatsApp.
    Acepta tanto JSON como form-urlencoded (como enviaba Botmaker/Deluge).
    """
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        body = await request.json()
    else:
        # form-urlencoded (como lo envía Deluge con parameters:sendData.toString())
        form = await request.form()
        body = dict(form)

    phone = body.get("Phone", "")
    nombre = body.get("Full_Name", "")
    plantilla_base = body.get("plantilla", "bienvenida")
    pais = body.get("Pais", "Argentina")

    if not phone:
        return {"process": "error", "detail": "Phone requerido"}

    # Construir nombre de plantilla con ISO de país
    iso = COUNTRY_ISO.get(pais, "arg")
    plantilla_name = f"{iso}_{plantilla_base}"

    # Variables de la plantilla (mismas que Botmaker)
    template_vars = {
        "Nombre_completo": body.get("Full_Name", ""),
        "Nombre del agente": body.get("Owner[name]", body.get("Owner_name", "")),
        "operador": body.get("Owner[name]", body.get("Owner_name", "")),
        "asesorEmail": body.get("Owner[email]", body.get("Owner_email", "")),
        "descuento": body.get("Descuento", ""),
        "obsequio": body.get("Obsequio", ""),
        "curso_nombre_plantilla": body.get("curso_nombre_plantilla", ""),
        "Cuotas": body.get("Cuotas", ""),
        "Link_temario": body.get("Link_temario", ""),
        "Agenda_calendly": body.get("Agenda_calendly", ""),
        "URL_Reunion": body.get("URL_Reunion", ""),
        "Link_calendly": body.get("Link_calendly", ""),
        "Certificaciones": body.get("Certificaciones", ""),
    }

    logger.info(
        "template_send_requested",
        phone=phone, plantilla=plantilla_name, pais=pais
    )

    background_tasks.add_task(_send_template_task, phone, plantilla_name, template_vars, pais)
    return {"process": "ok", "enviada": True, "plantilla": plantilla_name, "phone": phone}


async def _send_template_task(phone: str, template_name: str, template_vars: dict, pais: str):
    """Envía la plantilla vía Meta Cloud API o Twilio según configuración."""
    from config.settings import get_settings
    settings = get_settings()

    try:
        if settings.whatsapp_token and settings.whatsapp_phone_number_id:
            await _send_via_meta(phone, template_name, template_vars)
        elif settings.twilio_account_sid:
            await _send_via_twilio(phone, template_vars)
        else:
            logger.warning("template_no_channel_configured", phone=phone)
    except Exception as e:
        logger.error("template_send_error", phone=phone, template=template_name, error=str(e))


async def _send_via_meta(phone: str, template_name: str, template_vars: dict):
    """Envía template via Meta Cloud API."""
    from integrations.whatsapp_meta import WhatsAppMetaClient
    wa = WhatsAppMetaClient()

    # Construir components con los parámetros del template
    # Meta usa variables posicionales {{1}}, {{2}}, etc.
    # Las plantillas deben estar creadas en Meta Business Manager
    components = []

    # Body parameters — depende de la plantilla. Enviamos todas las variables como body params
    body_params = [
        {"type": "text", "text": v}
        for v in template_vars.values()
        if v and str(v).strip()
    ]

    if body_params:
        components.append({"type": "body", "parameters": body_params})

    # Detectar idioma por país
    lang_map = {
        "Argentina": "es_AR", "México": "es_MX", "Chile": "es_CL",
        "Colombia": "es_CO", "Ecuador": "es_EC", "Uruguay": "es_AR",
    }
    language = lang_map.get(pais, "es_AR")

    await wa.send_template(
        to=phone,
        template_name=template_name,
        language=language,
        components=components if components else None,
    )
    logger.info("template_sent_via_meta", phone=phone, template=template_name)


async def _send_via_twilio(phone: str, template_vars: dict):
    """
    Twilio sandbox no soporta templates de Meta.
    Enviamos el mensaje de bienvenida como texto plano para testing.
    """
    from integrations.twilio_whatsapp import TwilioWhatsAppClient
    twilio = TwilioWhatsAppClient()

    nombre = template_vars.get("Nombre_completo", "")
    operador = template_vars.get("Nombre del agente", "")
    curso = template_vars.get("curso_nombre_plantilla", "")

    msg = f"Hola {nombre}! 👋 Soy {operador} del equipo MSK."
    if curso:
        msg += f" Te contacto por tu interés en *{curso}*."
    msg += " ¿Podemos hablar?"

    await twilio.send_text(phone, msg)
    logger.info("template_sent_via_twilio_text", phone=phone)


# ─── HSM Templates para el Inbox ─────────────────────────────────────────────

class HSMRequest(BaseModel):
    session_id: str
    template_name: str
    language: str = "es_AR"
    body_params: list[str] = []
    header_params: list[str] = []


class CreateTemplateRequest(BaseModel):
    name: str                                # nombre_unico_sin_espacios
    category: str = "MARKETING"              # MARKETING | UTILITY | AUTHENTICATION
    language: str = "es_AR"
    body_text: str                            # Texto del cuerpo con {{1}}, {{2}}...
    header_text: str = ""                     # Texto del header (opcional)
    header_type: str = ""                    # IMAGE | VIDEO | DOCUMENT (para media headers)
    header_handle: str = ""                  # Handle de Meta (retornado por upload-media)
    footer_text: str = ""                     # Texto del footer (opcional)
    buttons: list[dict] = []                  # Botones (opcional)


@router.get("/hsm")
async def list_hsm_templates(user: dict = Depends(get_current_user)):
    """
    Lista las plantillas aprobadas de Meta Business Manager.
    Se cachean en Redis 5 minutos para no saturar la API de Meta.
    """
    from memory.conversation_store import get_conversation_store
    store = await get_conversation_store()

    # Check cache
    cached = await store._redis.get("hsm_templates_cache")
    if cached:
        data = cached.decode() if isinstance(cached, bytes) else cached
        return {"templates": json.loads(data)}

    from integrations.whatsapp_meta import WhatsAppMetaClient
    wa = WhatsAppMetaClient()
    templates = await wa.get_templates()

    # Cache 5 minutos
    if templates:
        await store._redis.setex("hsm_templates_cache", 300, json.dumps(templates, ensure_ascii=False))

    return {"templates": templates}


@router.post("/send-hsm")
async def send_hsm(req: HSMRequest, background_tasks: BackgroundTasks, user: dict = Depends(get_current_user)):
    """
    Envía una plantilla HSM a un contacto de WhatsApp desde el inbox.
    Guarda el mensaje en la conversación y notifica via SSE.
    """
    if not req.session_id or not req.template_name:
        raise HTTPException(status_code=400, detail="session_id y template_name requeridos")

    # Verificar que la sesión existe
    from memory.conversation_store import get_conversation_store
    from config.constants import Channel
    store = await get_conversation_store()
    conv = await store.get_by_external(Channel.WHATSAPP, req.session_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")

    background_tasks.add_task(
        _send_hsm_task,
        phone=req.session_id,
        template_name=req.template_name,
        language=req.language,
        body_params=req.body_params,
        header_params=req.header_params,
        user_name=user.get("name", "Agente"),
    )

    return {"status": "ok", "template": req.template_name}


async def _send_hsm_task(
    phone: str,
    template_name: str,
    language: str,
    body_params: list[str],
    header_params: list[str],
    user_name: str,
):
    """Envía la plantilla HSM y guarda en la conversación."""
    try:
        from integrations.whatsapp_meta import WhatsAppMetaClient
        from memory.conversation_store import get_conversation_store
        from models.message import Message, MessageRole
        from config.constants import Channel
        import datetime

        wa = WhatsAppMetaClient()

        # Construir components
        components = []
        if header_params:
            components.append({
                "type": "header",
                "parameters": [{"type": "text", "text": p} for p in header_params],
            })
        if body_params:
            components.append({
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in body_params],
            })

        # Enviar
        result = await wa.send_template(
            to=phone,
            template_name=template_name,
            language=language,
            components=components if components else None,
        )

        # Construir preview del mensaje para el historial
        preview = f"[Plantilla HSM: {template_name}]"
        if body_params:
            preview += f"\nVariables: {', '.join(body_params)}"

        # Guardar en conversación
        store = await get_conversation_store()
        conv = await store.get_by_external(Channel.WHATSAPP, phone)
        if conv:
            msg = Message(
                role=MessageRole.ASSISTANT,
                content=preview,
                metadata={
                    "agent": "humano",
                    "sender_name": user_name,
                    "is_template": True,
                    "template_name": template_name,
                },
            )
            await store.append_message(conv, msg)

            # Broadcast SSE
            from api.inbox import broadcast_event
            broadcast_event({
                "type": "new_message",
                "session_id": phone,
                "role": "assistant",
                "content": preview,
                "sender_name": user_name,
                "timestamp": msg.timestamp.isoformat(),
                "channel": "whatsapp",
                "is_template": True,
            })

        logger.info("hsm_sent_from_inbox", phone=phone, template=template_name, agent=user_name)

    except Exception as e:
        logger.error("hsm_send_error", phone=phone, template=template_name, error=str(e))


# ─── Gestión de plantillas (CRUD) ────────────────────────────────────────────

@router.get("/hsm/all")
async def list_all_templates(user: dict = Depends(get_current_user)):
    """
    Lista TODAS las plantillas (no solo aprobadas) para la gestión admin.
    Incluye: APPROVED, PENDING, REJECTED.
    """
    from integrations.whatsapp_meta import WhatsAppMetaClient
    from config.settings import get_settings
    import httpx

    settings = get_settings()
    waba_id = settings.whatsapp_waba_id
    if not waba_id:
        return {"templates": [], "error": "WHATSAPP_WABA_ID no configurado"}

    wa = WhatsAppMetaClient()
    url = f"https://graph.facebook.com/v19.0/{waba_id}/message_templates"
    params = {"limit": 200}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, params=params, headers=wa._headers)
            r.raise_for_status()
            data = r.json()
            templates = data.get("data", [])

            import re
            result = []
            for t in templates:
                components = t.get("components", [])
                body_text = ""
                header_info = None
                footer_text = ""
                buttons = []
                for comp in components:
                    if comp["type"] == "BODY":
                        body_text = comp.get("text", "")
                    elif comp["type"] == "HEADER":
                        header_info = {"format": comp.get("format", "TEXT"), "text": comp.get("text", "")}
                    elif comp["type"] == "FOOTER":
                        footer_text = comp.get("text", "")
                    elif comp["type"] == "BUTTONS":
                        buttons = comp.get("buttons", [])

                body_vars = re.findall(r'\{\{(\d+)\}\}', body_text)
                result.append({
                    "id": t.get("id", ""),
                    "name": t["name"],
                    "language": t.get("language", ""),
                    "category": t.get("category", ""),
                    "status": t.get("status", ""),
                    "body": body_text,
                    "header": header_info,
                    "footer": footer_text,
                    "buttons": buttons,
                    "body_var_count": len(body_vars),
                    "rejected_reason": t.get("rejected_reason", ""),
                })
            return {"templates": result}
    except Exception as e:
        logger.error("list_all_templates_error", error=str(e))
        return {"templates": [], "error": str(e)}


@router.post("/hsm/create")
async def create_template(req: CreateTemplateRequest, user: dict = Depends(get_current_user)):
    """
    Crea una nueva plantilla en Meta Business Manager.
    La plantilla queda en estado PENDING hasta que Meta la apruebe (24-48h).
    """
    from api.auth import require_role
    # Solo admin puede crear plantillas
    if user.get("role") not in ("admin",):
        raise HTTPException(status_code=403, detail="Solo admin puede crear plantillas")

    from integrations.whatsapp_meta import WhatsAppMetaClient
    wa = WhatsAppMetaClient()

    try:
        result = await wa.create_template(
            name=req.name,
            category=req.category,
            language=req.language,
            body_text=req.body_text,
            header_text=req.header_text,
            header_type=req.header_type,
            header_handle=req.header_handle,
            footer_text=req.footer_text,
            buttons=req.buttons if req.buttons else None,
        )

        # Invalidar cache
        from memory.conversation_store import get_conversation_store
        store = await get_conversation_store()
        await store._redis.delete("hsm_templates_cache")

        from utils.audit import audit_log
        await audit_log(user.get("id", ""), user.get("name", ""), "template_created", req.name,
                        {"category": req.category, "language": req.language})

        return {"status": "ok", "template_id": result.get("id", ""), "result": result}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        error_msg = str(e)
        # Intentar extraer error de Meta
        if hasattr(e, 'response'):
            try:
                error_data = e.response.json()
                error_msg = error_data.get("error", {}).get("message", error_msg)
            except Exception:
                pass
        raise HTTPException(status_code=400, detail=f"Error de Meta: {error_msg}")


@router.post("/hsm/upload-media")
async def upload_header_media(request: Request, user: dict = Depends(get_current_user)):
    """
    Sube un archivo media para usar como header de plantilla.
    Sube a Meta via Resumable Upload API y retorna el header_handle.
    Acepta multipart/form-data con campo 'file'.
    """
    if user.get("role") not in ("admin",):
        raise HTTPException(status_code=403, detail="Solo admin puede subir media")

    form = await request.form()
    file = form.get("file")
    if not file:
        raise HTTPException(status_code=400, detail="Archivo requerido")

    from pathlib import Path
    import uuid as uuid_mod

    original_name = getattr(file, "filename", "file")
    content_type = getattr(file, "content_type", "application/octet-stream") or "application/octet-stream"
    ext = Path(original_name).suffix.lower() or ".bin"

    # Validar tipo de archivo
    allowed_types = {
        "image/jpeg", "image/png", "image/webp",
        "video/mp4", "video/3gpp",
        "application/pdf",
    }
    if content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"Tipo no soportado: {content_type}. Usa JPG, PNG, MP4 o PDF.")

    # Guardar temporalmente
    media_dir = Path(__file__).parent.parent / "media" / "templates"
    media_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid_mod.uuid4().hex[:12]}{ext}"
    filepath = media_dir / filename
    content = await file.read()

    # Max 16MB
    if len(content) > 16 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Archivo demasiado grande (max 16MB)")

    filepath.write_bytes(content)

    try:
        from integrations.whatsapp_meta import WhatsAppMetaClient
        wa = WhatsAppMetaClient()
        handle = await wa.upload_media_for_template(str(filepath), content_type)

        # Determinar tipo de media
        media_type = "image"
        if content_type.startswith("video/"):
            media_type = "video"
        elif content_type == "application/pdf":
            media_type = "document"

        # URL pública del archivo (como backup/preview)
        public_url = f"/media/templates/{filename}"

        return {
            "status": "ok",
            "handle": handle,
            "media_type": media_type,
            "filename": original_name,
            "url": public_url,
        }
    except Exception as e:
        # Limpiar archivo si falla el upload a Meta
        filepath.unlink(missing_ok=True)
        logger.error("template_media_upload_error", error=str(e))
        raise HTTPException(status_code=400, detail=f"Error al subir a Meta: {str(e)}")


@router.delete("/hsm/{template_name}")
async def delete_template_endpoint(template_name: str, user: dict = Depends(get_current_user)):
    """
    Elimina una plantilla de Meta Business Manager.
    Solo admin puede eliminar plantillas.
    """
    if user.get("role") not in ("admin",):
        raise HTTPException(status_code=403, detail="Solo admin puede eliminar plantillas")

    from integrations.whatsapp_meta import WhatsAppMetaClient
    wa = WhatsAppMetaClient()

    try:
        result = await wa.delete_template(template_name)

        # Invalidar cache
        from memory.conversation_store import get_conversation_store
        store = await get_conversation_store()
        await store._redis.delete("hsm_templates_cache")

        from utils.audit import audit_log
        await audit_log(user.get("id", ""), user.get("name", ""), "template_deleted", template_name)

        return {"status": "ok", "result": result}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error de Meta: {str(e)}")
