# Session Handoff — Multi-Agente MSK Latam

> Este archivo es un **resumen denso** de la última sesión de trabajo con Claude Code,
> pensado para que **continúes en otra PC** sin perder contexto. Si sos Claude y estás
> leyendo esto en un repo recién clonado: bienvenido, acá está todo.

**Última sesión:** 2026-04-14
**Repo:** `github.com/GonzaloBazaMSK/multi-agente`
**Branch principal:** `main`
**Último commit de referencia:** ver `git log --oneline -1`

---

## 🎯 Qué es el proyecto

Sistema multi-agente de atención al cliente para **MSK Latam** (cursos médicos online, LATAM).

**Stack:**
- **Backend:** FastAPI (Python) + LangGraph + Pinecone (RAG) + Redis (cache) + Supabase Postgres (durable)
- **Frontend:** HTML + CSS + JS vanilla (cada `widget/*.html` es standalone, sin bundle)
- **Agentes IA:** `sales`, `collections`, `post_sales`, `closer` (autónomo)
- **Canales:** WhatsApp Meta Cloud API (principal), Botmaker, Twilio, widget web
- **Integraciones:** Zoho CRM, MercadoPago, Rebill, LMS (Moodle, Blackboard, Tropos), Sentry, Cloudflare R2, OpenAI (LLM + Whisper + TTS)
- **Deploy:** Droplet DigitalOcean `68.183.156.122:/opt/multiagente`, docker-compose, nginx → `agentes.msklatam.com`

---

## 🗺️ Workflow de desarrollo

```
# En la PC
git pull                       # siempre primero
# ... editar código ...
git add . && git commit -m "..." && git push

# En el droplet (deploy)
ssh root@68.183.156.122
cd /opt/multiagente
git pull && docker compose up -d --build
curl http://localhost:8000/health  # verificar
```

**No hay build step** (ni npm, ni webpack). El único build es el de Docker.

---

## 📦 Qué hicimos en esta sesión larga

### Infraestructura
- **Redis con password** (antes sin auth) — en `.env` del server
- **Cloudflare R2** para media — bucket `msk-multiagente-media`, URL pública `pub-7d7b59530d9049dba4a7e0c485f292f0.r2.dev`
- **Sentry** error tracking — configurado en `main.py`
- **Postgres dual-write** via Supabase (tabla `conversations`, `messages`, `snippets`, `lifecycle_stages` pendiente)
- **APScheduler** corriendo cada hora para retargeting autónomo (jobstore en Redis)

### Features de usuario
1. **Audio fix completo** — MIME type correcto, ffmpeg transcoding webm→ogg/opus para WhatsApp
2. **Whisper STT** — audios entrantes se transcriben automáticamente, el agente entiende
3. **OpenAI TTS** — botón 🔊 genera notas de voz con selector de voz (alloy default, nova/shimmer/onyx/echo/fable)
4. **AI Prompts inline** en el compositor (Tono/Traducir/Corregir/Reescribir/Simplificar)
5. **Snippets** con adjuntos (R2) + topics + admin CRUD. **Migración one-time** de quick-replies viejos.
6. **Closing Notes con IA** — al cerrar, modal con 6 categorías + resumen auto-generado por GPT-4o-mini
7. **Closing card inline** en el timeline (no banner)
8. **Panel consolidado** con 3 tabs: 🟢 En vivo / 📈 Histórico / 🤖 Autónomo
9. **Reports** (dentro del Panel tab Histórico): KPIs + leaderboard agentes + categorías + timeline
10. **Filtros unificados** tipo Botmaker — un solo botón 🧰 Filtros con submenús
11. **⚡ Acciones** — un solo dropdown consolida Tomar/Asignar/Clasificar/Cerrar/Descargar JSON
12. **Right-side contact panel** siempre visible (persistido en localStorage)
13. **Agent name auto-fill** desde login Supabase (no más "Agente" genérico)
14. **Bulk assign** fix — antes mostraba "undefined undefined"
15. **Zona horaria** fix — todos los timestamps en hora local
16. **Estado visible por conv** — border color + badge (bot/humano/sin asignar/cerrada)

