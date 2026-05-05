"""
Scheduler persistente usando APScheduler con jobstore en Redis.

Corre tareas autónomas del sistema:
  - Retargeting cycle: cada hora escanea leads inactivos y decide acciones
  - Auto-retry descartados: diariamente a las 10am busca leads descartados
    hace >20 días para reactivarlos.

Los jobs persisten en Redis (no se pierden en restart del container).

Leadership lock (Redis):
  Solo 1 worker corre el scheduler. El worker que lo gana setea la key
  `scheduler:lock` con su WORKER_ID y TTL 120s. Mientras esté vivo, un
  heartbeat job dentro del scheduler renueva el TTL cada 60s. Si el worker
  muere, el lock expira en ≤120s y otro worker lo agarra (todos los workers
  corren `try_acquire_scheduler_lock` periódicamente desde main.py).

  Esto cierra el bug donde el worker se reiniciaba dentro del TTL y el
  nuevo no podía agarrar el lock → scheduler muerto hasta el próximo deploy.
"""

from __future__ import annotations

import os
import uuid

import structlog
from apscheduler.jobstores.redis import RedisJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import get_settings

logger = structlog.get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None

# Identidad de este worker para el leadership lock. Hostname + PID es estable
# durante la vida del proceso pero único por instancia.
WORKER_ID = f"{os.environ.get('HOSTNAME', 'worker')}-{os.getpid()}-{uuid.uuid4().hex[:6]}"

SCHEDULER_LOCK_KEY = "scheduler:lock"
SCHEDULER_LOCK_TTL = 120  # segundos — heartbeat lo renueva cada 60s
SCHEDULER_HEARTBEAT_INTERVAL = 60


def _build_jobstore() -> dict:
    """Redis jobstore usando la misma URL que el app."""
    settings = get_settings()
    # APScheduler Redis jobstore accepts host/port/password/db separately.
    # Parseamos la REDIS_URL (redis://:pass@host:port/db)
    from urllib.parse import urlparse

    url = urlparse(settings.redis_url)
    return {
        "default": RedisJobStore(
            host=url.hostname or "redis",
            port=url.port or 6379,
            password=url.password or None,
            db=int((url.path or "/0").strip("/")) if url.path else 0,
            jobs_key="aps:jobs",
            run_times_key="aps:run_times",
        )
    }


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(
            jobstores=_build_jobstore(),
            timezone="America/Argentina/Buenos_Aires",
        )
    return _scheduler


