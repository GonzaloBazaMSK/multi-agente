"""
Dramatiq task queue — offload de trabajo pesado a workers separados.

Qué hace y por qué:
  - Hoy las llamadas al LangGraph supervisor corren en el worker de
    uvicorn que atiende el request. Un agente lento (Zoho tardío,
    OpenAI saturada) bloquea ese worker hasta que responde. Con 2
    workers uvicorn y 3 conversaciones lentas, el server no atiende
    nada más.
  - Con dramatiq, el webhook encola el trabajo y responde 200
    inmediatamente. Workers dramatiq (proceso separado) procesan
    asincrónicamente, con backpressure real y retry.

Qué se encola:
  - Procesar mensajes de WhatsApp/Botmaker/Twilio (webhook → background).
  - Sync Zoho pesados (actualización de cursadas, histórico).
  - TTS/STT — no bloquear el UI por 3-5s mientras openai procesa.

Qué NO se encola:
  - Requests del frontend en caliente (inbox, search) — el UI necesita
    respuesta sincrónica.
  - AI insights on-demand — el usuario abre la conv y quiere verlo ya.

Levantar workers en prod:
    docker compose run -d --name multiagente-worker-1 api \\
        dramatiq utils.tasks -p 2 -t 4

-p 2 procesos × -t 4 threads cada uno = 8 tareas concurrentes.
"""

from __future__ import annotations

import asyncio
import os

import dramatiq
import structlog
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import AgeLimit, Callbacks, Retries, ShutdownNotifications, TimeLimit

logger = structlog.get_logger(__name__)

# Broker Redis — reusa la misma conexión que la app.
# Separamos db para no mezclar con conversation cache.
_redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
# Usamos db 1 para tasks (la app usa db 0).
if _redis_url.endswith("/0"):
    _broker_url = _redis_url[:-2] + "/1"
else:
    _broker_url = _redis_url

_broker = RedisBroker(url=_broker_url)
_broker.add_middleware(AgeLimit(max_age=60 * 60 * 1000))  # 1h máx en cola
_broker.add_middleware(TimeLimit(time_limit=5 * 60 * 1000))  # 5min por task
_broker.add_middleware(Retries(max_retries=3, min_backoff=1000, max_backoff=60_000))
_broker.add_middleware(Callbacks())
_broker.add_middleware(ShutdownNotifications())
dramatiq.set_broker(_broker)


def _run_async(coro):
    """Helper para correr corutinas desde dramatiq actors síncronos."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─── Actors ──────────────────────────────────────────────────────────────────
# Cada actor es una tarea ejecutable. El `send(...)` la encola, retorna
# inmediato. El worker dramatiq la ejecuta asincronicamente.


@dramatiq.actor(queue_name="whatsapp", max_retries=3)
def process_whatsapp_meta_task(payload: dict) -> None:
    """Procesa un mensaje entrante de WhatsApp Meta.

    Antes se llamaba con `background_tasks.add_task()` dentro del endpoint
    — corría en el mismo worker uvicorn, bloqueando. Ahora va a workers
    dramatiq separados con backpressure real.
    """
    from channels.whatsapp_meta import process_whatsapp_message

    try:
        _run_async(process_whatsapp_message(payload))
    except Exception as e:
        logger.error("wa_meta_task_failed", error=str(e))
        raise  # que dramatiq lo reintente


@dramatiq.actor(queue_name="whatsapp", max_retries=3)
def process_botmaker_task(payload: dict) -> None:
    from channels.whatsapp import process_whatsapp_message

    try:
        _run_async(process_whatsapp_message(payload))
    except Exception as e:
        logger.error("botmaker_task_failed", error=str(e))
        raise


@dramatiq.actor(queue_name="whatsapp", max_retries=3)
def process_twilio_task(form_data: dict) -> None:
    from channels.twilio_whatsapp import process_twilio_message

    try:
        _run_async(process_twilio_message(form_data))
    except Exception as e:
        logger.error("twilio_task_failed", error=str(e))
        raise


@dramatiq.actor(queue_name="payments", max_retries=5)
def handle_mp_payment_task(payment_id: str) -> None:
    """Handle async del webhook MercadoPago — verifica pago y actualiza Zoho."""
    from api.webhooks import _handle_mp_payment

    _run_async(_handle_mp_payment(payment_id))


@dramatiq.actor(queue_name="payments", max_retries=5)
def handle_rebill_event_task(payload: dict, event_type: str) -> None:
    from api.webhooks import _handle_rebill_event

    _run_async(_handle_rebill_event(payload, event_type))
