"""
Test de transiciones usando el flujo real del menú widget (botones).
Simula lo que pasa cuando el usuario toca un botón en el widget.
"""
import asyncio
import httpx
import time

BASE = "http://localhost:8000"

LABEL_MAP = {
    "sales": "ventas", "collections": "cobranzas",
    "post_sales": "post_venta", "human": "humano",
    "ventas": "ventas", "cobranzas": "cobranzas",
    "post_venta": "post_venta", "humano": "humano",
    "menu": "menu", "bienvenida": "bienvenida",
}


async def run_scenario(c: httpx.AsyncClient, name: str, messages: list, idx: int):
    sid = f"btn-test-{idx}-{int(time.time())}"
    print(f"\n  {'='*60}")
    print(f"  {name}")
    print(f"  {'='*60}")

    # Greeting
    r = await c.post(f"{BASE}/widget/greeting", json={
        "session_id": sid, "country": "AR",
        "user_name": "Test User", "user_email": "test@buttons.com",
        "page_slug": "",
    })
    print(f"  GREETING: OK")

    for turn, (msg, expected) in enumerate(messages, 1):
        r = await c.post(f"{BASE}/widget/chat", json={
            "session_id": sid, "message": msg,
            "country": "AR", "user_name": "Test User",
            "user_email": "test@buttons.com",
        })
        if r.status_code != 200:
            print(f"  T{turn}: HTTP {r.status_code}")
            continue

        d = r.json()
        agent = LABEL_MAP.get(d.get("agent_used", "?"), d.get("agent_used", "?"))
        resp = d.get("response", "")[:120]

        # Para menu, aceptar siempre
        if agent == "menu":
            icon = "🔄"
            status = "menu"
        elif expected and agent == expected:
            icon = "✅"
            status = agent
        elif expected and agent != expected:
            icon = "❌"
            status = f"esperaba={expected} obtuvo={agent}"
        else:
            icon = "🔍"
            status = agent

        print(f"  T{turn}: {icon} {status} | {resp}")


async def main():
    print("=" * 70)
    print("  ROUTER TEST — Flujo real con botones de menú")
    print("=" * 70)

    scenarios = [
        {
            "name": "1. Botón Cobranzas → mantiene 3 turnos",
            "messages": [
                ("Soporte Cobros", "cobranzas"),
                ("Tengo cuotas vencidas del curso de cardiología", "cobranzas"),
                ("Sí quiero pagar, mandame el link", "cobranzas"),
            ],
        },
        {
            "name": "2. Botón Cobranzas → cambia a Ventas",
            "messages": [
                ("Soporte Cobros", "cobranzas"),
                ("Ya regularicé, ahora quiero info de otro curso de pediatría", "ventas"),
            ],
        },
        {
            "name": "3. Ventas → cambia a Cobranzas → vuelve a Ventas",
            "messages": [
                ("Quiero info del curso de cardiología", "ventas"),
                ("Ah espera, tengo una cuota vencida del curso anterior", "cobranzas"),
                ("Ok ya pagué, volvamos al curso nuevo que me interesa", "ventas"),
            ],
        },
        {
            "name": "4. Post_venta mantiene 3 turnos",
            "messages": [
                ("No puedo acceder al campus virtual", "post_venta"),
                ("Probé con Chrome y sigue sin funcionar", "post_venta"),
                ("¿Me podés dar el email de soporte técnico?", "post_venta"),
            ],
        },
        {
            "name": "5. Ventas → Post_venta → Ventas",
            "messages": [
                ("Me interesa el curso de urgencias", "ventas"),
                ("Ah pero del curso que ya compré, no puedo ver los videos", "post_venta"),
                ("Ok gracias, volvamos al curso de urgencias, quiero inscribirme", "ventas"),
            ],
        },
    ]

    async with httpx.AsyncClient(timeout=60) as c:
        for i, s in enumerate(scenarios):
            await run_scenario(c, s["name"], s["messages"], i)

    print(f"\n{'='*70}")
    print("  TEST COMPLETO")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
