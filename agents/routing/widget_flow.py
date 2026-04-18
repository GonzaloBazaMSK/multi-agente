"""
Máquina de estados del menú inicial del widget.

Gestiona el flujo de botones antes de delegar a los agentes IA.

Estados por sesión (Redis):
  None             → primera vez, no iniciado
  "main_menu"      → saludo enviado, esperando selección principal
  "asesoria_menu"  → submenú asesoría enviado, esperando Alumnos/Cobranzas
  "pending_email:<agente>"  → esperando que el usuario ingrese su email
  "done"           → menú terminado, routing normal de acá en adelante
"""
import re
import structlog
from typing import Optional

logger = structlog.get_logger(__name__)

# ── Redis ────────────────────────────────────────────────────────────────────
_KEY = "wflow:{sid}"
_TTL = 86400  # 24 h

# ── Estados ──────────────────────────────────────────────────────────────────
S_MAIN = "main_menu"
S_ASESORIA = "asesoria_menu"
S_EMAIL = "pending_email"
S_DONE = "done"

# ── Botones ──────────────────────────────────────────────────────────────────
MAIN_BUTTONS    = ["Explorar cursos 📖", "Asistencia 📩 💻"]
ASESORIA_BUTTONS = ["Soporte Alumnos 🛠️", "Soporte Cobros 🤝"]

_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+%-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# ── Helpers Redis ─────────────────────────────────────────────────────────────

def _k(session_id: str) -> str:
    return _KEY.format(sid=session_id)


async def _get(redis, session_id: str) -> Optional[str]:
    raw = await redis.get(_k(session_id))
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else str(raw)


async def _set(redis, session_id: str, state: str) -> None:
    await redis.setex(_k(session_id), _TTL, state)


# ── Utilidades de texto ───────────────────────────────────────────────────────

def _alpha(text: str) -> str:
    """Lowercase, solo letras y espacios (sin emojis ni puntuación)."""
    return " ".join(
        "".join(c for c in word if c.isalpha())
        for word in text.lower().split()
        if any(c.isalpha() for c in word)
    )


def _match(user_text: str, buttons: list[str]) -> Optional[str]:
    """
    Compara el texto del usuario con la lista de botones.
    Devuelve el botón que coincide o None.
    """
    u = _alpha(user_text)
    for btn in buttons:
        b = _alpha(btn)
        # Contenido completo
        if b in u or u in b:
            return btn
        # Al menos una palabra significativa en común (> 2 letras)
        b_words = {w for w in b.split() if len(w) > 2}
        u_words = {w for w in u.split() if len(w) > 2}
        if b_words & u_words:
            return btn
    return None


def fmt_buttons(text: str, buttons: list[str]) -> str:
    """Agrega el marcador [BUTTONS: ...] que el widget frontend parsea."""
    return f"{text} [BUTTONS: {' | '.join(buttons)}]"


# ── API pública ───────────────────────────────────────────────────────────────

async def init_state(redis, session_id: str) -> None:
    """
    Llamar cuando llega __widget_init__.
    Establece el estado inicial para que el próximo mensaje active el menú.
    """
    await _set(redis, session_id, S_MAIN)
    logger.info("wflow_init", session=session_id)


async def process_step(
    redis,
    session_id: str,
    user_message: str,
    user_email: str = "",
) -> Optional[dict]:
    """
    Avanza la máquina de estados del menú del widget.

    Retorna:
      None
        → sin estado activo o ya terminado; el caller usa routing normal.

      {"response": str, "needs_routing": False}
        → respuesta del menú (botones, pregunta de email).
          El caller guarda el mensaje y lo retorna al usuario.

      {"needs_routing": True, "forced_agent": str, "collected_email": str | None}
        → delegar a route_message con el agente indicado.
          Si collected_email no es None, el caller debe actualizar user_email.
    """
    state = await _get(redis, session_id)

    if state is None or state == S_DONE:
        return None

    # ── Menú principal ────────────────────────────────────────────────────────
    if state == S_MAIN:
        match = _match(user_message, MAIN_BUTTONS)

        if match and "cursos" in _alpha(match):
            await _set(redis, session_id, S_DONE)
            logger.info("wflow_to_agent", session=session_id, agent="ventas")
            return {"needs_routing": True, "forced_agent": "ventas", "collected_email": None}

        if match:  # "Asistencia" (único otro botón)
            await _set(redis, session_id, S_ASESORIA)
            logger.info("wflow_submenu", session=session_id)
            return {
                "response": fmt_buttons(
                    "¿Con qué necesitás asistencia?",
                    ASESORIA_BUTTONS,
                ),
                "needs_routing": False,
            }

        # No coincide con ningún botón → salir del menú, routing normal
        logger.info("wflow_no_match", session=session_id, state=state)
        await _set(redis, session_id, S_DONE)
        return None

    # ── Submenú asesoría ──────────────────────────────────────────────────────
    if state == S_ASESORIA:
        match = _match(user_message, ASESORIA_BUTTONS)

        if match and ("alumno" in _alpha(match) or "soporte" in _alpha(match) and "cobro" not in _alpha(match)):
            next_agent = "post_venta"
        elif match and ("cobro" in _alpha(match) or "cobranza" in _alpha(match) or "pago" in _alpha(match)):
            next_agent = "cobranzas"
        else:
            await _set(redis, session_id, S_DONE)
            return None

        # ¿Tenemos email? Si sí, ir directo al agente
        if user_email:
            await _set(redis, session_id, S_DONE)
            logger.info("wflow_to_agent", session=session_id, agent=next_agent, has_email=True)
            return {"needs_routing": True, "forced_agent": next_agent, "collected_email": None}

        # No hay email → pedirlo
        await _set(redis, session_id, f"{S_EMAIL}:{next_agent}")
        logger.info("wflow_ask_email", session=session_id, next_agent=next_agent)
        return {
            "response": (
                "Para gestionar tu cuenta necesito ubicar tu ficha. "
                "¿Me podés indicar el correo electrónico con el que ingresás al campus? 📧"
            ),
            "needs_routing": False,
        }

    # ── Esperando email ───────────────────────────────────────────────────────
    if state.startswith(S_EMAIL):
        next_agent = state.split(":", 1)[1] if ":" in state else "ventas"
        emails = _EMAIL_RE.findall(user_message)

        if emails:
            collected = emails[0].strip()
            await _set(redis, session_id, S_DONE)
            logger.info("wflow_email_collected", session=session_id, agent=next_agent)
            return {
                "needs_routing": True,
                "forced_agent": next_agent,
                "collected_email": collected,
            }

        # Email inválido → re-preguntar
        return {
            "response": (
                "No reconocí un correo válido. "
                "¿Podés escribirlo nuevamente? (ejemplo: nombre@gmail.com) 📧"
            ),
            "needs_routing": False,
        }

    # Estado desconocido → salir
    logger.warning("wflow_unknown_state", session=session_id, state=state)
    await _set(redis, session_id, S_DONE)
    return None
