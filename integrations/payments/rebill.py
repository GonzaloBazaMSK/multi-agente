"""
Rebill — payment links con cuotas para LATAM.
API v3: https://docs.rebill.com/api/reference/payment-links
"""
import httpx
from config.settings import get_settings
import structlog

logger = structlog.get_logger(__name__)

# Cuotas fijas por país — se fuerza la cantidad de cuotas del curso
INSTALLMENTS_BY_COUNTRY = {
    "AR": [12],
    "CL": [8],
    "MX": [12],
    "CO": [12],
    "PE": [6],
    "UY": [12],
}

REBILL_API_V3 = "https://api.rebill.com/v3"


class RebillClient:
    def __init__(self):
        settings = get_settings()
        self._api_key = settings.rebill_api_key
        self._headers = {
            "x-api-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ── Payment Link con cuotas (API v3) ────────────────────────────

    async def create_payment_link(
        self,
        title: str,
        amount: float,
        currency: str,
        country: str = "AR",
        customer_email: str = "",
        customer_name: str = "",
        is_single_use: bool = True,
    ) -> dict:
        """
        Crea un payment link instant con cuotas habilitadas según país.
        Retorna {url, id}.
        """
        installments = INSTALLMENTS_BY_COUNTRY.get(country.upper(), [1, 3, 6, 12])

        cur = currency.upper()
        payload: dict = {
            "type": "instant",
            "title": [
                {"text": title, "language": "es"},
            ],
            "description": [
                {"text": f"Inscripción: {title}", "language": "es"},
            ],
            "prices": [
                {
                    "amount": amount,
                    "currency": cur,
                },
            ],
            "paymentMethods": [
                {
                    "currency": cur,
                    "methods": ["card"],
                },
            ],
            "isSingleUse": is_single_use,
            "installmentsSettings": [
                {
                    "currency": cur,
                    "enabledInstallments": installments,
                },
            ],
        }

        # Pre-llenar datos del cliente si los tenemos
        if customer_email or customer_name:
            customer_data: dict = {}
            if customer_email:
                customer_data["email"] = customer_email
            if customer_name:
                customer_data["fullName"] = customer_name
            payload["prefilledFields"] = {"customer": customer_data}

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{REBILL_API_V3}/payment-links",
                json=payload,
                headers=self._headers,
                timeout=25,
            )
            if resp.status_code >= 400:
                logger.error(
                    "rebill_payment_link_error",
                    status=resp.status_code,
                    body=resp.text[:500],
                )
            resp.raise_for_status()
            data = resp.json()

        link_url = data.get("url", "")
        link_id = data.get("id", "")
        logger.info(
            "rebill_payment_link_created",
            link_id=link_id,
            amount=amount,
            currency=currency,
            country=country,
            installments=installments,
        )
        return {
            "checkout_url": link_url,
            "link_id": link_id,
        }

    # ── Suscripciones (legacy, para cobranzas) ──────────────────────

    async def get_subscription(self, subscription_id: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{REBILL_API_V3}/subscriptions/{subscription_id}",
                headers=self._headers,
                timeout=15,
            )
            resp.raise_for_status()
        return resp.json()

    async def pause_subscription(self, subscription_id: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{REBILL_API_V3}/subscriptions/{subscription_id}/pause",
                headers=self._headers,
                timeout=15,
            )
            resp.raise_for_status()
        return resp.json()

    async def resume_subscription(self, subscription_id: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{REBILL_API_V3}/subscriptions/{subscription_id}/resume",
                headers=self._headers,
                timeout=15,
            )
            resp.raise_for_status()
        return resp.json()

    async def get_active_subscription_link(self, customer_id: str) -> dict:
        """Obtiene el link de pago de la suscripción activa de un cliente."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{REBILL_API_V3}/subscriptions",
                params={"customer_id": customer_id, "status": "active"},
                headers=self._headers,
                timeout=15,
            )
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            data = resp.json()

        subscriptions = data.get("data", data if isinstance(data, list) else [])
        if not subscriptions:
            return {}

        sub_id = subscriptions[0].get("id", "")
        if not sub_id:
            return {}

        return await self.get_payment_link_for_overdue(sub_id)

    async def get_payment_link_for_overdue(self, subscription_id: str) -> dict:
        """Genera un link de pago para regularizar una suscripción con mora."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{REBILL_API_V3}/subscriptions/{subscription_id}/retry-payment-link",
                headers=self._headers,
                timeout=15,
            )
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
        return resp.json()
