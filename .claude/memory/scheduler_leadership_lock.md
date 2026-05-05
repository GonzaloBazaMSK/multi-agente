---
name: Scheduler leadership lock con heartbeat (anti-bug "scheduler muerto al restart")
description: Cómo funciona el lock para que solo 1 worker corra el scheduler, con heartbeat y reaquire para que sobreviva a restarts
type: project
---
**Problema original** (corregido 2026-05-05, commit `3b002cf`): el lock `scheduler:lock` tenía TTL fijo 1h sin renovar. Si el worker que tenía el scheduler se reiniciaba **dentro de la ventana TTL**, el nuevo proceso hacía `set nx=True` y fallaba (lock vivo del proceso muerto). Resultado: el scheduler quedaba muerto hasta el próximo deploy. **Caso real**: el cron `courses_sync` saltó 2 noches en abril por este bug.

**Solución — patrón leadership con heartbeat** (`utils/scheduler.py`):

- **`WORKER_ID`**: único por proceso (`hostname-pid-uuid6`). Lo seteamos al `set scheduler:lock`.
- **`SCHEDULER_LOCK_TTL = 120s`** + **`SCHEDULER_HEARTBEAT_INTERVAL = 60s`**: heartbeat dentro del scheduler renueva cada 60s, TTL es 120s — ventana de seguridad 2x.
- **`try_acquire_scheduler_lock()`**: llamado desde `main.py` periódicamente. Hace `set ex=120 nx=True`. Si falla, lee el value actual: si soy yo (worker reiniciado dentro del TTL), extiendo el TTL; si es otro worker, devuelvo False.
- **`_heartbeat_scheduler_lock()`**: registrado dentro del scheduler como job APScheduler cada 60s. Verifica que el lock siga siendo nuestro antes de renovar.
- **`_scheduler_acquirer_loop()`** en `main.py`: background task que cada 30s intenta `try_acquire_scheduler_lock`. Si lo gana y no hay scheduler corriendo, lo arranca.

**Timeline single-worker**:
- t=0: worker boota, `try_acquire_scheduler_lock` gana lock, `start_scheduler` arranca, heartbeat cada 60s extiende TTL.
- worker muere → heartbeat para → lock expira en ≤120s → en próximo restart, `try_acquire_scheduler_lock` agarra el lock fresco → arranca scheduler.

**Timeline multi-worker** (cuando escalemos):
- Worker A holds lock + scheduler. Workers B, C corren `_scheduler_acquirer_loop` cada 30s y fallan en agarrar el lock.
- A muere → lock expira en ≤120s → en el próximo tick (≤30s después), B o C agarra el lock y arranca su scheduler.
- Total gap máximo: 150s.

**Watchdog adicional** (`_watchdog_courses_sync`): cada 1h chequea Redis key `courses_sync:last_run`. Si el timestamp es >26h viejo, logea `ERROR` (Sentry lo captura). El job `_run_courses_sync` actualiza el timestamp al terminar.

**Why:** Single point of failure crítico de la arquitectura — sin esto, si el server se reinicia raro, el scheduler queda muerto silenciosamente y no nos enteramos hasta que algún job no corre.

**How to apply:** Si tenés que tocar el scheduler, NO romper la cadena: `WORKER_ID` debe persistir durante la vida del proceso, el heartbeat debe seguir corriendo, y `_scheduler_acquirer_loop` debe seguir activo. Si agregás workers, NO desactivar la lógica del lock — al contrario, asegurate que el reaquire loop esté en TODOS.
