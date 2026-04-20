-- Notificaciones in-app para la consola.
--
-- Cada notificación pertenece a UN usuario. El `type` es un discriminador
-- que el frontend usa para elegir ícono/label/acción — ver utils/notifications.py
-- para la lista actual de tipos válidos. `data` es JSONB libre para los
-- campos específicos de cada tipo (conv_id, client_name, template_name…).
--
-- Índice parcial sobre no-leídas porque la query dominante del frontend
-- (badge count + dropdown) filtra por `read_at IS NULL` — el parcial
-- mantiene el índice chico aunque la tabla crezca.
--
-- Retención: no hay DELETE automático acá. Si crece demasiado, se puede
-- sumar un cron que purgue leídas con > 90 días. Por ahora asumimos que
-- volumen es bajo (~1-5 notifs/agente/día).

create table if not exists public.notifications (
    id              uuid        primary key default gen_random_uuid(),
    user_id         uuid        not null,  -- FK lógica a auth.users.id / profiles.id
    type            text        not null,  -- "conv_assigned" | "new_message_mine" | "conv_stale" | "template_approved" | ...
    data            jsonb       not null default '{}'::jsonb,
    read_at         timestamptz,           -- NULL = no leída
    created_at      timestamptz not null default now()
);

create index if not exists idx_notifications_user_all
    on public.notifications (user_id, created_at desc);

create index if not exists idx_notifications_user_unread
    on public.notifications (user_id, created_at desc)
    where read_at is null;

create index if not exists idx_notifications_type_created
    on public.notifications (type, created_at desc);


-- Preferencias por usuario. Fila por usuario se crea lazily la primera vez
-- que pide preferencias (defaults razonables en el código, no en SQL, para
-- poder cambiarlos sin re-migrar).
create table if not exists public.notification_preferences (
    user_id              uuid        primary key,
    -- Toggles por tipo (true = recibir, false = silenciar ese tipo)
    conv_assigned        boolean     not null default true,
    new_message_mine     boolean     not null default true,
    conv_stale           boolean     not null default true,
    template_approved    boolean     not null default true,
    -- Preferencias UX
    sound_enabled        boolean     not null default false,  -- beep on push
    email_digest         boolean     not null default false,  -- digest diario por mail si hay pendientes
    -- Metadata
    updated_at           timestamptz not null default now()
);
