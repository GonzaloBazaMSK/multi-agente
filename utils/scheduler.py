"""
Scheduler persistente usando APScheduler con jobstore en Redis.

Corre tareas autónomas del sistema:
  - Retargeting cycle: cada hora escanea leads inactivos y decide acciones
  - Auto-retry descartados: diariamente a las 10am busca leads descartados
    hace >20 días para reactivarlos.

Los jobs persisten en Redis (no se pierden en restart del container).
"""

from __future__ import annotations

import structlog
from apscheduler.jobstores.redis import RedisJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import get_settings

logger = structlog.get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None


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

    s.start()
    logger.info("scheduler_started", jobs=[j.id for j in s.get_jobs()])


async def _run_courses_sync() -> None:
    """Wrapper para APScheduler: sincroniza todos los países habilitados."""
    from api.admin_courses import ENABLED_COUNTRIES
    from integrations import msk_courses

    for c in ENABLED_COUNTRIES:
        try:
            await msk_courses.sync_country(c, prune=True)
        except Exception as e:
            logger.error("courses_sync_job_failed", country=c, error=str(e))


async def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("scheduler_shutdown")
    _scheduler = None
