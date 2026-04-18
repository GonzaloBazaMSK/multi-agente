"""
Smoke test del /health endpoint.

Solo levantamos la app FastAPI y pegamos a /health con TestClient — así
validamos que el ensamblado de routers + middlewares no rompa, sin necesidad
de Postgres ni Redis reales (Redis ping fallará y devolverá 'degraded', lo
que es respuesta válida en este contexto).
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_json_with_status():
    # Import perezoso: create_app() arranca todo el árbol de imports y queremos
    # que cualquier error salga con stack trace claro.
    from main import create_app

    app = create_app()
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body
    # 'ok' o 'degraded' — depende de si Redis del entorno responde.
    assert body["status"] in ("ok", "degraded")
