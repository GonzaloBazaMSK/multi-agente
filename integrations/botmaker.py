"""
Botmaker — envío de mensajes y handoff a operador humano.
Docs: https://go.botmaker.com/api/docs

Autenticación OAuth2:
  POST /oauth2/token  { clientId, secretId, refreshToken, grantType: "refresh_token" }
  → { accessToken, expiresIn }

El access-token se renueva automáticamente cuando expira.
"""
import asyncio
import time
import hmac
import hashlib
import httpx
from config.settings import get_settings
import structlog

logger = structlog.get_logger(__name__)

_TOKEN_BUFFER_SECONDS = 60  # renovar 60s antes de que expire


class BotmakerClient:
    _instance: "BotmakerClient | None" = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._access_token: str = ""
            cls._instance._token_expires_at: float = 0.0
            cls._instance._lock = asyncio.Lock()
        return cls._instance

    def __init__(self):
        settings = get_settings()
        self._client_id = settings.botmaker_client_id
        self._secret_id = settings.botmaker_secret_id
        self._refresh_token = settings.botmaker_refresh_token
        self._static_api_key = settings.botmaker_api_key  # fallback
        self._base = settings.botmaker_base_url
        self._webhook_secret = settings.botmaker_webhook_secret

    def _uses_oauth(self) -> bool:
        return bool(self._client_id and self._secret_id and self._refresh_token)

    async def _get_access_token(self) -> str:
        """Retorna un access-token válido, renovándolo si expiró."""
        if not self._uses_oauth():
            return self._static_api_key

        now = time.time()
        if self._access_token and now < self._token_expires_at - _TOKEN_BUFFER_SECONDS:
            return self._access_token

        async with self._lock:
            # Double-check after acquiring lock
            now = time.time()
            if self._access_token and now < self._token_expires_at - _TOKEN_BUFFER_SECONDS:
                return self._access_token

            # Renovar token
            payload = {
                "clientId": self._client_id,
                "secretId": self._secret_id,
                "refreshToken": self._refresh_token,
                "grantType": "refresh_token",
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self._base}/oauth2/token",
                    json=payload,
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()

            self._access_token = data["accessToken"]
            expires_in = data.get("expiresIn", 3600)
            self._token_expires_at = time.time() + expires_in
            logger.info("botmaker_token_refreshed", expires_in=expires_in)
            return self._access_token

    async def _headers(self) -> dict:
        token = await self._get_access_token()
        return {
            "access-token": token,
            "Content-Type": "application/json",
        }

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        if not self._webhook_secret:
            return True
        expected = hmac.new(
            self._webhook_secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(f"sha256={expected}", signature)

    async def send_message(self, chat_id: str, text: str) -> dict:
        payload = {
            "chatId": chat_id,
            "message": {"type": "TEXT", "text": text},
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base}/message/send",
                json=payload,
                headers=await self._headers(),
                timeout=15,
            )
            resp.raise_for_status()
        return resp.json()

    async def send_buttons(self, chat_id: str, text: str, buttons: list[str]) -> dict:
        """Envía mensaje con botones de respuesta rápida."""
        payload = {
            "chatId": chat_id,
            "message": {
                "type": "BUTTONS",
                "text": text,
                "buttons": [{"text": b} for b in buttons],
            },
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base}/message/send",
                json=payload,
                headers=await self._headers(),
                timeout=15,
            )
            resp.raise_for_status()
        return resp.json()

    async def handoff_to_human(self, chat_id: str, reason: str = "") -> dict:
        """Transfiere la conversación a la bandeja de operadores en Botmaker."""
        payload = {
            "chatId": chat_id,
            "takeoverReason": reason or "El usuario solicitó hablar con un asesor",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base}/chat/takeover",
                json=payload,
                headers=await self._headers(),
                timeout=15,
            )
            resp.raise_for_status()
        logger.info("botmaker_handoff", chat_id=chat_id)
        return resp.json()

    async def send_message_to_channel(self, channel_id: str, contact_id: str, text: str) -> dict:
        """Envía mensaje outbound a un contacto en un canal específico (v2 API)."""
        payload = {
            "chat": {"channelId": channel_id, "contactId": contact_id},
            "messages": [{"text": text}],
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.botmaker.com/v2.0/chats-actions/send-messages",
                json=payload,
                headers=await self._headers(),
                timeout=15,
            )
            resp.raise_for_status()
        return resp.json()

    async def get_chat_info(self, chat_id: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base}/chat/{chat_id}",
                headers=await self._headers(),
                timeout=15,
            )
            resp.raise_for_status()
        return resp.json()
