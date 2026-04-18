"""Cliente Supabase - usa sb_secret key format."""
import os
import httpx
import structlog

logger = structlog.get_logger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]

def _headers() -> dict:
    return {
        "apikey": SECRET_KEY,
        "Authorization": f"Bearer {SECRET_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

async def sign_in_with_password(email: str, password: str) -> dict:
    """Autentica email/password via Supabase Auth."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            headers=_headers(),
            json={"email": email, "password": password},
            timeout=10,
        )
        if resp.status_code != 200:
            raise ValueError(f"Auth failed: {resp.text}")
        return resp.json()

async def get_profile(email: str) -> dict | None:
    """Obtiene perfil por email."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers=_headers(),
            params={"email": f"eq.{email}", "select": "*"},
            timeout=10,
        )
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0]
        return None

async def list_profiles() -> list:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers=_headers(),
            params={"select": "*", "order": "created_at.asc"},
            timeout=10,
        )
        return resp.json() if isinstance(resp.json(), list) else []

async def create_profile(
    email: str, name: str, role: str, queues: list, auth_user_id: str | None = None
) -> dict:
    """
    Crea un row en public.profiles.

    OJO: si tenés el id del auth.users (devuelto por admin_create_auth_user),
    pasalo en `auth_user_id`. Si no lo pasás, Postgres genera un uuid nuevo
    y profiles.id queda DESINCRONIZADO con auth.users.id — bug latente que
    fue arreglado en migrations/005_resync_profile_ids.sql. Este parámetro
    es lo que evita que vuelva a pasar.
    """
    payload: dict = {"email": email, "name": name, "role": role, "queues": queues}
    if auth_user_id:
        payload["id"] = auth_user_id
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers=_headers(),
            json=payload,
            timeout=10,
        )
        data = resp.json()
        return data[0] if isinstance(data, list) and data else data

async def update_profile(profile_id: str, updates: dict) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers=_headers(),
            params={"id": f"eq.{profile_id}"},
            json=updates,
            timeout=10,
        )
        data = resp.json()
        return data[0] if isinstance(data, list) and data else {}

async def delete_profile(profile_id: str) -> None:
    async with httpx.AsyncClient() as client:
        await client.delete(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers=_headers(),
            params={"id": f"eq.{profile_id}"},
            timeout=10,
        )

async def send_password_recovery(email: str, redirect_to: str) -> dict:
    """
    Dispara el flow de "olvidé mi contraseña" de Supabase Auth.
    Supabase manda un mail con un link tipo
        https://<project>.supabase.co/auth/v1/verify?token=...&type=recovery&redirect_to=<redirect_to>
    Cuando el user lo abre, Supabase redirige a `redirect_to` con
    `#access_token=...&refresh_token=...&type=recovery` en el hash.

    Importante: este endpoint NO confirma si el email existe (protección
    contra user enumeration) — siempre devuelve 200 aunque no haya match.
    El error de mail (SMTP, no llega) no se propaga acá.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/recover",
            headers=_headers(),
            json={"email": email, "options": {"redirectTo": redirect_to}},
            timeout=10,
        )
        # Supabase devuelve 200 incluso si el email no existe (anti-enum).
        # Solo logueamos si el código es != 200 — algo raro pasó.
        if resp.status_code != 200:
            logger.warning("password_recovery_failed",
                          email=email, status=resp.status_code, body=resp.text[:200])
        return {"ok": resp.status_code == 200}


async def update_user_password_with_token(access_token: str, new_password: str) -> dict:
    """
    Actualiza la password del user dueño del access_token.
    El access_token viene del flow de recovery (Supabase lo embebe en el hash
    de la URL al redirigir post-mail).
    """
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={
                "apikey": SECRET_KEY,
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"password": new_password},
            timeout=10,
        )
        if resp.status_code != 200:
            raise ValueError(f"Reset failed: {resp.text}")
        return resp.json()


async def admin_create_auth_user(email: str, password: str, name: str) -> dict:
    """Crea usuario en Supabase Auth."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/admin/users",
            headers=_headers(),
            json={"email": email, "password": password, "email_confirm": True,
                  "user_metadata": {"name": name}},
            timeout=10,
        )
        return resp.json()

# Alias for backward compatibility
async def admin_create_user(email: str, password: str, name: str) -> dict:
    return await admin_create_auth_user(email, password, name)

