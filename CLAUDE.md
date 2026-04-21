# MSK Multi-Agente — contexto operativo

Sistema multi-agente para MSK Latam (cursos médicos online, LATAM). Bot IA +
consola humana. Stack: FastAPI + LangGraph + Next.js 15. Deploy: DigitalOcean
droplet, Docker Compose, nginx.

## Arquitectura

```
┌─ Next.js 15 (frontend/) ────────────┐        ┌─ chat.js embebible ──┐
│  /inbox /prompts /users /templates   │        │  (msklatam.com,       │
│  /redis /dashboard /test-agent       │        │   msklatam.tech)      │
│  /agents /channels /analytics        │        └─┬─────────────────────┘
│  /settings /courses /login           │          │
└─┬────────────────────────────────────┘          │
  │ /api/*                                        │ /widget/*
  ▼                                               ▼
┌─ FastAPI (api/) ─────────────────────────────────────────────────────┐
│  /api/auth/*                   auth.py                                │
│  /api/inbox/*                  inbox_api.py  (13 endpoints REST)      │
│  /api/admin/*                  admin.py, admin_prompts.py,            │
│                                redis_admin.py, reports.py, etc.       │
│  /api/templates/*              templates.py (HSM)                     │
│  /widget/*                     widget.py (chat embebible público)     │
│  /webhook/*                    webhooks.py (Meta, MP, Rebill, Zoho)   │
│  /customer/*                   customer_auth.py (LMS)                 │
└───┬────────────────┬────────────────┬─────────────────────────────────┘
    │                │                │
    ▼                ▼                ▼
┌ Redis ──┐   ┌ Postgres ──┐   ┌ Integraciones ─────────────────────┐
│ Pub/Sub │   │ (Supabase) │   │ OpenAI (gpt-4o + mini + Whisper +  │
│ Cache   │   │ conv + msg │   │ TTS), Pinecone (RAG), Zoho CRM,    │
│ Session │   │ profiles   │   │ WhatsApp Meta Cloud API, Botmaker, │
│ Queues  │   │ audit_log  │   │ MercadoPago, Rebill, Cloudflare R2,│
│ Scheduler│  │            │   │ Sentry, Slack                       │
└─────────┘   └────────────┘   └────────────────────────────────────┘
```

## Convenciones de routing

**Única fuente de verdad**: el frontend Next.js consume todo bajo `/api/*`.
El cliente HTTP (`frontend/lib/api.ts`) antepone `/api` a cualquier path que
le pases — no hay casos especiales.

| Namespace | Quién lo consume | Ejemplo |
|---|---|---|
| `/api/*` | UI de la consola (Next.js) | `/api/inbox/conversations`, `/api/auth/login` |
| `/widget/*` | `chat.js` embebible en sitios externos | `/widget/chat`, `/widget/history/{id}` |
| `/webhook/*` | Integraciones (Meta/Botmaker/MP/Rebill/Zoho) | `/webhook/whatsapp`, `/webhook/mercadopago` |
| `/customer/*` | LMS externo | `/customer/login` |
| `/widget.js` | Bundle JS del widget público | — |
| `/health` | Uptime probe | — |

Agregar un endpoint nuevo del admin → va bajo `/api/admin/<cosa>` con prefix
en el router FastAPI correspondiente.

## Roles

Tres roles jerárquicos (hereda hacia arriba):

1. **`agente`** — solo ve Inbox. Filtrado server-side: ve sus conversaciones
   asignadas + las sin asignar dentro de sus `profiles.queues`
   (ej. `ventas_AR`, `cobranzas_MX`). No puede bulk ops.
2. **`supervisor`** — todo lo del agente + Analytics, Cursos, Plantillas HSM,
   Dashboard (Live/Histórico/Autónomo), Test Agent, Equipo (solo edita colas).
3. **`admin`** — todo + Agentes IA, Prompts, Canales, Redis, CRUD completo del
   Equipo. Único rol que crea/borra usuarios.

Enforcement:
- Backend: `Depends(require_role(...))` o `require_role_or_admin(...)` en cada
  endpoint sensible. En `list_conversations` el scope por rol se aplica en SQL.
- Frontend: `<RoleGate min="...">` en cada página + `rail.tsx` filtra items del
  nav.

## Deploy