### Sistema autónomo (el corazón del sprint)
- **Scheduler** corre cada hora: escanea leads WhatsApp abiertos con `last_user_msg` en días 1/3/7/15/28
- **Decisión AI-driven**: GPT-4o-mini evalúa cada lead y decide si mandar HSM y cuál de la lista aprobada
- **Auto-retry**: diariamente a las 10am busca convs cerradas como `descartado` hace >20d y las reactiva
- **Tab Autónomo en Panel**: monitor live, botones manuales (run-now, retry-now, toggle), lista de jobs y acciones recientes
- **Config en Redis**: `retargeting:config` con `enabled: true/false`

---

## 🔑 Credenciales y URLs importantes

### Servidor / Docker
- SSH: `root@68.183.156.122` (password no guardado en repo)
- Path: `/opt/multiagente`
- Containers: `multiagente-api-1`, `multiagente-redis-1`

### URLs en producción
- Inbox: `https://agentes.msklatam.com/inbox-ui`
- Panel: `https://agentes.msklatam.com/admin/dashboard-ui` (3 tabs)
- Test AI sandbox: `https://agentes.msklatam.com/admin/test-agent-ui`
- Flujos: `https://agentes.msklatam.com/admin/flows-ui`
- Prompts: `https://agentes.msklatam.com/admin/prompts-ui`

### Servicios externos
- **Supabase**: `https://supabase.com/dashboard/project/gfvmexzejtlhuxljywbr`
- **Sentry**: dashboard con el proyecto `multi-agente` (plan free)
- **Cloudflare R2**: bucket `msk-multiagente-media`, public `pub-7d7b59530d9049dba4a7e0c485f292f0.r2.dev`

### Variables de entorno en `/opt/multiagente/.env` del droplet
`REDIS_URL`, `REDIS_PASSWORD`, `DATABASE_URL`, `OPENAI_API_KEY`, `PINECONE_API_KEY`,
`SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`,
`WHATSAPP_WABA_ID`, `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN`,
`MP_ACCESS_TOKEN`, `REBILL_API_KEY`, `SENTRY_DSN`, `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_PUBLIC_URL`.

---

## 📚 Archivos clave que vale la pena conocer

| Archivo | Qué hace |
|---|---|
| `main.py` | Entry point FastAPI, lifespan (scheduler, pub/sub), routes |
| `agents/router.py` | Supervisor LangGraph, clasifica intent y despacha al agente |
| `agents/closer/agent.py` | Closer autónomo para cerrar leads |
| `memory/conversation_store.py` | Dual-write Redis + Postgres |
| `memory/postgres_store.py` | Schema SQL + CRUD via asyncpg |
| `integrations/storage.py` | Upload a R2 (boto3 S3-compat) |
| `integrations/stt.py` | Whisper STT para audios entrantes |
| `integrations/tts.py` | OpenAI TTS para notas de voz |
| `integrations/whatsapp_meta.py` | Cliente WhatsApp Cloud API |
| `integrations/zoho/*` | Zoho CRM (contacts, leads, cobranzas) |
| `api/inbox.py` | Endpoints del inbox (conversations, media, snippets, AI assist, TTS, closing) |
| `api/reports.py` | Endpoints `/admin/reports/*` (overview, leaderboard, categories, timeline) |
| `api/autonomous.py` | Endpoints `/admin/autonomous/*` (status, recent, toggle, run-now) |
| `api/test_agent.py` | Sandbox de agente IA |
| `utils/scheduler.py` | APScheduler con Redis jobstore |
| `utils/autonomous_tasks.py` | run_retargeting_cycle + run_auto_retry_cycle |
| `widget/inbox.html` | El inbox (4500+ líneas, single-file app) |
| `widget/dashboard.html` | Panel con 3 tabs (En vivo / Histórico / Autónomo) |
| `widget/test-agent.html` | Sandbox split UI |

---

