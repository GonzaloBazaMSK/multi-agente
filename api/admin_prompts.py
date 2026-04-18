"""
Endpoints de administración para editar prompts de agentes.

- GET  /admin/prompts/{agent}  → retorna el contenido del prompt del agente
- POST /admin/prompts/{agent}  → guarda nuevo contenido en el archivo del prompt

La UI para editarlos vive en el frontend Next.js (/prompts). La ruta vieja
`GET /admin/prompts` que servía `widget/admin_prompts.html` se eliminó con
la migración — bookmarks viejos se redirigen en main.py (redirect_prompts_ui).
"""
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from api.auth import require_role
import structlog

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

BASE_DIR = Path(__file__).parent.parent

PROMPT_FILES = {
    "ventas": "agents/sales/prompts.py",
    "cobranzas": "agents/collections/prompts.py",
    "post_venta": "agents/post_sales/prompts.py",
    "bienvenida": "agents/routing/greeting_prompt.py",
    "orquestador": "agents/routing/router_prompt.py",
}


class PromptUpdate(BaseModel):
    content: str


@router.get("/prompts/{agent}")
async def get_prompt(agent: str, user: dict = Depends(require_role("admin"))):
    """Retorna el contenido del archivo de prompt del agente indicado."""
    if agent not in PROMPT_FILES:
        raise HTTPException(
            status_code=404,
            detail=f"Agente '{agent}' no encontrado. Válidos: {list(PROMPT_FILES.keys())}",
        )
    file_path = BASE_DIR / PROMPT_FILES[agent]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Archivo no encontrado: {PROMPT_FILES[agent]}")
    content = file_path.read_text(encoding="utf-8")
    return {"agent": agent, "content": content}


@router.post("/prompts/{agent}")
async def save_prompt(agent: str, body: PromptUpdate, user: dict = Depends(require_role("admin"))):
    """Guarda el nuevo contenido en el archivo de prompt del agente."""
    if agent not in PROMPT_FILES:
        raise HTTPException(
            status_code=404,
            detail=f"Agente '{agent}' no encontrado. Válidos: {list(PROMPT_FILES.keys())}",
        )
    file_path = BASE_DIR / PROMPT_FILES[agent]
    try:
        file_path.write_text(body.content, encoding="utf-8")
        logger.info("prompt_saved", agent=agent, path=str(file_path))
    except Exception as e:
        logger.error("prompt_save_failed", agent=agent, error=str(e))
        raise HTTPException(status_code=500, detail=f"Error al guardar: {str(e)}")
    return {"status": "ok", "agent": agent}