**Servidor productivo**: `MSK-Server-NET` · `root@129.212.145.193` (Reserved IP) /
`157.245.8.143` (Public IPv4) · 16 GB RAM / 4 vCPU · Ubuntu 25.04.
El proyecto vive en `/opt/multiagente/` con `COMPOSE_PROJECT_NAME=msk-multiagente`
para evitar colisiones con el stack `msklatam.net` (otros 30+ containers del
mismo server).

SSH con key: `ssh -i ~/.ssh/msk_droplet -o ServerAliveInterval=30 root@129.212.145.193`
(configurado hace ~1 día, elimina el problema de plink cortándose en builds
largos). Password fallback: `tyxtbUKYUoQRMsD9ZtpboZQl`.

**Docker data root ya está en volumen separado** (`/mnt/volume_nyc3_02/docker`,
100 GB). El root disk (48 GB, 96% lleno de otros proyectos) NO se toca.

**Droplet viejo**: `root@68.183.156.122` · 2 GB RAM · se puede apagar/destruir
cuando Gonzalo diga (containers ya bajados, DNS switcheado 20 abr 2026).

**Containers** (`msk-multiagente-*` — prefix del project name):
- `msk-multiagente-api-1` (FastAPI + LangGraph, puerto 127.0.0.1:8000)
- `msk-multiagente-ui-1` (Next.js 15, puerto 127.0.0.1:3000)
- `msk-multiagente-redis-1` (Redis 7, **sin puerto expuesto** — uso interno)

**Flujo deploy**:
```bash
# local
git add . && git commit -m "..." && git push

# server (con tmux porque builds pueden tardar 1-5 min según cambios)
ssh -i ~/.ssh/msk_droplet root@129.212.145.193
cd /opt/multiagente
git pull

# Build corre en tmux así no muere si SSH se corta:
tmux new-session -d -s deploy 'docker compose -p msk-multiagente build api ui > /tmp/build.log 2>&1 && docker compose -p msk-multiagente up -d --force-recreate api ui && echo DEPLOY_DONE > /tmp/done.flag'

# Wait loop hasta ver el flag
until [ -f /tmp/done.flag ]; do sleep 8; done; rm /tmp/done.flag
tail -5 /tmp/build.log

# Para solo backend (código Python):
docker compose -p msk-multiagente build api && docker compose -p msk-multiagente up -d --force-recreate api

# Para solo frontend:
docker compose -p msk-multiagente build ui && docker compose -p msk-multiagente up -d --force-recreate ui
```

**Verificación**:
```bash
curl -sf https://agentes.msklatam.com/health                # 200 {"status":"ok"}
curl -sf https://agentes.msklatam.com/widget.js             # 200 ~45KB (bot SVG + logic)
curl -sf https://agentes.msklatam.com/static/bot-kawaii.png # 200 (FAB image)
```

⚠️ **Siempre `-p msk-multiagente`** en los comandos compose — sin el project
name, docker usa el nombre del directorio ("multiagente") y colisiona con
containers del otro stack msklatam.net. Nuestros containers tienen que
empezar con `msk-multiagente-` sí o sí.

⚠️ `--force-recreate` es **obligatorio** — sin eso compose reutiliza el
container viejo aunque la imagen sea nueva.

**Override de compose** (`docker-compose.override.yml`):
- Puertos a 127.0.0.1 (nginx del host rutea, no exposición directa)
- Redis sin ports (el `!reset []` necesita sintaxis YAML compose v2)
- `mem_limit: 2g` en ui para que next build no tire del 16 GB del server

## DB (Supabase Postgres)

Tablas:
- `auth.users` — managed por Supabase Auth (email + password + JWT).
- `public.profiles` — 1:1 con `auth.users`. Campos: `id` (uuid FK a
  `auth.users.id`), `email`, `name`, `role` ∈ `{admin, supervisor, agente}`,
  `queues` (text[] — colas asignadas).
- `public.conversations` — creadas por el bot (widget / WhatsApp).
- `public.messages` — mensajes de cada conversación.
- `public.conversation_meta` — metadata operativa (assigned_agent_id, status,
  queue, bot_paused, lifecycle, tags, needs_human). 1:1 con conversations.
- `public.inbox_audit_log` — acciones humanas en la consola (asignar,
  clasificar, takeover).

