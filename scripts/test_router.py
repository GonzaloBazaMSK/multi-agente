"""
Test exhaustivo del router: asignación de agentes y transiciones.
Prueba mensajes claros, ambiguos y cambios de tema.
"""
import asyncio
import httpx
import time

BASE = "http://localhost:8000"

# ── Casos de routing puro (sin contexto previo) ─────────────────────────
ROUTING_CASES = [
    # (mensaje, agente_esperado, descripción)
    # VENTAS — claro
    ("Quiero información sobre cursos de cardiología", "ventas", "Consulta curso directo"),
    ("Cuánto sale el curso de pediatría?", "ventas", "Precio curso"),
    ("Tienen algo de dermatología?", "ventas", "Búsqueda curso"),
    ("Me interesa inscribirme", "ventas", "Intención compra"),
    ("Qué cursos tienen para médicos generales?", "ventas", "Búsqueda por perfil"),

    # COBRANZAS — claro
    ("Tengo una cuota vencida", "cobranzas", "Cuota vencida"),
    ("No me pudieron cobrar la tarjeta", "cobranzas", "Problema cobro"),
    ("Quiero regularizar mi deuda", "cobranzas", "Regularizar deuda"),
    ("Me llegó un aviso de mora", "cobranzas", "Aviso mora"),
    ("Necesito cambiar la tarjeta de pago", "cobranzas", "Cambio medio pago"),

    # POST_VENTA — claro
    ("No puedo acceder al campus virtual", "post_venta", "Acceso campus"),
    ("Necesito mi certificado", "post_venta", "Certificado"),
    ("El video del módulo 3 no carga", "post_venta", "Problema técnico"),
    ("Cómo descargo el material del curso?", "post_venta", "Material curso"),
    ("Quiero dar de baja mi suscripción", "cobranzas", "Baja suscripción"),

    # HUMANO — claro
    ("Quiero hablar con una persona", "humano", "Pide humano directo"),
    ("Pasame con un asesor", "humano", "Pide asesor"),

    # AMBIGUOS — el router tiene que decidir
    ("Hola, necesito ayuda", "ventas", "Saludo genérico → ventas"),
    ("Tengo un problema con un curso", None, "Ambiguo: post_venta o cobranzas?"),
    ("Ya pagué pero no me aparece", None, "Ambiguo: cobranzas o post_venta?"),
    ("Cuándo empiezo?", None, "Ambiguo: ventas o post_venta?"),
]

# ── Casos de transición (conversación multi-turno) ──────────────────────
TRANSITION_CASES = [
    {
        "name": "Ventas -> Cobranzas",
        "messages": [
            ("Quiero info del curso de cardiología", "ventas"),
            ("Ah pero tengo una cuota vencida del curso anterior", "cobranzas"),
        ],
    },
    {
        "name": "Cobranzas -> Ventas",
        "messages": [
            ("Soporte Cobros", "cobranzas"),  # botón menú
            ("Ya regularicé todo, ahora quiero inscribirme a otro curso", "ventas"),
        ],
    },
    {
        "name": "Ventas -> Post_venta",
        "messages": [
            ("Cuánto sale el curso de urgencias?", "ventas"),
            ("Ya lo compré pero no puedo entrar al campus", "post_venta"),
        ],
    },
    {
        "name": "Cobranzas mantiene (3 turnos)",
        "messages": [
            ("Soporte Cobros", "cobranzas"),
            ("Tengo cuotas vencidas", "cobranzas"),
            ("Sí, quiero pagar. Cómo hago?", "cobranzas"),
        ],
    },
    {
        "name": "Ventas mantiene (3 turnos)",
        "messages": [
            ("Quiero info de cardiología", "ventas"),
            ("Cuántas horas dura?", "ventas"),
            ("Dale, inscribime", "ventas"),
        ],
    },
]


LABEL_MAP = {
    "sales": "ventas",
    "collections": "cobranzas",
    "post_sales": "post_venta",
    "human": "humano",
    "ventas": "ventas",
    "cobranzas": "cobranzas",
    "post_venta": "post_venta",
    "humano": "humano",
    "menu": "menu",
}


