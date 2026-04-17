import asyncio, sys, json, os
sys.path.insert(0, "/app")

async def main():
    from memory import postgres_store as pg
    pool = await pg.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT slug, raw FROM courses WHERE country=$1", "ar")
        total = len(rows)
        with_kb = 0
        with_dt = 0
        with_spf = 0
        no_kb = []
        for r in rows:
            raw = json.loads(r["raw"]) if isinstance(r["raw"], str) else r["raw"]
            kb = raw.get("kb_ai") or {}
            if kb and isinstance(kb, dict) and len(kb) > 0:
                with_kb += 1
                if kb.get("datos_tecnicos"):
                    with_dt += 1
            else:
                no_kb.append(r["slug"])
            sp = (raw.get("sections", {}).get("study_plan") or {})
            if sp.get("study_plan_file"):
                with_spf += 1
        print(f"Total cursos AR: {total}")
        print(f"Con kb_ai: {with_kb}")
        print(f"Con datos_tecnicos: {with_dt}")
        print(f"Con study_plan_file: {with_spf}")
        print(f"Sin kb_ai ({len(no_kb)}): {', '.join(no_kb[:15])}{'...' if len(no_kb) > 15 else ''}")

asyncio.run(main())
