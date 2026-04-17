"""
API REST para la nueva UI del inbox (frontend Next.js).

Endpoints:
  GET  /api/inbox/agents
  GET  /api/inbox/conversations           (filtros + paginación)
  GET  /api/inbox/conversations/{id}
  GET  /api/inbox/conversations/{id}/messages
  GET  /api/inbox/contacts/{email}        (Zoho contact + cobranzas)

  POST /api/inbox/conversations/{id}/assign     {agent_id|null}
  POST /api/inbox/conversations/{id}/status     {status}
  POST /api/inbox/conversations/{id}/snooze     {until_iso|null}
  POST /api/inbox/conversations/{id}/classify   {lifecycle}
  POST /api/inbox/conversations/{id}/queue      {queue}
  POST /api/inbox/conversations/{id}/bot        {paused}
  POST /api/inbox/conversations/{id}/tags       {add?, remove?}
  POST /api/inbox/conversations/{id}/takeover

  POST /api/inbox/bulk/assign     {ids[], agent_id|null}
  POST /api/inbox/bulk/status     {ids[], status}
  POST /api/inbox/bulk/snooze     {ids[], until_iso}

  POST /api/inbox/llm/correct-spelling  {text}
"""
from __future__ import annotations

from typing import Optional, Literal
from datetime import datetime, timezone, timedelta

import structlog
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field

from memory import postgres_store, conversation_meta as cm
from integrations.zoho.contacts import ZohoContacts
from api.admin import verify_admin_key  # ya existe — protege estos endpoints

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/inbox", tags=["inbox"], dependencies=[Depends(verify_admin_key)])


# ─── Schemas ─────────────────────────────────────────────────────────────────

class AgentOut(BaseModel):
    id: str
    name: str
    email: Optional[str] = None
    initials: Optional[str] = None
    color: Optional[str] = None


class ConversationOut(BaseModel):
    id: str
    session_id: str
    channel: str
    name: str = ""
    email: str = ""
    phone: str = ""
    country: str = "AR"
    last_message: str = ""
    last_timestamp: str = ""
    message_count: int = 0
    # meta
    assigned_agent_id: Optional[str] = None
    status: str = "open"
    lifecycle: str = "new"
    lifecycle_is_manual: bool = False
    queue: str = "sales"
    bot_paused: bool = False
    needs_human: bool = False
    snoozed_until: Optional[str] = None
    tags: list[str] = []
    unread: bool = False  # placeholder (necesita read_status separado)


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    at: str
    agent: Optional[str] = None


# ─── Reads ───────────────────────────────────────────────────────────────────

@router.get("/agents", response_model=list[AgentOut])
async def list_agents():
    return await cm.list_agents()


