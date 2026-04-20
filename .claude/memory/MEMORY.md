- [Deployment](deployment.md) — **Server nuevo MSK-Server-NET (16 GB)** en `root@129.212.145.193`. SSH con key `~/.ssh/msk_droplet` + `ServerAliveInterval=30` (no más cortes de plink). Project name `msk-multiagente` obligatorio en todos los compose commands porque el server comparte con el stack msklatam.net (30+ containers).
- [Sync workflow](sync_workflow.md) — 2 PCs (escritorio + notebook) + server sincronizados vía GitHub `GonzaloBazaMSK/multi-agente`. El droplet viejo `68.183.156.122` apagado (destruir cuando se confirme estabilidad).
- [Stack overview](stack_overview.md) — FastAPI + LangGraph + Pinecone + Redis; 4 agentes (sales, collections, post_sales, closer); canales WhatsApp Meta/Botmaker/Twilio/widget. **Agregado 20-abr**: notificaciones in-app (SSE + cron stale), Zoho Voice integration (read-only, ZDialer para iniciar llamadas), Analytics dashboard con SLA/TMR/heatmap/leaderboard, Pipeline kanban de convs por queue, widget con bot kawaii 3D.
- [Workflow preferences](workflow_preferences.md) — Trabajar en `main` directo, nunca usar worktrees de Claude Code. Verificación real (HTTP 200, screenshot) antes de decir "listo".
- [Language](language.md) — Español rioplatense informal.

## Features funcionales (al 20-abr-2026)

Ver [CLAUDE.md](../../CLAUDE.md) para el snapshot completo. Breve:

| Feature | Ruta / path | Estado |
|---|---|---|
| Inbox | `/inbox` | ✓ full |
| Notificaciones in-app | `/settings/notifications`, dropdown rail | ✓ 5/7 triggers wired |
| Analytics dashboard | `/analytics` | ✓ SLA/TMR/heatmap/leaderboard |
| Pipeline kanban | `/pipeline` | ✓ sin drag-drop aún |
| Widget embebible | `/widget.js` + msklatam.com/.tech | ✓ FAB bot 3D |
| Zoho Voice logs | panel de contacto | ✓ readonly |
| Settings sub-nav | `/settings/*` | ✓ 5 secciones |

## Deuda técnica pendiente (para próxima sesión)

- Pipeline: instalar `@dnd-kit/core` para drag-drop real entre columnas
- `template_approved` notif: wire en `api/webhooks.py` cuando llega HSM status de Meta
- `email_digest` notif: cron diario que mande pendientes no leídos
- Destruir droplet viejo `68.183.156.122` en DO panel (Power Off → Destroy)
- Posible: agregar pytest para stale_conversations.py + notifications helpers
