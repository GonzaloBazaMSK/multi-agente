"""
Tests del CircuitBreaker — pura lógica de estado, sin I/O.

Cubre:
  - Trip después de N failures consecutivos
  - Bloquear requests mientras está OPEN
  - Pasar a HALF_OPEN tras recovery_timeout
  - Cerrar de nuevo si el HALF_OPEN succeed-ea
  - Re-abrir si el HALF_OPEN falla
"""
from __future__ import annotations

import time

from utils.circuit_breaker import CircuitBreaker, CircuitState


def _fresh(name: str = "test", **kw) -> CircuitBreaker:
    # Nombre único por test para no colisionar en el _registry global.
    unique = f"{name}-{time.time_ns()}"
    return CircuitBreaker(unique, **kw)


def test_starts_closed_and_allows_requests():
    b = _fresh()
    assert b.state == CircuitState.CLOSED
    assert b.can_execute() is True


def test_trips_after_threshold_failures():
    b = _fresh(failure_threshold=3, recovery_timeout=60)
    for _ in range(2):
        b.record_failure()
    assert b.state == CircuitState.CLOSED  # todavía no llegó al threshold
    b.record_failure()
    assert b.state == CircuitState.OPEN
    assert b.can_execute() is False
    assert b.total_trips == 1


def test_half_open_after_recovery_timeout():
    b = _fresh(failure_threshold=2, recovery_timeout=0)  # 0 = inmediato
    b.record_failure()
    b.record_failure()
    assert b.state == CircuitState.OPEN
    # can_execute() con elapsed >= recovery_timeout → HALF_OPEN
    assert b.can_execute() is True
    assert b.state == CircuitState.HALF_OPEN


def test_half_open_success_closes_circuit():
    b = _fresh(failure_threshold=2, recovery_timeout=0)
    b.record_failure(); b.record_failure()
    b.can_execute()  # → HALF_OPEN
    b.record_success()
    assert b.state == CircuitState.CLOSED
    assert b.failure_count == 0


def test_half_open_failure_reopens_circuit():
    b = _fresh(failure_threshold=2, recovery_timeout=0)
    b.record_failure(); b.record_failure()
    b.can_execute()  # → HALF_OPEN
    b.record_failure()
    assert b.state == CircuitState.OPEN


def test_reset_clears_state():
    b = _fresh(failure_threshold=2)
    b.record_failure(); b.record_failure()
    assert b.state == CircuitState.OPEN
    b.reset()
    assert b.state == CircuitState.CLOSED
    assert b.failure_count == 0


def test_status_dict_shape():
    b = _fresh()
    s = b.status
    # Solo chequeamos que estén las keys que consume el endpoint /admin/breakers
    for key in ("name", "state", "failure_count", "success_count", "total_trips"):
        assert key in s


def test_registry_finds_by_name():
    b = _fresh(name="findme")
    found = CircuitBreaker.get(b.name)
    assert found is b
