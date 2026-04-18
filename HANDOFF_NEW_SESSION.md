# Handoff — MSK Console

Documento de contexto para retomar el trabajo en una sesión nueva sin perder
información. Pegá esto al inicio de la conversación nueva.

---

## 🎯 Qué es esto

**MSK Console** = back-office Next.js para operar el bot multi-agente MSK
(ventas / cobranzas / post-venta) en `agentes.msklatam.com`.

- Backend FastAPI ya existía. Estamos agregando una **UI nueva** moderna
  (estilo respond.io / Vercel) sin tocar el flujo del widget existente.
- Stack frontend: Next.js 15 + React 19 + Tailwind + TanStack Query.
- Auth: JWT vía `/auth/login` (opcional — usa admin key como fallback).

---

## 🟢 Lo que está EN PRODUCCIÓN funcional (15/15)

### Inbox (`/inbox`)
- Lista real de conversaciones desde Postgres con polling 15s + SSE real-time
- Filtros: 3 chips (Todas / No leídas / Mías) + dropdown embudo con vistas
  collapsibles (En cola, En atención humana, Con bot, Snoozed, Resueltas)
- **Filtro Cola → País**: 3 colas (Ventas/Cobranzas/Post-venta) × 6 sub-opciones
  fijas (AR/CL/EC/MX/CO/MP donde MP=resto de países). Counts reales del backend.
- Bulk select con toolbar (Asignar/Snooze/Cerrar a múltiples)
- Detalle de conversación con mensajes en markdown + audio player + auto-scroll
- Composer con: emoji picker WA-style, adjuntar archivos (R2), grabar voz
  (MediaRecorder real), plantillas rápidas, corrector ortográfico (OpenAI),
  enviar mensaje real (widget + WhatsApp)
- Panel derecho con 3 cards collapsibles consistentes:
  * **Insights IA** (LLM real con contexto de los últimos 20 mensajes, cache 5min)
  * **Contacto** (perfil Zoho real + cursos + scoring + tags + botón "Ver en Zoho")
  * **Cobranzas** (ricas: saldo vencido, cuotas, días atraso, link de pago)
- Acciones por conversación: Asignar, Snooze, Tomar control, Clasificar
  (todas persisten en Postgres `conversation_meta`)

### Páginas adicionales
- **`/courses`**: catálogo con `pitch_hook` editable inline (LLM-generado),
  filtro por país/búsqueda, badge `kb_ai`
- **`/analytics`**: métricas (totales, por día, por canal, por queue, por país,
  por lifecycle) con gráfico de barras
- **`/settings`**: alta/baja de agentes humanos del equipo
- **`/login`**: form de login con JWT (opcional)

### Backend (FastAPI)
- 21 endpoints REST en `/api/inbox/*` (todos protegidos con admin key):
  - GET: agents, conversations, messages, contacts, queue-stats, courses,
    analytics, audit-log, ai-insights, stream (SSE)
  - POST: assign, snooze, classify, queue, bot, tags, takeover, send,
    bulk/{assign,status,snooze}, llm/correct-spelling, upload (R2),
    courses/{country}/{slug}/pitch-hook, agents (CRUD)
- **Router persiste queue real** en cada turno (sales/closer→sales,
  collections→billing, post_sales→post-sales). El "humano" NO cambia queue,
  solo set `needs_human=true`.
- **Cron snooze**: cada 5 min despierta conversaciones snoozeadas vencidas
  + notif Slack
- **Audit log**: tabla `inbox_audit_log` registra acciones humanas

### DB (Supabase Postgres)
Migraciones aplicadas:
- `001` (legacy) — schema base
- `002_conversation_meta.sql` — assigned_agent_id, status, snoozed_until,
  lifecycle_override/auto, queue, bot_paused, tags, needs_human + view
  `conversation_lifecycle` + tabla `agents`
- `003_inbox_audit_log.sql` — audit log

### Infra
- **Producción**: `https://agentes.msklatam.com`
  - `/api/*` → FastAPI (puerto 8000 interno)
  - `/widget|webhook|admin|inbox|...` → FastAPI (legacy paths)
  - resto → Next.js UI (puerto 3000 interno)
- 3 containers en docker-compose: `multiagente-api-1`, `multiagente-ui-1`,
  `multiagente-redis-1`
- R2 (Cloudflare) para storage de adjuntos
- Nginx con SSL Let's Encrypt
- Logo MSK real en el rail

