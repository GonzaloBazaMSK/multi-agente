"""
Punto de entrada de la aplicación FastAPI.
Multi-agente para empresa de cursos médicos.
Canales: WhatsApp (Botmaker) + Widget web embebible.
"""
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
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
        send_default_pii=True,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            StarletteIntegration(transaction_style="endpoint"),
            AsyncioIntegration(),
        ],
    )

# NOTE: api.lifecycle existe pero no se registra por ahora — el Kanban + Reports
# ya cubren el use case. Se puede activar en el futuro si el equipo crece.
# from api.lifecycle import router as lifecycle_router
from api.reports import router as reports_router
from api.test_agent import router as test_agent_router
from api.webhooks import router as webhooks_router
from api.widget import router as widget_router
from api.admin import router as admin_router
from api.inbox import router as inbox_router
from api.templates import router as templates_router
from api.admin_prompts import router as admin_prompts_router
from api.flows import router as flows_router
from api.auth import router as auth_router
from api.redis_admin import router as redis_admin_router
from api.customer_auth import router as customer_auth_router
from config.settings import get_settings

# Rate limiter global
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown de la app."""
    settings = get_settings()
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

    yield

    # Cleanup
    pubsub_task.cancel()

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
    app.include_router(inbox_router)
    app.include_router(templates_router)
    app.include_router(admin_prompts_router)
    app.include_router(flows_router)
    app.include_router(redis_admin_router)
    app.include_router(reports_router)
    app.include_router(test_agent_router)

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
        return {"status": "ok"}

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

    @app.get("/test")
    async def serve_test_redirect():
        """Redirige /test → /msk."""
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/msk", status_code=301)

    @app.get("/inbox-ui")
    async def serve_inbox_page():
        """Dashboard de conversaciones para agentes humanos."""
        html_file = Path(__file__).parent / "widget" / "inbox.html"
        return FileResponse(
            str(html_file),
            media_type="text/html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
        )

    @app.get("/admin/prompts-ui")
    async def serve_admin_prompts_page():
        """Editor de prompts para administradores."""
        html_file = Path(__file__).parent / "widget" / "admin_prompts.html"
        return FileResponse(str(html_file), media_type="text/html")

    @app.get("/admin/flows-ui")
    async def serve_flows_page():
        """Visual flow builder para flujos de conversación."""
        html_file = Path(__file__).parent / "widget" / "flows.html"
        return FileResponse(str(html_file), media_type="text/html")

    @app.get("/login")
    async def serve_login():
        """Página de login para el panel de administración."""
        html_file = Path(__file__).parent / "widget" / "login.html"
        return FileResponse(str(html_file), media_type="text/html")

    @app.get("/admin/users-ui")
    async def serve_users_page():
        """Gestión de usuarios — solo admin."""
        html_file = Path(__file__).parent / "widget" / "users.html"
        return FileResponse(str(html_file), media_type="text/html")

    @app.get("/admin/redis-ui")
    async def serve_redis_page():
        """Visor de Redis para administradores."""
        html_file = Path(__file__).parent / "widget" / "redis.html"
        return FileResponse(str(html_file), media_type="text/html")

    @app.get("/admin/templates-ui")
    async def serve_templates_page():
        """Gestor de plantillas HSM de WhatsApp."""
        html_file = Path(__file__).parent / "widget" / "templates.html"
        return FileResponse(str(html_file), media_type="text/html")

    @app.get("/admin/dashboard-ui")
    async def serve_dashboard_page():
        """Dashboard de métricas para administradores."""
        html_file = Path(__file__).parent / "widget" / "dashboard.html"
        return FileResponse(str(html_file), media_type="text/html")

    @app.get("/admin/test-agent-ui")
    async def serve_test_agent_page():
        """Sandbox para probar los agentes IA sin afectar producción."""
        html_file = Path(__file__).parent / "widget" / "test-agent.html"
        return FileResponse(str(html_file), media_type="text/html")

    @app.get("/audio-test")
    async def serve_audio_test():
        """Página de diagnóstico de audio."""
        html_file = Path(__file__).parent / "widget" / "audio_test.html"
        return FileResponse(str(html_file), media_type="text/html")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
