import httpx
import structlog

from config.settings import get_settings

from .auth import ZohoAuth

logger = structlog.get_logger(__name__)


class ZohoLeads:
    def __init__(self):
        self._auth = ZohoAuth()
        self._base = get_settings().zoho_base_url

    async def create(self, data: dict) -> dict:
        """
        Crea un Lead en Zoho CRM.
        data: {last_name, first_name, phone, email, country, curso_de_interes,
               canal_origen, estado_pago, notas}
        """
        payload = {
            "data": [
                {
                    "Last_Name": data.get("last_name", data.get("name", "Sin nombre")),
                    "First_Name": data.get("first_name", ""),
                    "Phone": data.get("phone", ""),
                    "Email": data.get("email", ""),
                    "Country": data.get("country", "Argentina"),
                    "Lead_Source": "Widget",
                    "Lead_Status": "Atención BOT IA",
                    "Ad_Account": "Widget",
                    "Description": data.get("curso_de_interes", ""),
                    "Notas_Bot": data.get("notas", ""),
                }
            ]
        }
        headers = await self._auth.auth_headers()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base}/Leads",
                json=payload,
                headers={**headers, "Content-Type": "application/json"},
                timeout=15,
            )
            resp.raise_for_status()
            result = resp.json()

        lead_id = result["data"][0]["details"]["id"]
        logger.info("zoho_lead_created", lead_id=lead_id)
        return {"id": lead_id, **result["data"][0]}

    async def update(self, lead_id: str, data: dict) -> dict:
        payload = {"data": [{"id": lead_id, **data}]}
        headers = await self._auth.auth_headers()
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{self._base}/Leads",
                json=payload,
                headers={**headers, "Content-Type": "application/json"},
                timeout=15,
            )
            resp.raise_for_status()
        return resp.json()

    async def get(self, lead_id: str) -> dict | None:
        """
        Obtiene un Lead de Zoho por ID. Devuelve el dict crudo de Zoho
        (con `id`, `First_Name`, `Email`, `Pais`, `Programa.name`,
        `curso_nombre_plantilla`, `Link_checkout`, etc.).

        Devuelve None si el lead no existe (404).
        """
        if not lead_id:
            return None
        headers = await self._auth.auth_headers()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base}/Leads/{lead_id}",
                headers=headers,
                timeout=15,
            )
            if resp.status_code in (204, 404):
                return None
            resp.raise_for_status()
            data = resp.json()
        rows = data.get("data") or []
        return rows[0] if rows else None

    async def search_by_phone(self, phone: str) -> dict | None:
        headers = await self._auth.auth_headers()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base}/Leads/search",
                params={"phone": phone},
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 204:
                return None
            resp.raise_for_status()
            data = resp.json()
        return data.get("data", [None])[0]

    async def search_by_email(self, email: str) -> dict | None:
        headers = await self._auth.auth_headers()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base}/Leads/search",
                params={"email": email},
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 204:
                return None
            resp.raise_for_status()
            data = resp.json()
        return data.get("data", [None])[0]
