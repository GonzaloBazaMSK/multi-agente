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
