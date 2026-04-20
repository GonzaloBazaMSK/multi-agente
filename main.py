"""
Punto de entrada de la aplicación FastAPI.
Multi-agente para empresa de cursos médicos.
Canales: WhatsApp (Botmaker) + Widget web embebible.
"""

from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from config.settings import get_settings

# ─── Sentry (error tracking) — init BEFORE FastAPI() so the SDK wraps it ────
_settings_boot = get_settings()
if _settings_boot.sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=_settings_boot.sentry_dsn,
        environment=_settings_boot.app_env,
        traces_sample_rate=_settings_boot.sentry_traces_sample_rate,
        send_default_pii=False,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            StarletteIntegration(transaction_style="endpoint"),
            AsyncioIntegration(),
        ],
    )

from api.admin import router as admin_router
from api.admin_courses import router as admin_courses_router
from api.admin_prompts import router as admin_prompts_router
from api.auth import router as auth_router
from api.autonomous import router as autonomous_router
from api.customer_auth import router as customer_auth_router
from api.inbox_api import router as inbox_api_router
from api.notifications import router as notifications_router
from api.redis_admin import router as redis_admin_router
from api.reports import router as reports_router
from api.templates import router as templates_router
from api.test_agent import router as test_agent_router
from api.voice import router as voice_router
from api.webhooks import router as webhooks_router
from api.widget import router as widget_router
from api.widget_config import router as widget_config_router

# Structlog pipeline con PII scrubber. Aplica a TODOS los loggers del
# backend — previene que passwords/tokens/emails aparezcan en stdout,
# Sentry o cualquier destino futuro (Loki/Datadog). Ver utils/log_processors.
from utils.log_processors import pii_scrubber  # noqa: E402

