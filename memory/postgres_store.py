"""
Postgres durable store (Supabase).

Source of truth para conversations + messages. Redis sigue siendo el cache
caliente (TTL, lookups rápidos, pub/sub). Este módulo se encarga de:
  - Pool asyncpg compartido
  - Schema idempotente (ensure_schema)
  - CRUD de conversations/messages
  - Fallback read cuando Redis no tiene el dato

El dual-write vive en memory/conversation_store.py — acá solo exponemos
las operaciones atómicas.
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional
from uuid import UUID

import asyncpg
import structlog

from config.settings import get_settings
from models.conversation import Conversation, UserProfile
from models.message import Message, MessageRole
from config.constants import Channel, AgentType, ConversationStatus

logger = structlog.get_logger(__name__)


# ─── Pool singleton ───────────────────────────────────────────────────────────

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


def is_enabled() -> bool:
    return bool(get_settings().database_url)


async def get_pool() -> asyncpg.Pool:
    """Retorna el pool global, inicializándolo lazy."""
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is not None:
            return _pool
        settings = get_settings()
        # statement_cache_size=0 es requerido por PgBouncer transaction mode.
        # ssl="require" — Supabase pooler exige TLS.
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=1,
            max_size=10,
            statement_cache_size=0,
            command_timeout=10,
            ssl="require",
        )
        logger.info("postgres_pool_created")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


# ─── Schema ──────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
create table if not exists public.conversations (
    id uuid primary key,
    channel text not null,
    external_id text not null,
    current_agent text not null default 'sales',
    status text not null default 'active',
    user_profile jsonb not null default '{}'::jsonb,
    context jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists conversations_channel_external_idx
    on public.conversations (channel, external_id);
create index if not exists conversations_updated_idx
    on public.conversations (updated_at desc);

create table if not exists public.messages (
    id uuid primary key,
    conversation_id uuid not null references public.conversations(id) on delete cascade,
    role text not null,
    content text not null,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists messages_conv_time_idx
    on public.messages (conversation_id, created_at);

create table if not exists public.snippets (
    id uuid primary key,
    shortcut text not null,
    title text not null,
    content text not null,
    topics text[] not null default '{}',
    attachments jsonb not null default '[]'::jsonb,
    created_by text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists snippets_shortcut_idx on public.snippets (shortcut);
create index if not exists snippets_topics_idx on public.snippets using gin (topics);
create index if not exists snippets_updated_idx on public.snippets (updated_at desc);

-- Lifecycle stages: pipeline visual estilo CRM
create table if not exists public.lifecycle_stages (
    id uuid primary key,
    key text not null,
    label text not null,
    emoji text not null default '📍',
    color text not null default '#6366f1',
    position integer not null,
    is_lost boolean not null default false,
    is_won boolean not null default false,
    created_at timestamptz not null default now()
);
create unique index if not exists lifecycle_stages_key_idx on public.lifecycle_stages (key);

create table if not exists public.conversation_stage (
    conversation_id uuid primary key references public.conversations(id) on delete cascade,
    stage_key text not null,
    changed_by text,
    changed_at timestamptz not null default now()
);
create index if not exists conv_stage_key_idx on public.conversation_stage (stage_key);

-- ── Courses KB ──────────────────────────────────────────────────────────────
-- Catálogo sincronizado desde cms1.msklatam.com/wp-json/msk/v1/products-full
-- PK compuesta (country, slug) — un mismo curso vive por país con su precio.
-- Hot columns para filtrado rápido; `brief_md` para inyectar en el prompt del
-- agente de ventas; `raw` JSONB para drill-down on-demand (módulos, docentes).
create table if not exists public.courses (
    country text not null,
    slug text not null,
    product_id bigint,
    title text not null,
    categoria text,
    cedente text,
    duration_hours integer,
    modules_count integer,
    currency text,
    total_price numeric(14,2),
    max_installments integer,
    price_installments numeric(14,2),
    brief_md text,
    raw jsonb not null default '{}'::jsonb,
    source_updated_at timestamptz,
    synced_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    primary key (country, slug)
);

create index if not exists courses_title_idx on public.courses (lower(title));
create index if not exists courses_categoria_idx on public.courses (country, categoria);
create index if not exists courses_synced_idx on public.courses (synced_at desc);

create or replace function public.touch_conversation_updated_at()
returns trigger language plpgsql as $func$
begin
    update public.conversations set updated_at = now() where id = new.conversation_id;
    return new;
end;
$func$;

drop trigger if exists messages_touch_conv on public.messages;
create trigger messages_touch_conv
    after insert on public.messages
    for each row execute function public.touch_conversation_updated_at();
"""


