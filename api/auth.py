"""Auth endpoints y dependencia FastAPI.

Sesión: JWT emitido por Supabase (password grant), opaque-token nuestro
almacenado en Redis con TTL 8h. Se entrega al cliente de dos formas:

  1. `Set-Cookie: msk_session=<token>; HttpOnly; Secure; SameSite=Lax`
     — forma recomendada para el browser. No es leíble desde JS, así
     que un XSS que meta un npm comprometido NO puede robar el token.

  2. Response body `{token, user}` — compat temporal con curl/scripts y
     con el frontend viejo que todavía pueda estar en cache del browser.
     Cuando todos los clientes migren, se puede quitar del body.

El `get_current_user` acepta la cookie (preferida) o el header
`x-session-token` (compat). Así no hay deploy-break: el día 1 el backend
manda ambos, el frontend nuevo usa cookie, el viejo sigue usando header
hasta que se renueve el bundle.

CSRF: la cookie se setea con SameSite=Lax — cualquier form POST de
origen externo no envía la cookie. Cross-site fetches del frontend
funcionan porque los hace desde el mismo origin (agentes.msklatam.com).
"""

import json
import uuid

import structlog
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

SESSION_TTL = 28800  # 8 horas
SESSION_COOKIE_NAME = "msk_session"

ALL_QUEUES = [
    "ventas_AR",
    "ventas_MX",
    "ventas_CL",
    "ventas_CO",
    "ventas_EC",
    "ventas_MP",
    "ventas_UY",
    "ventas_PE",
    "cobranzas_AR",
    "cobranzas_MX",
    "cobranzas_CL",
    "cobranzas_CO",
    "cobranzas_EC",
    "cobranzas_MP",
    "post_venta_AR",
    "post_venta_MX",
    "post_venta_CL",
    "post_venta_CO",
    "post_venta_EC",
    "post_venta_MP",
]


class LoginRequest(BaseModel):
    email: str
    password: str


class CreateUserRequest(BaseModel):
    email: str
    password: str
    name: str
    role: str = "agente"
    queues: list[str] = []


class UpdateUserRequest(BaseModel):
    name: str | None = None
    role: str | None = None
    queues: list[str] | None = None


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    access_token: str
    new_password: str


async def get_current_user(
    msk_session: str | None = Cookie(None),
    x_session_token: str | None = Header(None),
) -> dict:
    """Dependencia FastAPI: verifica sesión en Redis.

    Acepta cookie httpOnly (preferida) o header x-session-token (compat).
    La cookie gana si ambas vienen — si el bundle JS viejo sigue mandando
    el header, pero ya hay cookie nueva, la cookie es más reciente.
    """
    token = msk_session or x_session_token
    if not token:
        raise HTTPException(status_code=401, detail="No autenticado")
    from memory.conversation_store import get_conversation_store

    store = await get_conversation_store()
    data = await store._redis.get(f"session:{token}")
    if not data:
        raise HTTPException(status_code=401, detail="Sesión expirada")
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return json.loads(data)


def _set_session_cookie(response: Response, token: str, secure: bool) -> None:
    """Setea la cookie de sesión con los flags correctos.

    secure=True en prod (https). En dev (http) se desactiva porque los
    browsers ignoran `Secure` sobre http y no persiste la cookie.
    """
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL,
        httponly=True,
        secure=secure,
        samesite="lax",  # lax permite navegación top-level, bloquea CSRF form POST
        path="/",
    )


def require_role(*roles: str):
    """Dependencia que verifica rol."""

    async def _check(user: dict = Depends(get_current_user)):
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="Sin permisos")
        return user

    return _check


auth_limiter = Limiter(key_func=get_remote_address)


