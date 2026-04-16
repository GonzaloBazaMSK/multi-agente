import httpx
from .auth import ZohoAuth
from config.settings import get_settings
import structlog

logger = structlog.get_logger(__name__)


class ZohoContacts:
    def __init__(self):
        self._auth = ZohoAuth()
        self._base = get_settings().zoho_base_url

    async def create(self, data: dict) -> dict:
        payload = {
            "data": [{
                "Last_Name": data.get("last_name", data.get("name", "Sin nombre")),
                "First_Name": data.get("first_name", ""),
                "Phone": data.get("phone", ""),
                "Email": data.get("email", ""),
                "Mailing_Country": data.get("country", "Argentina"),
                "Lead_Source": data.get("canal_origen", "WhatsApp"),
                "Curso_Inscripto": data.get("curso_inscripto", ""),
                "Estado_Pago": data.get("estado_pago", "Pendiente"),
                "LMS_User_ID": data.get("lms_user_id", ""),
                "Canal_Origen": data.get("canal_origen", ""),
            }]
        }
        headers = await self._auth.auth_headers()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base}/Contacts",
                json=payload,
                headers={**headers, "Content-Type": "application/json"},
                timeout=15,
            )
            resp.raise_for_status()
            result = resp.json()

        contact_id = result["data"][0]["details"]["id"]
        logger.info("zoho_contact_created", contact_id=contact_id)
        return {"id": contact_id}

    async def update(self, contact_id: str, data: dict) -> dict:
        payload = {"data": [{"id": contact_id, **data}]}
        headers = await self._auth.auth_headers()
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{self._base}/Contacts",
                json=payload,
                headers={**headers, "Content-Type": "application/json"},
                timeout=15,
            )
            resp.raise_for_status()
        return resp.json()

    async def search_by_phone(self, phone: str) -> dict | None:
        headers = await self._auth.auth_headers()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base}/Contacts/search",
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
                f"{self._base}/Contacts/search",
                params={"email": email},
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 204:
                return None
            resp.raise_for_status()
            data = resp.json()
        return data.get("data", [None])[0]

    async def get(self, contact_id: str) -> dict | None:
        headers = await self._auth.auth_headers()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base}/Contacts/{contact_id}",
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
        return data.get("data", [None])[0]

    async def get_cursadas(self, contact_id: str) -> list[dict]:
        """
        Obtiene el subformulario 'Formulario_de_cursada' (Cursadas) del contacto.
        Cada ítem contiene: Curso, Finalizo, Enrollamiento, Fecha_finalizaci_n, etc.
        """
        headers = await self._auth.auth_headers()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base}/Contacts/{contact_id}",
                params={"fields": "Formulario_de_cursada"},
                headers=headers,
                timeout=15,
            )
            if resp.status_code in (204, 404):
                return []
            resp.raise_for_status()
            data = resp.json()
        record = data.get("data", [{}])[0]
        return record.get("Formulario_de_cursada", [])

    async def search_by_email_with_cursadas(self, email: str) -> dict | None:
        """Busca contacto por email y enriquece con el historial de cursadas."""
        contact = await self.search_by_email(email)
        if not contact:
            return None
        contact_id = contact.get("id")
        if contact_id:
            cursadas = await self.get_cursadas(contact_id)
            contact["Formulario_de_cursada"] = cursadas
        return contact

    # Campos de perfil que queremos traer del contacto
    # OJO: los API names de Zoho normalizan tildes → "_" y a veces tienen typos
    # que no se pueden corregir sin romper integraciones existentes:
    #   - "Profesi_n" (Profesión)
    #   - "rea_donde_tabaja" (Área donde trabaja — "tabaja" sin "r", typo de Zoho)
    #   - "Colegio_Sociedad_o_Federaci_n" (…Federación)
    PROFILE_FIELDS = (
        "First_Name,Last_Name,Email,Phone,Owner,Lead_Source,Canal_Origen,"
        "Profesi_n,Especialidad,Especialidad_interes,Intereses_adicionales,"
        "Contenido_Interes,Formulario_de_cursada,"
        "Cargo,Lugar_de_trabajo,rea_donde_tabaja,"
        "Pertenece_a_un_colegio,Colegio_Sociedad_o_Federaci_n,"
        "Created_Time,Modified_Time"
    )

    async def get_full_profile(self, contact_id: str) -> dict | None:
        """
        Obtiene el perfil completo del contacto: campos profesionales + cursadas.
        Incluye: Profesión, Especialidad, Especialidad_interes,
                 Intereses_adicionales, Contenido_Interes, Formulario_de_cursada.
        """
        headers = await self._auth.auth_headers()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base}/Contacts/{contact_id}",
                params={"fields": self.PROFILE_FIELDS},
                headers=headers,
                timeout=15,
            )
            if resp.status_code in (204, 404):
                return None
            resp.raise_for_status()
            data = resp.json()
        return data.get("data", [None])[0]

    async def search_by_email_with_full_profile(self, email: str) -> dict | None:
        """Busca contacto por email y trae perfil completo + cursadas."""
        contact = await self.search_by_email(email)
        if not contact:
            return None
        contact_id = contact.get("id")
        if contact_id:
            full = await self.get_full_profile(contact_id)
            if full:
                contact.update(full)
        return contact
