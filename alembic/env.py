"""
Alembic env.py — carga DATABASE_URL desde .env y corre migraciones en
modo online (conexión sync con sqlalchemy).

La app usa asyncpg puro para hot path, pero Alembic requiere sync por
diseño (ejecuta SQL bloqueante). Usamos psycopg adapter.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Alembic Config object — acceso a valores del alembic.ini
config = context.config

# Setup logging del ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Resolver DATABASE_URL desde el .env de la app ────────────────────────────
# Evita duplicar el secret en alembic.ini.
try:
    from config.settings import get_settings

    _url = get_settings().database_url
except Exception:
    _url = os.environ.get("DATABASE_URL", "")

if not _url:
    raise RuntimeError("DATABASE_URL no configurado — setealo en .env para correr migraciones.")

# Supabase usa pgbouncer en transaction mode. asyncpg funciona bien ahí,
# pero psycopg2 (sync) necesita prepared_statement_cache_size=0 para no
# romper. Alembic usa su propio connection — lo forzamos via URL.
# Reemplazamos el scheme postgresql:// por postgresql+psycopg2:// y
# agregamos el flag como query param.
if _url.startswith("postgresql://"):
    _url = _url.replace("postgresql://", "postgresql+psycopg2://", 1)
if "prepared_statement_cache_size" not in _url:
    sep = "&" if "?" in _url else "?"
    _url = f"{_url}{sep}prepared_statement_cache_size=0"

config.set_main_option("sqlalchemy.url", _url)


# MetaData objetivo — por ahora None porque NO tenemos modelos SQLAlchemy
# (la app usa asyncpg crudo). Cuando se agregue un ORM, importar el
# metadata acá para auto-generate.
target_metadata = None


def run_migrations_offline() -> None:
    """Modo offline — genera el SQL sin conectarse a la DB.

    Útil para revisar una migración antes de aplicar, o para ejecutarla
    con psql directamente en un ambiente sin Python.
    """
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Modo online — abre conexión y ejecuta. Flow normal."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
