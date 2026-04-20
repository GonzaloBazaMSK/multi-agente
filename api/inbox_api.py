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

import uuid
from datetime import datetime
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from api.admin import require_role_or_admin, verify_admin_or_session  # admin key + session + role gate
from integrations.zoho.contacts import ZohoContacts
from memory import conversation_meta as cm
from memory import postgres_store
from utils.rate_limits import BULK_OPS_PER_USER, LLM_PER_USER, UPLOAD_PER_USER, limiter

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/inbox", tags=["inbox"], dependencies=[Depends(verify_admin_or_session)])


# ─── Schemas ─────────────────────────────────────────────────────────────────


class AgentOut(BaseModel):
    id: str
    name: str
    email: str | None = None
    initials: str | None = None
    color: str | None = None


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
    assigned_agent_id: str | None = None
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
    agent: str | None = None


# ─── Reads ───────────────────────────────────────────────────────────────────


@router.get("/stream")
async def stream():
    """
    SSE para nuevos eventos del inbox (mensajes, asignaciones, etc).

    Auth: heredada del router — `Depends(verify_admin_or_session)` ya
    validó cookie `msk_session` (preferido), header `x-session-token`
    (compat) o admin_key. EventSource moderno del browser manda la
    cookie automáticamente si se abre con `withCredentials: true`.

    No hace falta soporte de `?token=` en query — eso fue el workaround
    de la era localStorage, cuando el JS no podía setear headers sobre
    EventSource. Con cookies httpOnly el browser se encarga solo.
    """
    import asyncio
    import json as _json

    from fastapi.responses import StreamingResponse

    from utils.realtime import _sse_clients

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
                except TimeoutError:
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

# Prefijo del nombre de cola (definidos en api/auth.py ALL_QUEUES) → valor
# almacenado en conversation_meta.queue. Los agentes tienen colas tipo
# "ventas_AR", "cobranzas_MX"... que mapean a (queue, country).
QUEUE_PREFIX_MAP = {
    "ventas": "sales",
    "cobranzas": "billing",
    "post_venta": "post-sales",
}


def _agent_queue_scope_sql(user_queues: list[str]) -> str | None:
    """Arma el fragmento SQL que restringe las conversaciones visibles a un
    agente según sus colas asignadas (de `profiles.queues`).

    Cada cola del agente es tipo `ventas_AR`, `cobranzas_MP`, etc. El
    fragmento resultante va OR-ed entre todas las colas del agente.

    Retorna `None` si el agente no tiene colas (→ sin visibilidad fuera de
    conversaciones asignadas directamente a él, enforced aparte).
    Retorna SQL literal (sin params) porque los valores vienen de un enum
    cerrado definido en código (no user input).
    """
    if not user_queues:
        return None
    primary_list = ",".join(f"'{c}'" for c in sorted(PRIMARY_COUNTRIES))
    pieces: list[str] = []
    for raw in user_queues:
        if not isinstance(raw, str) or "_" not in raw:
            continue
        prefix, country = raw.rsplit("_", 1)
        queue_val = QUEUE_PREFIX_MAP.get(prefix)
        if not queue_val:
            continue
        country = country.upper()
        if country == "MP":
            pieces.append(
                f"(cm.queue = '{queue_val}' AND "
                f"upper(coalesce(c.user_profile->>'country','AR')) NOT IN ({primary_list}))"
            )
        else:
            # País fijo — validamos que sea [A-Z]{2} para cerrar toda vía de
            # injection incluso si alguien mete una queue rara en profiles.
            if not (len(country) == 2 and country.isalpha()):
                continue
            pieces.append(
                f"(cm.queue = '{queue_val}' AND "
                f"upper(coalesce(c.user_profile->>'country','AR')) = '{country}')"
            )
    if not pieces:
        return None
    return "(" + " OR ".join(pieces) + ")"


