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
  POST /api/inbox/conversations/{id}/classify   {lifecycle}
  POST /api/inbox/conversations/{id}/queue      {queue}
  POST /api/inbox/conversations/{id}/bot        {paused}
  POST /api/inbox/conversations/{id}/tags       {add?, remove?}
  POST /api/inbox/conversations/{id}/takeover

  POST /api/inbox/bulk/assign     {ids[], agent_id|null}
  POST /api/inbox/bulk/status     {ids[], status}

  POST /api/inbox/llm/correct-spelling  {text}
"""
from __future__ import annotations

from typing import Optional, Literal
from datetime import datetime, timezone, timedelta
import uuid

import structlog
from fastapi import APIRouter, HTTPException, Query, Depends, File, UploadFile
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
    tags: list[str] = []
    unread: bool = False  # placeholder (necesita read_status separado)


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    at: str
    agent: Optional[str] = None


# ─── Reads ───────────────────────────────────────────────────────────────────

@router.get("/stream")
async def stream(
    admin_key: Optional[str] = Query(None, alias="key"),
    session_token: Optional[str] = Query(None, alias="token"),
):
    """
    SSE para nuevos eventos del inbox (mensajes, asignaciones, etc).
    Auth: EventSource no permite headers custom, así que aceptamos:
      - `?token=<session_token>` (preferido, lo que usa el frontend logueado).
      - `?key=<admin_key>` (compat para herramientas internas/scripts).
    Reusa el bus broadcast del inbox legacy.
    """
    from config.settings import get_settings
    expected = get_settings().app_secret_key  # mismo que verify_admin_key

    authed = False
    # 1. Token de sesión: misma validación que get_current_user (Redis).
    if session_token:
        from memory.conversation_store import get_conversation_store
        store = await get_conversation_store()
        sess = await store._redis.get(f"session:{session_token}")
        if sess:
            authed = True
    # 2. Admin key: solo el secreto real, sin fallback hardcodeado.
    #    (Antes aceptábamos también "change-this-secret" como fallback —
    #    eso permitía que el frontend bypaseara el login en cualquier env
    #    que no hubiera rotado el secret. Removido.)
    if not authed and admin_key and admin_key == expected:
        authed = True

    if not authed:
        raise HTTPException(401, "no autenticado")

    from api.inbox import _sse_clients
    from fastapi.responses import StreamingResponse
    import asyncio
    import json as _json

    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _sse_clients.add(queue)

    async def event_gen():
        try:
            yield "retry: 5000\n\n"
            yield ": connected\n\n"
            while True:
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"data: {_json.dumps(evt)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            _sse_clients.discard(queue)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.get("/agents", response_model=list[AgentOut])
async def list_agents():
    return await cm.list_agents()


# NOTE: POST/DELETE /agents fueron eliminados en migration 004.
# Los agentes humanos = profiles. Para crearlos/borrarlos usar:
#   POST   /auth/users
#   DELETE /auth/users/{profile_id}
# El GET /agents de arriba lee de profiles via cm.list_agents().


# Países "primarios" — los que tienen su propia sub-cola en el inbox.
# El resto se agrupa bajo "MP" (multi-país).
PRIMARY_COUNTRIES = {"AR", "CL", "EC", "MX", "CO"}


@router.get("/analytics")
async def analytics(days: int = 30):
    """Métricas básicas para la página /analytics."""
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        # Conteos generales — usamos f-string con int(days) para evitar SQL injection
        # y sortear el bug de asyncpg con $1::interval (que espera timedelta, no str)
        total_convs = await conn.fetchval(f"select count(*) from public.conversations where created_at > now() - interval '{int(days)} days'")
        total_msgs = await conn.fetchval(f"select count(*) from public.messages where created_at > now() - interval '{int(days)} days'")
        active_today = await conn.fetchval("select count(*) from public.conversations where updated_at > now() - interval '24 hours'")
        resolved_count = await conn.fetchval("select count(*) from public.conversation_meta where status = 'resolved'")
        # Convs por día
        daily = await conn.fetch(f"""
            select date_trunc('day', created_at)::date as day, count(*)::int as cnt
            from public.conversations
            where created_at > now() - interval '{int(days)} days'
            group by 1 order by 1
        """)
        # Por canal
        by_channel = await conn.fetch(f"""
            select channel, count(*)::int as cnt
            from public.conversations
            where created_at > now() - interval '{int(days)} days'
            group by 1
        """)
        # Por queue
        by_queue = await conn.fetch(f"""
            select coalesce(cm.queue, 'sales') as queue, count(*)::int as cnt
            from public.conversations c
            left join public.conversation_meta cm on cm.conversation_id = c.id
            where c.created_at > now() - interval '{int(days)} days'
            group by 1
        """)
        # Por país
        by_country = await conn.fetch(f"""
            select upper(coalesce(user_profile->>'country', 'AR')) as cc, count(*)::int as cnt
            from public.conversations
            where created_at > now() - interval '{int(days)} days'
            group by 1 order by 2 desc limit 10
        """)
        # Lifecycle
        by_lifecycle = await conn.fetch(f"""
            select coalesce(cm.lifecycle_override, cm.lifecycle_auto, 'new') as lc, count(*)::int as cnt
            from public.conversations c
            left join public.conversation_meta cm on cm.conversation_id = c.id
            where c.created_at > now() - interval '{int(days)} days'
            group by 1
        """)

    return {
        "totals": {
            "conversations": total_convs or 0,
            "messages": total_msgs or 0,
            "active_today": active_today or 0,
            "resolved": resolved_count or 0,
        },
        "daily": [{"day": str(r["day"]), "count": r["cnt"]} for r in daily],
        "by_channel":   {r["channel"]: r["cnt"] for r in by_channel},
        "by_queue":     {r["queue"]: r["cnt"] for r in by_queue},
        "by_country":   {r["cc"]: r["cnt"] for r in by_country},
        "by_lifecycle": {r["lc"]: r["cnt"] for r in by_lifecycle},
    }


@router.get("/courses")
async def list_courses(country: str = "AR", limit: int = 200):
    """Lista de cursos del país con pitch_hook + pitch_by_profile."""
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            select slug, title, categoria, currency, max_installments,
                   price_installments, pitch_hook, pitch_by_profile,
                   (raw->'kb_ai') is not null as has_kb_ai
            from public.courses
            where country = $1
            order by categoria asc, title asc
            limit $2
            """,
            country.lower(), limit,
        )
    out = []
    import json as _json
    for r in rows:
        pbp = r["pitch_by_profile"]
        if isinstance(pbp, str):
            try: pbp = _json.loads(pbp)
            except Exception: pbp = {}
        out.append({
            "slug": r["slug"],
            "title": r["title"],
            "categoria": r["categoria"],
            "currency": r["currency"],
            "max_installments": r["max_installments"],
            "price_installments": float(r["price_installments"]) if r["price_installments"] else None,
            "pitch_hook": r["pitch_hook"],
            "pitch_by_profile": pbp,
            "has_kb_ai": bool(r["has_kb_ai"]),
        })
    return out