Migraciones en `migrations/` (002-006). Al correr una nueva:
```bash
pscp -batch -pw 'MSK!@L4t4m' migrations/00X.sql root@68.183.156.122:/tmp/
plink -batch -pw 'MSK!@L4t4m' root@68.183.156.122 \
  "docker cp /tmp/00X.sql multiagente-api-1:/tmp/ && \
   docker exec multiagente-api-1 python -c \"
import asyncio, asyncpg, os
async def main():
    c = await asyncpg.connect(os.environ['DATABASE_URL'], statement_cache_size=0)
    await c.execute(open('/tmp/00X.sql').read())
    await c.close()
asyncio.run(main())
\""
```

⚠️ `statement_cache_size=0` es **obligatorio** porque Supabase usa pgbouncer en
transaction mode.

## Agentes IA

4 agentes LangGraph bajo `agents/`:
- **`sales`** — RAG de cursos (Pinecone por país), pitch + link de pago
  (MercadoPago/Rebill).
- **`closer`** — toma el handoff de Ventas cuando el lead está caliente.
- **`collections`** — cobranzas (lee `area_cobranzas` de Zoho), regenera
  links de pago, gestiones.
- **`post_sales`** — soporte LMS (acceso, certificados, tickets).

**Router** (`agents/router.py`): gpt-4o-mini clasifica intent, despacha al
agente correspondiente. Persiste `queue` efectivo en `conversation_meta`.

**Widget flow** (`agents/routing/widget_flow.py`): máquina de estados
hardcoded para el menú inicial del widget (pre-IA). Estados: `main_menu` →
`asesoria_menu` → `pending_email` → `done`. Vive en Redis `wflow:{sid}`.

**Prompts**: editables en vivo desde `/prompts` (UI). Se persisten en
`agents/<agente>/prompts.py`. El bot lee el archivo en cada turno (no se
cachea el módulo). ⚠️ `docker compose build` sin pushear a git sobrescribe.

## Preferencias del usuario (Gonzalo Baza)

- Español rioplatense informal.
- Verificación real (HTTP 200, screenshot) antes de decir "listo".
- Trabajar directo en `main`, nunca worktrees.
- No inventar features ni nombres de archivos.
- Cambios pequeños y verificables, no big-bang.

## Features relevantes al 20-abr-2026

**Inbox**:
- Panel derecho de contacto con: Insights IA, CRUD Zoho (links directos a
  `crm.zoho.com/crm/msklatam/tab/Contacts/{id}` y `.../CustomModule20/{id}`),
  Cobranzas, Llamadas (Zoho Voice logs).
- Teléfono clickeable como `tel:` link → ZDialer Chrome extension lo
  intercepta si está instalado (llamadas salientes via Zoho Voice).

**Notificaciones in-app** (✓ funcional end-to-end):
- Dropdown en el rail (`components/layout/notifications-dropdown.tsx`)
- SSE push real-time (`/api/v1/notifications/stream`) + polling fallback
- Triggers wired: `conv_assigned` (assign endpoint), `new_message_mine` (WA
  Meta + Twilio channels), `conv_stale` (cron cada 15 min, ver
  `utils/stale_conversations.py` + `utils/scheduler.py`)
- Triggers stub (arquitectura lista, falta wire): `template_approved`
  (webhook Meta HSM status change)
- Settings: `/settings/notifications` con toggles por tipo + sound + digest
- DB: `public.notifications` + `public.notification_preferences` (migration
  007).

**Analytics** (`/analytics`) — dashboard operativo con:
- KPIs de volumen (convs, mensajes, hot leads) + estado AHORA (abiertas,
  needs_human, stale)
- SLA cards (takeover rate, bot-only %, SLA <15m / <1h, TMR p50/p90) con
  semáforo verde/rojo según umbrales
- Heatmap 7×24 (día × hora) para decidir turnos
- Leaderboard de agentes humanos (convs atendidas, TMR individual, load)
- Breakdowns por canal, cola, país, lifecycle con % sobre total

**Pipeline** (`/pipeline`) — kanban por **etiqueta IA del clasificador** (NO por
queue del router):
- **8 columnas** = 7 labels del classifier + `sin_clasificar`:
  `caliente` · `tibio` · `frio` · `convertido` · `esperando_pago` ·
  `seguimiento` · `no_interesa` · `sin_clasificar`
