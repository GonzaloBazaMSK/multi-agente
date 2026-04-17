"""
Zoho OAuth2 — manejo automático de access token con refresh.
El refresh_token se obtiene una vez desde la consola de Zoho Developer
y se almacena en .env. El access_token se renueva automáticamente.
"""
import asyncio
import time
import httpx
from config.settings import get_settings
import structlog

logger = structlog.get_logger(__name__)


class ZohoAuth:
    _instance: "ZohoAuth | None" = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._access_token: str | None = None
            cls._instance._token_expiry: float = 0
            cls._instance._lock = asyncio.Lock()
        return cls._instance

    async def get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expiry - 60:
            return self._access_token
        async with self._lock:
            # Double-check after acquiring lock
            if self._access_token and time.time() < self._token_expiry - 60:
                return self._access_token
            return await self._refresh()

    async def _refresh(self) -> str:
        settings = get_settings()
        url = f"{settings.zoho_accounts_url}/oauth/v2/token"
        params = {
            "refresh_token": settings.zoho_refresh_token,
            "client_id": settings.zoho_client_id,
            "client_secret": settings.zoho_client_secret,
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

        self._access_token = data["access_token"]
        self._token_expiry = time.time() + int(data.get("expires_in", 3600))
        logger.info("zoho_token_refreshed", expires_in=data.get("expires_in"))
        return self._access_token

    async def auth_headers(self) -> dict[str, str]:
        token = await self.get_access_token()
        return {"Authorization": f"Zoho-oauthtoken {token}"}