---

## 🟡 Lo que QUEDÓ pendiente (no incluido en los 15)

Estos NO los hice — el user los puso fuera de scope o no se llegaron:

1. **Editor de prompts visual** (página `/prompts`) — fuera de scope explícito
2. **Página Agentes IA** (configurar router/tools/temperatura) — fuera de scope
3. **Página Canales** (conectar/desconectar WA, configurar tokens) — placeholder
4. **Read receipts** (marcar mensajes como leídos al abrir conv)
5. **Indicador "escribiendo..."** del usuario en widget
6. **Notificación push** real-time de convs nuevas al equipo (Slack solo cuando
   se escala a humano, no en cada conv nueva)
7. **Lifecycle automático del bot**: hoy el bot NO calcula lifecycle (Hot/Cold/etc)
   automáticamente — solo se setea manualmente vía `/classify`. Hay que agregar
   `cm.set_lifecycle_auto()` al final de cada turno del agent runner.
8. **Multi-usuario completo**: backend tiene `/auth/login`, `/auth/me`, `/auth/users`
   pero falta:
   - Identidad real del agente actual en frontend (hoy `ME_ID = "u-gbaza"` hardcoded
     en `inbox/page.tsx`)
   - Filtro "Mías" usa el ME_ID hardcoded; debería leer del `useAuth().user.id`
   - Permisos por rol (admin/supervisor/agent)
9. **Sanitización HTML** en mensajes salientes
10. **Rate limiting** del endpoint `/llm/correct-spelling`

---

## 🔧 Comandos clave

### Local dev (Windows)
```bash
cd "C:/Users/Gonzalo/Documents/GitHub/multi-agente/frontend"
npm install
npm run dev   # http://localhost:3000
```
El proxy `/api/*` apunta a `https://agentes.msklatam.com/api/*` (configurado
en `.env.local`).

### Deploy backend + frontend a producción
```bash
# SSH al server
plink -batch -pw "MSK!@L4t4m" root@68.183.156.122

cd /opt/multiagente
bash deploy.sh                              # rebuildea api con --no-cache
docker compose up -d --build ui             # rebuildea ui

# Verificación
curl -sf http://localhost:8000/health
curl -sf -o /dev/null -w "%{http_code}\n" https://agentes.msklatam.com/inbox
```

### Aplicar nueva migración
```bash
# Local: crear archivo en migrations/00X_*.sql
# Subir y aplicar:
pscp -batch -pw "MSK!@L4t4m" "migrations/00X_xxx.sql" root@68.183.156.122:/tmp/00X.sql
plink -batch -pw "MSK!@L4t4m" root@68.183.156.122 \
  "docker cp /tmp/00X.sql multiagente-api-1:/tmp/00X.sql && \
   docker exec multiagente-api-1 python -c \"
import asyncio, asyncpg, os
async def main():
    c = await asyncpg.connect(os.environ['DATABASE_URL'], statement_cache_size=0)
    await c.execute(open('/tmp/00X.sql').read())
    await c.close()
asyncio.run(main())
\""
```

---

## 📂 Archivos clave del frontend

```
frontend/
├── app/
│   ├── layout.tsx                   # Root + QueryProvider + AuthProvider
│   ├── login/page.tsx               # Form de login
│   └── (app)/
│       ├── layout.tsx               # Rail + main
│       ├── inbox/page.tsx           # ⭐ página principal
│       ├── courses/page.tsx         # Catálogo editable
│       ├── analytics/page.tsx       # Métricas
│       ├── settings/page.tsx        # CRUD agentes
│       └── (placeholders) agents, channels, prompts
├── components/
│   ├── inbox/
│   │   ├── conversation-list.tsx    # Sidebar lista + filtros
│   │   ├── conversation-detail.tsx  # Header + mensajes + composer
│   │   ├── message-bubble.tsx       # Render con markdown + audio
│   │   ├── composer.tsx             # Input + emojis + upload + audio + LLM
│   │   └── contact-panel.tsx        # Insights IA + Contacto + Cobranzas
│   ├── layout/rail.tsx              # Sidebar estrecha estilo respond.io
│   └── ui/ (button, badge, input, dropdown, collapsible-section, flag, avatar)
└── lib/
    ├── api.ts                       # fetch wrapper con admin-key + JWT
    ├── api/inbox.ts                 # Hooks TanStack Query del inbox
    ├── api/sse.ts                   # Suscripción SSE real-time
    ├── auth.tsx                     # AuthProvider + useAuth + getAuthToken
    ├── mock-data.ts                 # Tipos centralizados (algunos mocks)
    └── utils.ts                     # cn(), countryFlag(), initials()
```