## 🚧 Pendientes al cierre de sesión

### Alta prioridad
- **Rotar credenciales que se expusieron en el chat** (SSH pass, R2 keys, Supabase pass, Sentry DSN, OAuth token). El usuario dijo "nadie tiene acceso" pero dejo pendiente la rotación por higiene.
- **Migrar Quick Replies viejos a Snippets**: Ya está el endpoint `POST /inbox/quick-replies/migrate-to-snippets`. Se dispara automáticamente la primera vez que un admin abre "Snippets" desde el nav-rail.

### Media prioridad
- **Tests automatizados** (pytest + GitHub Actions): router, auth, webhooks Meta signature, conversation_store dual-write, storage R2 upload.
- **Upgrade droplet** 1→2 vCPU + gunicorn 4 workers (~$6/mes extra).
- **Notificaciones del sistema autónomo a Slack** (cada vez que manda HSM, avisar en `#ops`).

### Baja prioridad / parking lot
- **Lifecycle Stages visual** — ya está el schema y API en `api/lifecycle.py` pero el router **no está registrado** en `main.py` (comentado). Reactivar si el equipo crece.
- **Broadcasts manuales** con segments — explícitamente descartado a favor del sistema autónomo. Dejamos el tema abierto por si cambia.
- **ElevenLabs TTS** — si las voces de OpenAI no convencen por el acento, switchear a ElevenLabs ($22/mes, voces rioplatenses reales).

### Conocido-y-aceptado (no son bugs)
- Los archivos viejos en `/app/media/` del container (pre-R2) siguen ahí — son tests, no hace falta migrarlos.
- Los 2 workers de uvicorn intentan crear el schema SQL al arrancar; el segundo pincha con "duplicate_type" — es inofensivo, solo genera un warning.

---

## ⚙️ Comandos útiles para retomar

```bash
# Ver estado en prod
ssh root@68.183.156.122 "cd /opt/multiagente && git log --oneline -5 && docker compose ps"

# Ver logs en vivo
ssh root@68.183.156.122 "docker logs multiagente-api-1 --tail 50 -f"

# Disparar retargeting manual
curl -X POST https://agentes.msklatam.com/admin/autonomous/run-now \
  -H "X-Session-Token: <TU_TOKEN>"

# Correr script de backfill Postgres
docker exec multiagente-api-1 python /app/scripts/backfill_postgres.py

# Verificar scheduler
docker exec multiagente-api-1 python -c "from utils.scheduler import get_scheduler; s=get_scheduler(); print('running:', s.running); print('jobs:', [j.id for j in s.get_jobs()])"
```

---

## 🧠 Decisiones de diseño que tomamos

- **Panel con tabs vs 2 páginas separadas**: El usuario eligió consolidar Panel + Reports en una sola pantalla con tabs. Se eliminó `widget/reports.html`.
- **Lifecycle Stages skip**: El Kanban + Reports ya cubren el funnel. Implementarlo sería redundante.
- **Broadcasts manuales → Sistema Autónomo**: El usuario quiere que el sistema trabaje solo, no mandar blasts manuales. Se pivoteó el feature #7 a un motor autónomo con IA-decide.
- **Snippets reemplaza Quick Replies**: Migración automática al primer click de admin en el nav-rail.
- **OpenAI TTS antes que ElevenLabs**: cheaper, already in stack, "good enough" para arrancar.

---

## 💬 Preferencias del usuario (Gonzalo Baza)

- **Idioma:** español rioplatense informal
- **Vos directo:** sin dar vueltas, sin paranoia innecesaria
- **Flujo Git:** trabajar directo en `main`, nunca en worktrees de Claude Code
- **Deploy:** siempre vía `git pull` en el servidor (no edición directa en `/opt/multiagente` salvo emergencia)
- **Feedback rápido:** si algo está mal, roll back y probar otra cosa

---

Si tenés dudas, el `AUDITORIA_ENTERPRISE.md` en la raíz del repo tiene el contexto histórico completo del proyecto (sprints 1-11).