@router.get("/analytics")
async def analytics(days: int = 30):
    """Métricas básicas para la página /analytics."""
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        # Conteos generales — usamos f-string con int(days) para evitar SQL injection
        # y sortear el bug de asyncpg con $1::interval (que espera timedelta, no str)
        total_convs = await conn.fetchval(
            f"select count(*) from public.conversations where created_at > now() - interval '{int(days)} days'"
        )
        total_msgs = await conn.fetchval(
            f"select count(*) from public.messages where created_at > now() - interval '{int(days)} days'"
        )
        active_today = await conn.fetchval(
            "select count(*) from public.conversations where updated_at > now() - interval '24 hours'"
        )
        resolved_count = await conn.fetchval(
            "select count(*) from public.conversation_meta where status = 'resolved'"
        )
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
        "by_channel": {r["channel"]: r["cnt"] for r in by_channel},
        "by_queue": {r["queue"]: r["cnt"] for r in by_queue},
        "by_country": {r["cc"]: r["cnt"] for r in by_country},
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
            country.lower(),
            limit,
        )
    out = []
    import json as _json

    for r in rows:
        pbp = r["pitch_by_profile"]
        if isinstance(pbp, str):
            try:
                pbp = _json.loads(pbp)
            except Exception:
                pbp = {}
        out.append(
            {
                "slug": r["slug"],
                "title": r["title"],
                "categoria": r["categoria"],
                "currency": r["currency"],
                "max_installments": r["max_installments"],
                "price_installments": float(r["price_installments"]) if r["price_installments"] else None,
                "pitch_hook": r["pitch_hook"],
                "pitch_by_profile": pbp,
                "has_kb_ai": bool(r["has_kb_ai"]),
            }
        )
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
            body.pitch_hook,
            country.lower(),
            slug,
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
        q: {c: 0 for c in [*sorted(PRIMARY_COUNTRIES), "MP"]} for q in ("sales", "billing", "post-sales")
    }
    for r in rows:
        q = r["queue"]
        c = r["country"]
        n = r["cnt"]
        if q not in out:
            continue  # ignora queues no oficiales
        bucket = c if c in PRIMARY_COUNTRIES else "MP"
        out[q][bucket] = out[q].get(bucket, 0) + n
    return out


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    limit: int = Query(50, le=200),
    offset: int = 0,
    view: str | None = None,
    lifecycle: str | None = None,
    channel: str | None = None,
    queue: str | None = None,
    country: str | None = None,
    assigned_to: str | None = None,
    search: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    auth: dict = Depends(verify_admin_or_session),
):
    """Lista de conversaciones con todos los filtros del inbox.

    Scope según rol del user logueado (ignorado si el caller usa admin key):
      - admin / supervisor → ven todo.
      - agente → solo conversaciones asignadas a él, o sin asignar dentro de
        sus colas (profiles.queues). Este enforcement es server-side para que
        un agente no pueda ver convs ajenas aunque forcee un query param.
    """
    pool = await postgres_store.get_pool()

    where_parts = []
    params: list = []
    idx = 1

    # Scope por rol — ANTES de los filtros del user, para que la query base
    # ya vuelva solo lo que el agente puede ver. Admin key (scripts) y roles
    # admin/supervisor pasan sin restricción.
    user = (auth or {}).get("user") if (auth or {}).get("auth") == "session" else None
    if user and user.get("role") == "agente":
        agent_id = user.get("id")
        queue_scope = _agent_queue_scope_sql(user.get("queues") or [])
        scope_parts = []
        if agent_id:
            scope_parts.append(f"cm.assigned_agent_id = ${idx}::uuid")
            params.append(agent_id)
            idx += 1
        if queue_scope:
            # Un agente ve las conversaciones de sus colas que estén SIN
            # asignar (libres para tomar). Si ya las asignó otro agente,
            # ese otro las gestiona. Esto reproduce el filtro de la UI
            # vieja (widget/inbox.html:2497).
            scope_parts.append(f"(cm.assigned_agent_id IS NULL AND {queue_scope})")
        if scope_parts:
            where_parts.append("(" + " OR ".join(scope_parts) + ")")
        else:
            # Sin id ni colas → no ve nada. Retornamos lista vacía para evitar
            # un SELECT sin scope.
            return []

    if date_from:
        where_parts.append(f"c.updated_at >= ${idx}::timestamptz")
        params.append(date_from + "T00:00:00Z")
        idx += 1
    if date_to:
        where_parts.append(f"c.updated_at <= ${idx}::timestamptz")
        params.append(date_to + "T23:59:59Z")
        idx += 1
    if channel:
        where_parts.append(f"c.channel = ${idx}")
        params.append(channel)
        idx += 1
    if assigned_to:
        # cm.assigned_agent_id es uuid (FK a profiles.id) — cast explícito necesario
        # porque asyncpg pasa el query param como text y postgres no infiere uuid=text
        where_parts.append(f"cm.assigned_agent_id = ${idx}::uuid")
        params.append(assigned_to)
        idx += 1
    if queue:
        where_parts.append(f"cm.queue = ${idx}")
        params.append(queue)
        idx += 1
    if country:
        # Filtro especial: "MP" = multi-país (todo país NO primario)
        if country.upper() == "MP":
            primary_list = ",".join(f"'{c}'" for c in sorted(PRIMARY_COUNTRIES))
            where_parts.append(f"upper(coalesce(c.user_profile->>'country', 'AR')) NOT IN ({primary_list})")
        else:
            where_parts.append(f"upper(coalesce(c.user_profile->>'country', 'AR')) = upper(${idx})")
            params.append(country)
            idx += 1
    if lifecycle:
        where_parts.append(f"coalesce(cm.lifecycle_override, cm.lifecycle_auto, 'new') = ${idx}")
        params.append(lifecycle)
        idx += 1
    if search:
        where_parts.append(
            f"(c.user_profile->>'name' ilike ${idx} OR c.user_profile->>'email' ilike ${idx} OR lm.content ilike ${idx})"
        )
        params.append(f"%{search}%")
        idx += 1

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

    params.append(limit)
    idx += 1
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
        out.append(
            ConversationOut(
                id=str(r["id"]),
                session_id=r["external_id"] or "",
                channel=r["channel"],
                name=profile.get("name") or "",
                email=profile.get("email") or "",
                phone=profile.get("phone") or "",
                country=profile.get("country") or "AR",
                last_message=last_msg or "",
                last_timestamp=(r["last_message_at"] or r["updated_at"]).isoformat()
                if (r["last_message_at"] or r["updated_at"])
                else "",
                message_count=r["message_count"] or 0,
                assigned_agent_id=str(r["assigned_agent_id"]) if r["assigned_agent_id"] else None,
                status=r["status"] or "open",
                lifecycle=r["lifecycle"] or "new",
                lifecycle_is_manual=bool(r["lifecycle_is_manual"]),
                queue=r["queue"] or "sales",
                bot_paused=bool(r["bot_paused"]),
                needs_human=bool(r["needs_human"]),
                tags=list(r["tags"] or []),
            )
        )
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
        out.append(
            {
                "id": str(r["id"]),
                "role": r["role"],
                "content": r["content"],
                "agent": meta.get("agent") or meta.get("sender_name"),
                "attachments": meta.get("attachments") or [],
                "at": r["created_at"].isoformat(),
            }
        )
    return out


