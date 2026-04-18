"""
Config de apariencia del widget embebible (chat.js).

Prefijo: /admin/widget-config

Antes este módulo era `api/flows.py` y mezclaba:
  - CRUD del flow-builder Drawflow (muerto — ningún canal lo ejecutaba, el
    runner en `agents/flow_runner.py` nunca se invocaba; el widget real
    usa `agents/routing/widget_flow.py` que es una máquina de estados
    hardcoded en Python).
  - Config del widget (título, color, saludo, avatar, quick_replies) —
    ESTE es el único uso real, consumido por `widget/static/chat.js` al
    inicializarse en sitios externos.

La limpieza: borrado todo el Drawflow (API + HTML + runner), renombrado
a widget-config para reflejar qué hace. El endpoint público se mantiene
porque el widget embebible lo necesita sin auth (está expuesto en
msklatam.tech, etc).
"""
import json
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from api.auth import require_role

router = APIRouter(prefix="/admin/widget-config", tags=["widget-config"])

WIDGET_CONFIG_KEY = "widget:config"


class WidgetConfig(BaseModel):
    title: str = "Asesor de Cursos"
    color: str = "#1a73e8"
    greeting: str = ""
    avatar: str = ""
    bubble_icon: str = ""
    position: str = "right"
    quick_replies: str = ""


async def _redis():
    from memory.conversation_store import get_conversation_store
    store = await get_conversation_store()
    return store._redis


@router.get("/public")
async def get_public():
    """Config del widget — accesible sin auth porque la consume el <script>
    que se embebe en sitios externos (msklatam.tech, etc)."""
    r = await _redis()
    raw = await r.get(WIDGET_CONFIG_KEY)
    if not raw:
        return WidgetConfig().model_dump()
    return json.loads(raw)


@router.get("")
async def get_config(user: dict = Depends(require_role("admin", "supervisor"))):
    r = await _redis()
    raw = await r.get(WIDGET_CONFIG_KEY)
    if not raw:
        return WidgetConfig().model_dump()
    return json.loads(raw)


@router.post("")
async def save_config(config: WidgetConfig, user: dict = Depends(require_role("admin"))):
    r = await _redis()
    await r.set(WIDGET_CONFIG_KEY, json.dumps(config.model_dump(), ensure_ascii=False))
    return {"ok": True}