async def admin_delete_auth_user(user_id: str) -> None:
    async with httpx.AsyncClient() as client:
        await client.delete(
            f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}",
            headers=_headers(),
            timeout=10,
        )

# Alias for backward compatibility
async def admin_delete_user(user_id: str) -> None:
    await admin_delete_auth_user(user_id)

async def get_customer_profile(email: str) -> dict | None:
    """Obtiene perfil de cliente desde tabla customers."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/customers",
            headers=_headers(),
            params={"email": f"eq.{email}", "select": "*"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                return data[0]
        return None


async def create_customer_profile(email: str, name: str, phone: str = None,
                                   country: str = None, courses: list = None) -> dict:
    """Crea perfil de cliente en tabla customers."""
    payload = {
        "email": email,
        "name": name,
        "courses": courses or [],
    }
    if phone:
        payload["phone"] = phone
    if country:
        payload["country"] = country

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/customers",
            headers=_headers(),
            json=payload,
            timeout=10,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            return data[0] if isinstance(data, list) and data else data
        logger.error("create_customer_profile_error", status=resp.status_code, body=resp.text)
        return {}


async def update_customer_profile(customer_id: str, updates: dict) -> dict:
    """Actualiza perfil de cliente."""
    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/customers",
            headers=_headers(),
            params={"id": f"eq.{customer_id}"},
            json=updates,
            timeout=10,
        )
        data = resp.json()
        return data[0] if isinstance(data, list) and data else {}


async def get_auth_user_by_email(email: str) -> dict | None:
    """Busca usuario en Supabase Auth por email."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{SUPABASE_URL}/auth/v1/admin/users",
            headers=_headers(),
            params={"email": email},
            timeout=10,
        )
        data = resp.json()
        users = data.get("users", [])
        return users[0] if users else None


async def list_all_customers() -> list:
    """Lista todos los customers (paginado)."""
    all_rows = []
    page = 0
    page_size = 1000
    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/customers",
                headers={**_headers(), "Range": f"{page*page_size}-{(page+1)*page_size - 1}"},
                params={"select": "id,email"},
                timeout=30,
            )
            if resp.status_code not in (200, 206):
                break
            data = resp.json()
            if not isinstance(data, list) or not data:
                break
            all_rows.extend(data)
            if len(data) < page_size:
                break
            page += 1
    return all_rows


async def delete_all_customers() -> int:
    """Elimina TODOS los customers. Devuelve cantidad borrada."""
    async with httpx.AsyncClient() as client:
        # Primero contar
        customers = await list_all_customers()
        count = len(customers)
        if count == 0:
            return 0
        # Delete con filtro que matchea todos
        resp = await client.delete(
            f"{SUPABASE_URL}/rest/v1/customers",
            headers=_headers(),
            params={"id": "not.is.null"},
            timeout=60,
        )
        logger.info("delete_all_customers", count=count, status=resp.status_code)
        return count


async def list_all_auth_users() -> list:
    """Lista todos los auth users vía admin API."""
    all_users = []
    page = 1
    per_page = 1000
    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                f"{SUPABASE_URL}/auth/v1/admin/users",
                headers=_headers(),
                params={"page": page, "per_page": per_page},
                timeout=30,
            )
            if resp.status_code != 200:
                break
            data = resp.json()
            users = data.get("users", []) if isinstance(data, dict) else []
            if not users:
                break
            all_users.extend(users)
            if len(users) < per_page:
                break
            page += 1
    return all_users


async def delete_all_customer_auth_users(keep_emails: list[str] | None = None) -> int:
    """
    Elimina auth users excepto los emails en keep_emails (admins).
    Devuelve cantidad borrada.
    """
    keep = set((e or "").lower() for e in (keep_emails or []))
    users = await list_all_auth_users()
    deleted = 0
    async with httpx.AsyncClient() as client:
        for u in users:
            email = (u.get("email") or "").lower()
            uid = u.get("id")
            if not uid:
                continue
            if email in keep:
                continue
            try:
                resp = await client.delete(
                    f"{SUPABASE_URL}/auth/v1/admin/users/{uid}",
                    headers=_headers(),
                    timeout=10,
                )
                if resp.status_code in (200, 204):
                    deleted += 1
            except Exception as e:
                logger.warning("delete_auth_user_failed", user_id=uid, error=str(e))
    logger.info("delete_all_customer_auth_users", deleted=deleted, kept=len(keep))
    return deleted