@router.get("/conversations/{conv_id}/ai-insights")
async def get_ai_insights(conv_id: str):
    """
    Genera resumen + próximo paso + razones del scoring de la conversación
    usando OpenAI gpt-4o-mini con los últimos 20 mensajes como contexto.
    Cacheado en Redis 5 min (no regenerar en cada refresh).
    """
    import json as _json

    from memory.conversation_store import get_conversation_store

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
    transcript = "\n".join(f"[{m['role']}] {m['content'][:300]}" for m in msgs)
    profile = conv_row["user_profile"] if conv_row else {}
    if isinstance(profile, str):
        try:
            profile = _json.loads(profile)
        except Exception:
            profile = {}

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

    user_msg = (
        f"PERFIL DEL CONTACTO:\n{profile_str}\n\nTRANSCRIPCIÓN (últimos {len(msgs)} mensajes):\n{transcript}"
    )

    try:
        from utils.llm_retry import call_with_retry

        resp = await call_with_retry(
            lambda: client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.3,
                max_tokens=600,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
            ),
            label="ai_insights",
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

    # Query paralela al módulo Area_de_cobranzas (CustomModule20) — su `id`
    # es el que usamos para armar el link "Ver en Zoho" de la card Cobranzas.
    # Si el contacto no tiene registro en ese módulo devuelve {} y el
    # frontend cae al link de lista del módulo.
    cobranza_zoho_id: str | None = None
    try:
        from integrations.zoho.area_cobranzas import ZohoAreaCobranzas

        zc = ZohoAreaCobranzas()
        cobranza = await zc.search_by_email(email)
        if cobranza:
            cobranza_zoho_id = cobranza.get("cobranzaId") or None
    except Exception as e:
        logger.debug("cobranza_zoho_lookup_failed", email=email, error=str(e))

    return {
        "zoho_id": profile.get("id"),
        "name": profile.get("Full_Name"),
        "first_name": profile.get("First_Name"),
        "last_name": profile.get("Last_Name"),
        "email": profile.get("Email"),
        "phone": profile.get("Phone"),
        "country": _country_iso(profile.get("Pais")),
        "country_name": profile.get("Pais"),
        "professional": {
            "profession": profile.get("Profesi_n"),
            "specialty": profile.get("Especialidad"),
            "cargo": profile.get("Cargo"),
            "workplace": profile.get("Lugar_de_trabajo"),
            "work_area": profile.get("rea_donde_tabaja"),
        },
        "jurisdictional_cert": {"code": _colegio_code(colegio), "name": colegio} if colegio else None,
        "courses_taken": cursos,
        "scoring": {
            "profile": int(profile.get("Scoring_Perfil") or 0),
            "sales": int(profile.get("Scoring_venta") or 0),
        },
        # Cobranzas: por ahora derivado del Zoho (más adelante: integration real)
        "cobranzas": _derive_cobranzas(profile, cobranza_zoho_id),
    }


