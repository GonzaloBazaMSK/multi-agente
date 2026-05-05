---
name: Framework de venta consultiva en sales prompt (reglas 0/0b/1/1b/1c + OBL)
description: Estructura de reglas en agents/sales/prompts.py para que el bot venda consultivamente, no solo informe
type: project
---
El sales prompt (`agents/sales/prompts.py`, ~85k chars al 2026-05-05) tiene una estructura de **reglas numeradas** que arman el framework de venta consultiva. NO son guías sueltas — son reglas duras que el bot tiene que chequear cada turno.

**Las "6 reglas de venta consultiva"** (sección "Las 6 reglas..."):

- **0️⃣** ANTES del primer pitch — preguntá 1 cosa específica para personalizar.
- **0b️⃣** Excavar dolor proactivamente — el 90% de users no cuentan su dolor solo, hay que sacárselo. Pregunta integrada perfil+dolor con **2-3 opciones concretas** (NO preguntas abiertas tipo *"¿algún tema en particular?"* — esas dan permiso a contestar "no" y matan la conv).
- **1️⃣** Si user cuenta historia clínica → conectar + drill-down al brief con `get_course_deep(slug, country, "modules")` para citar módulo + concepto real.
- **1b️⃣** Si pregunta *"es muy genérico?"* → preguntá UN caso específico ANTES de responder.
- **1c️⃣** Si pregunta PRECIO sin contexto → tirá precio + valor + pregunta de cierre con opciones de dolor (NO bloquear al user pidiendo perfil antes — eso era rígido y se suavizó 2026-05-05).
- **2️⃣** Si user expresa duda/limitación → SPIN antes del pitch.
- **3️⃣** Cuando das info → conectá feature → beneficio → outcome.
- **4️⃣** *"¿por qué MSK?"* → diferenciá con datos reales (banco: +200k alumnos, cedentes de élite, asincrónico, alianzas universidades).
- **5️⃣** vs competidor → atacá debilidades sin denigrar.

**4 reglas OBL (obediencia total)** — más arriba en el prompt, marcadas con 🛑🛑🛑:

- **OBL-0** NO ofrecer Másters bajo ningún concepto. 6 slugs prohibidos. Si user pregunta por uno, derivar a asesor académico humano (handoff). Ver `master_products_blocked.md`.
- **OBL-1** Nunca decir "asesor" suelto. SIEMPRE *"asesor académico"*.
- **OBL-2** Flujo del cupón en 2 pasos separados (NO juntar). El cupón **solo va con objeción de precio** — sin objeción no se ofrece.
- **OBL-3** No afirmar exclusividad si el brief no la dice EXPLÍCITAMENTE. Docente coordinador o 1 perfil con pitch detallado NO implican exclusividad.

**Why:** Antes el bot era "buscador" (solo informaba), no "asesor consultivo". Tests cualitativos lo daban 4.3/10 en venta. Tras estas reglas, sube a ~7/10 según re-tests del 2026-05-05.

**How to apply:** Si tenés que ajustar algo del bot de ventas, primero chequear contra estas reglas. NO duplicar; si una regla nueva pisa una vieja, actualizar la vieja. Las reglas existen porque cada una se violó en producción — no son hipotéticas.

**Estado de los tests al 2026-05-05** (escenarios E1-E5 + V2/V5/V6/V7 + M1-M4):
- ✅ E1 (DBT2 con objeción), E3 (genérico), E4 (paliativos múltiple), V5 (info paliativos), M1-M4 (masters): bien.
- ⚠️ V2 (soy clínico), V6 (hola), V7 (soy reumatólogo): el bot todavía pregunta abierto en perfiles que no tienen ejemplo en el prompt. Solución pendiente: agregar 2-3 ejemplos más al prompt de regla 0b.