async def test_single_routing(
    c: httpx.AsyncClient, msg: str, expected: str | None, desc: str, idx: int
):
    sid = f"router-test-{idx}-{int(time.time())}"
    await c.post(
        f"{BASE}/widget/greeting",
        json={
            "session_id": sid,
            "country": "AR",
            "user_name": "Test User",
            "user_email": "test@router.com",
            "page_slug": "",
        },
    )
    r = await c.post(
        f"{BASE}/widget/chat",
        json={
            "session_id": sid,
            "message": msg,
            "country": "AR",
            "user_name": "Test User",
            "user_email": "test@router.com",
        },
    )
    if r.status_code != 200:
        return f"  ❌ [{desc}] HTTP {r.status_code}", False

    d = r.json()
    agent_label = LABEL_MAP.get(d.get("agent_used", "?"), d.get("agent_used", "?"))

    if expected is None:
        return f"  🔍 [{desc}] → {agent_label} (ambiguo, sin expectativa)", True
    elif agent_label == expected:
        return f"  ✅ [{desc}] → {agent_label}", True
    else:
        resp_preview = d.get("response", "")[:80]
        return f"  ❌ [{desc}] esperaba={expected} obtuvo={agent_label} | {resp_preview}", False


async def test_transition(c: httpx.AsyncClient, case: dict, idx: int):
    sid = f"router-trans-{idx}-{int(time.time())}"
    name = case["name"]
    results = [f"\n  📋 {name}:"]
    all_ok = True

    await c.post(
        f"{BASE}/widget/greeting",
        json={
            "session_id": sid,
            "country": "AR",
            "user_name": "Test User",
            "user_email": "test@router.com",
            "page_slug": "",
        },
    )

    for turn, (msg, expected) in enumerate(case["messages"], 1):
        r = await c.post(
            f"{BASE}/widget/chat",
            json={
                "session_id": sid,
                "message": msg,
                "country": "AR",
                "user_name": "Test User",
                "user_email": "test@router.com",
            },
        )
        if r.status_code != 200:
            results.append(f"    T{turn}: ❌ HTTP {r.status_code} | msg={msg[:40]}")
            all_ok = False
            continue

        d = r.json()
        agent_label = LABEL_MAP.get(d.get("agent_used", "?"), d.get("agent_used", "?"))

        # Para "menu" en primer turno de botones, aceptar
        if agent_label == "menu" and turn == 1 and msg in (
            "Soporte Cobros",
            "Explorar cursos",
        ):
            results.append(
                f"    T{turn}: 🔄 menu (botón → siguiente turno activa agente)"
            )
            continue

        if agent_label == expected:
            results.append(f"    T{turn}: ✅ {agent_label} | «{msg[:50]}»")
        else:
            results.append(
                f"    T{turn}: ❌ esperaba={expected} obtuvo={agent_label} | «{msg[:50]}»"
            )
            all_ok = False

    return "\n".join(results), all_ok


async def main():
    print("=" * 70)
    print("  ROUTER TEST SUITE — Asignación de agentes")
    print("=" * 70)

    async with httpx.AsyncClient(timeout=60) as c:
        # ── Parte 1: Routing puro ────────────────────────────────
        print("\n📌 PARTE 1: Mensajes aislados (sin historial)")
        print("-" * 50)

        ok = fail = ambig = 0
        for i, (msg, expected, desc) in enumerate(ROUTING_CASES):
            result, passed = await test_single_routing(c, msg, expected, desc, i)
            print(result)
            if "✅" in result:
                ok += 1
            elif "❌" in result:
                fail += 1
            else:
                ambig += 1

        print(f"\n  Resultado: {ok} ✅ | {fail} ❌ | {ambig} 🔍")

        # ── Parte 2: Transiciones ────────────────────────────────
        print("\n\n📌 PARTE 2: Transiciones entre agentes")
        print("-" * 50)

        trans_ok = trans_fail = 0
        for i, case in enumerate(TRANSITION_CASES):
            result, passed = await test_transition(c, case, i)
            print(result)
            if passed:
                trans_ok += 1
            else:
                trans_fail += 1

        print(f"\n  Resultado: {trans_ok} ✅ | {trans_fail} ❌")

    print("\n" + "=" * 70)
    print("  TEST SUITE COMPLETO")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