@router.post("/login")
@auth_limiter.limit("5/minute")
async def login(request: Request, response: Response, req: LoginRequest):
    from config.settings import get_settings
    from integrations.supabase_client import get_profile, sign_in_with_password

    try:
        await sign_in_with_password(req.email, req.password)
        profile = await get_profile(req.email)
        if not profile:
            raise HTTPException(status_code=403, detail="Usuario sin perfil. Contactá al administrador.")
        token = str(uuid.uuid4())
        user_info = {
            "id": profile.get("id", ""),
            "email": req.email,
            "name": profile.get("name", req.email.split("@")[0]),
            "role": profile.get("role", "agente"),
            "queues": profile.get("queues", []),
        }
        from memory.conversation_store import get_conversation_store

        store = await get_conversation_store()
        await store._redis.setex(f"session:{token}", SESSION_TTL, json.dumps(user_info))

        # Cookie httpOnly — es la forma canónica de guardar el token.
        # El body sigue devolviendo el token en plano para backward-compat
        # (bundle JS viejo en cache, scripts de QA, curl manual).
        settings = get_settings()
        _set_session_cookie(response, token, secure=settings.is_production)

        logger.info("user_login", email=req.email, role=user_info["role"])
        return {"token": token, "user": user_info}
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")
    except Exception as e:
        logger.error("login_error", error=str(e))
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.post("/forgot-password")
@auth_limiter.limit("3/minute")
async def forgot_password(request: Request, req: ForgotPasswordRequest):
    """
    Dispara mail de recovery via Supabase Auth.

    Respuesta intencionalmente genérica (no confirma si el email existe) para
    evitar user enumeration. El user recibe un mail con un link que redirige a
    /reset-password con un access_token en el hash fragment de la URL.

    Seguridad — `redirect_to` validado contra whitelist:
    El `redirect_to` que mandamos a Supabase determina a dónde apunta el link
    del mail. Si lo dejáramos inferir libremente del header `Origin`/`Referer`,
    un atacante podría:
        POST /api/v1/auth/forgot-password
        Origin: https://attacker.com
        body: {"email": "victim@msk.com"}
    → la víctima recibiría un mail "de MSK" con link a https://attacker.com/
    reset-password#access_token=... y al clickear filtra el token. Open
    redirect clásico. Por eso validamos `Origin` contra `settings.cors_origins`
    (whitelist explícita) — si no matchea, fallback al dominio de prod.
    """
    from urllib.parse import urlparse

    from config.settings import get_settings
    from integrations.supabase_client import send_password_recovery

    settings = get_settings()
    raw_origin = (
        request.headers.get("origin")
        or request.headers.get("referer", "").rstrip("/")
        or ""
    )
    # Normalizar — Referer suele venir como URL completa con path, queremos
    # solo el origen (scheme://host[:port]).
    if raw_origin:
        parsed = urlparse(raw_origin)
        if parsed.scheme and parsed.netloc:
            raw_origin = f"{parsed.scheme}://{parsed.netloc}"

    allowed = set(settings.cors_origins)
    origin = raw_origin if raw_origin in allowed else "https://agentes.msklatam.com"
    if raw_origin and raw_origin != origin:
        # No matcheó la whitelist — log sin email (PII) para detectar abuso.
        logger.warning(
            "forgot_password_origin_rejected",
            origin_received=raw_origin,
            fallback=origin,
        )

    redirect_to = f"{origin}/reset-password"
    try:
        await send_password_recovery(req.email, redirect_to)
    except Exception as e:
        # No exponer el error al user — siempre devolver OK
        logger.warning("forgot_password_error", error=str(e), email=req.email)
    # Mensaje genérico independientemente del resultado
    return {
        "ok": True,
        "message": "Si el email existe, te enviamos instrucciones para restablecer tu contraseña.",
    }


@router.post("/reset-password")
@auth_limiter.limit("5/minute")
async def reset_password(request: Request, req: ResetPasswordRequest):
    """
    Aplica la nueva password usando el access_token que vino del mail de recovery.
    El frontend extrae el token del hash fragment (#access_token=...) y lo manda acá.
    """
    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 8 caracteres")
    from integrations.supabase_client import update_user_password_with_token

    try:
        await update_user_password_with_token(req.access_token, req.new_password)
    except ValueError:
        # Token expirado o inválido
        raise HTTPException(
            status_code=401, detail="El link de recuperación es inválido o ya expiró. Pedí uno nuevo."
        )
    except Exception as e:
        logger.error("reset_password_error", error=str(e))
        raise HTTPException(status_code=500, detail="Error al actualizar la contraseña. Intentá de nuevo.")
    return {"ok": True, "message": "Contraseña actualizada. Ya podés iniciar sesión."}