@router.get("/queue-stats")
async def queue_stats():
    """Retorna conteo de conversaciones por (queue, country). Para el filtro
    Cola → País del inbox."""
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            select
                coalesce(cm.queue, 'sales') as queue,
                upper(coalesce(c.user_profile->>'country', 'AR')) as country,
                count(*)::int as cnt
            from public.conversations c
            left join public.conversation_meta cm on cm.conversation_id = c.id
            where coalesce(cm.status, 'open') != 'resolved'
            group by 1, 2
            order by 1, 2
        """)
    out: dict[str, dict[str, int]] = {"sales": {}, "billing": {}, "post-sales": {}}
    for r in rows:
        q = r["queue"]; c = r["country"]; n = r["cnt"]
        if q not in out:
            out[q] = {}
        out[q][c] = n
    return out


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    limit: int = Query(50, le=200),
    offset: int = 0,
    view:        Optional[str] = None,
    lifecycle:   Optional[str] = None,
    channel:     Optional[str] = None,
    queue:       Optional[str] = None,
    country:     Optional[str] = None,
    assigned_to: Optional[str] = None,
    search:      Optional[str] = None,
    date_from:   Optional[str] = None,
    date_to:     Optional[str] = None,
):
    """Lista de conversaciones con todos los filtros del inbox."""
    pool = await postgres_store.get_pool()

    where_parts = []
    params: list = []
    idx = 1

    if date_from:
        where_parts.append(f"c.updated_at >= ${idx}::timestamptz")
        params.append(date_from + "T00:00:00Z"); idx += 1
    if date_to:
        where_parts.append(f"c.updated_at <= ${idx}::timestamptz")
        params.append(date_to + "T23:59:59Z"); idx += 1
    if channel:
        where_parts.append(f"c.channel = ${idx}")
        params.append(channel); idx += 1
    if assigned_to:
        where_parts.append(f"cm.assigned_agent_id = ${idx}")
        params.append(assigned_to); idx += 1
    if queue:
        where_parts.append(f"cm.queue = ${idx}")
        params.append(queue); idx += 1
    if country:
        # filtra por user_profile.country (ISO-2). Case-insensitive.
        where_parts.append(f"upper(coalesce(c.user_profile->>'country', 'AR')) = upper(${idx})")
        params.append(country); idx += 1
    if lifecycle:
        where_parts.append(f"coalesce(cm.lifecycle_override, cm.lifecycle_auto, 'new') = ${idx}")
        params.append(lifecycle); idx += 1
    if search:
        where_parts.append(
            f"(c.user_profile->>'name' ilike ${idx} OR c.user_profile->>'email' ilike ${idx} OR lm.content ilike ${idx})"
        )
        params.append(f"%{search}%"); idx += 1

    # vistas
    if view == "unread":
        # placeholder: aún no tenemos read_status. Por ahora open + sin asignar
        where_parts.append("(cm.assigned_agent_id is null)")
    elif view == "queue":
        where_parts.append("cm.assigned_agent_id is null AND cm.needs_human = true")
    elif view == "human-attn":
        where_parts.append("(cm.bot_paused = true OR cm.assigned_agent_id is not null)")
    elif view == "with-bot":
        where_parts.append("(cm.bot_paused = false AND coalesce(cm.needs_human,false) = false)")
    elif view == "snoozed":
        where_parts.append("cm.snoozed_until is not null AND cm.snoozed_until > now()")
    elif view == "resolved":
        where_parts.append("cm.status = 'resolved'")
    elif view in (None, "all"):
        # default: ocultar resueltas y snoozed activos
        where_parts.append("(cm.status is null OR cm.status != 'resolved')")
        where_parts.append("(cm.snoozed_until is null OR cm.snoozed_until < now())")

    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    params.append(limit); idx += 1
    params.append(offset)

    sql = f"""
        SELECT
            c.id, c.channel, c.external_id, c.user_profile, c.updated_at, c.created_at,
            lm.content   AS last_message,
            lm.created_at AS last_message_at,
            mc.cnt        AS message_count,
            cm.assigned_agent_id,
            cm.status,
            cm.queue,
            cm.bot_paused,
            cm.needs_human,
            cm.snoozed_until,
            cm.tags,
            coalesce(cm.lifecycle_override, cm.lifecycle_auto, 'new') as lifecycle,
            (cm.lifecycle_override is not null) as lifecycle_is_manual
        FROM public.conversations c
        LEFT JOIN public.conversation_meta cm ON cm.conversation_id = c.id
        LEFT JOIN LATERAL (
            SELECT content, created_at
            FROM public.messages
            WHERE conversation_id = c.id
            ORDER BY created_at DESC
            LIMIT 1
        ) lm ON true
        LEFT JOIN LATERAL (
            SELECT count(*)::int AS cnt FROM public.messages WHERE conversation_id = c.id
        ) mc ON true
        {where}
        ORDER BY c.updated_at DESC
        LIMIT ${idx - 1} OFFSET ${idx}
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    out: list[ConversationOut] = []
    for r in rows:
        profile = r["user_profile"] or {}
        if isinstance(profile, str):
            import json as _json
            try:
                profile = _json.loads(profile)
            except Exception:
                profile = {}
        last_msg = (r["last_message"] or "")[:120]
        out.append(ConversationOut(
            id=str(r["id"]),
            session_id=r["external_id"] or "",
            channel=r["channel"],
            name=profile.get("name") or "",
            email=profile.get("email") or "",
            phone=profile.get("phone") or "",
            country=profile.get("country") or "AR",
            last_message=last_msg or "",
            last_timestamp=(r["last_message_at"] or r["updated_at"]).isoformat() if (r["last_message_at"] or r["updated_at"]) else "",
            message_count=r["message_count"] or 0,
            assigned_agent_id=r["assigned_agent_id"],
            status=r["status"] or "open",
            lifecycle=r["lifecycle"] or "new",
            lifecycle_is_manual=bool(r["lifecycle_is_manual"]),
            queue=r["queue"] or "sales",
            bot_paused=bool(r["bot_paused"]),
            needs_human=bool(r["needs_human"]),
            snoozed_until=r["snoozed_until"].isoformat() if r["snoozed_until"] else None,
            tags=list(r["tags"] or []),
        ))
    return out