# ─── Mutations: por conversación ─────────────────────────────────────────────


class AssignBody(BaseModel):
    agent_id: str | None = None


@router.post("/conversations/{conv_id}/assign")
async def assign(conv_id: str, body: AssignBody):
    await cm.assign(conv_id, body.agent_id)
    from utils.inbox_jobs import log_action

    await log_action("system", "assign", conv_id, {"agent_id": body.agent_id})

    # Notificación al agente asignado — fire-and-forget, no falla el assign
    # si la notif no se puede crear (ver utils/notifications.py:notify).
    if body.agent_id:
        from utils.notifications import notify

        try:
            conv = await cm.get(conv_id)
            data = {
                "conversation_id": conv_id,
                "client_name": (conv or {}).get("name") or "cliente",
                "queue": (conv or {}).get("queue"),
                "channel": (conv or {}).get("channel"),
            }
            await notify(body.agent_id, "conv_assigned", data)
        except Exception as e:
            logger.debug("notify_assign_failed", conv_id=conv_id, error=str(e))

    return {"ok": True}


@router.get("/audit-log")
async def get_audit_log(limit: int = 100, conversation_id: str | None = None, actor_id: str | None = None):
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
@limiter.limit(UPLOAD_PER_USER)
async def upload_attachment(request: Request, file: UploadFile = File(...)):
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
            key,
            data,
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
    filename: str | None = None
    content_type: str | None = None
    size: int | None = None


class SendMessageBody(BaseModel):
    text: str = ""
    agent_id: str | None = None
    agent_name: str | None = "Agente humano"
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
        raise HTTPException(501, f"Canal '{channel}' aún no soportado para envío desde la UI nueva.")

    # 2) Marcar takeover en meta
    await cm.set_bot_paused(conv_id, True)
    if body.agent_id:
        await cm.assign(conv_id, body.agent_id)
    await cm.set_needs_human(conv_id, False)

    # 3) Persistir + broadcast usando el mismo flujo que el inbox legacy
    import time as _time

    from config.constants import Channel as Ch
    from memory.conversation_store import get_conversation_store
    from models.message import Message, MessageRole
    from utils.realtime import broadcast_event

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
                parts.append("🎤 Mensaje de voz")
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
    broadcast_event(
        {
            "type": "new_message",
            "session_id": external_id,
            "role": "assistant",
            "content": final_text,
            "sender_name": body.agent_name or "Agente",
            "attachments": attachments_meta,
            "timestamp": msg.timestamp.isoformat(),
        }
    )

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
    agent_id: str | None = None


@router.post("/bulk/assign")
@limiter.limit(BULK_OPS_PER_USER)
async def bulk_assign(
    request: Request,
    body: BulkAssignBody,
    auth: dict = Depends(require_role_or_admin("admin", "supervisor")),
):
    n = await cm.bulk_assign(body.ids, body.agent_id)
    return {"ok": True, "updated": n}