- Labels vienen de `agents/classifier.py` → persiste en Redis `conv_label:{session_id}`.
  Ver `LABELS` dict + `SYSTEM_PROMPT` en ese archivo.
- Cards con cliente (resuelto a nombre real via `useAgents()` hook), lifecycle,
  canal, badges de flags (needs_human, bot_paused), preview del último msg,
  "Asignada a <name>" cuando aplica.
- **Drag-and-drop funcional** via `@dnd-kit/core` + optimistic update
  (`frontend/app/(app)/pipeline/page.tsx`). Al soltar en otra columna,
  POST `/api/v1/inbox/conversations/{id}/label` con el nuevo label.
- **Filtros**: días (Hoy/7/30/90/365), canal (Widget/WhatsApp), agente
  ("me"/unassigned/cada agente del equipo).
- Endpoint agregador: `GET /api/v1/inbox/pipeline` con batch `mget` de labels
  desde Redis (no hace N+1). Query params: `days`, `channel`, `agent_id`,
  `unassigned`, `include_resolved`.
- ⚠️ La etiqueta IA reemplaza al viejo "Lifecycle" en la UI del panel derecho
  del Inbox — hay un `{false && (...)}` en `conversation-detail.tsx` que oculta
  el dropdown viejo (preservado para backcompat de DB, no se renderiza).

**Catálogo de cursos** (`/courses`) — ver sección dedicada más abajo. Tiene 2
botones nuevos para disparar sync del WP y regeneración de pitches con IA
desde la UI.

**Widget embebible** (`/widget.js`, usado en msklatam.tech y .com):
- Render SYNC — aparece en <100ms, sin esperar fetch de config remota.
- FAB customizable via `bubble_icon` en Redis `widget:config`. Hoy es el
  bot kawaii 3D (`/static/bot-kawaii.png`, 73 KB optimizado con Pillow
  LANCZOS de un render Gemini).
- CSS: si `#cm-fab` tiene clase `.cm-fab-image` (JS la agrega cuando hay
  imagen), pierde círculo y primary background → se ve la silueta del PNG
  con drop-shadow violeta. Si no, muestra SVG bot custom con ojos
  animados (blink cada 4.5s + mouse-follow via requestAnimationFrame).
- Lock sincrónico `window.__mskWidgetBooted` previene doble-mount en
  Next.js (strategy `afterInteractive` a veces inyecta 2 veces).
- z-index 2147483647 + `isolation: isolate` → nunca tapado por overlays
  del site embebedor.
- CORP header `cross-origin` para `/widget*`, `/static/*`, `/media/*`
  (resto del backend mantiene `same-site`).

**Zoho Voice**:
- OAuth app separada de CRM (ZOHO_VOICE_CLIENT_ID/SECRET/REFRESH_TOKEN
  en `.env`). Scope: `ZohoVoice.call.READ` + `ZohoVoice.call.CREATE`.
- Endpoint `/api/v1/voice/logs?phone=+549...` trae call logs desde
  `voice.zoho.com/rest/json/zv/logs` con params camelCase Zoho
  (`from`, `size`, `userNumber`, `fromDate`, `toDate`, `callType`).
- El INICIO de llamadas lo hace ZDialer (Chrome extension) — Zoho Voice
  no tiene API REST pública para marcar desde sistemas externos.

**Settings** (`/settings`) con sub-nav:
- `/settings/agents` → CRUD del equipo humano (Supabase Auth + profiles)
- `/settings/queues` → matriz de agentes × 18 colas (ventas/cobranzas/
  post-venta × 8 países)
- `/settings/notifications` → preferencias del user logueado (sound, email
  digest, toggles por tipo)
- `/settings/audit` → audit log viewer (admin)
- `/settings/workspace` → estado de integraciones (admin)

## Catálogo de cursos — flujo de actualización

**Fuente de datos**: WP headless `cms1.msklatam.com/wp-json/msk/v1/products-full`.
Un producto tiene el mismo `kb_ai` en todos los países donde está publicado,
así que pitches se generan una vez por slug y se replican a las N filas país.

**Pipeline** (3 fuentes distintas, 3 momentos):

