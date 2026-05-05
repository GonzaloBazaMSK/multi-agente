"""
Zoho Voice OAuth — access token refresh con lock (singleton).

Zoho Voice usa una app OAuth separada de la de CRM. Por eso tiene su propio
singleton en vez de reutilizar `ZohoAuth` — los refresh_token son distintos,
los client_id/secret también, y conceptualmente el Voice podría tener su
propio DC (.com/.eu/.in) diferente del CRM.

El access_token se refresca con el refresh_token; el singleton cachea hasta
60s antes del vencimiento y usa un asyncio.Lock para evitar que N corutinas
concurrentes hagan N refreshes en paralelo.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import structlog

from config.settings import get_settings

logger = structlog.get_logger(__name__)


class ZohoVoiceAuth:
    _instance: ZohoVoiceAuth | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._access_token: str | None = None
            cls._instance._token_expiry: float = 0.0
            cls._instance._lock = asyncio.Lock()
        return cls._instance

    async def get_access_token(self) -> str:
        # Fast path sin lock: si el token es válido por al menos 60s más, lo
        # devolvemos directo. Solo agarramos el lock cuando toca refrescar,
        # y adentro re-chequeamos (double-checked locking) porque otra
        # corutina puede haber refrescado mientras esperábamos.
        if self._access_token and time.time() < self._token_expiry - 60:
            return self._access_token
        async with self._lock:
            if self._access_token and time.time() < self._token_expiry - 60:
                return self._access_token
            return await self._refresh()

    async def _refresh(self) -> str:
        settings = get_settings()
        if not settings.zoho_voice_refresh_token:
            raise RuntimeError(
                "ZOHO_VOICE_REFRESH_TOKEN no configurado. " "Agregalo al .env (ver config/settings.py)."
            )
        url = f"{settings.zoho_accounts_url}/oauth/v2/token"
        params = {
            "refresh_token": settings.zoho_voice_refresh_token,
            "client_id": settings.zoho_voice_client_id,
            "client_secret": settings.zoho_voice_client_secret,
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

        if "access_token" not in data:
            logger.error("zoho_voice_token_refresh_failed", body=data)
            raise RuntimeError(f"Zoho Voice token refresh falló: {data}")

        self._access_token = data["access_token"]
        self._token_expiry = time.time() + int(data.get("expires_in", 3600))
        logger.info("zoho_voice_token_refreshed", expires_in=data.get("expires_in"))
        return self._access_token

    async def auth_headers(self) -> dict[str, str]:
        token = await self.get_access_token()
        return {"Authorization": f"Zoho-oauthtoken {token}"}
