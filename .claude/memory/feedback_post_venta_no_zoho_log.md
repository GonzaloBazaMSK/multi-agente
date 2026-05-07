---
name: Post-venta — NO loguear problemas técnicos/accesos/certificados en Zoho Cobranzas
description: El módulo Cobranzas de Zoho no es para soporte técnico. El único canal con seguimiento real es el portal de tickets MSK.
type: feedback
---
**Regla**: cuando un alumno post-venta tiene un problema (técnico, de acceso al campus, de certificado, etc.), el bot **NO debe registrar nada en Zoho** — debe dar la info que pueda + el link al portal de tickets `https://ayuda.msklatam.com/portal/es/newticket` y listo.

**Why:** la implementación anterior tenía 3 tools (`log_technical_issue`, `request_campus_access`, `request_certificate`) que llamaban a `ZohoCollections.log_interaction()` posteando un record al módulo custom `Cobranzas`. Eso:
1. Pone notas de soporte técnico en un módulo que el equipo de cobros mira (no técnico).
2. No genera notificación a nadie.
3. Le dice al alumno *"He registrado tu problema, el equipo lo revisará"* — promesa vacía: nadie del equipo técnico va a leer ese registro.
4. El portal de tickets oficial sí tiene seguimiento humano real, así que es el único path verdadero.

**How to apply:** cuando agregues funcionalidad al agente de post-venta, NO uses `ZohoCollections.log_interaction()` para tickets/soporte/accesos. La FAQ del prompt + portal de tickets como escape es suficiente. Si en el futuro hay integración con Zoho Desk o un sistema de tickets real, recién ahí vale la pena meter una tool nueva. Mientras tanto, simplicidad: prompt + ticket portal.

**Tools que sí pueden seguir**: `get_student_info` (Zoho Contacts lookup, valor real), `send_nps_survey` (chequear si el flujo NPS sí se monitorea — si no, también sacar).
