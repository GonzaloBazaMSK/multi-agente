"""Verificación rápida del schema + datos migrados en Postgres."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from memory import postgres_store


async def main():
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        tables = await conn.fetch(
            "select tablename from pg_tables where schemaname = $1 order by tablename",
            "public",
        )
        print("tablas en public:", [r["tablename"] for r in tables])
        convs = await conn.fetchval("select count(*) from public.conversations")
        msgs = await conn.fetchval("select count(*) from public.messages")
        print(f"conversations: {convs}")
        print(f"messages:      {msgs}")
        print()
        print("ultimas 3 conversaciones:")
        rows = await conn.fetch(
            "select channel, external_id, status, updated_at from public.conversations "
            "order by updated_at desc limit 3"
        )
        for r in rows:
            ext = r["external_id"][:25]
            print(f"  {r['channel']:10} {ext:25} {r['status']:10} {r['updated_at']}")
    await postgres_store.close_pool()


asyncio.run(main())
