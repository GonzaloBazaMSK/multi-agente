-- ============================================================================
-- Migration 004: unificar agentes humanos en public.profiles
--
-- Hasta hoy había DOS tablas paralelas para representar usuarios humanos:
--   - public.profiles  → fuente de verdad (auth + roles + colas)
--   - public.agents    → tabla redundante creada en migration 002
--                        sin role/queues, solo para el dropdown del inbox
--
-- Esta migración:
--   1. Cambia conversation_meta.assigned_agent_id de text → uuid
--   2. Migra el dato matcheando por email entre agents.email y profiles.email
--   3. Apunta la FK a profiles(id)
--   4. Drop public.agents
--
-- A partir de acá: una sola tabla de usuarios = profiles.
-- El dropdown de "asignar a" del inbox lee de profiles
-- (filtrando role IN admin/supervisor/agente).
--
-- Aplicar:  psql $DATABASE_URL -f migrations/004_drop_agents_use_profiles.sql
-- ============================================================================

begin;

-- 1) Columna nueva con el tipo correcto + FK a profiles
alter table public.conversation_meta
    add column if not exists assigned_profile_id uuid references public.profiles(id) on delete set null;

-- 2) Migrar el dato existente: a.id (text) → p.id (uuid) vía email match.
--    Si no hay match (ej. id="u-gbaza" y no existe gonzalobaza en profiles),
--    queda NULL — preferible a romper la FK.
update public.conversation_meta cm
   set assigned_profile_id = p.id
  from public.agents a
  join public.profiles p on lower(p.email) = lower(a.email)
 where cm.assigned_agent_id = a.id
   and cm.assigned_profile_id is null;

-- 3) Borrar columna vieja (rompe la FK a public.agents) y renombrar la nueva
alter table public.conversation_meta drop column assigned_agent_id;
alter table public.conversation_meta rename column assigned_profile_id to assigned_agent_id;

-- 4) Recrear índice
drop index if exists idx_conv_meta_assigned;
create index idx_conv_meta_assigned on public.conversation_meta (assigned_agent_id);

-- 5) Drop la tabla agents (ya nadie la referencia)
drop table if exists public.agents;

commit;

-- ============================================================================
-- Después del deploy:
--   - GET  /api/inbox/agents  → lee de profiles
--   - POST /auth/users        → crea agentes (no hay más POST /api/inbox/agents)
--   - assigned_agent_id        → uuid de profiles.id
-- ============================================================================