1. **Sync WP → brief_md** (automático + manual):
   - Auto diario 3:30am AR (`utils/scheduler.py:88` cron `courses_sync`).
   - Manual: `POST /api/v1/admin/courses/sync` (todos) o
     `/sync/{country}` (uno) con `X-Admin-Key`.
   - **También disparable desde UI**: botón "Actualizar briefs" en `/courses`
     → endpoint `POST /api/v1/inbox/courses/jobs/sync-briefs`. Progreso en
     Redis `courses_job:sync_briefs` (TTL 7d), polling cada 3s.
   - `integrations/msk_courses.py:build_brief_md()` arma el markdown desde
     `kb_ai` (datos_tecnicos, perfiles_dirigidos, descripcion_y_problematica,
     objetivos_de_aprendizaje) + fallbacks WP. Se regenera en CADA sync — si
     editás el brief a mano en la DB se pisa.
   - **Cómo cambiar el brief**: editar `kb_ai` en el WP, no la DB.

2. **Pitches con LLM** (manual, cuando lo decidís):
   - `scripts/gen_pitch_hooks.py` — CLI, requiere `docker cp` al container +
     `docker exec -e PYTHONPATH=/app -w /app python /tmp/gen_pitch_hooks.py`.
     Env vars: `PITCH_FORCE=1` (regenera todo), `PITCH_ONLY_SLUGS=a,b,c`
     (subset).
   - **También disparable desde UI**: botón "Generar pitches faltantes" en
     `/courses` → endpoint `POST /api/v1/inbox/courses/jobs/regenerate-pitches`
     (`?force=true` para regen total). Progreso en `courses_job:regen_pitches`.
   - Módulo compartido: `integrations/msk_courses_pitches.py` — lógica reusable
     entre el script y el endpoint. Contiene el `SYSTEM_PROMPT` con las 8
     claves canónicas (medico, medico_jefe, residente, estudiante, enfermeria,
     tecnico_salud, licenciado_salud, otros) y el formato JSON esperado.
   - Costo: ~$0.01 por slug (gpt-4o, 1 llamada). 50 slugs = ~$0.50.
   - **Requisito**: el slug tiene que tener `kb_ai`. Sin kb_ai no se genera
     pitch (el script los skippea).

3. **Override manual del pitch** (fino):
   - UI `/courses` → botón "Editar pitch" en la card → `PUT /api/v1/inbox/
     courses/{country}/{slug}/pitch-hook`. Solo edita `pitch_hook`, no el
     `pitch_by_profile`.
   - Útil cuando el LLM genera algo flojo y querés corregir puntualmente.

**Estado al 20-abr-2026** (post sync + regen masivo):
- 116 slugs únicos. 96 con kb_ai → 96 con pitch.
- **20 slugs sin kb_ai (sin pitch)** — hay que cargarles el kb_ai en el WP.
  Prioridades:
  - 🥇 4 con cobertura 16 países: `geriatria-y-gerontologia`, `neonatologia`,
    `gestion-de-la-calidad-en-salud`, `medicina-estetica-facial-laser-y-bioestimulacion`.
  - 🥈 4 Másters AMIR (4 países, ticket premium UYU ~1M en 12 cuotas):
    `urgencias-y-emergencias`, `cuidados-paliativos`,
    `imagen-clinica-y-ecografia`, `rehabilitacion-y-fisioterapia-del-deporte`.
  - 🥉 `salud-familiar-y-comunitaria-amir`, `accsap`,
    `ateneos-medicina-de-urgencias`, `curso-medicina-legal-y-forense`,
    `medicina-legal-y-forense`, `medicina-laboral`,
    `nefrologia-con-mencion-en-hemodialisis-dialisis-peritoneal-y-trasplante`,
    `proteccion-radiologica`, `salud-familiar-y-comunitaria`.
  - `extension-3/6/9` — productos de extensión de cuotas, chequear con
    operaciones si van al pitch o son administrativos.

**Scripts útiles** (en `scripts/`, todos gitignored — son tools ad-hoc):
- `count_pitch_state.py` — conteo de slugs con/sin kb_ai/pitch + breakdown país.
- `check_briefs_for_pitch.py` — valida que los briefs tengan la longitud y
  secciones mínimas antes de generar pitch.
- `sync_all_countries.py` — wrapper CLI del sync (los 17 países con logging).
- `list_courses_no_pitch.py` — lista plana de cursos sin pitch ordenada por
  título.
- `list_no_pitch_flat.py` — versión con países + cedente.
- `list_masters.py` / `masters_detail.py` — filtrado por Máster.

