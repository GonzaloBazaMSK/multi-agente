---
name: Cobranzas — 2 tools de Rebill son INDEPENDIENTES, sin fallback cruzado
description: Comportamiento esperado cuando buscar_suscripcion_rebill falla
type: feedback
---
Las dos tools de pago Rebill (`buscar_suscripcion_rebill` y `generar_insta_link_rebill`) son **flujos distintos e independientes**. NO se llaman una a la otra como fallback automático.

**Si `buscar_suscripcion_rebill` falla** (típicamente porque el alumno no está registrado con ese email/customer en Rebill, o no hay suscripción activa): el bot **debe derivar a un asesor de cobranzas**, NO debe intentar `generar_insta_link_rebill` por su cuenta.

**Why:** un fallo de suscripción es síntoma de un problema de registro/data del alumno, no algo que se resuelva con un link one-shot. Si el bot mete un insta-link automático, está enmascarando el problema real (alumno mal registrado, ID_Cliente sin sincronizar, etc.) y deja al recurrente roto. Mejor que un humano lo investigue.

**How to apply:** cuando ajustes el prompt o las tools de cobranzas, mantené esta separación. El árbol de decisión de Caso B (1 cuota → suscripcion / multi-cuotas → insta-link / total → insta-link) NO se modifica con cross-fallbacks. Si una tool falla, el bot deriva.

**Lo que NO hay que hacer:** sugerir en el prompt cosas tipo "si suscripcion falla, llamá insta_link como puente". Eso es exactamente lo que pidió NO meter.
