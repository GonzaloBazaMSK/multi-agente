# Migraciones Alembic

Cada `.py` en este directorio es una migración. Se genera con:

```bash
alembic revision -m "descripcion corta"
```

Se aplican con:

```bash
alembic upgrade head        # aplicar todo lo pendiente
alembic upgrade +1           # aplicar la siguiente
alembic downgrade -1         # rollback 1
alembic current              # ver qué versión está aplicada
alembic history              # listar todas las versiones
```

## Baseline

Las migraciones SQL crudas que vivían en `migrations/002*` a `migrations/006*`
YA están aplicadas en prod. Alembic arranca con esa realidad como baseline
— la primera migración que agregue `revision -m "..."` va a ser la 007
conceptual, pero con el nuevo sistema de versioning.

Para marcar el estado actual como baseline sin re-aplicar nada:

```bash
# En el server, una sola vez:
docker exec multiagente-api-1 alembic stamp head
```

Esto le dice a Alembic "la DB ya está al día con la última revisión registrada".
Después cada `alembic revision` nueva agrega un step sobre eso.

## Convenciones

- Un cambio = una migración. No agrupar cosas no relacionadas.
- Siempre implementar `downgrade()` — sin rollback es deuda futura.
- Nombres descriptivos: `add_conversation_meta_read_status.py` no `fix_stuff.py`.
- Para operaciones destructivas (DROP COLUMN, etc), comentar el riesgo
  en el docstring de la migración.
