"""
Punto de entrada de la aplicación FastAPI.
Multi-agente para empresa de cursos médicos.
Canales: WhatsApp (Botmaker) + Widget web embebible.
"""
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from config.settings import get_settings

# ─── Sentry (error tracking) — init BEFORE FastAPI() so the SDK wraps it ────
_settings_boot = get_settings()
if _settings_boot.sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration
    from sentry_sdk.integrations.asyncio import AsyncioIntegration

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

# NOTE: api.lifecycle existe pero no se registra por ahora — el Kanban + Reports
# ya cubren el use case. Se puede activar en el futuro si el equipo crece.
# from api.lifecycle import router as lifecycle_router
from api.autonomous import router as autonomous_router
from api.reports import router as reports_router
from api.test_agent import router as test_agent_router
from api.webhooks import router as webhooks_router
from api.widget import router as widget_router
from api.admin import router as admin_router
from api.admin_courses import router as admin_courses_router
from api.inbox import router as inbox_router
from api.inbox_api import router as inbox_api_router
from api.templates import router as templates_router
from api.admin_prompts import router as admin_prompts_router
from api.flows import router as flows_router
from api.auth import router as auth_router
from api.redis_admin import router as redis_admin_router
from api.customer_auth import router as customer_auth_router

# Rate limiter global
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

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
    from api.inbox import start_pubsub_listener
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

    # Cleanup
    pubsub_task.cancel()
    try:
        from utils.scheduler import shutdown_scheduler
        await shutdown_scheduler()
    except Exception:
        pass

    logger.info("app_shutdown")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Multi-Agente Cursos Médicos",
        description="Sistema multi-agente: Ventas (RAG) · Cobranzas · Post-venta",
        version="1.0.0",
        docs_url="/docs" if not settings.is_production else None,
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
            if request.url.scheme == "https":
                response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            return response

    app.add_middleware(SecurityHeadersMiddleware)

    # Rate limiting
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Routers
    app.include_router(auth_router)
    app.include_router(customer_auth_router)
    app.include_router(webhooks_router)
    app.include_router(widget_router)
    app.include_router(admin_router)
    app.include_router(admin_courses_router)
    app.include_router(inbox_router)
    app.include_router(inbox_api_router)
    app.include_router(templates_router)
    app.include_router(admin_prompts_router)
    app.include_router(flows_router)
    app.include_router(redis_admin_router)
    app.include_router(reports_router)
    app.include_router(test_agent_router)
    app.include_router(autonomous_router)

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

    @app.get("/health")
    async def health():
        checks = {"status": "ok"}
        try:
            import redis.asyncio as aioredis
            client = aioredis.from_url(get_settings().redis_url)
            await client.ping()
            checks["redis"] = "ok"
            await client.aclose()
        except Exception:
            checks["redis"] = "error"
            checks["status"] = "degraded"
        return checks

    @app.get("/widget.js")
    async def serve_widget_js():
        """Script del widget embebible para WordPress."""
        js_file = Path(__file__).parent / "widget" / "static" / "chat.js"
        return FileResponse(str(js_file), media_type="application/javascript")

    @app.get("/msk")
    async def serve_msk_page():
        """Sitio web MSK Latam — demo con login de clientes."""
        html_file = Path(__file__).parent / "widget" / "test.html"
        return FileResponse(str(html_file), media_type="text/html")

    @app.get("/demo/curso/cardiologia-amir")
    async def serve_demo_cardiologia_amir():
        """Página de prueba: simula estar en /curso/cardiologia-amir.
        El widget se carga con data-page-slug='cardiologia-amir', lo que
        permite probar el routing contextual (pre-compra vs cobranzas)."""
        html_file = Path(__file__).parent / "widget" / "curso_cardiologia_amir.html"
        return FileResponse(str(html_file), media_type="text/html")

    @app.get("/test")
    async def serve_test_redirect():
        """Redirige /test → /msk."""
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/msk", status_code=301)

    # Rutas legacy de la UI vieja (widget/*.html) — todas las páginas fueron
    # migradas a Next.js en frontend/app/(app)/*. Los HTMLs se borraron pero
    # dejamos redirects 301 para que bookmarks viejos del equipo sigan
    # cayendo en la UI nueva.
    # El único que sigue sirviendo HTML es /admin/flows-ui (builder Drawflow
    # no migrado — canvas complejo).
    from fastapi.responses import RedirectResponse

    @app.get("/inbox-ui")
    @app.get("/inbox-ui/{session_id}")
    async def redirect_inbox_ui(session_id: str = ""):
        target = f"/inbox?conv={session_id}" if session_id else "/inbox"
        return RedirectResponse(url=target, status_code=301)

    @app.get("/admin/prompts-ui")
    async def redirect_prompts_ui():
        return RedirectResponse(url="/prompts", status_code=301)

    @app.get("/admin/flows-ui")
    async def serve_flows_page():
        """Visual flow builder (Drawflow) — única página HTML legacy activa.
        La UI nueva (/flows) linkea acá para editar la topología de nodos."""
        html_file = Path(__file__).parent / "widget" / "flows.html"
        return FileResponse(str(html_file), media_type="text/html")

    @app.get("/admin/users-ui")
    async def redirect_users_ui():
        return RedirectResponse(url="/users", status_code=301)

    @app.get("/admin/redis-ui")
    async def redirect_redis_ui():
        return RedirectResponse(url="/redis", status_code=301)

    @app.get("/admin/templates-ui")
    async def redirect_templates_ui():
        return RedirectResponse(url="/templates", status_code=301)

    @app.get("/admin/dashboard-ui")
    async def redirect_dashboard_ui():
        return RedirectResponse(url="/dashboard", status_code=301)

    @app.get("/admin/test-agent-ui")
    async def redirect_test_agent_ui():
        return RedirectResponse(url="/test-agent", status_code=301)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
