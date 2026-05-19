"""
Audit / Timeline API de Zoho CRM v6.

Trae el historial completo de un record (creado, actualizado campo por campo,
quién lo hizo y cuándo). Usado por /api/v1/admin/leads/{id}/audit para
investigar de qué integración / usuario vino una modificación.

Doc: https://www.zoho.com/crm/developer/docs/api/v6/timeline-api.html
"""

from __future__ import annotations

import httpx
import structlog

from config.settings import get_settings

from .auth import ZohoAuth

logger = structlog.get_logger(__name__)


class ZohoAudit:
    def __init__(self):
        self._auth = ZohoAuth()
        self._base = get_settings().zoho_base_url

    async def get_timeline(
        self,
        module: str,
        record_id: str,
        page_size: int = 200,
    ) -> dict:
        """
        Devuelve el __timeline del record: lista de acciones con done_by + diff
        de campos.

        Retorna dict con `events` (lista) y `raw` (respuesta cruda de Zoho)
        para debug. Cada event tiene: action, audited_time, done_by, source,
        field_history (lista de cambios campo-por-campo).
        """
        headers = await self._auth.auth_headers()
        url = f"{self._base}/{module}/{record_id}/__timeline"
        params = {"per_page": page_size, "sort_order": "desc"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, params=params, timeout=20)
        if resp.status_code == 204:
            return {"events": [], "raw": None}
        resp.raise_for_status()
        raw = resp.json()
        events = raw.get("__timeline") or raw.get("timeline") or []
        return {"events": events, "raw": raw}

    async def get_record(self, module: str, record_id: str) -> dict | None:
        """Trae el record completo (último estado)."""
        headers = await self._auth.auth_headers()
        url = f"{self._base}/{module}/{record_id}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=20)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("data") or []
        return rows[0] if rows else None
