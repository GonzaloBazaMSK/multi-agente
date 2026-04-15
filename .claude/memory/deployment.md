---
name: Deployment del proyecto en producción
description: Dónde y cómo corre multi-agente en producción, y cómo deployar vía git
type: project
originSessionId: 6b8fdedc-5447-4fbf-8b01-d3f3827058a1
---
El proyecto **multi-agente** (MSK Latam) corre en un droplet DigitalOcean.

- **Servidor**: `68.183.156.122` (NYC3), user `root`, acceso SSH por password
- **Ubicación**: `/opt/multiagente` — **es repo git** tracking `origin/main` (sincronizado 2026-04-14)
- **Docker Compose**: `multiagente-api-1` (uvicorn :8000) + `multiagente-redis-1` (:6379)
- **Reverse proxy**: nginx → `agentes.msklatam.com`
- **Healthcheck**: `GET /health` → `{"status":"ok"}`
- **`.env`**: vive solo en el servidor (no en git). Si hay que agregar vars, editar `/opt/multiagente/.env` y `docker compose restart api`.

**Flujo de deploy** (desde 2026-04-14):
1. Local: `git add . && git commit && git push`
2. SSH al droplet: `cd /opt/multiagente && git pull && docker compose up -d --build`
3. Verificar: `curl http://localhost:8000/health` (esperar ~10s a que esté ready)

⚠️ **`--build` es OBLIGATORIO en CADA deploy**, incluso para cambios de HTML/CSS/JS estáticos. El `docker-compose.yml` NO monta `/opt/multiagente` como volumen — solo `media_data:/app/media`. El código fuente (Python + widget/*.html) está bakeado en la imagen vía `Dockerfile COPY`. Si hacés solo `git pull`, el FS del host se actualiza pero el container sigue sirviendo la versión vieja desde su imagen interna. Síntoma: el archivo en disco tiene el fix pero `curl /endpoint` devuelve contenido viejo con `last-modified` anterior al deploy.

**Backup disponible**: `/tmp/multiagente-backup-20260414-1046.tar.gz` (snapshot pre-git del server).

**Why:** Gonzalo trabaja con Claude directamente en el server (SSH), por eso durante un tiempo el server divergió del repo. Se re-sincronizó: servidor fue fuente de verdad → push force a GitHub → local alineado.

**How to apply:** Para operar, conectar por SSH con `plink` en Windows (sshpass no está disponible). Credenciales no se guardan en memoria — pedirlas cada sesión.