class UpdatePitchHookBody(BaseModel):
    pitch_hook: str

@router.put("/courses/{country}/{slug}/pitch-hook")
async def update_pitch_hook(country: str, slug: str, body: UpdatePitchHookBody):
    """Edita manualmente el pitch_hook de un curso."""
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "update public.courses set pitch_hook=$1 where country=$2 and slug=$3",
            body.pitch_hook, country.lower(), slug,
        )
    # Invalidar cache Redis del curso
    try:
        from integrations import courses_cache
        await courses_cache.invalidate_country(country.lower())
    except Exception:
        pass
    return {"ok": True}


@router.get("/queue-stats")
async def queue_stats():
    """
    Conteo de conversaciones por (queue, country). Cada cola siempre devuelve
    las mismas 6 sub-categorías: AR, CL, EC, MX, CO, MP. Países no primarios
    se acumulan en "MP" (multi-país).
    """
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

    # Inicializar las 3 colas × 6 categorias en cero
    out: dict[str, dict[str, int]] = {
        q: {c: 0 for c in [*sorted(PRIMARY_COUNTRIES), "MP"]}
        for q in ("sales", "billing", "post-sales")
    }
    for r in rows:
        q = r["queue"]; c = r["country"]; n = r["cnt"]
        if q not in out:
            continue  # ignora queues no oficiales
        bucket = c if c in PRIMARY_COUNTRIES else "MP"
        out[q][bucket] = out[q].get(bucket, 0) + n
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
        # cm.assigned_agent_id es uuid (FK a profiles.id) — cast explícito necesario
        # porque asyncpg pasa el query param como text y postgres no infiere uuid=text
        where_parts.append(f"cm.assigned_agent_id = ${idx}::uuid")
        params.append(assigned_to); idx += 1
    if queue:
        where_parts.append(f"cm.queue = ${idx}")
        params.append(queue); idx += 1
    if country:
        # Filtro especial: "MP" = multi-país (todo país NO primario)
        if country.upper() == "MP":
            primary_list = ",".join(f"'{c}'" for c in sorted(PRIMARY_COUNTRIES))
            where_parts.append(
                f"upper(coalesce(c.user_profile->>'country', 'AR')) NOT IN ({primary_list})"
            )
        else:
            where_parts.append(
                f"upper(coalesce(c.user_profile->>'country', 'AR')) = upper(${idx})"
            )
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
    elif view == "resolved":
        where_parts.append("cm.status = 'resolved'")
    elif view in (None, "all"):
        # default: ocultar resueltas
        where_parts.append("(cm.status is null OR cm.status != 'resolved')")

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
            assigned_agent_id=str(r["assigned_agent_id"]) if r["assigned_agent_id"] else None,
            status=r["status"] or "open",
            lifecycle=r["lifecycle"] or "new",
            lifecycle_is_manual=bool(r["lifecycle_is_manual"]),
            queue=r["queue"] or "sales",
            bot_paused=bool(r["bot_paused"]),
            needs_human=bool(r["needs_human"]),
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
            "attachments": meta.get("attachments") or [],
            "at": r["created_at"].isoformat(),
        })
    return out


