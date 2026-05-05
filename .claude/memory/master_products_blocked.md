---
name: Másters NO se venden por checkout — bloqueados en 3 capas
description: Los 6 cursos Máster (product_id 8000000-8000005) no tienen checkout, hay que derivar a asesor humano
type: project
---
**Decisión de negocio (2026-05-05)**: los 6 productos "Máster" NO se venden por el sitio. Las landing no tienen precio público y el flujo de inscripción es vía asesor académico humano.

**Identificación**:
- Por `product_id`: rango `8000000-8999999` (los 6 actuales son 8000000-8000005).
- Por título: empieza con `Máster` (con M y á acentuada). Ambos criterios coinciden 1:1.
- Por slug: **NO sirve** — los slugs no tienen prefijo "master".

**Los 6 Másters (slug | título | países)**:
| product_id | slug | title | Países |
|---|---|---|---|
| 8000000 | `cuidados-paliativos` | Máster en cuidados paliativos | AR, CL, PY, UY |
| 8000001 | `urgencias-y-emergencias` | Máster en urgencias y emergencias | AR, CL, PY, UY |
| 8000002 | `nutricion-antiaging-microbiota-y-glp` | Máster en nutrición, antiaging, microbiota y GLP | AR, CL, PY, UY |
| 8000003 | `imagen-clinica-y-ecografia` | Máster en imagen clínica y ecografía | AR, CL, PY, UY |
| 8000004 | `rehabilitacion-y-fisioterapia-del-deporte` | Máster avanzado en rehabilitación y fisioterapia del deporte | AR, CL, PY, UY |
| 8000005 | `clinica-infanto-juvenil` | Máster en clínica infanto-juvenil | AR, CL, PY, UY |

**Defensa en 3 capas** (commit `e1792e3`):

1. **Catálogo del prompt** — `memory/postgres_store.py:get_catalog_compact` filtra `product_id BETWEEN 8000000 AND 8999999`. El LLM ya no ve los 6 másters cuando le inyectamos el catálogo del país.
2. **Tools** — `agents/sales/tools.py:get_course_brief` y `get_course_deep` retornan respuesta dura *"⛔ STOP — es MÁSTER PREMIUM, derivá a asesor académico humano"* si el slug es máster. Si el LLM intenta consultar el brief de un máster, el tool le responde con instrucciones de derivar.
3. **Prompt OBL-0** — regla explícita en `agents/sales/prompts.py` con los 6 slugs, mensaje de derivación, política de ofrecer alternativa NO-máster cuando exista.

**Constantes**: `config/constants.py` tiene `MASTER_PRODUCT_ID_MIN/MAX`, `MASTER_SLUGS` (frozenset), helpers `is_master_slug()` / `is_master_product_id()`.

**Tests** (en `sales_test.mjs`): M1, M2, M3, M4 verifican el comportamiento. Pasaron al 2026-05-05.

**Why:** Si el bot recomienda un máster y da link de checkout (`/checkout/cuidados-paliativos`), el link explota porque el producto NO está en el sistema de checkout. User reclama, venta perdida, y peor: imagen MSK afectada.

**How to apply:** Si vas a tocar la lógica de catálogo, búsqueda de cursos, o pitch — chequear que la lista `MASTER_SLUGS` siga viva y los filtros estén aplicados. Si MSK lanza un nuevo máster, agregar el slug a `config/constants.py:MASTER_SLUGS` (la regla del rango 8000000-8999999 ya lo captura por DB pero el slug helper hardcodea — los dos tienen que estar alineados).
