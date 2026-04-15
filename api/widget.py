"""
API del widget web:
- POST /widget/chat → procesa mensaje y retorna respuesta
- GET  /widget/chat/stream → SSE streaming de respuesta
- GET  /widget/history/{session_id} → historial de conversación
"""
import uuid
import asyncio
import json
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from channels.widget import process_widget_message
from memory.conversation_store import get_conversation_store
from config.constants import Channel

router = APIRouter(prefix="/widget", tags=["widget"])


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    country: str = "AR"
    user_name: str = ""
    user_email: str = ""
    user_courses: str = ""
    page_slug: str = ""   # slug del curso que está mirando el usuario (si aplica)


class ChatResponse(BaseModel):
    session_id: str
    response: str
    agent_used: str
    handoff_requested: bool


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    Endpoint principal del widget web.
    El frontend envía el mensaje y recibe la respuesta del bot.
    """
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Mensaje vacío")

    session_id = req.session_id or str(uuid.uuid4())

    result = await process_widget_message(
        session_id=session_id,
        message_text=req.message,
        country=req.country,
        user_name=req.user_name,
        user_email=req.user_email,
        user_courses=req.user_courses,
        page_slug=req.page_slug,
    )

    return ChatResponse(
        session_id=session_id,
        response=result["response"],
        agent_used=result["agent_used"],
        handoff_requested=result["handoff_requested"],
    )


@router.get("/chat/stream")
async def chat_stream(
    session_id: str,
    message: str,
    country: str = "AR",
    user_name: str = "",
    user_email: str = "",
    user_courses: str = "",
    page_slug: str = "",
):
    """
    SSE streaming — el widget recibe la respuesta palabra por palabra.
    Usar este endpoint para experiencia más fluida en el widget.
    """
    async def event_generator():
        # Procesar el mensaje normalmente
        result = await process_widget_message(
            session_id=session_id,
            message_text=message,
            country=country,
            user_name=user_name,
            user_email=user_email,
            user_courses=user_courses,
            page_slug=page_slug,
        )
        response = result["response"]

        # Simular streaming: enviar la respuesta por palabras
        words = response.split(" ")
        for i, word in enumerate(words):
            chunk = word + (" " if i < len(words) - 1 else "")
            data = json.dumps({"chunk": chunk, "done": False})
            yield f"data: {data}\n\n"
            await asyncio.sleep(0.02)

        # Evento final con metadata
        final = json.dumps({
            "chunk": "",
            "done": True,
            "agent_used": result["agent_used"],
            "handoff_requested": result["handoff_requested"],
            "session_id": session_id,
        })
        yield f"data: {final}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/history/{session_id}")
async def get_history(session_id: str):
    """Retorna el historial de mensajes de una sesión del widget."""
    store = await get_conversation_store()
    conversation = await store.get_by_external(Channel.WIDGET, session_id)
    if not conversation:
        return {"messages": [], "session_id": session_id}

    messages = []
    for m in conversation.messages:
        msg = {
            "role": m.role.value,
            "content": m.content,
            "timestamp": m.timestamp.isoformat(),
            "agent": m.metadata.get("agent", ""),
        }
        if m.metadata.get("media_url"):
            msg["media_url"] = m.metadata["media_url"]
            msg["media_type"] = m.metadata.get("media_type", "")
            msg["media_mime"] = m.metadata.get("media_mime", "")
            msg["media_filename"] = m.metadata.get("media_filename", "")
        messages.append(msg)
    return {
        "messages": messages,
        "session_id": session_id,
        "status": conversation.status.value,
    }
