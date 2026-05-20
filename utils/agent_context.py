"""
ContextVars para que las herramientas (tools) del agente sales/closer
puedan acceder al `session_id` y `conversation_id` actual SIN recibirlos
como argumento explícito (el LLM no los pasa al hacer tool call).

Uso típico:
    from utils.agent_context import current_session_id

    # En el endpoint que invoca al agente:
    current_session_id.set(conv_id)
    # ... agent.ainvoke(...)

    # Dentro de una @tool:
    from utils.agent_context import current_session_id, log_to_conv

    @tool
    async def mi_tool(arg1):
        await log_to_conv("tool_called", {"tool": "mi_tool", "arg1": arg1})
        ...

`log_to_conv` es un wrapper conveniente sobre `utils.conv_events.log_event`
que toma el session_id del ContextVar automáticamente.
"""

from __future__ import annotations

from contextvars import ContextVar

# session_id que se va a usar para `log_event` (lo mismo que el inbox usa
# como conversation_id en el endpoint /conversations/{conv_id}/events).
current_session_id: ContextVar[str] = ContextVar("current_session_id", default="")

# Canal actual ("widget" | "whatsapp"). Útil para que las tools sepan
# en qué contexto están y ajusten su comportamiento si hace falta.
current_channel: ContextVar[str] = ContextVar("current_channel", default="")

# Flag de handoff a Másters. Lo setea la tool `create_or_update_lead`
# cuando recibe `brand="Master"`. El widget/whatsapp endpoint lo lee post-
# ejecución del agente y dispara la asignación al asesor + pausa bot +
# needs_human. Es la señal MECÁNICA (no depende de que el LLM emita el tag
# textual `[DERIVAR_MASTERS_VANESA]`, que es frágil — el LLM suele
# reformular o saltearlo).
masters_handoff_requested: ContextVar[bool] = ContextVar(
    "masters_handoff_requested", default=False
)

# Flag: ¿el usuario está autenticado en el sitio MSK?
# Lo setea el endpoint del canal (widget) en función de la fuente del email:
#   - Email vino en `req.user_email` del payload inicial (lo pasa msk-front
#     porque el visitante tiene sesión activa en msklatam.com) → True
#   - Email lo tipeó el usuario en el chat (collected_email del widget_flow)
#     → False (es anónimo "diciendo ser X" — no podemos confiar para PII)
#
# Las tools de post_venta (get_student_info) deben rechazar el acceso a info
# de cuenta cuando este flag es False. Ver agents/post_sales/tools.py.
current_user_authenticated: ContextVar[bool] = ContextVar(
    "current_user_authenticated", default=False
)


async def log_to_conv(event_type: str, data: dict) -> None:
    """
    Loggea un evento a la conversación actual leyendo `current_session_id`
    del ContextVar. Si el ContextVar está vacío, hace no-op (no rompe).

    Args:
        event_type: "info" | "action" | "intent" | "error" | "tool"
        data: payload — se merguea con el evento base ({ts, type}).
    """
    sid = current_session_id.get()
    if not sid:
        return
    try:
        from utils.conv_events import log_event

        await log_event(sid, event_type, data)
    except Exception:
        # Silencioso — nunca queremos que el log rompa el flujo del agente.
        pass