class BulkStatusBody(BaseModel):
    ids: list[str]
    status: Literal["open", "pending", "resolved"]


@router.post("/bulk/status")
@limiter.limit(BULK_OPS_PER_USER)
async def bulk_status(
    request: Request,
    body: BulkStatusBody,
    auth: dict = Depends(require_role_or_admin("admin", "supervisor")),
):
    n = await cm.bulk_set_status(body.ids, body.status)
    return {"ok": True, "updated": n}


# ─── LLM: Corrección ortográfica ─────────────────────────────────────────────


class CorrectBody(BaseModel):
    text: str
    style: Literal["professional", "casual"] = "professional"


@router.post("/llm/correct-spelling")
@limiter.limit(LLM_PER_USER)
async def correct_spelling(request: Request, body: CorrectBody):
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
        from utils.llm_retry import call_with_retry

        resp = await call_with_retry(
            lambda: client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.1,
                max_tokens=400,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": body.text},
                ],
            ),
            label="correct_spelling",
        )
        corrected = resp.choices[0].message.content.strip()
        # Limpiar comillas decorativas si el LLM las puso igual
        if corrected.startswith('"') and corrected.endswith('"'):
            corrected = corrected[1:-1]
        return {"corrected": corrected, "changed": corrected != body.text}
    except Exception as e:
        logger.error("correct_spelling_failed", error=str(e))
        raise HTTPException(500, f"LLM correction failed: {e}")


# ─── Métricas del sistema ────────────────────────────────────────────────────


@router.get("/metrics")
async def get_metrics():
    """KPIs en vivo consumidos por /dashboard (tab 'En vivo').

    Conversaciones de hoy (total / bot / humano / contención), bar chart de
    7 días aproximado, tabla de agentes (nombre + estado en Redis), FRT
    promedio por agente, alertas de inactividad en convs con humano.
    """
    import datetime as _dt
    import json as _json
    import time as _time

    from memory.conversation_store import get_conversation_store
    from utils.bot_state import bot_disabled_key as _bot_key

    store = await get_conversation_store()
    r = store._redis

    # Conversaciones de hoy: scan de los índices
    session_ids: list[str] = []
    async for k in r.scan_iter("idx:widget:*", count=500):
        session_ids.append(k.decode() if isinstance(k, bytes) else k)
    async for k in r.scan_iter("idx:whatsapp:*", count=500):
        session_ids.append(k.decode() if isinstance(k, bytes) else k)

    today_total = len(session_ids)
    today_human = 0
    for key in session_ids:
        sid = key.split("idx:widget:")[-1] if "idx:widget:" in key else key.split("idx:whatsapp:")[-1]
        val = await r.get(_bot_key(sid))
        if val:
            today_human += 1
    today_bot = today_total - today_human
    bot_containment = round((today_bot / today_total) * 100) if today_total > 0 else 0

    # Últimos 7 días (placeholder — sólo hoy tiene dato real, el resto queda en 0
    # hasta que tengamos agregados históricos persistidos)
    last_7_days = []
    for i in range(6, -1, -1):
        d = (_dt.date.today() - _dt.timedelta(days=i)).isoformat()
        last_7_days.append({"date": d, "total": today_total if i == 0 else 0})

    # Equipo + estado (disponible/ausente/etc)
    agents: list[dict] = []
    try:
        from integrations.supabase_client import list_profiles

        profiles = await list_profiles()
        for p in profiles:
            if p.get("role") in ("agente", "supervisor", "admin"):
                uid = p.get("id") or p.get("email", "")
                st = await r.get(f"agent_available:{uid}")
                status = (st.decode() if isinstance(st, bytes) else st) if st else "offline"
                agents.append(
                    {
                        "name": p.get("name", ""),
                        "email": p.get("email", ""),
                        "role": p.get("role", "agente"),
                        "status": status,
                        "handled": 0,
                        "avg_response": "< 2s",
                    }
                )
    except Exception:
        pass

    # FRT promedio por agente (muestra de 200 entradas recientes)
    frt_data: dict[str, dict[str, int]] = {}
    try:
        frt_keys = []
        async for k in r.scan_iter("frt:*", count=500):
            frt_keys.append(k)
        for k in frt_keys[:200]:
            raw = await r.get(k)
            if raw:
                frt = _json.loads(raw)
                agent = frt.get("agent", "unknown")
                if agent not in frt_data:
                    frt_data[agent] = {"total": 0, "sum_seconds": 0}
                frt_data[agent]["total"] += 1
                frt_data[agent]["sum_seconds"] += frt.get("seconds", 0)
    except Exception:
        pass
    frt_summary = [
        {
            "agent": agent,
            "avg_seconds": round(data["sum_seconds"] / data["total"]) if data["total"] else 0,
            "count": data["total"],
        }
        for agent, data in frt_data.items()
    ]

    # Alertas de inactividad (convs con humano sin respuesta > threshold)
    inactive_alerts: list[dict] = []
    for key in session_ids[:100]:
        sid = key.split("idx:widget:")[-1] if "idx:widget:" in key else key.split("idx:whatsapp:")[-1]
        if not await r.get(_bot_key(sid)):
            continue
        label_raw = await r.get(f"conv_label:{sid}")
        label = label_raw.decode() if isinstance(label_raw, bytes) else (label_raw or "")
        last = await r.get(f"last_reply:{sid}")
        if not last:
            continue
        try:
            elapsed = _time.time() - float(last)
        except (TypeError, ValueError):
            continue
        threshold = 300 if label == "caliente" else 900  # 5' hot / 15' resto
        if elapsed > threshold:
            name_raw = await r.get(f"conv_assigned_name:{sid}")
            name = name_raw.decode() if isinstance(name_raw, bytes) else (name_raw or "")
            inactive_alerts.append(
                {
                    "session_id": sid,
                    "agent": name,
                    "minutes_inactive": round(elapsed / 60),
                    "label": label,
                }
            )

    return {
        "today_total": today_total,
        "today_human": today_human,
        "today_bot": today_bot,
        "bot_containment": bot_containment,
        "last_7_days": last_7_days,
        "agents": agents,
        "frt_summary": frt_summary,
        "inactive_alerts": inactive_alerts,
    }


