"""
Smoke tests de imports.

Filosofía: si un módulo crashea al import-time (typo, dep faltante, syntax
error en un f-string raro), queremos enterarnos en CI antes de hacer deploy.
NO ejecutan lógica de negocio — solo validan que el árbol de imports no esté
roto.

Si querés agregar un módulo nuevo, sumalo a IMPORTS abajo.
"""
from __future__ import annotations

import importlib

import pytest

# Módulos que tienen que importar limpio en cualquier entorno con las deps
# de prod instaladas. Excluimos los que pegan a la DB en module-load (no hay,
# por convención los pools se inicializan en startup).
IMPORTS = [
    # Config / settings
    "config.settings",
    # API routers
    "api.auth",
    "api.inbox",
    "api.inbox_api",
    "api.admin",
    "api.webhooks",
    "api.widget",
    "api.flows",
    # Memory layer
    "memory.conversation_store",
    "memory.conversation_meta",
    "memory.postgres_store",
    # Agentes
    "agents.classifier",
    "agents.router",
    "agents.flow_runner",
    # Utils
    "utils.audit",
    "utils.circuit_breaker",
    "utils.inbox_jobs",
    "utils.scheduler",
    "utils.conv_events",
    # Integraciones críticas
    "integrations.supabase_client",
    # Entry point
    "main",
]


@pytest.mark.parametrize("module_name", IMPORTS)
def test_module_imports(module_name: str):
    importlib.import_module(module_name)