@router.get("/conversations/{conv_id}/messages")
async def get_messages(conv_id: str):
    """Mensajes de una conversación, en orden cronológico."""
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, role, content, metadata, created_at
            FROM public.messages
            WHERE conversation_id = $1
            ORDER BY created_at ASC
            """,
            conv_id,
        )
    out = []
    for r in rows:
        meta = r["metadata"] or {}
        if isinstance(meta, str):
            import json as _json
            try:
                meta = _json.loads(meta)
            except Exception:
                meta = {}
        out.append({
            "id": str(r["id"]),
            "role": r["role"],
            "content": r["content"],
            "agent": meta.get("agent") or meta.get("sender_name"),
            "at": r["created_at"].isoformat(),
        })
    return out


@router.get("/contacts/{email}")
async def get_contact(email: str):
    """
    Trae perfil completo del contacto desde Zoho (datos personales + cursos +
    cobranzas). Para la card derecha del inbox.
    """
    z = ZohoContacts()
    profile = await z.search_by_email_with_full_profile(email)
    if not profile:
        raise HTTPException(404, f"Contacto no encontrado en Zoho: {email}")

    # Deduplicar cursos (un mismo curso puede aparecer N veces si el alumno
    # lo cursó múltiples ediciones)
    cursos_set: list[str] = []
    seen: set[str] = set()
    for c in (profile.get("Formulario_de_cursada") or [])[:50]:
        nombre = (c.get("Nombre_de_curso") or {}).get("name")
        if nombre and nombre not in seen:
            seen.add(nombre)
            cursos_set.append(nombre)
    cursos = cursos_set

    colegio = (profile.get("Colegio_Sociedad_o_Federaci_n") or [None])[0]

    return {
        "zoho_id":     profile.get("id"),
        "name":        profile.get("Full_Name"),
        "first_name":  profile.get("First_Name"),
        "last_name":   profile.get("Last_Name"),
        "email":       profile.get("Email"),
        "phone":       profile.get("Phone"),
        "country":     _country_iso(profile.get("Pais")),
        "country_name": profile.get("Pais"),
        "professional": {
            "profession":  profile.get("Profesi_n"),
            "specialty":   profile.get("Especialidad"),
            "cargo":       profile.get("Cargo"),
            "workplace":   profile.get("Lugar_de_trabajo"),
            "work_area":   profile.get("rea_donde_tabaja"),
        },
        "jurisdictional_cert":
            {"code": _colegio_code(colegio), "name": colegio} if colegio else None,
        "courses_taken": cursos,
        "scoring": {
            "profile": int(profile.get("Scoring_Perfil") or 0),
            "sales":   int(profile.get("Scoring_venta") or 0),
        },
        # Cobranzas: por ahora derivado del Zoho (más adelante: integration real)
        "cobranzas": _derive_cobranzas(profile),
    }


# ─── Mutations: por conversación ─────────────────────────────────────────────

class AssignBody(BaseModel):
    agent_id: Optional[str] = None

@router.post("/conversations/{conv_id}/assign")
async def assign(conv_id: str, body: AssignBody):
    await cm.assign(conv_id, body.agent_id)
    return {"ok": True}

class StatusBody(BaseModel):
    status: Literal["open", "pending", "resolved"]

@router.post("/conversations/{conv_id}/status")
async def status(conv_id: str, body: StatusBody):
    await cm.set_status(conv_id, body.status)
    return {"ok": True}

class SnoozeBody(BaseModel):
    until_iso: Optional[str] = None
    duration: Optional[Literal["1h", "4h", "tomorrow", "next-week"]] = None

@router.post("/conversations/{conv_id}/snooze")
async def snooze(conv_id: str, body: SnoozeBody):
    until = body.until_iso or _resolve_duration(body.duration)
    await cm.snooze(conv_id, until)
    return {"ok": True, "until": until}

class ClassifyBody(BaseModel):
    lifecycle: Literal["new", "hot", "customer", "cold"]

@router.post("/conversations/{conv_id}/classify")
async def classify(conv_id: str, body: ClassifyBody):
    await cm.classify(conv_id, body.lifecycle)
    return {"ok": True}

class QueueBody(BaseModel):
    queue: Literal["sales", "billing", "post-sales"]

@router.post("/conversations/{conv_id}/queue")
async def queue_set(conv_id: str, body: QueueBody):
    await cm.set_queue(conv_id, body.queue)
    return {"ok": True}

class BotBody(BaseModel):
    paused: bool

@router.post("/conversations/{conv_id}/bot")
async def bot_toggle(conv_id: str, body: BotBody):
    await cm.set_bot_paused(conv_id, body.paused)
    return {"ok": True}

class TagsBody(BaseModel):
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)

@router.post("/conversations/{conv_id}/tags")
async def tags(conv_id: str, body: TagsBody):
    if body.add:
        await cm.add_tags(conv_id, body.add)
    for t in body.remove:
        await cm.remove_tag(conv_id, t)
    return {"ok": True}


@router.post("/conversations/{conv_id}/takeover")
async def takeover(conv_id: str, body: AssignBody):
    """Tomar control humano: pausa bot + asigna al agente + saca needs_human."""
    if body.agent_id:
        await cm.assign(conv_id, body.agent_id)
    await cm.set_bot_paused(conv_id, True)
    await cm.set_needs_human(conv_id, False)
    return {"ok": True}


# ─── Enviar mensaje desde el back-office (humano) ────────────────────────────

class SendMessageBody(BaseModel):
    text: str
    agent_id: Optional[str] = None
    agent_name: Optional[str] = "Agente humano"

@router.post("/conversations/{conv_id}/send")
async def send_message(conv_id: str, body: SendMessageBody):
    """
    Envía un mensaje desde la nueva UI del back-office (intervención humana).
    Por ahora SOLO soporta widget — para WhatsApp queda pendiente.

    Reusa exactamente el flujo del endpoint legacy `/inbox/{sid}/reply`:
      1. Persiste el mensaje como role='assistant' con metadata.agent='humano'
      2. Pausa el bot + asigna al agente
      3. Broadcast del evento al SSE del inbox (lo recibe el widget abierto)
    """
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "text vacío")

    # 1) Cargar conversación
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "select id, channel, external_id from public.conversations where id=$1",
            conv_id,
        )
    if not row:
        raise HTTPException(404, "Conversación no encontrada")

    channel = row["channel"]
    external_id = row["external_id"]

    if channel != "widget":
        raise HTTPException(
            501,
            f"Canal '{channel}' aún no soportado para envío desde la UI nueva. Solo widget por ahora."
        )

    # 2) Marcar takeover en meta
    await cm.set_bot_paused(conv_id, True)
    if body.agent_id:
        await cm.assign(conv_id, body.agent_id)
    await cm.set_needs_human(conv_id, False)

    # 3) Persistir + broadcast usando el mismo flujo que el inbox legacy
    from memory.conversation_store import get_conversation_store
    from models.message import Message, MessageRole
    from config.constants import Channel as Ch
    from api.inbox import broadcast_event
    import time as _time

    store = await get_conversation_store()
    conv = await store.get_by_external(Ch.WIDGET, external_id)
    if not conv:
        raise HTTPException(404, "Conversación no encontrada en store")

    msg = Message(
        role=MessageRole.ASSISTANT,
        content=text,
        metadata={"agent": "humano", "sender_name": body.agent_name or "Agente"},
    )
    await store.append_message(conv, msg)

    # Broadcast al SSE — el widget abierto recibe este evento
    broadcast_event({
        "type": "new_message",
        "session_id": external_id,
        "role": "assistant",
        "content": text,
        "sender_name": body.agent_name or "Agente",
        "timestamp": msg.timestamp.isoformat(),
    })

    # Limpiar typing lock
    try:
        await store._redis.delete(f"typing:{external_id}")
        await store._redis.set(f"last_reply:{external_id}", str(_time.time()))
    except Exception:
        pass

    return {"ok": True, "delivered": True, "channel": channel}


# ─── Bulk ────────────────────────────────────────────────────────────────────

class BulkAssignBody(BaseModel):
    ids: list[str]
    agent_id: Optional[str] = None

@router.post("/bulk/assign")
async def bulk_assign(body: BulkAssignBody):
    n = await cm.bulk_assign(body.ids, body.agent_id)
    return {"ok": True, "updated": n}

class BulkStatusBody(BaseModel):
    ids: list[str]
    status: Literal["open", "pending", "resolved"]

@router.post("/bulk/status")
async def bulk_status(body: BulkStatusBody):
    n = await cm.bulk_set_status(body.ids, body.status)
    return {"ok": True, "updated": n}

class BulkSnoozeBody(BaseModel):
    ids: list[str]
    until_iso: Optional[str] = None
    duration: Optional[Literal["1h", "4h", "tomorrow", "next-week"]] = None

@router.post("/bulk/snooze")
async def bulk_snooze(body: BulkSnoozeBody):
    until = body.until_iso or _resolve_duration(body.duration)
    if not until:
        raise HTTPException(400, "until_iso o duration requeridos")
    n = await cm.bulk_snooze(body.ids, until)
    return {"ok": True, "updated": n}


# ─── LLM: Corrección ortográfica ─────────────────────────────────────────────

class CorrectBody(BaseModel):
    text: str
    style: Literal["professional", "casual"] = "professional"

@router.post("/llm/correct-spelling")
async def correct_spelling(body: CorrectBody):
    """Corrige ortografía + gramática usando OpenAI, respetando el estilo."""
    if not body.text.strip():
        return {"corrected": body.text, "changed": False}

    from openai import AsyncOpenAI
    from config.settings import get_settings

    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(500, "OPENAI_API_KEY no configurada")

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    style_hint = (
        "tono profesional con tuteo rioplatense (vos, tenés)"
        if body.style == "professional"
        else "tono casual y amistoso"
    )

    system = (
        "Sos un corrector ortográfico y gramatical en español argentino. "
        f"Devolvé EXACTAMENTE el texto del usuario corregido, manteniendo el {style_hint}. "
        "Reglas:\n"
        "- Corregí typos, abreviaturas (q→que, xq→porque, tmb→también, etc.)\n"
        "- Capitalizá inicio de oración, agregá tildes faltantes\n"
        "- Espacios después de coma/punto si faltan\n"
        "- NO cambies el sentido, NO agregues contenido nuevo\n"
        "- NO uses comillas alrededor de la respuesta, NO expliques nada\n"
        "- Devolvé SOLO el texto corregido"
    )

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.1,
            max_tokens=400,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": body.text},
            ],
        )
        corrected = resp.choices[0].message.content.strip()
        # Limpiar comillas decorativas si el LLM las puso igual
        if corrected.startswith('"') and corrected.endswith('"'):
            corrected = corrected[1:-1]
        return {"corrected": corrected, "changed": corrected != body.text}
    except Exception as e:
        logger.error("correct_spelling_failed", error=str(e))
        raise HTTPException(500, f"LLM correction failed: {e}")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _resolve_duration(d: Optional[str]) -> Optional[str]:
    if not d:
        return None
    now = datetime.now(timezone.utc)
    if d == "1h":
        delta = timedelta(hours=1)
    elif d == "4h":
        delta = timedelta(hours=4)
    elif d == "tomorrow":
        # mañana 9am hora local del server (UTC-3 AR)
        tomorrow = (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
        return tomorrow.isoformat()
    elif d == "next-week":
        delta = timedelta(days=7)
    else:
        return None
    return (now + delta).isoformat()


_COUNTRY_MAP = {
    "Argentina": "AR", "México": "MX", "Mexico": "MX", "Chile": "CL",
    "Colombia": "CO", "Perú": "PE", "Peru": "PE", "Uruguay": "UY",
    "Ecuador": "EC", "España": "ES", "Espana": "ES", "Bolivia": "BO",
    "Paraguay": "PY", "Venezuela": "VE", "Costa Rica": "CR",
    "Guatemala": "GT", "Honduras": "HN", "Nicaragua": "NI",
    "Panamá": "PA", "Panama": "PA", "El Salvador": "SV",
}

def _country_iso(country: Optional[str]) -> str:
    if not country:
        return "AR"
    return _COUNTRY_MAP.get(country, "AR")


_COLEGIO_MAP = {
    "Colegio de Médicos de la Provincia de Misiones": "COLEMEMI",
    "Colegio de Médicos de Catamarca": "COLMEDCAT",
    "Consejo Superior Médico de La Pampa": "CSMLP",
    "Consejo Médico de Santa Cruz": "CMSC",
    "Colegio de Médicos de Santa Fe 1ra": "CMSF1",
}

def _colegio_code(colegio: Optional[str]) -> Optional[str]:
    if not colegio:
        return None
    return _COLEGIO_MAP.get(colegio)


def _derive_cobranzas(profile: dict) -> Optional[dict]:
    """
    Deriva info de cobranzas desde el Zoho contact.
    Por ahora simplificado: solo si tiene cursadas con Estado_de_OV.
    Más adelante: query a Sales Orders / payments real de Zoho.
    """
    cursadas = profile.get("Formulario_de_cursada") or []
    if not cursadas:
        return None
    # Heurística simple: si hay activas, mostrar resumen; si no, omitir
    activas = [c for c in cursadas if c.get("Estado_de_OV") == "Activo"]
    if not activas:
        return None

    return {
        "status": "ok",
        "currency": "ARS",
        "overdueAmount":      0,
        "totalDueAmount":     0,
        "contractAmount":     0,
        "installmentValue":   0,
        "lastPaymentAmount":  0,
        "totalInstallments":  len(activas),
        "paidInstallments":   0,
        "overdueInstallments": 0,
        "pendingInstallments": len(activas),
        "daysOverdue":     0,
        "contractStatus":  "Activo",
        "paymentMethod":   "Configurar en Zoho",
        "nextDue":         None,
        "paymentLink":     None,
        # Nota: completar cuando integremos sales_orders Zoho
        "_pending_integration": True,
    }
