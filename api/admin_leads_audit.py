"""
Audit trail de records de Zoho CRM (Leads / Contacts / etc.) para
investigar de dónde vino una creación o modificación.

- GET /api/v1/admin/leads/{lead_id}/audit  → timeline + estado actual del lead
- GET /api/v1/admin/zoho/{module}/{record_id}/audit  → mismo para cualquier módulo

Acepta sesión admin (cookie) o X-Admin-Key. Solo lectura.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path

from api.admin import require_role_or_admin, verify_admin_or_session
from integrations.zoho import ZohoAudit

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin-leads-audit"])


def _summarize_event(ev: dict) -> dict:
    """Aplana un evento del timeline a lo que importa para el UI."""
    done_by = ev.get("done_by") or {}
    source = ev.get("source") or {}
    return {
        "action": ev.get("action"),
        "audited_time": ev.get("audited_time"),
        "done_by_name": done_by.get("name"),
        "done_by_id": done_by.get("id"),
        "source": source.get("type") or source.get("source") or "manual",
        "field_changes": [
            {
                "field": fh.get("api_name") or fh.get("field"),
                "label": fh.get("label"),
                "old": fh.get("old_value"),
                "new": fh.get("new_value"),
            }
            for fh in (ev.get("field_history") or [])
        ],
        "raw": ev,
    }


def _summarize_record(rec: dict | None) -> dict | None:
    if not rec:
        return None
    keep = [
        "id",
        "Email",
        "First_Name",
        "Last_Name",
        "Phone",
        "Mobile",
        "Lead_Source",
        "Lead_Status",
        "Created_Time",
        "Modified_Time",
        "Last_Activity_Time",
        "Created_By",
        "Modified_By",
        "Owner",
        "Pais",
        "Brand",
        "Description",
        "Tag",
        "Ad_Campaign",
        "Ad_Set",
        "Ad_Account",
        "Tipo_de_recupero",
        "Automatizacion",
        "Canal_de_contacto",
        "Programa",
        "curso_nombre_plantilla",
    ]
    out = {k: rec.get(k) for k in keep if k in rec}
    # Cualquier custom field no vacío que no esté en `keep`.
    extras = {
        k: v
        for k, v in rec.items()
        if v not in (None, "", [], {})
        and k not in keep
        and not k.startswith("$")
        and not k.startswith("_")
    }
    return {"core": out, "extras": extras}


async def _audit_payload(module: str, record_id: str) -> dict:
    audit = ZohoAudit()
    try:
        record = await audit.get_record(module, record_id)
        timeline = await audit.get_timeline(module, record_id)
    except Exception as e:
        logger.error("zoho_audit_failed", module=module, record_id=record_id, err=str(e))
        raise HTTPException(status_code=502, detail=f"Zoho error: {e}") from e

    if record is None:
        raise HTTPException(status_code=404, detail=f"Record {record_id} no existe en {module}")

    events = [_summarize_event(ev) for ev in timeline["events"]]
    return {
        "module": module,
        "record_id": record_id,
        "record": _summarize_record(record),
        "events_count": len(events),
        "events": events,
    }


@router.get(
    "/leads/{lead_id}/audit",
    dependencies=[Depends(verify_admin_or_session), Depends(require_role_or_admin("admin", "supervisor"))],
)
async def get_lead_audit(lead_id: str = Path(..., min_length=10)):
    """Audit trail del lead: estado actual + timeline de cambios."""
    return await _audit_payload("Leads", lead_id)


@router.get(
    "/zoho/{module}/{record_id}/audit",
    dependencies=[Depends(verify_admin_or_session), Depends(require_role_or_admin("admin", "supervisor"))],
)
async def get_zoho_audit(module: str = Path(..., min_length=2), record_id: str = Path(..., min_length=10)):
    """Audit trail genérico para cualquier módulo Zoho (Leads, Contacts, Deals, etc.)."""
    return await _audit_payload(module, record_id)