async def start_scheduler() -> None:
    """Inicia el scheduler y registra los jobs recurrentes (idempotente)."""
    s = get_scheduler()
    if s.running:
        return

    # Registrar jobs (replace_existing=True para que cada deploy actualice la config)
    from utils.autonomous_tasks import run_auto_retry_cycle, run_retargeting_cycle

    s.add_job(
        run_retargeting_cycle,
        trigger=IntervalTrigger(hours=1),
        id="retargeting_cycle",
        name="Retargeting cycle (decide HSM por lead)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    s.add_job(
        run_auto_retry_cycle,
        trigger=CronTrigger(hour=10, minute=0),  # 10am local
        id="auto_retry_cycle",
        name="Reactivar leads descartados hace >20 días",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Sincronización diaria del catálogo de cursos desde el WP headless.
    # 3:30am local → baja tráfico, precios del WP ya consolidados si cambian a fin de mes.
    s.add_job(
        _run_courses_sync,
        trigger=CronTrigger(hour=3, minute=30),
        id="courses_sync",
        name="Sincronizar catálogo de cursos MSK (todos los países habilitados)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Detectar convs sin respuesta humana >2h y notificar al agente asignado.
    # Cada 15 min; dedup por conv en Redis 4h (ver utils/stale_conversations.py).
    from utils.stale_conversations import run_stale_conversations_check

    s.add_job(
        run_stale_conversations_check,
        trigger=IntervalTrigger(minutes=15),
        id="stale_conversations_check",
        name="Notificar convs sin respuesta humana >2h",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Digest diario de notificaciones — 9:00 AR (hora local del scheduler).
    # Solo envía si RESEND_API_KEY o EMAIL_SMTP_* están configurados; sino
    # logea advertencia y no hace nada (el job queda registrado para cuando
    # se configure el provider sin requerir redeploy del scheduler).
    from utils.email_digest import run_email_digest

    s.add_job(
        run_email_digest,
        trigger=CronTrigger(hour=9, minute=0),
        id="email_digest",
        name="Digest diario de notificaciones sin leer",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Heartbeat del leadership lock — renueva el TTL cada 60s. Si este worker
    # muere, el lock expira en <=120s y otro worker lo agarra. Sin este job,
    # el lock duraba 1h fijo y un restart dentro de la ventana mataba el
    # scheduler hasta el próximo deploy.
    s.add_job(
        _heartbeat_scheduler_lock,
        trigger=IntervalTrigger(seconds=SCHEDULER_HEARTBEAT_INTERVAL),
        id="scheduler_lock_heartbeat",
        name="Renueva el TTL del leadership lock",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Watchdog del courses_sync — cada 1h chequea que la última sincronización
    # haya corrido en las últimas 26h (el job es 3:30am diario). Si pasaron 2
    # noches sin sync (caso real reportado), logea ERROR. Esto NO restaura el
    # job — es alerta para que veamos en Sentry/logs.
    s.add_job(
        _watchdog_courses_sync,
        trigger=IntervalTrigger(hours=1),
        id="courses_sync_watchdog",
        name="Alerta si courses_sync no corrió en >26h",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    s.start()
    logger.info("scheduler_started", worker=WORKER_ID, jobs=[j.id for j in s.get_jobs()])


async def try_acquire_scheduler_lock() -> bool:
    """
    Intenta agarrar el leadership lock. Devuelve True si lo agarró (o lo
    sigue teniendo este mismo worker).

    Llamado desde main.py: una vez al startup + un loop background cada 30s
    para que si el holder muere, otro worker lo levante en ≤150s.
    """
    from memory.conversation_store import get_conversation_store

    try:
        store = await get_conversation_store()
        # nx=True solo gana si la key no existe. Si la tenemos nosotros (este
        # worker fue el holder y heartbeateó), también devuelve None pero
        # nuestro scheduler ya está corriendo — chequeamos con get.
        got = await store._redis.set(
            SCHEDULER_LOCK_KEY, WORKER_ID, ex=SCHEDULER_LOCK_TTL, nx=True
        )
        if got:
            return True
        # El lock existe — si lo tenemos nosotros (worker reiniciado dentro
        # del TTL), sobre-escribimos para extender. Si lo tiene otro worker,
        # devolvemos False.
        current = await store._redis.get(SCHEDULER_LOCK_KEY)
        current_str = current.decode() if isinstance(current, bytes) else current
        if current_str == WORKER_ID:
            await store._redis.expire(SCHEDULER_LOCK_KEY, SCHEDULER_LOCK_TTL)
            return True
        return False
    except Exception as e:
        logger.warning("scheduler_lock_acquire_failed", error=str(e))
        return False


async def _heartbeat_scheduler_lock() -> None:
    """Renueva el TTL del leadership lock. Corre cada 60s mientras el
    scheduler de este worker esté activo."""
    from memory.conversation_store import get_conversation_store

    try:
        store = await get_conversation_store()
        # Verificamos que el lock siga siendo nuestro antes de renovar — si
        # otro worker lo robó (raro pero posible en split-brain), no
        # interferimos.
        current = await store._redis.get(SCHEDULER_LOCK_KEY)
        current_str = current.decode() if isinstance(current, bytes) else current
        if current_str == WORKER_ID:
            await store._redis.expire(SCHEDULER_LOCK_KEY, SCHEDULER_LOCK_TTL)
        else:
            logger.warning(
                "scheduler_lock_lost", current_holder=current_str, this_worker=WORKER_ID
            )
    except Exception as e:
        logger.warning("scheduler_heartbeat_failed", error=str(e))


async def _watchdog_courses_sync() -> None:
    """Verifica que `courses_sync` haya corrido en las últimas 26h. Loguea
    ERROR si no — Sentry lo captura."""
    from datetime import UTC, datetime, timedelta

    from memory.conversation_store import get_conversation_store

    try:
        store = await get_conversation_store()
        last = await store._redis.get("courses_sync:last_run")
        if last is None:
            logger.warning("courses_sync_watchdog_no_record")
            return
        last_str = last.decode() if isinstance(last, bytes) else last
        last_dt = datetime.fromisoformat(last_str)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=UTC)
        age = datetime.now(UTC) - last_dt
        if age > timedelta(hours=26):
            logger.error(
                "courses_sync_overdue",
                last_run=last_str,
                hours_since=int(age.total_seconds() / 3600),
            )
    except Exception as e:
        logger.warning("courses_sync_watchdog_failed", error=str(e))


async def _run_courses_sync() -> None:
    """Wrapper para APScheduler: sincroniza todos los países habilitados.
    Persiste timestamp en Redis para el watchdog."""
    from datetime import UTC, datetime

    from api.admin_courses import ENABLED_COUNTRIES
    from integrations import msk_courses
    from memory.conversation_store import get_conversation_store

    for c in ENABLED_COUNTRIES:
        try:
            await msk_courses.sync_country(c, prune=True)
        except Exception as e:
            logger.error("courses_sync_job_failed", country=c, error=str(e))

    # Marca de éxito (al menos parcial) — el watchdog lee este key.
    try:
        store = await get_conversation_store()
        await store._redis.set(
            "courses_sync:last_run", datetime.now(UTC).isoformat()
        )
    except Exception as e:
        logger.warning("courses_sync_timestamp_failed", error=str(e))


async def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("scheduler_shutdown")
    _scheduler = None