async def ensure_schema() -> None:
    """Crea tablas/índices/trigger si no existen. Idempotente."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Ejecutamos cada statement por separado (PgBouncer transaction mode)
        for stmt in _split_sql(SCHEMA_SQL):
            await conn.execute(stmt)
    logger.info("postgres_schema_ready")


def _split_sql(sql: str) -> list[str]:
    """Separa el SQL en statements balanceando dollar-quoted blocks."""
    stmts: list[str] = []
    buf: list[str] = []
    in_dollar = False
    for line in sql.splitlines():
        buf.append(line)
        # detectar apertura/cierre de $func$ ... $func$
        if "$func$" in line:
            in_dollar = not in_dollar
        if not in_dollar and line.rstrip().endswith(";"):
            stmts.append("\n".join(buf).strip())
            buf = []
    if buf and "\n".join(buf).strip():
        stmts.append("\n".join(buf).strip())
    return [s for s in stmts if s]


# ─── Upserts ─────────────────────────────────────────────────────────────────

async def upsert_conversation(conv: Conversation) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            insert into public.conversations
                (id, channel, external_id, current_agent, status, user_profile, context, created_at, updated_at)
            values ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8, $9)
            on conflict (id) do update set
                current_agent = excluded.current_agent,
                status        = excluded.status,
                user_profile  = excluded.user_profile,
                context       = excluded.context,
                updated_at    = excluded.updated_at
            """,
            UUID(conv.id),
            conv.channel.value,
            conv.external_id,
            conv.current_agent.value,
            conv.status.value,
            conv.user_profile.model_dump_json(),
            json.dumps(conv.context, default=str),
            conv.created_at,
            conv.updated_at,
        )


async def insert_messages(conversation_id: str, messages: list[Message]) -> int:
    """Inserta mensajes, ignora duplicados por PK. Retorna cantidad insertada."""
    if not messages:
        return 0
    pool = await get_pool()
    rows = [
        (
            UUID(m.id),
            UUID(conversation_id),
            m.role.value,
            m.content,
            json.dumps(m.metadata, default=str),
            m.timestamp,
        )
        for m in messages
    ]
    async with pool.acquire() as conn:
        # ON CONFLICT DO NOTHING para idempotencia: reenviar la misma lista no duplica
        await conn.executemany(
            """
            insert into public.messages (id, conversation_id, role, content, metadata, created_at)
            values ($1, $2, $3, $4, $5::jsonb, $6)
            on conflict (id) do nothing
            """,
            rows,
        )
    return len(rows)


async def save_conversation(conv: Conversation) -> None:
    """Upsert conversación + inserta todos los mensajes (idempotente)."""
    await upsert_conversation(conv)
    await insert_messages(conv.id, conv.messages)


# ─── Reads (fallback cuando Redis no tiene el dato) ──────────────────────────

async def get_conversation(conversation_id: str) -> Conversation | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        conv_row = await conn.fetchrow(
            "select * from public.conversations where id = $1",
            UUID(conversation_id),
        )
        if not conv_row:
            return None
        msg_rows = await conn.fetch(
            "select * from public.messages where conversation_id = $1 order by created_at asc",
            UUID(conversation_id),
        )
    return _build_conversation(conv_row, msg_rows)


async def get_by_external(channel: Channel, external_id: str) -> Conversation | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        conv_row = await conn.fetchrow(
            "select * from public.conversations where channel = $1 and external_id = $2",
            channel.value,
            external_id,
        )
        if not conv_row:
            return None
        msg_rows = await conn.fetch(
            "select * from public.messages where conversation_id = $1 order by created_at asc",
            conv_row["id"],
        )
    return _build_conversation(conv_row, msg_rows)


