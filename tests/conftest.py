"""
Configuración base de pytest.

Filosofía: smoke tests + unit tests de funciones puras. NO hacemos integration
tests acá porque:
  - El sistema depende de Postgres (Supabase), Redis, OpenAI, Zoho, Meta — todo
    externo, levantarlos en CI es un infra rabbit hole.
  - Si querés probar el flow real, los scripts en scripts/run_simulated.py
    están para eso.

Lo que SÍ probamos:
  - Que los módulos importen sin crashear (catches deps rotas, syntax errors).
  - Funciones puras (circuit breaker, parsers, etc) en isolation.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Aseguramos que el root del proyecto esté en sys.path para que tests puedan
# hacer `from utils.circuit_breaker import ...` sin instalar el paquete.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Variables de entorno mínimas para que `config.settings` no explote al
# instanciarse durante imports. Valores fake — no hacen llamadas reales.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-fake")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ENVIRONMENT", "test")
