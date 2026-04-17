"""
Webhooks externos:
- POST /webhook/botmaker  → mensajes de WhatsApp
- POST /webhook/mercadopago → notificaciones de pago MP
- POST /webhook/rebill → notificaciones de pago Rebill
- POST /webhook/verificar-pago-inmediato → verifica pago Zoho y responde via Botmaker
- POST /webhook/monitorear-pago → loop async 15min/90min post-link-rebill
"""
import asyncio
import hashlib
import hmac
import json
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import Response
from integrations.botmaker import BotmakerClient
from integrations.zoho.sales_orders import ZohoSalesOrders
from integrations.zoho.area_cobranzas import ZohoAreaCobranzas
from integrations.notifications import notify_payment_confirmed
from channels.whatsapp import process_whatsapp_message
from channels.whatsapp_meta import process_whatsapp_message as process_wa_meta
from channels.twilio_whatsapp import process_twilio_message as process_wa_twilio
from config.settings import get_settings
import structlog

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhooks"])


# ─── Meta WhatsApp Cloud API ──────────────────────────────────────────────────

@router.get("/whatsapp")
async def whatsapp_verify(request: Request):
    """
    Verificación del webhook de Meta.
    Meta hace GET con hub.challenge para confirmar que la URL es válida.
    """
    settings = get_settings()
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        logger.info("whatsapp_webhook_verified")
        return int(challenge)

    logger.warning("whatsapp_verify_failed", token=token)
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Recibe mensajes entrantes de WhatsApp directo desde Meta Cloud API.
    Verifica la firma X-Hub-Signature-256 y procesa en background.
    """
    settings = get_settings()
    body = await request.body()

    # Verificar firma de Meta
    signature = request.headers.get("X-Hub-Signature-256", "")
    if settings.whatsapp_app_secret:
        if not signature:
            logger.warning("whatsapp_meta_missing_signature")
            raise HTTPException(status_code=401, detail="Missing signature")
        expected = "sha256=" + hmac.new(
            settings.whatsapp_app_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            logger.warning("whatsapp_meta_invalid_signature")
            raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Responder 200 inmediatamente — Meta requiere respuesta en < 5s
    # Detectar si es un mensaje o un status update
    try:
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})

        if "statuses" in value:
            # Es un delivery status update (sent/delivered/read/failed)
            background_tasks.add_task(_handle_wa_status, value["statuses"])
        elif "messages" in value:
            background_tasks.add_task(process_wa_meta, payload)
    except Exception:
        background_tasks.add_task(process_wa_meta, payload)

    return {"status": "ok"}


async def _handle_wa_status(statuses: list):
    """Procesa status updates de Meta (sent/delivered/read/failed)."""
    try:
        from api.inbox import broadcast_event
        from memory.conversation_store import get_conversation_store
        store = await get_conversation_store()

        for status in statuses:
            phone = status.get("recipient_id", "")
            msg_id = status.get("id", "")
            st = status.get("status", "")  # sent, delivered, read, failed
            ts = status.get("timestamp", "")

            if not phone or not st:
                continue

            # Guardar último status por message_id
            if msg_id:
                await store._redis.setex(
                    f"wa_status:{msg_id}",
                    86400,  # 24h
                    json.dumps({"status": st, "timestamp": ts, "phone": phone})
                )

            # Broadcast al inbox para actualizar UI
            broadcast_event({
                "type": "delivery_status",
                "session_id": phone,
                "message_id": msg_id,
                "status": st,
                "timestamp": ts,
            })

            if st == "failed":
                errors = status.get("errors", [])
                error_msg = errors[0].get("title", "Unknown error") if errors else "Unknown"
                logger.warning("wa_delivery_failed", phone=phone, msg_id=msg_id, error=error_msg)

    except Exception as e:
        logger.warning("wa_status_handler_error", error=str(e))


@router.post("/botmaker")
async def botmaker_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Recibe mensajes entrantes de WhatsApp vía Botmaker.
    Botmaker envía POST por cada mensaje del usuario.
    """
    body = await request.body()

    # Verificar firma si está configurada
    botmaker = BotmakerClient()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if botmaker._webhook_secret:
        if not signature:
            logger.warning("botmaker_missing_signature")
            raise HTTPException(status_code=401, detail="Missing signature")
        if not botmaker.verify_signature(body, signature):
            logger.warning("botmaker_invalid_signature")
            raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Ignorar eventos que no son mensajes de usuario
    msg_type = payload.get("type", payload.get("messageType", ""))
    if msg_type not in ("text", "TEXT", "message", ""):
        logger.debug("botmaker_non_text_event", type=msg_type)
        return {"status": "ignored"}

    # Procesar en background para responder 200 rápido a Botmaker
    background_tasks.add_task(process_whatsapp_message, payload)
    return {"status": "ok"}


