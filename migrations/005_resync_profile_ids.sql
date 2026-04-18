-- ============================================================================
-- Migration 005: re-sincronizar profiles.id ← auth.users.id + agregar FKs
--                pendientes en inbox_audit_log
--
-- Background:
--   En el QA del 2026-04-18 detectamos que public.profiles.id NO coincide con
--   auth.users.id en NINGUNO de los 5 perfiles existentes. La causa es que
--   create_profile() en supabase_client.py hace un POST a /rest/v1/profiles
--   sin pasar `id`, así que Postgres genera un uuid nuevo (default
--   gen_random_uuid()) en lugar de reusar el id del auth.user.
--
--   Esto NO rompe el login en runtime porque /auth/me y /auth/login resuelven
--   el profile por EMAIL, no por id. Pero rompe cualquier suposición de FK
--   contra auth.users (por eso el primer intento de password reset fue al
--   uuid equivocado — usé profile.id en lugar de auth.users.id).
--
-- Esta migración:
--   1. Construye mapping (profile.id antiguo → auth.users.id) matcheando
--      por email, en una tabla temp.
--   2. Cascadea el cambio: actualiza conversation_meta.assigned_agent_id
--      y inbox_audit_log.actor_id (los actor_id que sean uuids de profiles
--      — el resto, ej. 'system', queda intacto).
--   3. Re-keya public.profiles.id al uuid de auth.users.
--   4. Agrega la FK que faltaba en inbox_audit_log.conversation_id →
--      conversations(id) ON DELETE CASCADE.
--   5. NO agrega FK en actor_id porque puede ser 'system' / 'bot' / etc.
--      (se loguea desde codigo backend sin user humano).
--
-- Idempotente: si ya está re-sincronizado (profile.id = auth.user.id),
-- el UPDATE no toca filas y los ALTER TABLE usan IF NOT EXISTS.
--
-- Riesgo: medio. Cambia PKs. Hacé backup de profiles + conversation_meta +
-- inbox_audit_log antes de aplicar:
--   pg_dump --table=public.profiles --table=public.conversation_meta \
--           --table=public.inbox_audit_log $DATABASE_URL > backup_005.sql
--
-- Aplicar:  psql $DATABASE_URL -f migrations/005_resync_profile_ids.sql
-- ============================================================================

begin;

-- 1) Mapping (old → new). Usamos auth.users.id como source of truth.
--    Si un profile no tiene match por email en auth.users, queda fuera del
--    mapping y no se toca (caso raro: profile creado a mano sin auth.user).
create temporary table _profile_id_remap (
    old_id uuid not null,
    new_id uuid not null,
    email  text not null
) on commit drop;

insert into _profile_id_remap (old_id, new_id, email)
select p.id, u.id, p.email
  from public.profiles p
  join auth.users u on lower(u.email) = lower(p.email)
 where p.id <> u.id;  -- solo los que están desincronizados

-- Telemetría: cuántos vamos a tocar.
do $$
declare
    n int;
begin
    select count(*) into n from _profile_id_remap;
    raise notice 'profile id remap: % filas a re-sincronizar', n;
end$$;

-- 2) Cascadear a conversation_meta.assigned_agent_id (FK existente).
--    No hace falta dropear la FK porque el UPDATE actualiza ambas filas en
--    el mismo statement (Postgres lo permite dentro de tx).
--    Pero por las dudas — y porque la FK no es DEFERRABLE — desactivamos
--    la validación temporalmente con un workaround: update children PRIMERO
--    apuntando al nuevo uuid (que aún no existe en profiles), después
--    update profiles.
--
--    Como eso violaría la FK, dropeamos y re-creamos la constraint.
alter table public.conversation_meta
    drop constraint if exists conversation_meta_assigned_profile_id_fkey;
alter table public.conversation_meta
    drop constraint if exists conversation_meta_assigned_agent_id_fkey;

-- 3) Update children primero (no tienen FK al final ahora).
update public.conversation_meta cm
   set assigned_agent_id = m.new_id
  from _profile_id_remap m
 where cm.assigned_agent_id = m.old_id;

-- inbox_audit_log.actor_id es text (puede contener 'system', 'bot', etc).
-- Solo actualizamos los que matchean uuid de profile.
update public.inbox_audit_log al
   set actor_id = m.new_id::text
  from _profile_id_remap m
 where al.actor_id = m.old_id::text;

-- 4) Update profiles.id (la PK). Nadie está apuntando ahora.
update public.profiles p
   set id = m.new_id
  from _profile_id_remap m
 where p.id = m.old_id;

-- 5) Re-crear FK con el nombre nuevo, apuntando a profiles.
alter table public.conversation_meta
    add constraint conversation_meta_assigned_agent_id_fkey
    foreign key (assigned_agent_id) references public.profiles(id)
    on delete set null;

-- 6) Verificación post-migración: 0 desincronizados.
do $$
declare
    leftover int;
begin
    select count(*) into leftover
      from public.profiles p
      join auth.users u on lower(u.email) = lower(p.email)
     where p.id <> u.id;
    if leftover > 0 then
        raise exception 'profile id remap incompleto: % filas siguen desincronizadas', leftover;
    end if;
    raise notice 'profile id remap: OK, todos sincronizados con auth.users';
end$$;

-- 7) FK que faltaba en inbox_audit_log.conversation_id.
--    on delete cascade: si se borra la conversación (lo hace el endpoint
--    de admin), se borra su audit. Es la política consistente con
--    conversation_meta.
--
--    OJO: si hay rows con conversation_id apuntando a conversaciones que
--    ya no existen (audit log huérfano post-delete sin FK), el ALTER
--    falla. Limpiamos primero.
delete from public.inbox_audit_log al
 where al.conversation_id is not null
   and not exists (
       select 1 from public.conversations c where c.id = al.conversation_id
   );

alter table public.inbox_audit_log
    drop constraint if exists inbox_audit_log_conversation_id_fkey;
alter table public.inbox_audit_log
    add constraint inbox_audit_log_conversation_id_fkey
    foreign key (conversation_id) references public.conversations(id)
    on delete cascade;

commit;

-- ============================================================================
-- Post-deploy:
--   1. Verificar que el login sigue andando (gonzalobaza@msklatam.com).
--   2. Verificar /api/inbox/agents devuelve la lista esperada.
--   3. Verificar que asignar una conversación a un agente funciona.
--   4. Idealmente también: arreglar create_profile() en
--      integrations/supabase_client.py para que pase id=auth.user.id al
--      crear, y así nunca más se desincronizan.
-- ============================================================================