# Rate limiter global — usa `user_or_ip` como key_func para dar cuota
# separada a cada sesión autenticada (no solo IP agregada). Los límites
# por endpoint viven en utils/rate_limits.py.
from utils.rate_limits import limiter  # noqa: E402

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        pii_scrubber,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown de la app."""
    settings = get_settings()

    # Block startup if default secret key is used in production
    if settings.is_production and settings.app_secret_key == "change-this-secret":
        raise RuntimeError("CRITICAL: app_secret_key must be changed in production")

    # Validate critical environment variables
    if not settings.openai_api_key:
        logger.critical("OPENAI_API_KEY is required")
        raise RuntimeError("OPENAI_API_KEY must be set")
    if not settings.database_url:
        logger.warning("DATABASE_URL not set — running without Postgres persistence")

    logger.info(
        "app_startup",
        env=settings.app_env,
        model=settings.openai_model,
    )
    # Pre-warming: inicializar el store de Redis
    from memory.conversation_store import get_conversation_store

    try:
        await get_conversation_store()
        logger.info("redis_connected")
    except Exception as e:
        logger.warning("redis_connection_failed", error=str(e))

    # Postgres: pool + schema idempotente
    from memory import postgres_store

    if postgres_store.is_enabled():
        try:
            await postgres_store.ensure_schema()
            logger.info("postgres_connected")
        except Exception as e:
            logger.error("postgres_init_failed", error=str(e))

    # Iniciar listener de Redis Pub/Sub para SSE cross-worker
    import asyncio

    from utils.realtime import start_pubsub_listener

    pubsub_task = asyncio.create_task(start_pubsub_listener())
    logger.info("pubsub_listener_started")

    # Scheduler autónomo: retargeting cycle cada 1h + auto-retry diario 10am.
    # Solo 1 worker debe correr el scheduler — usamos lock en Redis.
    try:
        from memory.conversation_store import get_conversation_store

        store = await get_conversation_store()
        got_lock = await store._redis.set("scheduler:lock", "1", ex=3600, nx=True)
        if got_lock:
            from utils.scheduler import start_scheduler

            await start_scheduler()
            logger.info("autonomous_scheduler_active")
        else:
            logger.info("autonomous_scheduler_skipped_already_running")
    except Exception as e:
        logger.warning("scheduler_start_failed", error=str(e))

    yield

    # ── Graceful shutdown ────────────────────────────────────────────────
    logger.info("app_shutdown_start")

    # 1. Cancelar el listener de Pub/Sub (background task) — dejamos que
    # drene mensajes en vuelo durante 2s antes de forzar.
    pubsub_task.cancel()
    try:
        await asyncio.wait_for(pubsub_task, timeout=2.0)
    except (TimeoutError, asyncio.CancelledError):
        pass

    # 2. Cerrar SSE clients conectados (vacía sus colas → event_gen sale
    # del while y el StreamingResponse termina). Sin esto, clientes
    # abiertos mantienen el worker vivo bloqueando shutdown.
    try:
        from utils.realtime import _sse_clients

        for q in list(_sse_clients):
            try:
                q.put_nowait({"type": "server_shutdown"})
            except asyncio.QueueFull:
                pass
        _sse_clients.clear()
    except Exception:
        pass

    # 3. Shutdown del scheduler — APScheduler tiene su propio stop path.
    try:
        from utils.scheduler import shutdown_scheduler

        await shutdown_scheduler()
    except Exception:
        pass

    # 4. Cerrar pool Postgres si estaba abierto.
    try:
        from memory import postgres_store

        if postgres_store.is_enabled():
            await postgres_store.close_pool()
    except Exception:
        pass

    logger.info("app_shutdown_done")


def create_app() -> FastAPI:
    settings = get_settings()

    # Docs + OpenAPI schema bajo /api/v1/* (matchea la versión de la API).
    # Cuando exista /api/v2 se va a servir también `/api/v2/openapi.json` en
    # paralelo, sin romper clientes v1. El script `codegen:types` del
    # frontend apunta a la misma URL.
    app = FastAPI(
        title="MSK Multi-Agente",
        description="Backend del bot multi-agente y la consola humana.",
        version="1.0.0",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    # CORS para el widget embebido en WordPress
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Session-Token", "X-Customer-Token", "X-Admin-Key"],
    )

    # Security headers
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import Response

    class SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: StarletteRequest, call_next):
            response: Response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
            # Cross-origin isolation — bloquea iframes externos cargando
            # recursos del origen (aumenta postura frente a spectre-like).
            response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
            response.headers["Cross-Origin-Resource-Policy"] = "same-site"
            if request.url.scheme == "https":
                response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            return response

    app.add_middleware(SecurityHeadersMiddleware)

    # Body size limit — protege contra DoS por upload gigante. Uvicorn no
    # limita por default. Endpoints de media aceptan hasta 25MB, el resto
    # 1MB (cabe cualquier JSON de negocio).
    from utils.body_limit import BodySizeLimitMiddleware

    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=1_000_000,
        upload_paths=(
            "/api/v1/inbox/upload",
            "/api/v1/templates/hsm/upload-media",
        ),
        upload_max_bytes=25_000_000,
    )

    # Request context (structlog contextvars + request_id + duration log).
    # Se agrega DESPUÉS de security/body-limit para que los logs vean la
    # ruta final. Starlette ejecuta middlewares en orden inverso al add.
    from utils.request_context import RequestContextMiddleware

    app.add_middleware(RequestContextMiddleware)

    # Rate limiting
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Prometheus metrics — expone /metrics con counters por endpoint +
    # histograma de latencia + tamaños de request/response. Scrapeado por
    # el container Prometheus en el compose. No expone información
    # sensible (rutas sí, valores de params no).
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        Instrumentator(
            should_group_status_codes=True,
            should_instrument_requests_inprogress=True,
            excluded_handlers=["/health", "/metrics"],  # evita loop de scrape
            env_var_name="ENABLE_METRICS",
            inprogress_labels=True,
        ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
        logger.info("prometheus_metrics_enabled")
    except ImportError:
        logger.warning("prometheus_instrumentator_not_installed")

    # Routers — toda la UI consola consume bajo /api/* (consistencia con
    # frontend/lib/api.ts). Los públicos (widget embebible, webhooks,
    # customer LMS) viven fuera del namespace /api/ por compat con
    # consumidores externos.
    app.include_router(auth_router)  # /api/auth/*
    app.include_router(inbox_api_router)  # /api/inbox/*
    app.include_router(admin_router)  # /api/admin/{status,channels-status}
    app.include_router(admin_courses_router)  # /api/admin/courses/*
    app.include_router(admin_prompts_router)  # /api/admin/prompts/*
    app.include_router(widget_config_router)  # /api/admin/widget-config/*
    app.include_router(redis_admin_router)  # /api/admin/redis/*
    app.include_router(reports_router)  # /api/admin/reports/*
    app.include_router(test_agent_router)  # /api/admin/test-agent
    app.include_router(autonomous_router)  # /api/admin/autonomous/*
    app.include_router(templates_router)  # /api/templates/*
    app.include_router(voice_router)  # /api/v1/voice/* (call logs Zoho Voice)
    app.include_router(notifications_router)  # /api/v1/notifications/*

    # Públicos — fuera del namespace /api/ (consumidores externos).
    app.include_router(customer_auth_router)  # /customer/* (LMS)
    app.include_router(webhooks_router)  # /webhook/* (Meta, MP, Rebill, Zoho)
    app.include_router(widget_router)  # /widget/* (chat embebible)

    # El router legacy `api/inbox.py` se eliminó. Los helpers que vivían
    # ahí (broadcast_event, start_pubsub_listener, auto_assign_round_robin,
    # bot_disabled keys) están en `utils/realtime.py`, `utils/bot_state.py`
    # y `memory/assignment.py`.

    # Servir los archivos estáticos del widget
    static_dir = Path(__file__).parent / "widget" / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Servir archivos multimedia — StaticFiles soporta HEAD + Range requests
    import mimetypes

    mimetypes.add_type("audio/webm", ".webm")
    mimetypes.add_type("audio/ogg", ".ogg")
    mimetypes.add_type("audio/opus", ".opus")
    media_dir = Path(__file__).parent / "media"
    media_dir.mkdir(exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(media_dir)), name="media")

    @app.get("/health", include_in_schema=False)
    async def health():
        """Liveness probe — 200 si el proceso responde. Usado por Docker
        healthcheck y load balancers. NO chequea dependencias externas
        (evita cascading failures y falsos positivos)."""
        return {"status": "ok"}

    @app.get("/api/v1/health/live", include_in_schema=False)
    async def health_live():
        """Liveness bajo /api/v1 para consumidores del frontend."""
        return {"status": "ok"}

    @app.get("/api/v1/health/ready")
    async def health_ready():
        """Readiness probe — chequea dependencias críticas con timeout
        corto. Devuelve 200 si todo OK, 503 si algo crítico falla.

        Críticas (sin ellas el servicio no funciona): Redis, Postgres.
        Opcionales (degradan pero no tumban): OpenAI, Pinecone, Zoho.
        """
        import asyncio

        import redis.asyncio as aioredis
        from fastapi.responses import JSONResponse

        checks: dict[str, str] = {}
        critical_ok = True

        # Redis (crítico — sesiones, pubsub, cache)
        try:
            client = aioredis.from_url(get_settings().redis_url)
            await asyncio.wait_for(client.ping(), timeout=2.0)
            checks["redis"] = "ok"
            await client.aclose()
        except Exception as e:
            checks["redis"] = f"error: {type(e).__name__}"
            critical_ok = False

        # Postgres (crítico — conversaciones, profiles, audit)
        try:
            from memory import postgres_store

            if postgres_store.is_enabled():
                pool = await postgres_store.get_pool()
                async with pool.acquire() as conn:
                    await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=2.0)
                checks["postgres"] = "ok"
            else:
                checks["postgres"] = "disabled"
        except Exception as e:
            checks["postgres"] = f"error: {type(e).__name__}"
            critical_ok = False

        # Config fingerprint — útil para verificar qué versión está corriendo
        s = get_settings()
        checks["env"] = s.app_env
        checks["openai"] = "ok" if s.openai_api_key else "not_configured"
        checks["supabase"] = "ok" if s.supabase_url else "not_configured"

        body = {"status": "ready" if critical_ok else "not_ready", "checks": checks}
        return body if critical_ok else JSONResponse(status_code=503, content=body)

    @app.get("/widget.js")
    async def serve_widget_js():
        """Widget embebible — JS que cargan los sitios externos
        (msklatam.com, msklatam.tech) vía <script src=".../widget.js">."""
        js_file = Path(__file__).parent / "widget" / "static" / "chat.js"
        return FileResponse(str(js_file), media_type="application/javascript")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