@router.get("/conversations/{conv_id}/ai-insights")
async def get_ai_insights(conv_id: str):
    """
    Genera resumen + próximo paso + razones del scoring de la conversación
    usando OpenAI gpt-4o-mini con los últimos 20 mensajes como contexto.
    Cacheado en Redis 5 min (no regenerar en cada refresh).
    """
    from memory.conversation_store import get_conversation_store
    import json as _json

    store = await get_conversation_store()
    cache_key = f"ai_insights:{conv_id}"

    # Cache hit
    cached = await store._redis.get(cache_key)
    if cached:
        try:
            return _json.loads(cached)
        except Exception:
            pass

    # Cargar mensajes
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            select role, content, metadata, created_at
            from public.messages
            where conversation_id = $1
            order by created_at desc
            limit 20
            """,
            conv_id,
        )
        # Cargar perfil del usuario también
        conv_row = await conn.fetchrow(
            "select user_profile from public.conversations where id = $1",
            conv_id,
        )

    if not rows:
        return {
            "summary": "Conversación sin mensajes todavía.",
            "nextStep": "Esperar al primer turno del usuario.",
            "scoringReasons": [],
        }

    # Reconstruir cronológico
    msgs = list(reversed(rows))
    transcript = "\n".join(
        f"[{m['role']}] {m['content'][:300]}" for m in msgs
    )
    profile = conv_row["user_profile"] if conv_row else {}
    if isinstance(profile, str):
        try: profile = _json.loads(profile)
        except Exception: profile = {}

    profile_str = "\n".join(f"  - {k}: {v}" for k, v in (profile or {}).items() if v) or "  (sin datos)"

    from openai import AsyncOpenAI
    from config.settings import get_settings
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(500, "OPENAI_API_KEY no configurada")

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    system = """Sos un asistente que analiza conversaciones del bot de ventas de MSK Latam (cursos médicos) y produce insights accionables para el agente humano.

