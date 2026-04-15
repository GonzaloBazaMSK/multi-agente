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


def is_enabled() -> bool:
    return bool(get_settings().database_url)


async def get_pool() -> asyncpg.Pool:
    """Retorna el pool global, inicializándolo lazy."""
    global _pool
    if _pool is None:
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
    regular_price numeric(14,2),
    sale_price numeric(14,2),
    total_price numeric(14,2),
    max_installments integer,
    price_installments numeric(14,2),
    is_free boolean not null default false,
    url text,
    image_url text,
    excerpt text,
    brief_md text,
    raw jsonb not null default '{}'::jsonb,
    source_cache text,
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


# ─── Courses ─────────────────────────────────────────────────────────────────

async def upsert_course(row: dict) -> None:
    """Upsert de un curso. `row` debe incluir todas las hot columns + raw + brief_md."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            insert into public.courses (
                country, slug, product_id, title, categoria, cedente,
                duration_hours, modules_count, currency, regular_price,
                sale_price, total_price, max_installments, price_installments,
                is_free, url, image_url, excerpt, brief_md, raw,
                source_cache, source_updated_at, synced_at
            ) values (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                $11, $12, $13, $14, $15, $16, $17, $18, $19, $20::jsonb,
                $21, $22, now()
            )
            on conflict (country, slug) do update set
                product_id = excluded.product_id,
                title = excluded.title,
                categoria = excluded.categoria,
                cedente = excluded.cedente,
                duration_hours = excluded.duration_hours,
                modules_count = excluded.modules_count,
                currency = excluded.currency,
                regular_price = excluded.regular_price,
                sale_price = excluded.sale_price,
                total_price = excluded.total_price,
                max_installments = excluded.max_installments,
                price_installments = excluded.price_installments,
                is_free = excluded.is_free,
                url = excluded.url,
                image_url = excluded.image_url,
                excerpt = excluded.excerpt,
                brief_md = excluded.brief_md,
                raw = excluded.raw,
                source_cache = excluded.source_cache,
                source_updated_at = excluded.source_updated_at,
                synced_at = now()
            """,
            row["country"], row["slug"], row.get("product_id"),
            row["title"], row.get("categoria"), row.get("cedente"),
            row.get("duration_hours"), row.get("modules_count"),
            row.get("currency"), row.get("regular_price"),
            row.get("sale_price"), row.get("total_price"),
            row.get("max_installments"), row.get("price_installments"),
            bool(row.get("is_free", False)), row.get("url"),
            row.get("image_url"), row.get("excerpt"),
            row.get("brief_md"), json.dumps(row.get("raw", {}), default=str),
            row.get("source_cache"), row.get("source_updated_at"),
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
                   max_installments, price_installments, url, image_url,
                   synced_at
            from public.courses
            where country = $1
            order by title asc
            limit $2
            """,
            country.lower(), limit,
        )
    return [dict(r) for r in rows]


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