@router.post("/logout")
async def logout(
    response: Response,
    msk_session: str | None = Cookie(None),
    x_session_token: str | None = Header(None),
):
    token = msk_session or x_session_token
    if token:
        from memory.conversation_store import get_conversation_store

        store = await get_conversation_store()
        await store._redis.delete(f"session:{token}")
    # Invalida la cookie en el browser (expira inmediatamente)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return user


@router.get("/queues")
async def get_queues():
    return ALL_QUEUES


@router.get("/users")
async def list_users(user: dict = Depends(require_role("admin", "supervisor"))):
    from integrations.supabase_client import list_profiles

    return await list_profiles()


@router.post("/users")
async def create_user(req: CreateUserRequest, user: dict = Depends(require_role("admin"))):
    from integrations.supabase_client import admin_create_auth_user, create_profile

    auth_result = await admin_create_auth_user(req.email, req.password, req.name)
    if "error" in auth_result or auth_result.get("msg"):
        raise HTTPException(status_code=400, detail=str(auth_result))
    # Reusamos el id del auth.user para que profiles.id == auth.users.id.
    # Sin esto, Postgres genera un uuid nuevo y los dos quedan desfasados —
    # ese era el bug que arregló la migración 005.
    auth_user_id = auth_result.get("id") or auth_result.get("user", {}).get("id")
    profile = await create_profile(req.email, req.name, req.role, req.queues, auth_user_id=auth_user_id)
    return profile


@router.patch("/users/{profile_id}")
async def update_user(
    profile_id: str, req: UpdateUserRequest, user: dict = Depends(require_role("admin", "supervisor"))
):
    from integrations.supabase_client import update_profile

    updates = {k: v for k, v in req.dict().items() if v is not None}
    # Supervisores solo pueden cambiar colas, no el rol
    if user.get("role") == "supervisor":
        updates = {k: v for k, v in updates.items() if k == "queues"}
        if not updates:
            raise HTTPException(status_code=403, detail="Supervisores solo pueden modificar colas")
    return await update_profile(profile_id, updates)


@router.delete("/users/{profile_id}")
async def delete_user(profile_id: str, user: dict = Depends(require_role("admin"))):
    from integrations.supabase_client import delete_profile

    await delete_profile(profile_id)
    return {"ok": True}


# ── Agent availability status ──────────────────────────────────────────────────

VALID_STATUSES = ("available", "busy", "away")


class AgentStatusRequest(BaseModel):
    status: str  # "available" | "busy" | "away"


@router.get("/agent-status")
async def get_agent_status(user: dict = Depends(get_current_user)):
    """Obtiene el estado de disponibilidad del agente actual."""
    from memory.conversation_store import get_conversation_store

    store = await get_conversation_store()
    user_id = user.get("id") or user.get("email", "unknown")
    val = await store._redis.get(f"agent_available:{user_id}")
    status = (val.decode() if isinstance(val, bytes) else val) if val else "available"
    return {"status": status, "user_id": user_id}


@router.post("/agent-status")
async def set_agent_status(req: AgentStatusRequest, user: dict = Depends(get_current_user)):
    """Actualiza el estado de disponibilidad del agente."""
    if req.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Estado inválido. Opciones: {VALID_STATUSES}")
    from memory.conversation_store import get_conversation_store

    store = await get_conversation_store()
    user_id = user.get("id") or user.get("email", "unknown")
    await store._redis.set(f"agent_available:{user_id}", req.status, ex=SESSION_TTL)
    logger.info("agent_status_updated", user_id=user_id, status=req.status)
    return {"status": req.status, "user_id": user_id}
