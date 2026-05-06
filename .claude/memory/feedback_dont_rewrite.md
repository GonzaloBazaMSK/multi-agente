---
name: No reescribir lo que ya está armado — solo fixear el bug
description: Cuando hay un bug, fixear quirúrgicamente sin rediseñar la lógica que ya estaba probada
type: feedback
---
Cuando el user reporta un bug en código que ya estaba andando, **no rediseñar la lógica** — fixear el bug puntual y dejar el resto como estaba.

**Why:** lo que estaba armado fue diseñado con criterio (a veces con feedback de quien usa el sistema). Reemplazarlo por mi interpretación introduce regresiones y obliga a revisar todo de nuevo. Pasó concretamente con las tools de cobranzas: el árbol de decisión de Caso B (1 cuota → suscripcion / multi → insta_link) ya estaba pensado, y yo intenté meter cross-fallbacks que no correspondían.

**How to apply:**
- Antes de tocar lógica existente, identificá si el cambio es **fix del bug** o **rediseño**. Si es rediseño, preguntar primero.
- Si la decisión es "el bot deriva cuando algo falla", NO inventar que pruebe otra tool primero.
- Cambios cosméticos (mensajes de error, comentarios) son aceptables. Cambios estructurales (decision trees, fallback chains) requieren confirmación del user.
- Cuando el user dice "no cambies las cosas que ya están armadas", significa eso literal: solo fix del bug, no refactor.
