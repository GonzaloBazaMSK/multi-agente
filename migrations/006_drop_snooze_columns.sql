-- ============================================================================
-- Migration 006: dropear columnas e índice de snooze
--
-- Background:
--   La feature de "snooze" (postergar conversaciones N horas) se removió en
--   abril 2026. No agregaba valor real al flujo de la inbox: cuando un agente
--   quería sacarse algo de encima, lo marcaba como `resolved` o lo pasaba a
--   `pending` — nadie usaba el snooze. Encima exigía mantener un cron de
--   wake-up corriendo en un solo worker (con lock en Redis), agregando
--   complejidad operativa para cero beneficio.
--
--   Esta migración limpia los restos en DB. El código ya no escribe ni lee
--   `snoozed_until` / `snoozed_at` (api/inbox_api.py, memory/conversation_meta.py
--   y utils/inbox_jobs.py fueron limpiados en el mismo commit), así que es
--   seguro dropear sin downtime.
--
-- Si querés volver a postergar conversaciones en el futuro, usá
-- `set_status(conv_id, 'pending')` y filtrá por status en la inbox — sin cron.
--
-- Idempotente: usa IF EXISTS en todo. Re-correrla no rompe nada.
-- ============================================================================

begin;

-- 1. Dropear el índice parcial sobre snoozed_until.
--    (Postgres lo dropea solo cuando se cae la columna, pero lo hacemos
--    explícito para no depender de ese comportamiento implícito.)
drop index if exists public.idx_conv_meta_snoozed_until;

-- 2. Dropear las columnas. CASCADE no es necesario — no hay FKs ni vistas
--    que dependan de estas columnas.
alter table public.conversation_meta drop column if exists snoozed_until;
alter table public.conversation_meta drop column if exists snoozed_at;

-- 3. Verificación: las columnas no deben existir después de correr esto.
do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public'
      and table_name = 'conversation_meta'
      and column_name in ('snoozed_until', 'snoozed_at')
  ) then
    raise exception 'Migration 006 failed: snooze columns still present in conversation_meta';
  end if;
end $$;

commit;
