# Project Context — MSK Multi-Agente

> **Lee esto primero.** Es el dump exhaustivo del conocimiento del proyecto que
> Claude acumula durante las sesiones largas y se pierde al resumir. Si vas a
> tocar código, leé este archivo + `HANDOFF_NEW_SESSION.md` antes.

---

## 1. Quién es el user y cómo trabaja

**Gonzalo Baza** — CTO/founder de **MSK Latam**, empresa de cursos médicos
online (cardiología, pediatría, medicina interna, etc.) con presencia en
Argentina, Chile, México, Colombia, Ecuador, Perú, Uruguay y resto LATAM.

**Idioma**: castellano argentino. Usa "vos", "vamos", "dale", "che". Hablale así.

**Su rol acá**: cliente directo. Te pide tareas técnicas concretas y espera que
las verifiques antes de declararlas hechas.

**Patrones de feedback que aprendí (a la mala):**

- *"estas haciendo las cosas rapido pero mal"* — cuando entrego sin verificar.
  **Lección**: después de cada cambio, verificar con `curl`, `docker logs`, o
  test real antes de decir "listo".
- *"yo te juro que estoy decepcionado, todavia no me arreglaste el filtro"* —
  cuando el cambio no se ve en prod (cache, container no rebuildeado, etc.).
  **Lección**: confirmar deploy con `docker ps`, hash del último commit,
  HTTP code real.
- *"podes hacer QA vos? al final hago todo yo"* — cuando le paso tareas a él.
  **Lección**: probar yo el flujo end-to-end antes de pasarle.
- *"no veo nada nuevo"* (10+ veces) — cache del browser. **Lección**: incluir
  "hacé Ctrl+F5" en el mensaje cuando deployo frontend.
- *"como no voy a poder mandar un audio sin texto"* — bugs estúpidos de UX.
  **Lección**: pensar el flujo desde el lado del usuario antes de codear.
- *"ya te lo dije como 10 veces"* — cuando no escuché bien una directiva.
  **Lección**: cuando dice algo dos veces, es algo crítico que no entendí.

**Lo que valora:**

- Verificación con outputs reales (HTTP 200, size del response, screenshot).
- Honestidad: si algo no anda, decirlo. No vender humo.
- Velocidad sin sacrificar correctitud (tensión real).
- Que mantenga la "memoria" del proyecto entre sesiones.

**Lo que detesta:**

- Inventar features o nombres de archivos sin verificar que existen.
- Decir "listo, deployado" cuando no hice el `docker compose up -d --build`.
- Repetirle algo que ya sabe.
- Que rompa funcionalidad existente al agregar features nuevos.

**Cuenta de prueba** (para QA real):
- Email: `gbaza2612@gmail.com`
- Zoho ID: `5344455000160260053`
- Perfil: Personal médico, Cardiología, Auxiliar-Asistente, Hospital Italiano,
  COLEMEMI (Misiones, Argentina)
- País: AR

---

## 2. El negocio — qué hace MSK Latam

**Producto**: cursos online de medicina (~150 cursos en catálogo). Se compran
por país con precios locales. Hay planes a 1 pago, 3 cuotas, 6 cuotas, 12
cuotas. Cobranza con **MercadoPago** (AR/MX) y **Rebill** (multi-país).

**CRM**: **Zoho CRM** — Leads, Contacts, Sales Orders, módulo custom de
Cobranzas (`area_cobranzas`). Ya hay miles de contactos cargados.

**Canales de contacto del cliente:**
1. **WhatsApp** vía Botmaker o Meta Cloud API directo.
2. **Widget web** embebible en WordPress (`msklatam.com`, `msklatam.tech`).

**Bot multi-agente**: 4 agentes especializados que toman cada conversación:
- **Ventas** (`sales`) — RAG sobre catálogo, asesora prospectos, agarra el
  curso que viste en la página, sugiere planes.
- **Closer** (`closer`) — toma el handoff de ventas cuando el lead está
  caliente, manda link de pago.
- **Cobranzas** (`collections`) — recupera deuda vencida (lee `area_cobranzas`
  de Zoho).
- **Post-venta** (`post_sales`) — soporte de alumnos activos (acceso al LMS,
  certificados, cambios de curso).

**Decisión de a qué agente derivar**: la toma un **router** (LangGraph) con
gpt-4o-mini liviano que clasifica intent. El user puede forzar agente desde
el widget vía `forced_agent`. Si detecta keywords de handoff humano
(`HANDOFF_KEYWORDS` en `config/constants.py`) o el agent emite tag
`HANDOFF_REQUIRED`, escala.

**Países primarios**: AR, CL, EC, MX, CO. Resto agrupado como **MP** (Multi-país).
Esto es importante para los filtros del inbox (`PRIMARY_COUNTRIES` en backend).

**Horario de atención**: Lun-Vie 9-18h ART. Fuera de eso el bot manda
`off_hours_message` (en `config/settings.py`).

---

## 3. Arquitectura — quién corre dónde

### Producción (DigitalOcean droplet)