@router.post("/mercadopago")
async def mercadopago_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Notificaciones de pago de MercadoPago (IPN).
    """
    params = dict(request.query_params)
    body = await request.body()

    # Verificar firma HMAC
    settings = get_settings()
    if settings.mp_webhook_secret:
        manifest = (
            f"id:{params.get('data.id', '')};request-id:{request.headers.get('x-request-id', '')};ts:{params.get('ts', '')};"
        )
        expected = hmac.new(
            settings.mp_webhook_secret.encode(),
            manifest.encode(),
            hashlib.sha256,
        ).hexdigest()
        received = request.headers.get("x-signature", "").split(",")
        ts_hash = {p.split("=")[0]: p.split("=")[1] for p in received if "=" in p}
        if ts_hash.get("v1") != expected:
            logger.warning("mp_invalid_signature")
            raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {}

    topic = params.get("topic") or payload.get("type", "")
    if topic != "payment":
        return {"status": "ignored"}

    background_tasks.add_task(_handle_mp_payment, params.get("id") or payload.get("data", {}).get("id", ""))
    return {"status": "ok"}


async def _handle_mp_payment(payment_id: str):
    if not payment_id:
        return
    from integrations.payments.mercadopago import MercadoPagoClient
    mp = MercadoPagoClient()
    try:
        payment = await mp.verify_webhook(payment_id)
        status = payment.get("status", "")
        external_ref = payment.get("external_reference", "")

        if status == "approved":
            orders = ZohoSalesOrders()
            # external_reference es el zoho order_id o course_id_email
            if external_ref and "_" not in external_ref:
                # Es un order_id de Zoho
                await orders.update_payment_status(external_ref, "Pago confirmado", payment_id)

            payer = payment.get("payer", {})
            await notify_payment_confirmed(
                user_name=payer.get("email", ""),
                course_name=payment.get("description", "Curso"),
                amount=float(payment.get("transaction_amount", 0)),
                currency=payment.get("currency_id", "ARS"),
                order_id=external_ref,
            )
            logger.info("mp_payment_approved", payment_id=payment_id, ref=external_ref)
    except Exception as e:
        logger.error("mp_payment_handler_error", error=str(e), payment_id=payment_id)


@router.post("/rebill")
async def rebill_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Notificaciones de Rebill (suscripciones/pagos recurrentes).
    """
    body = await request.body()
    settings = get_settings()

    # Verificar firma Rebill si está configurada
    if settings.rebill_api_key:
        signature = request.headers.get("x-rebill-signature", "")
        if not signature:
            logger.warning("rebill_missing_signature")
            raise HTTPException(status_code=401, detail="Missing signature")
        expected = hmac.new(
            settings.rebill_api_key.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            logger.warning("rebill_invalid_signature")
            raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = payload.get("event", payload.get("type", ""))
    logger.info("rebill_webhook_received", event=event_type)

    background_tasks.add_task(_handle_rebill_event, payload, event_type)
    return {"status": "ok"}


async def _handle_rebill_event(payload: dict, event_type: str):
    try:
        orders = ZohoSalesOrders()
        subscription = payload.get("subscription", payload.get("data", {}))
        external_id = subscription.get("external_id", "")
        subscription_id = subscription.get("id", "")

        if event_type in ("subscription.payment.succeeded", "payment.succeeded"):
            if external_id:
                await orders.update_payment_status(external_id, "Pago confirmado", subscription_id)
            customer = subscription.get("customer", {})
            await notify_payment_confirmed(
                user_name=f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip(),
                course_name=subscription.get("plan", {}).get("name", "Curso"),
                amount=float(subscription.get("amount", 0)),
                currency=subscription.get("currency", "ARS"),
                order_id=external_id,
            )
            logger.info("rebill_payment_succeeded", subscription_id=subscription_id)

        elif event_type in ("subscription.payment.failed", "payment.failed"):
            if external_id:
                await orders.update_payment_status(external_id, "Pago fallido")
            logger.warning("rebill_payment_failed", subscription_id=subscription_id)

        elif event_type in ("subscription.cancelled",):
            if external_id:
                await orders.update_payment_status(external_id, "Cancelado")
            logger.info("rebill_subscription_cancelled", subscription_id=subscription_id)

    except Exception as e:
        logger.error("rebill_event_handler_error", error=str(e), event=event_type)


# ─── Verificación inmediata de pago ──────────────────────────────────────────

@router.post("/verificar-pago-inmediato")
async def verificar_pago_inmediato(request: Request, background_tasks: BackgroundTasks):
    """
    Triggered cuando el alumno dice "ya pagué".
    Lee Redis, consulta Zoho, responde via Botmaker y limpia Redis.
    """
    body = await request.json()
    phone = body.get("phone", "")
    pais = body.get("pais", "Argentina")
    if not phone:
        raise HTTPException(status_code=400, detail="phone required")
    background_tasks.add_task(_verificar_pago_task, phone, pais)
    return {"status": "ok"}


async def _verificar_pago_task(phone: str, pais: str):
    try:
        from memory.conversation_store import get_conversation_store
        store = await get_conversation_store()
        r = store._redis

        raw = await r.get(f"rebill_pendiente:{phone}")
        if not raw:
            logger.info("verificar_pago_no_pendiente", phone=phone)
            return

        data = json.loads(raw)
        cobranza_id = data.get("cobranzaId", "")

        zoho = ZohoAreaCobranzas()
        ficha = await zoho.get_by_id(cobranza_id)
        pagado = ficha.get("pagado", False)

        botmaker = BotmakerClient()
        channel_id = _get_channel_id(pais)

        if pagado:
            msg = "¡Tu pago fue confirmado exitosamente! Ya tenés acceso completo a tu programa. 🎉"
            await botmaker.send_message_to_channel(channel_id, phone, msg)
            await r.delete(f"rebill_pendiente:{phone}")
            await r.delete(f"followup_pendiente:{phone}")
            await zoho.set_tag(cobranza_id, "Pago recibido")
            logger.info("pago_verificado_confirmado", phone=phone)
        else:
            msg = "Aún no vemos tu pago reflejado en el sistema. Puede tardar unos minutos. ¿Podés enviarnos el comprobante? 📄"
            await botmaker.send_message_to_channel(channel_id, phone, msg)
            logger.info("pago_verificado_no_encontrado", phone=phone)
    except Exception as e:
        logger.error("verificar_pago_error", phone=phone, error=str(e))


# ─── Monitoreo de pago async (15min / 90min) ─────────────────────────────────

@router.post("/monitorear-pago")
async def monitorear_pago(request: Request, background_tasks: BackgroundTasks):
    """
    Triggered cuando el bot envía un link Rebill.
    Espera 15 min, verifica, si no pagó espera 90 min más y envía followup.
    """
    body = await request.json()
    phone = body.get("phone", "")
    pais = body.get("pais", "Argentina")
    cobranza_id = body.get("cobranzaId", "")
    if not phone or not cobranza_id:
        raise HTTPException(status_code=400, detail="phone and cobranzaId required")
    background_tasks.add_task(_monitorear_pago_task, phone, pais, cobranza_id)
    return {"status": "ok"}


async def _monitorear_pago_task(phone: str, pais: str, cobranza_id: str):
    try:
        from memory.conversation_store import get_conversation_store
        store = await get_conversation_store()
        r = store._redis

        # Esperar 15 minutos
        await asyncio.sleep(15 * 60)

        raw = await r.get(f"rebill_pendiente:{phone}")
        if not raw:
            logger.info("monitorear_pago_already_handled", phone=phone)
            return

        zoho = ZohoAreaCobranzas()
        ficha = await zoho.get_by_id(cobranza_id)
        botmaker = BotmakerClient()
        channel_id = _get_channel_id(pais)

        if ficha.get("pagado", False):
            msg = "¡Tu pago fue confirmado exitosamente! Ya tenés acceso completo a tu programa. 🎉"
            await botmaker.send_message_to_channel(channel_id, phone, msg)
            await r.delete(f"rebill_pendiente:{phone}")
            await r.delete(f"followup_pendiente:{phone}")
            await zoho.set_tag(cobranza_id, "Pago recibido")
            return

        # No pagó — esperar 75 minutos más (total 90 min desde el link)
        await asyncio.sleep(75 * 60)

        raw = await r.get(f"rebill_pendiente:{phone}")
        if not raw:
            return

        msg = (
            "Hola, te recordamos que tu pago aún está pendiente. "
            "El enlace que te enviamos sigue vigente. "
            "Si tenés alguna duda, estamos aquí para ayudarte. 😊"
        )
        await botmaker.send_message_to_channel(channel_id, phone, msg)
        await zoho.set_tag(cobranza_id, "Sin pago recibido")
        logger.info("monitorear_pago_followup_enviado", phone=phone)

        # Marcar key con TTL de 24h para auto-limpieza (en vez de sleep 22h)
        await r.expire(f"rebill_pendiente:{phone}", 86400)

    except Exception as e:
        logger.error("monitorear_pago_error", phone=phone, error=str(e))


# ─── Twilio WhatsApp ──────────────────────────────────────────────────────────

@router.post("/twilio")
async def twilio_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Recibe mensajes de WhatsApp vía Twilio Sandbox / API.
    Twilio envía form-urlencoded POST por cada mensaje entrante.
    No requiere verificación de firma en sandbox (opcional en producción).
    """
    form_data = await request.form()
    data = dict(form_data)

    logger.info("twilio_webhook_received", from_=data.get("From", ""), body=data.get("Body", "")[:50])

    # Twilio espera respuesta TwiML — respondemos vacío para no duplicar mensajes
    # El mensaje real lo enviamos con la API REST desde el background task
    background_tasks.add_task(process_wa_twilio, data)

    # Respuesta TwiML vacía (no queremos que Twilio envíe nada por su cuenta)
    return Response(
        content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        media_type="application/xml",
    )


def _get_channel_id(pais: str) -> str:
    channel_map = {
        "Argentina": "medicalscientificknowledge-whatsapp-5491139007715",
        "Colombia":  "medicalscientificknowledge-whatsapp-5753161349",
        "Mexico":    "medicalscientificknowledge-whatsapp-5215599904940",
        "Ecuador":   "medicalscientificknowledge-whatsapp-593998158115",
        "Chile":     "medicalscientificknowledge-whatsapp-56224875300",
        "Uruguay":   "medicalscientificknowledge-whatsapp-5491152170771",
    }
    return channel_map.get(pais, channel_map["Argentina"])
