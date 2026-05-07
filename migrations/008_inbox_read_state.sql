-- Estado de lectura por agente y conversación.
-- Una row por (user_id, conv_id). last_read_at = última vez que el agente
-- abrió la conv. Una conv aparece como "no leída" si tiene un mensaje del
-- USER (no del bot ni del propio agente) posterior al last_read_at.
--
-- Si NO hay row → la conv nunca fue abierta por este agente → no leída si
-- hay algún mensaje user.

CREATE TABLE IF NOT EXISTS public.inbox_read_state (
    user_id          uuid        NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    conversation_id  uuid        NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
    last_read_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_inbox_read_state_user
    ON public.inbox_read_state (user_id, last_read_at DESC);