- **Server**: `root@68.183.156.122`, password `MSK!@L4t4m`
- **Dominio**: `agentes.msklatam.com` (DNS apunta al droplet, SSL Let's Encrypt)
- **Path**: `/opt/multiagente` (clone del repo, `git pull` para actualizar)

### Containers (`docker-compose.yml`)

```
multiagente-api-1     FastAPI puerto 8000 — bot + endpoints inbox
multiagente-ui-1      Next.js puerto 3000 — UI nueva (MSK Console)
multiagente-redis-1   Redis 7 puerto 6379 (solo localhost)
```

`docker-compose.yml` original SOLO tenía `api` + `redis`. El `ui` se agregó
en commit `17c452e` cuando agregamos el frontend Next.js. La `Dockerfile` del
ui está en `frontend/Dockerfile`.

**Volúmenes**:
- `redis_data` — persistencia AOF de Redis
- `media_data` → `/app/media` — fallback local de adjuntos cuando R2 no está
  configurado

### Nginx (`/etc/nginx/sites-available/agentes.msklatam.com`)

Persistido en repo en `deploy/nginx-agentes.msklatam.com.conf`. Routing:

```
/api/*                              → FastAPI 8000 (regex location /api/)
/widget/* /webhook/* /admin/*       → FastAPI 8000 (regex location ~ ^/(widget|...)/)
/inbox-ui /auth /templates /flows
/reports /test-agent /autonomous
/customer-auth /health /static
/media /demo

/widget.js                          → FastAPI 8000 (location = exact)
/msk /test /audio-test /health      → FastAPI 8000 (location = exact)
/inbox-ui                           → FastAPI 8000 (location = exact)

todo lo demás (/)                   → Next.js 3000
```

**Por qué los `location =` exactos**: el regex `^/(...)/` requiere `/` después
del prefix. `/widget.js` no termina en `/`, así que caía en `location /` →
Next.js → 404. Esto rompió el widget en `msklatam.tech`. Fix en commit `c32d165`.

### Almacenamiento

- **Postgres (Supabase)** — tabla principal `conversations`, mensajes,
  `conversation_meta`, `agents`, `inbox_audit_log`, `profiles`, `customers`,
  `auth.users`. Connection via `aws-1-us-east-1.pooler.supabase.com:6543`
  (transaction pooler, **statement_cache_size=0 obligatorio**, ver §10).
- **Redis** — cache de conversaciones calientes (TTL 7 días), session tokens,
  pubsub para SSE cross-worker, locks (`scheduler:lock`).
- **R2 (Cloudflare)** — adjuntos del inbox (audio, imágenes, files). Bucket
  `msk-multiagente-media`. URL pública vía `r2_public_url`.

### Integraciones externas

- **OpenAI** — gpt-4o (agentes) + gpt-4o-mini (router, AI insights, spell
  correction)
- **Pinecone** — vector store para RAG de cursos
- **Zoho CRM** — módulos Leads, Contacts, Sales Orders, Cobranzas
- **MercadoPago** — checkout AR/MX
- **Rebill** — checkout multi-país, suscripciones
- **Botmaker** — envío de WhatsApp + handoff humano
- **Meta WhatsApp Cloud API** — alternativa directa a Botmaker
- **Twilio** — sandbox WhatsApp para tests
- **Slack** — webhook para notificar handoffs y snooze wakes
- **Sentry** — error tracking (opcional via `sentry_dsn`)

---

## 4. El bot — multi-agente con LangGraph

### Router (`agents/router.py`)

```python
class SupervisorState(TypedDict):
    messages: list                  # historial completo
    current_agent: str              # quién va a contestar
    country: str                    # AR/MX/CL/...
    channel: str                    # whatsapp/widget
    conversation_id: str
    phone: str
    email: str                      # del user_profile (widget logueado)
    user_name: str
    page_slug: str                  # ej "cardiologia-amir" (widget)
    has_debt: bool                  # de cobranzas cacheada
    is_student: bool                # tiene cursadas en Zoho
    handoff_requested: bool
    handoff_reason: str
    link_rebill_enviado: bool
    verificar_pago: bool
    forced_agent: str               # widget puede forzar
```

**Flow**:
1. `classify_intent_node` decide agente (LLM o forced).
2. Llama al subgrafo del agente correspondiente.
3. Agente responde + posiblemente emite `HANDOFF_REQUIRED:motivo`.
4. Router persiste queue y `needs_human` en `conversation_meta` (commit del
   `feat(inbox): /analytics + ...`).

**Mapeo agente → queue**:
```python
queue_map = {
    AgentType.SALES.value: "sales",
    AgentType.CLOSER.value: "sales",
    AgentType.COLLECTIONS.value: "billing",
    AgentType.POST_SALES.value: "post-sales",
}
```

⚠️ **El "humano" NO cambia queue** — solo setea `needs_human=true`. La queue
sigue siendo la última que se calculó (ej si era cobranzas y escala humano,
queue queda en `billing`).

### Agentes — cada uno tiene su propio subdir

```
agents/<agent>/
├── agent.py       # build_<agent>_agent() — devuelve subgrafo compilado
├── prompts.py     # SYSTEM_PROMPT cacheado al inicio del proceso
└── tools.py       # tools de LangChain (Zoho, Rebill, RAG, etc)
```

**Agentes activos**: `sales`, `closer`, `collections`, `post_sales`.

**Prompts cacheados al inicio** — no hay hot-reload. Para cambiar un prompt
hay que reiniciar el container.

### Routing extra (`agents/routing/`)

- `router_prompt.py` — system prompt del clasificador
- `greeting_prompt.py` — saludo inicial cuando el widget abre
- `widget_flow.py` — botones del widget (forced_agent shortcuts)

### Classifier (`agents/classifier.py`)

Pre-clasificador antes del router LLM. Detecta cosas obvias por keywords sin
gastar tokens.

### Flow runner (`agents/flow_runner.py`)

Ejecuta flujos visuales creados desde `/admin/flows-ui`.

---

## 5. Lógica del inbox (`/inbox`)

### Queues + países

```
sales        × {AR, CL, EC, MX, CO, MP}   = 6 sub-buckets
billing      × {AR, CL, EC, MX, CO, MP}   = 6 sub-buckets
post-sales   × {AR, CL, EC, MX, CO, MP}   = 6 sub-buckets
```

`MP` = Multi-país, agrupa todo lo que no es AR/CL/EC/MX/CO.

`PRIMARY_COUNTRIES = {"AR", "CL", "EC", "MX", "CO"}` está hardcoded en el
backend (`api/inbox_api.py`). Si vas a sumar países primarios, hay que tocarlo
ahí.

El country se deriva de `conversations.user_profile->>'country'` en SQL.

### Vistas (filtros principales del inbox)

```
"En cola"              status='open', needs_human=false, snoozed_until is null
"En atención humana"   needs_human=true OR assigned_agent_id is not null
"Con bot"              bot_paused=false, status='open'
"Snoozed"              snoozed_until > now()
"Resueltas"            status='resolved'
```

### Lifecycle

5 valores: `new`, `hot`, `warm` (usado en UI pero no en check), `cold`,
`customer`. El check de Postgres acepta `('new', 'hot', 'customer', 'cold')`.

⚠️ **Bug pendiente**: el bot NO calcula lifecycle automático. Solo se setea
manualmente desde el frontend vía `/classify`. La columna `lifecycle_auto`
existe pero nunca se llena. Falta agregar `cm.set_lifecycle_auto()` al final
de cada turno del agent runner.

La view `conversation_lifecycle` resuelve el "efectivo":
```sql
coalesce(lifecycle_override, lifecycle_auto, 'new')
```

### Snooze

- Botón "Snooze" en la UI elige duración: 1h, 4h, mañana 9am, lunes 9am, custom.
- Se persiste `snoozed_until` (timestamp).
- Cron en `utils/inbox_jobs.py` corre cada 5min: `wake_expired_snoozed()` libera
  los vencidos + manda Slack.

⚠️ **Bug**: el cron arranca en cada worker uvicorn. Si subimos a 2+ workers,
corre 2× al mismo tiempo. Hay que copiar el patrón de `autonomous_scheduler`
en `main.py:117` que usa Redis lock (`scheduler:lock`).

### Bulk actions

Toolbar arriba de la lista cuando hay seleccionados:
- Asignar a agente
- Snooze (mismo selector)
- Cerrar (status=resolved)

### Send message

`POST /api/inbox/conversations/{id}/send` — body `{text, attachments[]}`.
1. Persiste el message en Postgres
2. Broadcast SSE
3. Push al canal:
   - **widget** → Redis pubsub `widget:reply:{conversation_id}` que el
     endpoint SSE del widget escucha
   - **whatsapp** → Meta Cloud API directo (`integrations/whatsapp_meta.py`)

⚠️ Para WhatsApp: hoy si hay attachments, manda la URL R2 como mensaje
APARTE de texto. Meta soporta media nativo (audio/image/document) — habría
que refactorear para usar esos endpoints específicos.

### Spell correction

`POST /api/inbox/llm/correct-spelling` — body `{text}`. Llama a gpt-4o-mini
con prompt "corrector ortográfico tono profesional argentino". Sin rate
limit (TODO).

### AI Insights

`GET /api/inbox/conversations/{id}/ai-insights` — toma últimos 20 mensajes,
llama a gpt-4o-mini y devuelve:
```json
{
  "summary": "...",
  "nextStep": "...",
  "scoringReasons": ["..."]
}
```
Cache 5min en Redis (`ai_insights:{conversation_id}`).

### SSE (real-time)

`GET /api/inbox/stream?key=<admin_key>` — Server-Sent Events.
- En frontend: `lib/api/sse.ts` → `useInboxSSE()` que invalida queries
  cuando llega un evento.
- En backend: `broadcast_event()` en `api/inbox.py` mete el event en
  Redis pubsub channel `inbox:events`. Un listener cross-worker
  (`start_pubsub_listener`) lo retransmite a todos los SSE clients
  conectados al worker actual.

Eventos típicos:
- `new_message` — mensaje nuevo entró a una conv
- `conversation_updated` — assigned/snoozed/classified
- `snooze_woken` — cron despertó una conv

---

## 6. Schema de la DB (Supabase Postgres)

### Tabla `conversations` (legacy, no la creamos nosotros)

```sql
id              uuid primary key
channel         text         -- 'widget' | 'whatsapp'
phone           text
email           text
session_id      text
user_profile    jsonb        -- {name, email, country, ...}
created_at      timestamptz
updated_at      timestamptz
last_message_at timestamptz
```

### Tabla `messages` (legacy)

```sql
id              uuid primary key
conversation_id uuid references conversations(id)
role            text         -- 'user' | 'assistant' | 'system'
content         text
metadata        jsonb        -- {agent: 'sales', attachments: [...], ...}
created_at      timestamptz
```

⚠️ El campo `agent` está en `metadata->>'agent'`, NO como columna directa.
Bug que ya nos comimos antes — `select agent from messages` rompe.

### Tabla `agents` (nueva, migración 002)

```sql
id          text primary key   -- 'u-gbaza', 'u-mtottil', ...
name        text
email       text
initials    text
color       text                -- 'from-pink-500 to-fuchsia-600' (Tailwind)
active      boolean
created_at  timestamptz
```

### Tabla `conversation_meta` (nueva, migración 002)

1:1 con `conversations`. Toda acción humana se persiste acá. **No** modificamos
`conversations` original.

```sql
conversation_id    uuid pk references conversations(id)
assigned_agent_id  text references agents(id)
assigned_at        timestamptz
status             text default 'open'
                   -- 'open' | 'pending' | 'resolved'
snoozed_until      timestamptz
snoozed_at         timestamptz
lifecycle_override text  -- 'new' | 'hot' | 'customer' | 'cold'
lifecycle_overridden_at timestamptz
queue              text default 'sales'
                   -- 'sales' | 'billing' | 'post-sales' | 'support'
bot_paused         boolean default false
bot_paused_at      timestamptz
tags               text[] default '{}'
needs_human        boolean default false
lifecycle_auto     text  -- mismo enum, llenado por el bot (TODO)
updated_at         timestamptz   -- trigger
```

Indexes en: status, assigned_agent_id, queue, snoozed_until, needs_human, tags.

Function helper: `ensure_conversation_meta(uuid)` — get-or-create idempotente.

View: `conversation_lifecycle` — resuelve override + auto.

### Tabla `inbox_audit_log` (migración 003)

```sql
id              uuid pk
actor_id        text not null   -- user.id de quien hizo la acción
action          text not null   -- 'assign', 'snooze', 'classify', etc
conversation_id uuid
detail          jsonb
created_at      timestamptz
```

### Supabase Auth (tabla `auth.users` — managed)

Login vía email+password. El `sign_in_with_password` se hace contra
`{SUPABASE_URL}/auth/v1/token?grant_type=password`. Devuelve session JWT.

### Tabla `profiles` (custom de la app)

```sql
id          uuid pk
email       text unique
name        text
role        text          -- 'admin' | 'supervisor' | 'agente'
queues      text[]        -- subset de ALL_QUEUES (ventas_AR, cobranzas_MX...)
created_at  timestamptz
```

### Tabla `customers` (custom)

Para alumnos del LMS — distinto a `profiles` (admin/agente).

```sql
id, email, name, phone, country, courses[], ...
```

---

## 7. Endpoints — catálogo completo

Todos los del inbox están protegidos con header `X-Admin-Key:
change-this-secret` (cambiar para prod real). El frontend lo manda
automáticamente desde `lib/api.ts`.

### `/auth/*` (sin prefix `/api/`!)

- `POST /auth/login` — `{email, password}` → `{token, user}`
- `POST /auth/logout` — header `x-session-token`
- `GET /auth/me` — header `x-session-token` → user
- `GET /auth/queues` — devuelve `ALL_QUEUES`
- `GET /auth/users` — admin/supervisor only
- `POST /auth/users` — admin only — crea user en Supabase Auth + profile
- `PATCH /auth/users/{id}` — admin/supervisor (supervisor solo queues)
- `DELETE /auth/users/{id}` — admin only
- `GET /auth/agent-status` — estado disponibilidad
- `POST /auth/agent-status` — `{status}` available/busy/away

⚠️ **Importante**: el frontend pegaba a `/api/auth/*` y eso devolvía 404.
Fix en commit `35a727f`: ahora pega a `/auth/*` directo. La rule de nginx
para `^/(...|auth|...)/`  rutea bien.

### `/api/inbox/*` (todos con admin key)

GETs:
- `/agents` — lista agentes activos
- `/conversations?view=&lifecycle=&channel=&queue=&country=&search=` — lista
  con filtros
- `/conversations/{id}/messages` — historial (mapea agent desde metadata)
- `/contacts/{email}` — perfil enriquecido Zoho + cobranzas + cursos
- `/queue-stats` — `{sales: {AR:15, CL:0, ..., MP:3}, billing: {...}, ...}`
- `/courses?country=&q=` — catálogo
- `/analytics?from=&to=` — totales, por día, canal, queue, país, lifecycle
- `/audit-log?conversation_id=&actor_id=&limit=`
- `/conversations/{id}/ai-insights` — gpt-4o-mini con cache 5min
- `/stream?key=<admin>` — SSE

POSTs:
- `/conversations/{id}/assign` — `{agent_id}`
- `/conversations/{id}/snooze` — `{duration}` (1h|4h|tomorrow|next_monday|custom)
- `/conversations/{id}/classify` — `{lifecycle}`
- `/conversations/{id}/queue` — `{queue}`
- `/conversations/{id}/bot` — `{paused}` (bool)
- `/conversations/{id}/tags` — `{tags: []}`
- `/conversations/{id}/takeover` — el agente actual toma control
- `/conversations/{id}/send` — `{text, attachments: []}`
- `/conversations/{id}/status` — `{status}` open|pending|resolved
- `/bulk/assign` — `{ids: [], agent_id}`
- `/bulk/status` — `{ids: [], status}`
- `/bulk/snooze` — `{ids: [], duration}`
- `/llm/correct-spelling` — `{text}` → `{corrected}`
- `/upload` — multipart `file` → `{url, filename, size, mime}`
- `/courses/{country}/{slug}/pitch-hook` (PUT) — `{pitch_hook}`
- `/agents` (POST/DELETE) — CRUD del equipo

### Otros routers que existen y NO toqué

- `/widget/*` — endpoint del widget embebible (chat, history, stream)
- `/webhook/botmaker`, `/webhook/whatsapp`, `/webhook/mercadopago`,
  `/webhook/rebill` — entrada de eventos externos
- `/admin/*` — admin courses, prompts, flows, redis, templates, dashboard,
  test-agent (UIs HTML viejas servidas por FastAPI)
- `/customer-auth/*` — login del LMS
- `/templates/*` — HSM templates de WhatsApp
- `/flows/*` — visual flow builder
- `/reports/*` — reportes
- `/test-agent/*` — sandbox
- `/autonomous/*` — retargeting cycle

---

## 8. Frontend — Next.js 15 + React 19

### Stack

```
next@15
react@19
tailwindcss@3
@tanstack/react-query@5
lucide-react              -- íconos
react-markdown + remark-gfm  -- render mensajes
emoji-picker-react         -- WA-style picker
```

### Estructura

```
frontend/
├── app/
│   ├── layout.tsx                  Root layout — QueryProvider + AuthProvider
│   ├── page.tsx                    redirect → /inbox
│   ├── globals.css
│   ├── login/page.tsx              Form login (auth opcional)
│   └── (app)/                      Group route con layout compartido
│       ├── layout.tsx              Rail izquierdo + main
│       ├── inbox/page.tsx          ⭐ Página principal
│       ├── courses/page.tsx        Catálogo + pitch_hook editable
│       ├── analytics/page.tsx      Métricas con barras
│       ├── settings/page.tsx       CRUD agentes
│       ├── agents/page.tsx         (placeholder)
│       ├── channels/page.tsx       (placeholder)
│       └── prompts/page.tsx        (placeholder)
├── components/
│   ├── inbox/
│   │   ├── conversation-list.tsx   Sidebar lista + filtros
│   │   ├── conversation-detail.tsx Header + mensajes + composer
│   │   ├── message-bubble.tsx      Render con markdown + audio + img
│   │   ├── composer.tsx            Input + emojis + upload + audio + LLM
│   │   └── contact-panel.tsx       3 cards: Insights/Contacto/Cobranzas
│   ├── layout/
│   │   └── rail.tsx                Sidebar narrow estilo respond.io
│   └── ui/
│       ├── avatar.tsx
│       ├── badge.tsx
│       ├── button.tsx
│       ├── collapsible-section.tsx ⭐ Card con chevron a la DERECHA
│       ├── dropdown.tsx
│       ├── flag.tsx                Usa flagcdn.com SVG (NO emoji flags)
│       └── input.tsx
└── lib/
    ├── api.ts                      fetch wrapper (admin-key + JWT)
    ├── api/
    │   ├── inbox.ts                Hooks TanStack Query
    │   └── sse.ts                  useInboxSSE() — invalida queries
    ├── auth.tsx                    AuthProvider + useAuth + getAuthToken
    ├── mock-data.ts                Tipos (algunos mocks heredados)
    ├── query-provider.tsx
    └── utils.ts                    cn(), countryFlag(), initials()
```

### Hooks principales (`lib/api/inbox.ts`)

Queries:
- `useConversations(filters)` — refetchInterval 15s + invalidate por SSE
- `useMessages(conversationId)` — auto-scroll al bottom
- `useContact(email)` — perfil Zoho + cobranzas + cursos
- `useQueueStats()`
- `useAIInsights(conversationId)` — cache 5min server-side
- `useAgents()`

Mutations (todas invalidan queries afectados):
- `useAssign`, `useSnooze`, `useClassify`, `useToggleBot`, `useTakeover`
- `useSetStatus`
- `useBulkAssign`, `useBulkResolve`, `useBulkSnooze`
- `useSendMessage`
- `useCorrectSpelling`

Utility:
- `uploadFile(file)` → R2 → devuelve `{url, filename, size, mime}`
- `apiToListItem(c)` — mapper API response → ConversationListItem

### Conversion-list filters

3 chips arriba de la lista:
- **Todas** — sin filtro
- **No leídas** — TODO (hoy = todas)
- **Mías** — `assigned_agent_id == ME_ID`

⚠️ `ME_ID = "u-gbaza"` está **hardcoded** en `inbox/page.tsx`. TODO leer de
`useAuth().user.id`.

Dropdown "Filtros avanzados" colapsibles:
- Vistas (5 sub-vistas)
- **Cola → País** (3 colas × 6 países, `defaultOpen={true}` siempre)
- Por canal
- Por lifecycle
- Asignado a
- Por tag

### Composer

Features:
- Texto + emoji picker (lazy import, `theme: dark`, width 340)
- Adjuntar archivo → R2 → mete URL en `attachments`
- Grabar voz → MediaRecorder → blob webm → R2 → audio attachment
- Plantillas rápidas (4 preset)
- Corrector ortográfico (gpt-4o-mini)
- Send disabled si no hay conversationId o canal no es widget/whatsapp

⚠️ Cuando solo hay audio sin texto, manda placeholder "🎤 Mensaje de voz"
para que el bubble no quede vacío.

### Contact panel

3 cards consistentes — todas `CollapsibleSection` con chevron a la **derecha**:
1. **Insights IA** — summary + nextStep + scoring reasons (badge con N reasons)
2. **Contacto** — Zoho data + cursos + tags + botón "Ver en Zoho"
3. **Cobranzas** — saldo vencido, saldo total, contrato, valor cuota, último
   pago, cuotas (pagas/vencidas/pendientes con barra), días atraso (rojo si
   >0), modo pago, link de pago

⚠️ Lección de UX: las cards tenían chevrons en lados distintos antes. Gonzalo
lo señaló: *"porque las flechas estan de diferentes lados......es mucho esto
eh"*. **Siempre chevron a la derecha**.

### Banderas

NO usar emoji flags 🇦🇷 — Windows no las renderiza, se ven como `AR`. Usar
`<Flag country="AR" />` que devuelve `<img src="https://flagcdn.com/...">`.
Bug histórico que se resolvió.

⚠️ **NO duplicar la flag**: estaba en el avatar Y al lado del nombre.
*"repetis las banderas 2 veces.. al pedo"*. Solo al lado del nombre.

---

## 9. Config & ENV vars (`.env`)

Pydantic Settings en `config/settings.py`. Las críticas:

```bash
# LLM
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o

# Postgres + Redis
DATABASE_URL=postgresql://postgres.gfvmexzejtlhuxljywbr:...@aws-1-us-east-1.pooler.supabase.com:6543/postgres
REDIS_URL=redis://:password@redis:6379/0

# Supabase Auth
SUPABASE_URL=https://gfvmexzejtlhuxljywbr.supabase.co
SUPABASE_SECRET_KEY=

# CRM
ZOHO_CLIENT_ID=
ZOHO_CLIENT_SECRET=
ZOHO_REFRESH_TOKEN=

# WhatsApp Meta Cloud
WHATSAPP_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_VERIFY_TOKEN=
WHATSAPP_APP_SECRET=

# Botmaker
BOTMAKER_API_KEY=

# Pagos
MP_ACCESS_TOKEN=
REBILL_API_KEY=

# R2
R2_ENDPOINT=https://<account_id>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=msk-multiagente-media
R2_PUBLIC_URL=https://pub-XXXX.r2.dev

# App
APP_ENV=production
APP_SECRET_KEY=  # MUST change for prod
ALLOWED_ORIGINS=https://msklatam.com,https://msklatam.tech,https://agentes.msklatam.com

# Notifs
SLACK_WEBHOOK_URL=

# Sentry (opcional)
SENTRY_DSN=
SENTRY_TRACES_SAMPLE_RATE=0.1
```

---

## 10. Convenciones técnicas y gotchas

### Postgres / asyncpg

**Supabase usa pgbouncer en transaction mode** en el pooler `:6543`. Esto
implica:

1. **`statement_cache_size=0` obligatorio** al hacer `asyncpg.connect()` o el
   pool. Si no, a la segunda query con prepared statement explota porque
   pgbouncer no lo soporta en transaction mode.
2. Tipos de los parámetros tienen que ser **exactos**. asyncpg con cache 0
   no infiere — `datetime.fromisoformat(str)` para timestamptz, no `str`.
   Bug histórico del snooze: pasaba `until_iso` como str → 500.
3. `json.dumps(detail or {})` para columnas jsonb cuando se pasa `text→jsonb`.

### Logs

Estructura: `structlog` con campos kwargs. Nada de `print()`.

```python
logger.info("snooze_set", conv_id=conv_id, until=until.isoformat())
```

### Migraciones

Idempotentes con `if not exists` / `create or replace`. Aplicar con:

```bash
pscp -batch -pw "MSK!@L4t4m" "migrations/00X.sql" root@68.183.156.122:/tmp/
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

### Cron jobs

Patrón con asyncio loop + Redis lock para single-worker:

```python
got_lock = await redis.set("scheduler:lock", "1", ex=3600, nx=True)
if got_lock:
    await start_scheduler()
```

⚠️ El cron de snooze (`utils/inbox_jobs.py`) **NO usa lock todavía**. Si subimos
a multi-worker, corre N veces. Pendiente.

### Frontend

- `npm install` antes de tocar (Next.js 15 quisquilloso con peer deps)
- `.env.local` para apuntar a prod desde dev: `NEXT_PUBLIC_API_BASE=https://agentes.msklatam.com`
- Hot reload sí funciona en `npm run dev`
- Producción: el rewrite de `next.config.js` apunta `/api/*` al backend prod.

### Docker rebuild

`docker compose restart` **NO rebuildea** la imagen. Como `Dockerfile` usa
`COPY . .`, necesitás `docker compose up -d --build api` o
`docker compose up -d --build ui`. Bug histórico — me lo comí varias veces.

### Cache de browser / Cloudflare

Después de deployar UI: hard reload (`Ctrl+Shift+R` / `Cmd+Shift+R`). Si hay
cloudflare delante, considerar purge.

### Cloudflare en `msklatam.tech`

WordPress (no nuestro código) embed `<script src="https://agentes.msklatam.com/widget.js">`.
Si cambian las CSP de WordPress o purgan Cloudflare, el script puede desaparecer.

---

## 11. Bugs conocidos / TODOs priorizados

### Crítico / a fixear pronto

1. **`ME_ID` hardcoded** (`frontend/app/(app)/inbox/page.tsx`). Hoy hace que
   el filtro "Mías" siempre filtre a Gonzalo, y el `assigned_agent_id` cuando
   tomás control. Fix: leer `useAuth().user.id`.

2. **Cron snooze sin Redis lock** (`utils/inbox_jobs.py`). Si hay 2+ workers,
   corre N veces. Copiar patrón de `autonomous_scheduler` en `main.py:117`.

3. **Lifecycle automático del bot no se calcula** — la columna `lifecycle_auto`
   nunca se llena. Hay que agregar `cm.set_lifecycle_auto()` al final de cada
   turno del agent runner según señales (mensajes recientes, palabras clave,
   compra, etc).

### Importante

4. **Auth no enforced en frontend** — sin token cae al admin key. Para prod
   real activar redirect en `lib/auth.tsx`:
   ```tsx
   if (!t) { router.replace("/login"); return; }
   ```

5. **WhatsApp send con adjuntos** — manda URL R2 como texto aparte. Refactor:
   usar endpoints media nativos de Meta Cloud API
   (`integrations/whatsapp_meta.py`).

6. **`get_auth_user_by_email` no filtra** — `params={"email": email}` al
   admin endpoint de Supabase no es filtro, es paginación. Devuelve
   `users[0]` siempre. Fix: filtrar en código después de listar.

7. **Sanitización HTML** mensajes salientes (XSS si el agente humano pega
   algo raro).

8. **Rate limit** de `/llm/correct-spelling` (sin límite hoy).

### Nice to have

9. **Read receipts** — marcar leído al abrir conv.
10. **Indicador "escribiendo..."** del usuario en widget.
11. **Notif push real-time de convs nuevas** al equipo (Slack solo cuando
    escala humano hoy).
12. **Editor de prompts visual** (`/prompts` page). Fuera de scope explícito
    pero pendiente para futuro.
13. **Página Agentes IA** (configurar router/tools/temperatura). Fuera de
    scope.
14. **Página Canales** (conectar/desconectar WA, configurar tokens).
    Placeholder hoy.

---

## 12. Comandos comunes

### Local dev (Windows)

```bash
# Frontend
cd "C:/Users/Gonzalo/Documents/GitHub/multi-agente/frontend"
npm install
npm run dev   # http://localhost:3000

# Backend (necesita Python 3.12)
cd "C:/Users/Gonzalo/Documents/GitHub/multi-agente"
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Deploy a prod

```bash
# Push los cambios
git push origin main

# SSH al server
plink -batch -pw "MSK!@L4t4m" root@68.183.156.122

# En el server:
cd /opt/multiagente
git pull
bash deploy.sh                  # rebuildea api con --no-cache
docker compose up -d --build ui # rebuildea ui (cuando hay cambios frontend)

# Verificar
curl -sf http://localhost:8000/health
curl -sf -o /dev/null -w "%{http_code}\n" https://agentes.msklatam.com/inbox
docker ps
```

### Aplicar migración

Ver §10 — receta completa con `pscp + docker exec`.

### Ver logs

```bash
plink -batch -pw "MSK!@L4t4m" root@68.183.156.122 \
  "docker logs multiagente-api-1 --since 30m 2>&1 | tail -100"

# Solo errores
docker logs multiagente-api-1 --since 1h 2>&1 | grep -iE "error|exception|traceback"

# Logs de auth
docker logs multiagente-api-1 --since 30m 2>&1 | grep -iE "login|auth"
```

### Conectarse a la DB (read only desde local)

```bash
# Necesitás psql instalado
psql "postgresql://postgres.gfvmexzejtlhuxljywbr:...@aws-1-us-east-1.pooler.supabase.com:6543/postgres"
```

### Ejecutar query rápida desde el container

```bash
plink -batch -pw "MSK!@L4t4m" root@68.183.156.122 \
  "docker exec multiagente-api-1 python -c \"
import asyncio, asyncpg, os
async def main():
    c = await asyncpg.connect(os.environ['DATABASE_URL'], statement_cache_size=0)
    rows = await c.fetch('select queue, count(*) from conversation_meta group by queue')
    for r in rows: print(dict(r))
    await c.close()
asyncio.run(main())
\""
```

### Restart container suave (sin rebuild)

```bash
docker compose restart api
docker compose restart ui
```

### Backup / restore nginx config

```bash
# Backup
cp /etc/nginx/sites-available/agentes.msklatam.com \
   /etc/nginx/sites-available/agentes.msklatam.com.bak.$(date +%s)

# Restore desde repo
cp /opt/multiagente/deploy/nginx-agentes.msklatam.com.conf \
   /etc/nginx/sites-available/agentes.msklatam.com
nginx -t && systemctl reload nginx
```

---

## 13. Decisiones de diseño y por qué

### Por qué Next.js en mismo server (no Vercel)

Gonzalo preguntó si llevarlo a Vercel. Razones de quedarnos en DigitalOcean:
- Mismo dominio (`agentes.msklatam.com`) sin subdominios extra.
- Nginx ya estaba.
- Cero costo extra (el droplet sobra).
- Acceso a la red interna del backend sin CORS dance.
- Vercel sigue siendo opción a futuro si el tráfico justifica.

### Por qué `conversation_meta` separada y no columnas en `conversations`

- `conversations` es legacy del bot. No la queremos tocar para no romper.
- 1:1 con FK on delete cascade — si borrás una conv, se borra la meta.
- Permite agregar columnas nuevas sin migrar la tabla legacy.

### Por qué view `conversation_lifecycle`

- El frontend no debería tener que hacer `coalesce(override, auto, 'new')`.
- Si cambia la regla, se cambia en SQL sin tocar frontend.

### Por qué admin key en vez de JWT en producción inicial

- Auth opcional para no bloquear pruebas mientras el equipo se acostumbra.
- Multi-usuario completo es item pendiente (item #1 / #4 arriba).

### Por qué SSE y no WebSocket

- Más simple — solo server → client (no necesitamos el otro lado).
- Atraviesa proxies/firewalls mejor.
- Re-conexión automática del browser.
- Suficiente para los eventos del inbox.

### Por qué Redis Pub/Sub para SSE cross-worker

- Sin esto, un evento broadcasted en el worker 1 no llega a los SSE clients
  conectados al worker 2.
- Pub/Sub es fire-and-forget — perfecto para eventos ephemeral.

### Por qué `flagcdn.com` y no emojis

- Windows no renderiza emoji flags regionales (las muestra como letras "AR").
- Linux/Mac sí, pero queremos consistencia visual.

### Por qué chevrons SIEMPRE a la derecha

- Gonzalo lo señaló como UX inconsistente. Patrón establecido.

### Por qué `defaultOpen={true}` en Cola → País del filtro

- Bug histórico: estaba `defaultOpen={active}` → user tenía que clickear cada
  cola para ver los 6 países. Insufrible. Ahora siempre abierto.

---

## 14. Archivos clave — qué hace cada uno

### Backend

- `main.py` — FastAPI factory, registra todos los routers, cron jobs,
  health endpoint, sirve `/widget.js` y demás HTMLs viejos.
- `api/inbox_api.py` ⭐ — todos los 21+ endpoints de la nueva UI.
- `api/inbox.py` — legacy: SSE bus + reply endpoint del widget viejo +
  `start_pubsub_listener()`.
- `api/auth.py` — login/logout/me + CRUD usuarios (admin-only).
- `api/widget.py` — endpoint del widget embebible (chat, history, stream).
- `api/webhooks.py` — entrada de Botmaker, MercadoPago, Rebill.
- `agents/router.py` ⭐ — supervisor LangGraph + persistencia de queue.
- `agents/<agent>/agent.py` — subgrafo de cada agente.
- `agents/<agent>/prompts.py` — system prompt cacheado al boot.
- `agents/<agent>/tools.py` — tools LangChain (Zoho, Rebill, RAG).
- `memory/conversation_meta.py` — CRUD de la tabla meta (assign, snooze,
  classify, queue, bot_paused, tags, needs_human, bulk_*, wake_expired_snoozed).
- `memory/conversation_store.py` — Redis store (TTL 7 días).
- `memory/postgres_store.py` — pool asyncpg + queries de conversations/courses.
- `utils/inbox_jobs.py` ⭐ — cron snooze + Slack + audit log.
- `utils/scheduler.py` — autonomous_scheduler con Redis lock (modelo a copiar).
- `integrations/supabase_client.py` — auth, profiles, customers (tiene bug
  de get_auth_user_by_email).
- `integrations/zoho/*.py` — leads/contacts/cobranzas/sales_orders.
- `integrations/whatsapp_meta.py` — Meta Cloud API directo.
- `integrations/storage.py` — R2 upload (boto3 S3-compat).
- `config/settings.py` — Pydantic Settings.
- `config/constants.py` — AgentType, Country, Channel, HANDOFF_KEYWORDS.

### Frontend

- `app/(app)/inbox/page.tsx` ⭐ — main page, conecta todos los hooks.
- `components/inbox/conversation-list.tsx` — sidebar + filtros.
- `components/inbox/conversation-detail.tsx` — header + mensajes + composer.
- `components/inbox/composer.tsx` — input completo (emojis/upload/audio/LLM).
- `components/inbox/message-bubble.tsx` — markdown + audio player + img.
- `components/inbox/contact-panel.tsx` — 3 cards consistentes.
- `lib/api/inbox.ts` ⭐ — todos los hooks/mutations TanStack Query.
- `lib/api/sse.ts` — useInboxSSE() invalida queries.
- `lib/api.ts` — fetch wrapper (admin-key + JWT auto).
- `lib/auth.tsx` — AuthProvider + useAuth.

### Deploy

- `Dockerfile` — backend Python 3.12.
- `frontend/Dockerfile` — Next.js standalone build.
- `docker-compose.yml` — solo api + redis (NOTA: ui falta agregarse acá; en
  prod ya está pero el compose del repo lo pierde si se hace `down`).
- `deploy.sh` — `git pull && docker compose build --no-cache api && docker compose up -d`
- `deploy/nginx-agentes.msklatam.com.conf` — config nginx persistido.

⚠️ **Atención**: el `docker-compose.yml` del repo NO tiene el servicio `ui`.
En el server prod hay un override o el archivo está modificado. Verificar
con `cat docker-compose.yml` en el server antes de ejecutar `down/up`.

---

## 15. Cómo trabajar con Gonzalo — protocolo recomendado

1. **Antes de codear**:
   - Si la request es ambigua, preguntar (1-2 preguntas, no más).
   - Si la request es clara, no preguntar — empezar.

2. **Mientras trabajás**:
   - Usá el plan/todo si la tarea tiene >3 pasos.
   - Avanzá, no comentes cada step.

3. **Después de cada cambio significativo**:
   - **Verificá** con curl/test/screenshot REAL.
   - Mostrale el HTTP code o el output que confirma.

4. **Para deployar**:
   - Commit con mensaje descriptivo (root cause + qué cambió).
   - Push.
   - SSH + git pull + docker rebuild del servicio afectado.
   - Verificar HTTP de los endpoints relevantes en prod.
   - Decirle "probá ahora" + qué URL + recordatorio de hard reload.

5. **Cuando algo falla**:
   - **No inventes** root cause. Debug con logs reales.
   - Arreglá el síntoma Y la causa.
   - Documentá el fix en HANDOFF si es no-trivial.

6. **Al final de sesión larga**:
   - Actualizá HANDOFF + PROJECT_CONTEXT con cualquier learning.
   - Commit + push.
   - Si va a abrir nueva conv, recordale leer ambos docs.

---

## 16. Historia de fixes recientes (timeline)

- **Apr 13**: Cuenta `gonzalobaza@msklatam.com` creada en Supabase, profile
  con role admin.
- **Apr 14**: Backend FastAPI estable, widget funcionando en `msklatam.com`.
- **Apr 15-16**: Construcción del frontend Next.js — 15 features, varios
  rounds de iteración por feedback de UI.
- **Apr 17**:
  - Deploy de los 15 features a prod.
  - Commit `34c05da` — handoff inicial.
  - Commit `35a727f` — fix `/api/auth/*` → `/auth/*` (login no funcionaba).
  - Commit `e4eddf7` — actualización handoff.
  - Commit `c32d165` — fix nginx widget.js (widget desaparecido en
    msklatam.tech) + persistir nginx config en `deploy/`.

---

## 17. Por dónde podría seguir

Si Gonzalo no especifica, los siguientes pasos lógicos en orden de impacto:

1. **Multi-usuario real** — leer ME_ID de auth, esconder login fallback en
   prod. (1-2h)
2. **Lifecycle auto del bot** — meter `cm.set_lifecycle_auto(...)` al final
   del runner. Reglas: nuevo si <3 mensajes, hot si pidió pago, customer si
   tiene compra reciente, cold si no responde +7d. (3-4h)
3. **Redis lock al cron snooze** — copiar patrón scheduler. (30m)
4. **WhatsApp media nativo** — refactor send con Meta endpoints específicos
   para audio/image/document. (2-3h)
5. **Página Agentes IA** — UI para ajustar prompts/tools de los 4 agentes
   sin reiniciar el server (hot-reload de prompts vía Redis). (1 día)
6. **Página Canales** — conectar/desconectar WA, ver salud del Botmaker
   token, refresh manual. (medio día)

---

## 18. Notas finales

- El `SESSION_HANDOFF.md` (sin fecha) es viejo, ignoralo.
- `AUDITORIA_ENTERPRISE.md` es un dump de Gemini — no es ground truth.
- Si tenés dudas, **preguntá** antes de inventar.
- Si rompés algo, **avisá** sin maquillar.
- Verificá. Verificá. Verificá.
