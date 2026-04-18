"""
Helpers del inbox:
  - slack_notify / notify_human_request: notifs a Slack cuando una conv escala.
  - log_action / list_audit_log:         persistencia del audit log de acciones.

ANTES había también un cron loop (`_cron_loop`, `start_inbox_jobs`) que
despertaba conversaciones snoozeadas vencidas. Se removió junto con la
feature de snooze. Ya no hay nada que arrancar al startup desde acá.
"""
from __future__ import annotations

import json
from typing import Optional

import httpx
import structlog

from memory.postgres_store import get_pool
from config.settings import get_settings

logger = structlog.get_logger(__name__)


# ─── Notifs Slack ────────────────────────────────────────────────────────────

async def slack_notify(text: str, blocks: list | None = None) -> None:
    """Manda mensaje al webhook Slack del workspace (si está configurado)."""
    settings = get_settings()
    url = settings.slack_webhook_url
    if not url:
        return
    payload: dict = {"text": text}
    if blocks:
        payload["blocks"] = blocks
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except Exception as e:
        logger.warning("slack_notify_failed", error=str(e))


async def notify_human_request(conv_id: str, contact_name: str, last_msg: str) -> None:
    """Llama a Slack cuando una conv requiere atención humana."""
    text = f"⚠️ *{contact_name}* necesita atención humana"
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f">{last_msg[:200]}"}},
        {
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": "Abrir conversación"},
                "url": f"https://agentes.msklatam.com/inbox?conv={conv_id}",
            }],
        },
    ]
    await slack_notify(text, blocks)


# ─── Audit log ───────────────────────────────────────────────────────────────

async def log_action(
    actor_id: str,
    action: str,
    conversation_id: Optional[str] = None,
    detail: Optional[dict] = None,
) -> None:
    """Persiste una acción humana al audit log."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """
                insert into public.inbox_audit_log
                  (actor_id, action, conversation_id, detail, created_at)
                values ($1, $2, $3, $4::jsonb, now())
                """,
                actor_id, action, conversation_id,
                json.dumps(detail or {}),
            )
        except Exception as e:
            logger.warning("audit_log_failed", error=str(e))


async def list_audit_log(
    limit: int = 100,
    conversation_id: Optional[str] = None,
    actor_id: Optional[str] = None,
) -> list[dict]:
    pool = await get_pool()
    where_parts = []
    params: list = []
    idx = 1
    if conversation_id:
        where_parts.append(f"conversation_id = ${idx}")
        params.append(conversation_id); idx += 1
    if actor_id:
        where_parts.append(f"actor_id = ${idx}")
        params.append(actor_id); idx += 1
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    params.append(limit)
    sql = f"""
        select id, actor_id, action, conversation_id, detail, created_at
        from public.inbox_audit_log
        {where}
        order by created_at desc
        limit ${idx}
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [
        {
            "id": str(r["id"]),
            "actor_id": r["actor_id"],
            "action": r["action"],
            "conversation_id": str(r["conversation_id"]) if r["conversation_id"] else None,
            "detail": r["detail"] if isinstance(r["detail"], dict) else (json.loads(r["detail"]) if r["detail"] else {}),
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