**Recetario práctico** (ya ejecutado hoy, deja guía para mañana):
1. Cargaste kb_ai en el WP para nuevos slugs.
2. `/courses` → **Actualizar briefs** (5-8 min, 17 países, 1560 filas).
3. `/courses` → **Generar pitches faltantes** (1-5 min según cuántos).
4. Inspección visual en `/courses` país AR (el que más cobertura tiene).
5. Si alguno queda flojo: override manual desde la UI, o
   `PITCH_ONLY_SLUGS=... PITCH_FORCE=1` vía CLI.

## Deuda técnica conocida y Hallazgos del auditor (20-abr-2026)

**Hallazgos críticos del análisis de seguridad/arquitectura** (ver
`SECURITY_AUDIT_20260420.md` si se crea el doc, o commit `4ac991c+`):

- 🔴 **R1 Open redirect en `/api/v1/auth/forgot-password`** — `origin` header
  se usa sin validar para `redirect_to` de Supabase mail. Fix: whitelist
  contra `settings.cors_origins`.
- 🔴 **R2 Scheduler lock sin heartbeat** (`main.py:137`) — Redis lock 1h sin
  renovación. Si el worker que lo ganó muere entre horas → nadie corre
  scheduler. **Ya pasó: 18→20 abr el cron `courses_sync` saltó 2 noches**.
  Fix: APScheduler job que renueva TTL cada 10 min + alerta Slack si jobs
  vacíos.
- 🔴 **R3 Sin Content-Security-Policy** — consola Next.js renderiza
  user-generated content (nombres, mensajes WA). XSS stored posible.
  Fix: CSP `default-src 'self'` + nonce en scripts, report-only 1 semana
  antes de enforce.
- 🔴 **R4 Single droplet = SPOF** — si la droplet se cae, todo offline.
  Fix corto: snapshot diario DO ($3/mes) + segundo droplet standby.
- 🔴 **R5 Password reset sin lockout por cuenta** — 5/min por IP no previene
  credential stuffing desde botnet. Fix: counter Redis `login_failed:
  {email_hash}` con lockout 10 fallos/hora.
- 🔴 **R6 Redis backup solo AOF local** — sesiones, prompts, widget config,
  scheduler jobs, conv_labels sin backup offsite. Fix: cron diario
  `BGSAVE` + upload a R2.

**Medios:**
- Cobertura de tests baja (5 archivos: health, circuit-breaker, imports
  smoke, agent queue scope, conftest). Faltan tests de: login + role gate,
  webhook HMAC, agent routing, pitch generator.
- Prompts en archivo `.py` sin lock de concurrencia — dos admins editando
  `/prompts` simultáneo: el último gana. Fix: Redis lock
  `prompts_edit_lock:{agent}` + ETag.
- Duplicación de auth: `require_role` (auth.py) vs `require_role_or_admin`
  (admin.py). Deprecar el primero.
- `inbox_api.py` creciendo (>1700 líneas post-jobs). Candidato a split por
  sub-dominio.
- Scripts de migración MANUAL — no hay `alembic upgrade head` usado.

**Features pendientes de cablear:**
- `template_approved` notif stub — falta wire en `api/webhooks.py` cuando
  llega el webhook de Meta con status de HSM.
- `email_digest` notif marcado "Próximamente" — falta cron diario que
  mande mail con pendientes no leídos (el job `run_email_digest` está
  registrado en `utils/scheduler.py:118`, solo falta el provider SMTP o
  Resend API key).
- Script `msk-front` (msklatam.tech) tiene el
  `<Script src=agentes...widget.js>` en `nonprod-rediseño-preview` — el
  deploy a prod depende del pipeline Vercel del equipo frontend (fuera de
  este repo).
- Droplet viejo (`68.183.156.122`) todavía existe apagado — destruir cuando
  confirmen 1+ semana sin issues.

**Scores del análisis (score 1-10):**
- Seguridad: 7.0
- Escalabilidad: 5.5
- Mantenibilidad: 8.0
- Infra/Disponibilidad: 4.0
- **Global: 6.1** — MVP productivo con muy buena ingeniería de código pero
  débil infra. Lo que falta está concentrado en infra (SPOF, DRP, backup)
  más que en código. ~8 horas de trabajo cubren R1+R2+R3+R6 y suben el
  score a 7+.