def _build_conversation(conv_row, msg_rows) -> Conversation:
    profile = conv_row["user_profile"]
    if isinstance(profile, str):
        profile = json.loads(profile)
    context = conv_row["context"]
    if isinstance(context, str):
        context = json.loads(context)

    messages = []
    for r in msg_rows:
        meta = r["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        messages.append(
            Message(
                id=str(r["id"]),
                role=MessageRole(r["role"]),
                content=r["content"],
                timestamp=r["created_at"],
                metadata=meta,
            )
        )

    return Conversation(
        id=str(conv_row["id"]),
        channel=Channel(conv_row["channel"]),
        external_id=conv_row["external_id"],
        current_agent=AgentType(conv_row["current_agent"]),
        status=ConversationStatus(conv_row["status"]),
        user_profile=UserProfile.model_validate(profile),
        context=context,
        messages=messages,
        created_at=conv_row["created_at"],
        updated_at=conv_row["updated_at"],
    )


# ─── List (para inbox histórico) ─────────────────────────────────────────────

async def list_conversations_summary(
    limit: int = 500,
    offset: int = 0,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """
    Lista resumida de conversaciones para el inbox.
    Consulta directo a Postgres — incluye históricas fuera de Redis.
    Devuelve dicts con metadata + último mensaje (sin cargar todos los mensajes).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        conditions = []
        params: list = []
        idx = 1

        if date_from:
            conditions.append(f"c.updated_at >= ${idx}::timestamptz")
            params.append(date_from + "T00:00:00Z")
            idx += 1
        if date_to:
            conditions.append(f"c.updated_at <= ${idx}::timestamptz")
            params.append(date_to + "T23:59:59Z")
            idx += 1

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        params.append(limit)
        params.append(offset)

        query = f"""
            SELECT c.id, c.channel, c.external_id, c.current_agent, c.status,
                   c.user_profile, c.context, c.created_at, c.updated_at,
                   lm.content   AS last_message,
                   lm.created_at AS last_message_at,
                   mc.cnt        AS message_count
            FROM public.conversations c
            LEFT JOIN LATERAL (
                SELECT content, created_at
                FROM public.messages
                WHERE conversation_id = c.id
                ORDER BY created_at DESC
                LIMIT 1
            ) lm ON true
            LEFT JOIN LATERAL (
                SELECT count(*)::int AS cnt
                FROM public.messages
                WHERE conversation_id = c.id
            ) mc ON true
            {where}
            ORDER BY c.updated_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
        """

        rows = await conn.fetch(query, *params)

    results = []
    for r in rows:
        profile = r["user_profile"]
        if isinstance(profile, str):
            profile = json.loads(profile)

        last_msg = r["last_message"] or ""
        if len(last_msg) > 80:
            last_msg = last_msg[:80] + "…"

        results.append({
            "id": str(r["id"]),
            "session_id": r["external_id"],
            "channel": r["channel"],
            "name": profile.get("name", ""),
            "email": profile.get("email", ""),
            "phone": profile.get("phone", ""),
            "country": profile.get("country", "AR"),
            "status": r["status"],
            "current_agent": r["current_agent"],
            "message_count": r["message_count"] or 0,
            "last_message": last_msg,
            "last_timestamp": r["last_message_at"].isoformat() if r["last_message_at"] else "",
            "created_at": r["created_at"].isoformat() if r["created_at"] else "",
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else "",
        })
    return results


# ─── Courses ─────────────────────────────────────────────────────────────────

async def upsert_course(row: dict) -> None:
    """Upsert de un curso. `row` debe incluir todas las hot columns + raw + brief_md."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            insert into public.courses (
                country, slug, product_id, title, categoria, cedente,
                duration_hours, modules_count, currency,
                total_price, max_installments, price_installments,
                brief_md, raw, source_updated_at, synced_at
            ) values (
                $1, $2, $3, $4, $5, $6, $7, $8, $9,
                $10, $11, $12,
                $13, $14::jsonb, $15, now()
            )
            on conflict (country, slug) do update set
                product_id = excluded.product_id,
                title = excluded.title,
                categoria = excluded.categoria,
                cedente = excluded.cedente,
                duration_hours = excluded.duration_hours,
                modules_count = excluded.modules_count,
                currency = excluded.currency,
                total_price = excluded.total_price,
                max_installments = excluded.max_installments,
                price_installments = excluded.price_installments,
                brief_md = excluded.brief_md,
                raw = excluded.raw,
                source_updated_at = excluded.source_updated_at,
                synced_at = now()
            """,
            row["country"], row["slug"], row.get("product_id"),
            row["title"], row.get("categoria"), row.get("cedente"),
            row.get("duration_hours"), row.get("modules_count"),
            row.get("currency"), row.get("total_price"),
            row.get("max_installments"), row.get("price_installments"),
            row.get("brief_md"), json.dumps(row.get("raw", {}), default=str),
            row.get("source_updated_at"),
        )


async def get_course(country: str, slug: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow(
            "select * from public.courses where country = $1 and slug = $2",
            country.lower(), slug,
        )
    if not r:
        return None
    d = dict(r)
    if isinstance(d.get("raw"), str):
        d["raw"] = json.loads(d["raw"])
    return d


async def list_courses(country: str, limit: int = 200) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            select country, slug, product_id, title, categoria, cedente,
                   duration_hours, modules_count, currency, total_price,
                   max_installments, price_installments, synced_at
            from public.courses
            where country = $1
            order by title asc
            limit $2
            """,
            country.lower(), limit,
        )
    return [dict(r) for r in rows]