# ─── Helpers ─────────────────────────────────────────────────────────────────

# (Antes vivía acá `_resolve_duration` para el snooze. Removido junto con la
#  feature de snooze.)


_COUNTRY_MAP = {
    "Argentina": "AR",
    "México": "MX",
    "Mexico": "MX",
    "Chile": "CL",
    "Colombia": "CO",
    "Perú": "PE",
    "Peru": "PE",
    "Uruguay": "UY",
    "Ecuador": "EC",
    "España": "ES",
    "Espana": "ES",
    "Bolivia": "BO",
    "Paraguay": "PY",
    "Venezuela": "VE",
    "Costa Rica": "CR",
    "Guatemala": "GT",
    "Honduras": "HN",
    "Nicaragua": "NI",
    "Panamá": "PA",
    "Panama": "PA",
    "El Salvador": "SV",
}


def _country_iso(country: str | None) -> str:
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


def _colegio_code(colegio: str | None) -> str | None:
    if not colegio:
        return None
    return _COLEGIO_MAP.get(colegio)


def _derive_cobranzas(profile: dict, cobranza_zoho_id: str | None = None) -> dict | None:
    """
    Deriva info de cobranzas desde el Zoho contact.
    Por ahora simplificado: solo si tiene cursadas con Estado_de_OV.
    Más adelante: query a Sales Orders / payments real de Zoho.

    `cobranza_zoho_id` es el id del registro en Area_de_cobranzas (CustomModule20)
    — se usa para armar el link directo al detalle en crm.zoho.com. Si no hay,
    el frontend cae a la lista del módulo.
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
        "overdueAmount": 0,
        "totalDueAmount": 0,
        "contractAmount": 0,
        "installmentValue": 0,
        "lastPaymentAmount": 0,
        "totalInstallments": len(activas),
        "paidInstallments": 0,
        "overdueInstallments": 0,
        "pendingInstallments": len(activas),
        "daysOverdue": 0,
        "contractStatus": "Activo",
        "paymentMethod": "Configurar en Zoho",
        "nextDue": None,
        "paymentLink": None,
        "cobranzaZohoId": cobranza_zoho_id,
        # Nota: completar cuando integremos sales_orders Zoho
        "_pending_integration": True,
    }
