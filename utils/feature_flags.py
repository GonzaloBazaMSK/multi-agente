"""
Feature flags backed por Redis.

Permite activar/desactivar features sin deploy:
  - Experimentos A/B (% de usuarios ven la feature nueva)
  - Kill switches (apagar un endpoint que empezó a fallar)
  - Rollouts graduales (feature solo para admin, después supervisor, etc)

Uso:

    from utils.feature_flags import is_enabled

    if await is_enabled("ai_autonomous_closer"):
        await run_closer_agent(...)
    else:
        # código viejo
        ...

Admin manipula flags via Redis CLI o /api/v1/admin/feature-flags (futuro).
Por ahora:

    redis-cli SET feature:ai_autonomous_closer '{"enabled": true}'
    redis-cli SET feature:beta_dashboard '{"enabled": true, "allow_roles": ["admin"]}'
    redis-cli SET feature:slow_endpoint '{"enabled": false}'  # kill switch

Config JSON:
  enabled: bool
  allow_roles: [str]            — si está, solo esos roles ven la feature
  rollout_pct: int (0-100)      — % del hash del user_id para rollout
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)

_KEY_PREFIX = "feature:"


@dataclass
class FeatureFlag:
    name: str
    enabled: bool = False
    allow_roles: list[str] | None = None
    rollout_pct: int | None = None


async def _load(name: str) -> FeatureFlag:
    from memory.conversation_store import get_conversation_store

    store = await get_conversation_store()
    raw = await store._redis.get(f"{_KEY_PREFIX}{name}")
    if not raw:
        return FeatureFlag(name=name, enabled=False)
    try:
        data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    except Exception:
        logger.warning("feature_flag_parse_error", name=name)
        return FeatureFlag(name=name, enabled=False)
    return FeatureFlag(
        name=name,
        enabled=bool(data.get("enabled", False)),
        allow_roles=data.get("allow_roles"),
        rollout_pct=data.get("rollout_pct"),
    )


def _user_bucket(user_id: str) -> int:
    """Bucket estable 0-99 del user_id (para rollout_pct).

    Usa CRC32 porque es rápido y determinístico. Mismo user_id → mismo
    bucket siempre. Perfecto para canary releases: si decís rollout 10,
    los mismos 10% de usuarios ven la feature toda la vida.
    """
    return zlib.crc32(user_id.encode()) % 100


async def is_enabled(
    flag_name: str,
    *,
    user_id: str | None = None,
    user_role: str | None = None,
) -> bool:
    """Consulta si la feature está activa para el caller.

    Orden de chequeos:
      1. `enabled=False` → no, punto.
      2. `allow_roles` → si está y el user_role no está en la lista, no.
      3. `rollout_pct` → hash del user_id cae dentro del %.
      4. Enabled sin filtros → sí.
    """
    flag = await _load(flag_name)
    if not flag.enabled:
        return False
    if flag.allow_roles is not None and user_role not in flag.allow_roles:
        return False
    if flag.rollout_pct is not None:
        if user_id is None:
            # Sin user_id no podemos hacer el hash — conservador: no
            return False
        return _user_bucket(user_id) < max(0, min(100, flag.rollout_pct))
    return True