Devolvé SOLO un JSON con esta estructura:
{
  "summary": "<resumen 1-2 oraciones de la conversación>",
  "nextStep": "<próximo paso recomendado para cerrar la venta o avanzar — 1-2 oraciones>",
  "scoringReasons": ["<razón 1>", "<razón 2>", "<razón 3>", "<razón 4>"]
}

Reglas:
- summary: descriptivo, sin opinión
- nextStep: accionable, concreto (ej: "Enviar link de pago AMIR + cupón BOT20 si no responde en 24h")
- scoringReasons: 3-5 bullets cortos sobre por qué este lead es caliente/tibio/frio
- En español argentino profesional
- DEVOLVER SOLO EL JSON, sin comillas decorativas ni texto extra."""

    user_msg = f"PERFIL DEL CONTACTO:\n{profile_str}\n\nTRANSCRIPCIÓN (últimos {len(msgs)} mensajes):\n{transcript}"

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.3,
            max_tokens=600,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
        )
        data = _json.loads(resp.choices[0].message.content)
    except Exception as e:
        logger.warning("ai_insights_failed", conv_id=conv_id, error=str(e))
        return {
            "summary": "(no se pudieron generar insights — error LLM)",
            "nextStep": "Revisar manualmente la conversación.",
            "scoringReasons": [],
        }

    out = {
        "summary": data.get("summary") or "",
        "nextStep": data.get("nextStep") or "",
        "scoringReasons": data.get("scoringReasons") or [],
    }
    # Cache 5 min
    await store._redis.setex(cache_key, 300, _json.dumps(out))
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
    from utils.inbox_jobs import log_action
    await log_action("system", "assign", conv_id, {"agent_id": body.agent_id})
    return {"ok": True}


@router.get("/audit-log")
async def get_audit_log(limit: int = 100, conversation_id: Optional[str] = None, actor_id: Optional[str] = None):
    from utils.inbox_jobs import list_audit_log
    return await list_audit_log(limit=limit, conversation_id=conversation_id, actor_id=actor_id)

class StatusBody(BaseModel):
    status: Literal["open", "pending", "resolved"]

@router.post("/conversations/{conv_id}/status")
async def status(conv_id: str, body: StatusBody):
    await cm.set_status(conv_id, body.status)
    return {"ok": True}

class ClassifyBody(BaseModel):
    lifecycle: Literal["new", "hot", "customer", "cold"]

@router.post("/conversations/{conv_id}/classify")
async def classify(conv_id: str, body: ClassifyBody):
    await cm.classify(conv_id, body.lifecycle)
    from utils.inbox_jobs import log_action
    await log_action("system", "classify", conv_id, {"lifecycle": body.lifecycle})
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


# ─── Upload de adjuntos a R2 ─────────────────────────────────────────────────

@router.post("/upload")
async def upload_attachment(file: UploadFile = File(...)):
    """
    Sube un archivo a R2 y devuelve la URL pública. Usado por el composer
    para audio + adjuntos antes de enviarlos al canal.
    """
    from integrations import storage
    if not storage.is_enabled():
        raise HTTPException(503, "R2 storage no configurado en el server")

    data = await file.read()
    if not data:
        raise HTTPException(400, "archivo vacio")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "archivo demasiado grande (max 25MB)")

    # Key por fecha/uuid para no colisionar
    today = datetime.utcnow().strftime("%Y/%m/%d")
    safe_name = file.filename.replace("/", "_") if file.filename else "file"
    key = f"inbox/{today}/{uuid.uuid4().hex[:8]}-{safe_name}"

    try:
        url = await storage.upload_bytes(
            key, data,
            content_type=file.content_type or "application/octet-stream",
        )
    except Exception as e:
        logger.error("upload_failed", error=str(e))
        raise HTTPException(500, f"upload failed: {e}")

    return {
        "ok": True,
        "url": url,
        "key": key,
        "size": len(data),
        "content_type": file.content_type,
        "filename": file.filename,
    }


# ─── Enviar mensaje desde el back-office (humano) ────────────────────────────

class Attachment(BaseModel):
    url: str
    filename: Optional[str] = None
    content_type: Optional[str] = None
    size: Optional[int] = None

class SendMessageBody(BaseModel):
    text: str = ""
    agent_id: Optional[str] = None
    agent_name: Optional[str] = "Agente humano"
    attachments: list[Attachment] = Field(default_factory=list)

@router.post("/conversations/{conv_id}/send")
async def send_message(conv_id: str, body: SendMessageBody):
    """
    Envía un mensaje desde la nueva UI del back-office (intervención humana).
    Por ahora SOLO soporta widget — para WhatsApp queda pendiente.

    Soporta texto + lista de adjuntos (URLs ya subidas vía /upload).

    Reusa exactamente el flujo del endpoint legacy `/inbox/{sid}/reply`:
      1. Persiste el mensaje como role='assistant' con metadata.agent='humano'
      2. Pausa el bot + asigna al agente
      3. Broadcast del evento al SSE del inbox (lo recibe el widget abierto)
    """
    text = body.text.strip()
    if not text and not body.attachments:
        raise HTTPException(400, "text y attachments vacíos — al menos uno requerido")

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

    if channel not in ("widget", "whatsapp"):
        raise HTTPException(
            501,
            f"Canal '{channel}' aún no soportado para envío desde la UI nueva."
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
    ch_enum = Ch.WHATSAPP if channel == "whatsapp" else Ch.WIDGET
    conv = await store.get_by_external(ch_enum, external_id)
    if not conv:
        raise HTTPException(404, "Conversación no encontrada en store")

    # Si hay texto vacío y solo adjuntos, armar un placeholder legible
    final_text = text
    if not final_text and body.attachments:
        parts = []
        for a in body.attachments:
            if a.content_type and a.content_type.startswith("audio/"):
                parts.append(f"🎤 Mensaje de voz")
            elif a.content_type and a.content_type.startswith("image/"):
                parts.append(f"🖼 {a.filename or 'imagen'}")
            else:
                parts.append(f"📎 {a.filename or 'archivo'}")
        final_text = " · ".join(parts)

    attachments_meta = [a.dict() for a in body.attachments] if body.attachments else []

    msg = Message(
        role=MessageRole.ASSISTANT,
        content=final_text,
        metadata={
            "agent": "humano",
            "sender_name": body.agent_name or "Agente",
            "attachments": attachments_meta,
        },
    )
    await store.append_message(conv, msg)

    # Broadcast al SSE — UI del back-office se entera al toque
    broadcast_event({
        "type": "new_message",
        "session_id": external_id,
        "role": "assistant",
        "content": final_text,
        "sender_name": body.agent_name or "Agente",
        "attachments": attachments_meta,
        "timestamp": msg.timestamp.isoformat(),
    })

    # Push real al canal del usuario
    if channel == "whatsapp":
        try:
            from integrations.whatsapp_meta import WhatsAppMetaClient
            wa = WhatsAppMetaClient()
            phone = (conv.context or {}).get("phone") if conv.context else None
            phone = phone or external_id
            # Enviar texto
            if final_text:
                await wa.send_text(phone, final_text)
            # Si hay adjuntos, mandar URL como mensaje aparte (Meta Cloud API
            # soporta media via URL — por ahora mandamos el link público R2)
            for a in body.attachments:
                if a.url:
                    await wa.send_text(phone, a.url)
        except Exception as e:
            logger.warning("inbox_send_wa_failed", conv_id=conv_id, error=str(e))
            return {"ok": False, "delivered": False, "channel": channel, "error": str(e)}

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

# (Antes vivía acá `_resolve_duration` para el snooze. Removido junto con la
#  feature de snooze.)


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
