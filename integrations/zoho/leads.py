import json

import httpx
import structlog

from config.settings import get_settings

from .auth import ZohoAuth

logger = structlog.get_logger(__name__)

# TTL del cache de leads en Redis. 1h = balance entre ahorrar GETs a Zoho y
# que los datos no queden tan stale. Se invalida manualmente en `update()`
# y en cualquier otro punto donde el lead se modifique.
_LEAD_CACHE_TTL = 3600
_LEAD_CACHE_PREFIX = "zoho_lead:"


async def _get_redis():
    """Lazy import del cliente Redis compartido. Devuelve None si Redis cae,
    así el cache se degrada a no-op en lugar de romper el get."""
    try:
        from memory.conversation_store import get_conversation_store

        store = await get_conversation_store()
        return store._redis
    except Exception:
        return None


class ZohoLeads:
    def __init__(self):
        self._auth = ZohoAuth()
        self._base = get_settings().zoho_base_url

    @staticmethod
    def _normalize_pais(value: str) -> str:
        """
        Normaliza el valor del campo `Pais` (picklist en Zoho) a uno de los
        nombres válidos en español. Si llega ISO-2 (ej. "AR") lo mapea al
        nombre completo. Si ya está en español, lo devuelve igual.

        Si llega algo no reconocido, devuelve "Argentina" como fallback
        (más probable error: que el field quede vacío).
        """
        if not value:
            return "Argentina"
        v = str(value).strip()
        # Mapa ISO-2 → nombre picklist Zoho. Mantener en sync con la picklist
        # configurada en el módulo Leads de Zoho CRM.
        iso2_to_name = {
            "AR": "Argentina",
            "BO": "Bolivia",
            "CL": "Chile",
            "CO": "Colombia",
            "CR": "Costa Rica",
            "EC": "Ecuador",
            "ES": "España",
            "GT": "Guatemala",
            "HN": "Honduras",
            "MX": "México",
            "NI": "Nicaragua",
            "PA": "Panamá",
            "PE": "Perú",
            "PY": "Paraguay",
            "SV": "El Salvador",
            "UY": "Uruguay",
            "VE": "Venezuela",
            "DO": "República Dominicana",
            "PR": "Puerto Rico",
        }
        # ISO-2 (2 chars en mayúscula): mapear.
        if len(v) == 2 and v.upper() in iso2_to_name:
            return iso2_to_name[v.upper()]
        # Normalización suave para coincidir con la picklist (sin tildes ni mayúsculas).
        canon = v.lower().replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
        name_lookup = {
            "argentina": "Argentina",
            "bolivia": "Bolivia",
            "chile": "Chile",
            "colombia": "Colombia",
            "costa rica": "Costa Rica",
            "ecuador": "Ecuador",
            "espana": "España",
            "guatemala": "Guatemala",
            "honduras": "Honduras",
            "mexico": "México",
            "nicaragua": "Nicaragua",
            "panama": "Panamá",
            "peru": "Perú",
            "paraguay": "Paraguay",
            "el salvador": "El Salvador",
            "uruguay": "Uruguay",
            "venezuela": "Venezuela",
            "republica dominicana": "República Dominicana",
            "puerto rico": "Puerto Rico",
        }
        if canon in name_lookup:
            return name_lookup[canon]
        # Fallback: devolver tal cual (puede ser un nombre nuevo de la picklist).
        return v

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
                    # `Pais` es el API name real en Zoho (picklist en español).
                    # `Country` quedaba vacío porque ese field NO existe en el módulo.
                    "Pais": self._normalize_pais(data.get("country", "Argentina")),
                    "Lead_Source": data.get("lead_source", "Widget"),
                    "Lead_Status": data.get("lead_status", "Atención BOT IA"),
                    "Ad_Account": data.get("ad_account", "Widget"),
                    "Ad_ID": data.get("ad_id", ""),
                    "Ad_Name": data.get("ad_name", ""),
                    "Tipo_de_lead": data.get("tipo_de_lead", ""),
                    "Lead_ID_social": data.get("lead_id_social", ""),
                    # `Brand` (picklist en Zoho) — para Másters va "Master".
                    # Para cursos normales lo dejamos en blanco (se setea desde
                    # el lead nuevo el default de la picklist).
                    "Brand": data.get("brand", ""),
                    "Description": data.get("curso_de_interes", ""),
                    "Notas_Bot": data.get("notas", ""),
                }
            ]
        }
        # Log de intento (antes del POST) — útil para debug cuando Zoho devuelve 401/duplicate
        logger.info(
            "zoho_lead_create_attempt",
            email=data.get("email"),
            phone=data.get("phone"),
            country=data.get("country"),
            curso=data.get("curso_de_interes"),
        )
        headers = await self._auth.auth_headers()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self._base}/Leads",
                    json=payload,
                    headers={**headers, "Content-Type": "application/json"},
                    timeout=15,
                )
                resp.raise_for_status()
                result = resp.json()
        except httpx.HTTPStatusError as e:
            # Zoho devuelve detalle del error en el body — incluirlo en el log.
            body_excerpt = (e.response.text or "")[:500]
            logger.error(
                "zoho_lead_create_failed",
                status=e.response.status_code,
                body=body_excerpt,
                email=data.get("email"),
                phone=data.get("phone"),
            )
            raise
        except Exception as e:
            logger.error(
                "zoho_lead_create_failed",
                error=str(e),
                email=data.get("email"),
                phone=data.get("phone"),
            )
            raise

        lead_id = result["data"][0]["details"]["id"]
        logger.info("zoho_lead_created", lead_id=lead_id, email=data.get("email"))
        return {"id": lead_id, **result["data"][0]}

    async def update(self, lead_id: str, data: dict) -> dict:
        """Actualiza un Lead existente. Loggea inicio + resultado para visibilidad."""
        logger.info(
            "zoho_lead_update_attempt",
            lead_id=lead_id,
            fields=list(data.keys()),
        )
        payload = {"data": [{"id": lead_id, **data}]}
        headers = await self._auth.auth_headers()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.put(
                    f"{self._base}/Leads",
                    json=payload,
                    headers={**headers, "Content-Type": "application/json"},
                    timeout=15,
                )
                resp.raise_for_status()
                result = resp.json()
        except httpx.HTTPStatusError as e:
            body_excerpt = (e.response.text or "")[:500]
            logger.error(
                "zoho_lead_update_failed",
                lead_id=lead_id,
                status=e.response.status_code,
                body=body_excerpt,
            )
            raise
        except Exception as e:
            logger.error("zoho_lead_update_failed", lead_id=lead_id, error=str(e))
            raise

        logger.info("zoho_lead_updated", lead_id=lead_id, fields=list(data.keys()))
        # Invalidar cache para que el próximo `get()` traiga los datos frescos.
        await self.invalidate_cache(lead_id)
        return result

    async def get(self, lead_id: str, use_cache: bool = True) -> dict | None:
        """
        Obtiene un Lead de Zoho por ID. Devuelve el dict crudo de Zoho
        (con `id`, `First_Name`, `Email`, `Pais`, `Programa.name`,
        `curso_nombre_plantilla`, `Link_checkout`, etc.).

        Devuelve None si el lead no existe (404).

        Cache: Redis `zoho_lead:{lead_id}` con TTL 1h. Se invalida
        automáticamente en `update()`. Pasá `use_cache=False` para forzar
        re-fetch (útil en debug o si sospechás staleness).
        """
        if not lead_id:
            return None

        key = f"{_LEAD_CACHE_PREFIX}{lead_id}"
        redis = await _get_redis() if use_cache else None

        # Hit cache → devolver del Redis sin tocar Zoho.
        if redis is not None:
            try:
                cached = await redis.get(key)
                if cached:
                    logger.debug("zoho_lead_cache_hit", lead_id=lead_id)
                    return json.loads(cached.decode() if isinstance(cached, bytes) else cached)
            except Exception as e:
                # Cache jodido → caer al fetch fresh, no romper.
                logger.debug("zoho_lead_cache_read_failed", lead_id=lead_id, error=str(e))

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
        lead = rows[0] if rows else None

        # Cache miss → guardar el resultado (incluso si es None? NO — no cacheo
        # 404s para que un lead recién creado se vea apenas existe).
        if lead and redis is not None:
            try:
                await redis.set(key, json.dumps(lead, ensure_ascii=False), ex=_LEAD_CACHE_TTL)
                logger.debug("zoho_lead_cache_set", lead_id=lead_id)
            except Exception as e:
                logger.debug("zoho_lead_cache_write_failed", lead_id=lead_id, error=str(e))

        return lead

    @staticmethod
    async def invalidate_cache(lead_id: str) -> None:
        """Borra el cache Redis de un lead. Llamar después de un update/delete
        que cambie campos relevantes para el bot."""
        if not lead_id:
            return
        redis = await _get_redis()
        if redis is None:
            return
        try:
            await redis.delete(f"{_LEAD_CACHE_PREFIX}{lead_id}")
            logger.debug("zoho_lead_cache_invalidated", lead_id=lead_id)
        except Exception as e:
            logger.debug("zoho_lead_cache_invalidate_failed", lead_id=lead_id, error=str(e))

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
