"""
Tareas autónomas que corre el scheduler.

run_retargeting_cycle: cada hora, escanea conversaciones inactivas y para
cada una decide con IA si mandar un HSM template de follow-up y cuál.

run_auto_retry_cycle: diariamente, busca convs cerradas como "descartado"
hace >20 días y las reactiva (marca para que el próximo retargeting las incluya).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import structlog

logger = structlog.get_logger(__name__)


# ── Retargeting cycle (AI-driven) ────────────────────────────────────────────

# Días candidatos para follow-up. Si el último contacto está dentro de estas
# franjas (± unas horas), el closer decide si mandar HSM.
RETARGETING_DAYS = [1, 3, 7, 15, 28]

# Labels elegibles para retargeting automático.
ELIGIBLE_LABELS = {"caliente", "tibio", "esperando_pago", "seguimiento"}

# Labels que excluyen retargeting (lead explícitamente terminado).
EXCLUDE_LABELS = {"convertido", "no_interesa"}


async def run_retargeting_cycle() -> None:
    """Entry point llamado por APScheduler cada hora."""
    try:
        from memory.conversation_store import get_conversation_store
        from memory import postgres_store

        store = await get_conversation_store()
        r = store._redis

        # Leer config (puede estar deshabilitado)
        cfg_raw = await r.get("retargeting:config")
        if cfg_raw:
            cfg = json.loads(cfg_raw.decode() if isinstance(cfg_raw, bytes) else cfg_raw)
            if not cfg.get("enabled", True):
                logger.info("retargeting_disabled_by_config")
                return

        candidates = await _find_candidates()
        logger.info("retargeting_cycle_start", candidates=len(candidates))

        sent = 0
        skipped = 0
        for session_id, label, days_inactive in candidates:
            try:
                acted = await _process_lead(session_id, label, days_inactive)
                if acted:
                    sent += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.warning("retargeting_lead_failed", session_id=session_id, error=str(e))

        # Stats
        stats = {
            "last_run": datetime.now(timezone.utc).isoformat(),
            "candidates": len(candidates),
            "sent": sent,
            "skipped": skipped,
        }
        await r.set("retargeting:stats", json.dumps(stats))
        logger.info("retargeting_cycle_end", **stats)
    except Exception as e:
        logger.error("retargeting_cycle_crashed", error=str(e))


async def _find_candidates() -> list[tuple[str, str, int]]:
    """
    Retorna [(session_id, label, days_inactive)] de conversaciones que caen
    en alguna ventana de retargeting y matchean criterio.
    """
    from memory.conversation_store import get_conversation_store
    store = await get_conversation_store()
    r = store._redis

    # Scan todas las keys conv:*
    results: list[tuple[str, str, int]] = []
    now = datetime.now(timezone.utc)

    async for raw_key in r.scan_iter(match="conv:*", count=200):
        key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
        conv_id = key.replace("conv:", "", 1)
        try:
            raw = await r.get(key)
            if not raw:
                continue
            from models.conversation import Conversation
            conv = Conversation.model_validate_json(raw)
        except Exception:
            continue

        # Solo WhatsApp (HSM templates requieren WA)
        if conv.channel.value != "whatsapp":
            continue
        # Solo conversaciones abiertas
        if conv.status.value == "closed":
            continue

        # Label
        label_key = f"conv_label:{conv.external_id}"
        label_raw = await r.get(label_key)
        label = (label_raw.decode() if isinstance(label_raw, bytes) else label_raw) if label_raw else ""

        if label in EXCLUDE_LABELS:
            continue

        # Tiempo desde último mensaje (del cliente)
        last_user_msg = None
        for m in reversed(conv.messages):
            if m.role.value == "user":
                last_user_msg = m
                break
        if not last_user_msg:
            continue

        ts = last_user_msg.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        days_inactive = (now - ts).days

        # ¿Cae en alguna ventana de retargeting?
        if days_inactive not in RETARGETING_DAYS:
            continue

        # ¿Ya se le mandó retargeting ESTE día?
        sent_key = f"retarget_sent:{conv.external_id}:day{days_inactive}"
        already = await r.get(sent_key)
        if already:
            continue

        results.append((conv.external_id, label, days_inactive))

    return results


async def _process_lead(phone: str, label: str, days_inactive: int) -> bool:
    """Decide con IA qué template mandar a este lead y lo envía. Retorna True si actuó."""
    from memory.conversation_store import get_conversation_store
    from integrations.whatsapp_meta import WhatsAppMetaClient
    from integrations.zoho import leads as zoho_leads
    from config.constants import Channel
    from openai import AsyncOpenAI
    from config.settings import get_settings

    store = await get_conversation_store()
    conv = await store.get_by_external(Channel.WHATSAPP, phone)
    if not conv:
        return False

    # Construir contexto para el LLM
    history = conv.messages[-10:]
    transcript = "\n".join(f"[{m.role.value}] {m.content[:200]}" for m in history if m.content)

    # Templates HSM disponibles (cache Meta + fetch si no hay)
    templates = await _get_hsm_templates(store)
    if not templates:
        logger.info("retargeting_no_templates", phone=phone)
        return False

    # Formatear lista de templates para el LLM
    def _body_text(t):
        # Meta templates: components is a list; el body component tiene "text"
        comps = t.get("components") or []
        for c in comps:
            if c.get("type") == "BODY":
                return (c.get("text") or "")[:150]
        return (t.get("body") or "")[:150]

    template_list = "\n".join(
        f"- {t.get('name')}: {_body_text(t)}"
        for t in templates[:30]
        if t.get("status", "APPROVED") == "APPROVED"
    )

    settings = get_settings()
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    system = (
        "Sos el agente closer de MSK Latam (cursos médicos online). "
        "Tu tarea es decidir si enviar una plantilla HSM de WhatsApp a un lead "
        f"que lleva {days_inactive} días inactivo (label actual: {label or 'sin label'}). "
        "\n\nRespondé SOLO en JSON con los campos:\n"
        "{\n"
        '  "send": true|false,            // true si hay que mandar HSM, false para saltar\n'
        '  "template_name": "nombre",     // exacto, de la lista provista\n'
        '  "reason": "breve justificación"\n'
        "}\n\n"
        "REGLAS:\n"
        "- Si el último mensaje del cliente muestra que no está interesado, send=false.\n"
        "- Si ya hubo muchos follow-ups sin respuesta, send=false.\n"
        "- Priorizá plantillas que hagan match con el contexto (precio, info, oferta, urgencia).\n"
        "- Si no hay template adecuado, send=false.\n"
    )

    user_prompt = (
        f"Transcript (últimos mensajes):\n{transcript}\n\n"
        f"Plantillas HSM disponibles:\n{template_list}"
    )

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,
            max_tokens=200,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = resp.choices[0].message.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
        decision = json.loads(content)
    except Exception as e:
        logger.warning("retargeting_decision_failed", phone=phone, error=str(e))
        return False

    if not decision.get("send"):
        logger.info("retargeting_skip", phone=phone, reason=decision.get("reason"))
        # Marca como visto para no reevaluar todo el día
        await store._redis.setex(f"retarget_sent:{phone}:day{days_inactive}", 86400, "skipped")
        return False

    template_name = decision.get("template_name", "").strip()
    if not any(t.get("name") == template_name for t in templates):
        logger.warning("retargeting_invalid_template", phone=phone, suggested=template_name)
        return False

    # Enviar HSM
    wa = WhatsAppMetaClient()
    try:
        await wa.send_template(phone, template_name, language_code="es_AR")
        logger.info("retargeting_sent", phone=phone, template=template_name, day=days_inactive, reason=decision.get("reason"))
        # Marca como enviado (evitar re-envío el mismo día)
        await store._redis.setex(f"retarget_sent:{phone}:day{days_inactive}", 86400 * 3, template_name)
        return True
    except Exception as e:
        logger.error("retargeting_send_failed", phone=phone, template=template_name, error=str(e))
        return False


async def _get_hsm_templates(store) -> list[dict]:
    """Lee templates desde Redis cache; si no hay, fetchea de Meta y cachea 5min."""
    cached = await store._redis.get("hsm_templates_cache")
    if cached:
        try:
            data = cached.decode() if isinstance(cached, bytes) else cached
            return json.loads(data)
        except Exception:
            pass
    # Fetch de Meta
    try:
        from integrations.whatsapp_meta import WhatsAppMetaClient
        wa = WhatsAppMetaClient()
        templates = await wa.get_templates() or []
        if templates:
            await store._redis.setex("hsm_templates_cache", 300, json.dumps(templates, ensure_ascii=False))
        return templates
    except Exception as e:
        logger.warning("hsm_fetch_failed", error=str(e))
        return []


# ── Auto-retry descartados ──────────────────────────────────────────────────

async def run_auto_retry_cycle() -> None:
    """
    Cada día a las 10am:
    busca conversaciones cerradas con category='descartado' hace >=20 días
    y las reactiva (status=active) para que el retargeting las vuelva a considerar.
    """
    try:
        from memory import postgres_store
        if not postgres_store.is_enabled():
            return

        cutoff = datetime.now(timezone.utc) - timedelta(days=20)
        pool = await postgres_store.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                select id, external_id, channel, updated_at
                from public.conversations
                where status = 'closed'
                  and context->'closing_note'->>'category' = 'descartado'
                  and updated_at < $1
                  and (context->'closing_note'->>'retry_attempted' is null
                       or context->'closing_note'->>'retry_attempted' = 'false')
                limit 100
                """,
                cutoff,
            )

        reactivated = 0
        from memory.conversation_store import get_conversation_store
        from config.constants import Channel, ConversationStatus

        store = await get_conversation_store()
        for r in rows:
            try:
                conv = await store.get(str(r["id"]))
                if not conv:
                    continue
                conv.status = ConversationStatus.ACTIVE
                cn = conv.context.get("closing_note") or {}
                cn["retry_attempted"] = "true"
                cn["retry_at"] = datetime.now(timezone.utc).isoformat()
                conv.context["closing_note"] = cn
                await store.save(conv)
                reactivated += 1
                logger.info("auto_retry_reactivated", session_id=r["external_id"])
            except Exception as e:
                logger.warning("auto_retry_lead_failed", session_id=r["external_id"], error=str(e))

        logger.info("auto_retry_cycle_end", reactivated=reactivated, candidates=len(rows))
    except Exception as e:
        logger.error("auto_retry_crashed", error=str(e))