## 📂 Archivos clave del backend

```
multi-agente/
├── api/
│   ├── inbox_api.py                 # ⭐ NUEVA UI — todos los endpoints
│   ├── inbox.py                     # Legacy (SSE bus + reply endpoint)
│   └── ... (auth.py, widget.py, webhooks.py, etc)
├── memory/
│   ├── conversation_meta.py         # CRUD de la nueva tabla meta
│   └── postgres_store.py            # Pool + queries cursos/conversations
├── utils/
│   └── inbox_jobs.py                # Cron snooze + Slack + audit log
├── agents/
│   ├── router.py                    # ⚠️ persiste queue desde acá
│   └── (sales, closer, post_sales, collections)
└── migrations/
    ├── 001_*.sql
    ├── 002_conversation_meta.sql
    └── 003_inbox_audit_log.sql
```

---

## 🐛 Bugs conocidos

1. **Auth not enforced**: si no hay token, el frontend NO redirige a `/login` —
   usa el admin key. Esto fue intencional para no bloquear pruebas. Para
   producción real hay que activar el redirect en `lib/auth.tsx`.
2. **`ME_ID` hardcoded**: en `inbox/page.tsx` hay `const ME_ID = "u-gbaza"`.
   Cuando se complete la integración con `useAuth()`, debe leer del user real.
3. **Send WhatsApp con adjuntos**: hoy manda la URL R2 como mensaje aparte de
   texto. Meta Cloud API soporta media nativo (audio/image/document) — debería
   refactorizarse para usar esos endpoints específicos.
4. **Cron de snooze**: `start_inbox_jobs()` arranca en cada worker uvicorn.
   Si hay 2+ workers, corre 2x. Debería usar el lock Redis como
   `autonomous_scheduler`.
5. **`get_auth_user_by_email` no filtra**: en `integrations/supabase_client.py`
   pasa `params={"email": email}` al admin endpoint de Supabase, pero ese
   endpoint NO filtra por email (es solo paginación). Devuelve `users[0]` que
   es el primer user de toda la lista, no el buscado. Hoy no afecta el login
   (Supabase Auth verifica password directo) pero podría romper otros usos.
   Fix: filtrar manualmente por email en el código después de listar.

---

## 🔧 Fix aplicado en última sesión (17/04/2026)

**Síntoma**: el user no podía loguearse con `gonzalobaza@msklatam.com`.

**Root cause**: el frontend pegaba a `/api/auth/login`, `/api/auth/me`,
`/api/auth/logout` pero los endpoints reales son `/auth/*` (sin prefix `/api/`).
Nginx pasaba el path entero a FastAPI → 404, que el form interpretaba como
credenciales mal.

**Fix**: commit `35a727f` — `frontend/lib/auth.tsx` ahora usa `/auth/*` directo.
La rule de nginx ya rutea `/auth/*` a FastAPI (puerto 8000).

**Verificación**:
- ✅ Cuenta `gonzalobaza@msklatam.com` confirmada en Supabase desde el 13/4
- ✅ Role `admin`, NO baneada
- ✅ Último login exitoso registrado: `2026-04-17T23:10:18Z`
- ✅ `POST https://agentes.msklatam.com/auth/login` responde 401 con creds
   inválidas (no 404)
- ✅ UI rebuildeada y deployada en prod

---

## 🔐 Credenciales (para esta sesión)

- Server SSH: `root@68.183.156.122` / `MSK!@L4t4m`
- Admin key API: `change-this-secret`
- Postgres: `postgresql://postgres.gfvmexzejtlhuxljywbr:...@aws-1-us-east-1.pooler.supabase.com:6543/postgres`
- Slack webhook: configurado en `settings.slack_webhook_url`
- OpenAI: `OPENAI_API_KEY` en `.env`

---

## 📞 Contacto / Cuenta de prueba

- Email del usuario: `gbaza2612@gmail.com`
- Su perfil Zoho: Personal médico + Cardiología + Auxiliar-Asistente +
  Hospital Italiano + COLEMEMI (Misiones)
- Su zoho_id: `5344455000160260053`