async def get_catalog_compact(country: str) -> str:
    """
    Devuelve el catálogo compacto de un país para inyectar en el system prompt.
    ~40 tokens por curso × 100 cursos ≈ 4,000 tokens.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            select slug, title, categoria, currency, max_installments,
                   price_installments, pitch_hook
            from public.courses
            where country = $1
            order by categoria asc, title asc
            """,
            country.lower(),
        )
    if not rows:
        return ""
    cc = country.upper()
    # Envolvemos el catálogo en un tag XML propio para que el LLM no mezcle
    # líneas del catálogo con texto cercano (instrucciones, brief, etc.) y
    # para poder referirlo explícitamente ("mirá dentro de <catalogo_AR>").
    lines = [f"<catalogo_{cc} total_cursos=\"{len(rows)}\">"]
    lines.append(f"# Catálogo de cursos activos en {cc}")
    lines.append("")
    lines.append("Columna **qué te deja** = gancho de valor clínico (usalo como pitch de 1 línea cuando listés este curso; si está vacío, referite al título y la categoría solamente — NO inventes).")
    lines.append("")
    lines.append("| Slug | Título | Categoría | Qué te deja | Precio |")
    lines.append("|---|---|---|---|---|")
    for r in rows:
        slug = r["slug"]
        title = (r["title"] or "").replace("|", "/")
        cat = (r["categoria"] or "").replace("|", "/")
        hook = (r["pitch_hook"] or "").replace("|", "/").replace("\n", " ").strip()
        inst = r["max_installments"]
        val = r["price_installments"]
        cur = r["currency"] or ""
        if inst and val:
            precio = f"{inst}x {cur} {val:,.0f}"
        else:
            precio = "Consultar"
        lines.append(f"| {slug} | {title} | {cat} | {hook} | {precio} |")
    lines.append(f"</catalogo_{cc}>")
    return "\n".join(lines)


async def delete_missing_courses(country: str, keep_slugs: list[str]) -> int:
    """Borra cursos de un país cuyo slug ya no viene del WP (curso discontinuado)."""
    if not keep_slugs:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.execute(
            "delete from public.courses where country = $1 and slug <> all($2::text[])",
            country.lower(), keep_slugs,
        )
    # r es "DELETE n"
    try:
        return int(r.split()[-1])
    except Exception:
        return 0
