- [Deployment](deployment.md) — **Server MSK-Server-NET (16 GB)** en `root@129.212.145.193`. SSH con key `~/.ssh/msk_droplet` + `ServerAliveInterval=30`. Project name `msk-multiagente` obligatorio (server compartido con stack msklatam.net 30+ containers).
- [Sync workflow](sync_workflow.md) — 2 PCs (escritorio + notebook) + server sincronizados vía GitHub `GonzaloBazaMSK/multi-agente`. Droplet viejo `68.183.156.122` apagado (destruir cuando se confirme estabilidad).
- [Stack overview](stack_overview.md) — FastAPI + LangGraph + Pinecone + Redis; 4 agentes (sales, collections, post_sales, closer); canales WhatsApp Meta/Botmaker/Twilio/widget. Notifs in-app, Zoho Voice, Analytics, Pipeline kanban, widget bot kawaii.
- [Sales consultive rules](sales_consultive_rules.md) — Framework venta consultiva: 6 reglas (0/0b/1/1b/1c/2-5) + 4 OBL. Bot chequea contra OBL cada turno. Score venta subió de 4.3/10 → ~7/10.
- [Master products blocked](master_products_blocked.md) — Los 6 másters (product_id 8000000-8000005) NO se venden. Defensa 3 capas: catálogo filtrado + tools dummy + prompt OBL-0.
- [Frontend theme system](frontend_theme_system.md) — Toggle light/dark con CSS vars RGB + script anti-flash + `useTheme` hook. Botón sun/moon en el rail.
- [Scheduler leadership lock](scheduler_leadership_lock.md) — Lock con WORKER_ID + heartbeat 60s/TTL 120s + reaquire loop 30s. Fix bug "scheduler muerto al restart" (saltó 2 noches courses_sync). Watchdog de last_run.
- [Pending: Supabase rotation](pending_supabase_rotation.md) — Rotar SUPABASE_SECRET_KEY que estuvo hardcodeada.
- [Pending: sales prompt enhancements](pending_sales_prompt_enhancements.md) — Mejoras pendientes script de Gino: banco objeciones + cierre cálido (sondeo parcialmente cubierto por regla 0b).
- [Feedback: cobranzas tools independientes](feedback_collections_tools.md) — Las 2 tools de Rebill (suscripcion + insta_link) NO tienen fallback cruzado. Si suscripcion falla → derivar, NO probar insta_link.
- [Feedback: no reescribir lo que está armado](feedback_dont_rewrite.md) — Si hay bug, fixear quirúrgico. No rediseñar decision trees existentes.
- [Workflow preferences](workflow_preferences.md) — Trabajar en `main` directo, nunca worktrees. Verificación real (HTTP 200, screenshot) antes de decir "listo".
- [Language](language.md) — Español rioplatense informal.

## Pendientes activos (al 2026-05-05)

**Riesgos producción** (audit):
- R1: Open redirect en `/api/v1/auth/forgot-password` — origin sin validar contra cors_origins.
- R3: Falta CSP — XSS stored posible en consola.
- R5: Password lockout por cuenta — credential stuffing posible.
- R6: Redis backup solo AOF local, sin offsite a R2.

**Features incompletas**:
- `template_approved` notif: stub listo, falta wire en `api/webhooks.py` con HSM status Meta.
- `email_digest` cron: job registrado pero falta provider (RESEND_API_KEY o EMAIL_SMTP_*).
- Destruir droplet viejo 68.183.156.122 en panel DO.
- Sales rule 0b: agregar 2-3 ejemplos más para perfiles amplios (clínico, reumatólogo) — el bot todavía pregunta abierto en V2/V7.
