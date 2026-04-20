---
name: Deployment del proyecto en producción
description: Dónde y cómo corre multi-agente en producción, y cómo deployar vía git
type: project
originSessionId: 6b8fdedc-5447-4fbf-8b01-d3f3827058a1
---

## Estado actual (20-abr-2026)

**Servidor productivo NUEVO**: `MSK-Server-NET`
- IP Reserved (usar esta): `129.212.145.193`
- IP Public IPv4 (backup): `157.245.8.143`
- 16 GB RAM / 4 vCPU / Ubuntu 25.04
- El server **ya tiene otro stack corriendo** (msklatam.net: 30+ containers con
  crm/lms/payments/gateway/scoring/etc). Nuestro stack coexiste usando
  `COMPOSE_PROJECT_NAME=msk-multiagente` para evitar colisiones.

**SSH setup** (ya configurado, zero friction):
- Key privada: `~/.ssh/msk_droplet` (local)
- Key pública autorizada en el server
- Conectar: `ssh -i ~/.ssh/msk_droplet -o ServerAliveInterval=30 root@129.212.145.193`
- Keepalive 30s evita los cortes que daba plink durante builds largos
- Password fallback (si la key falla): `tyxtbUKYUoQRMsD9ZtpboZQl`

**Ubicación del proyecto**: `/opt/multiagente/` (repo git tracking origin/main).
El docker data root fue movido al volumen adjunto: `/mnt/volume_nyc3_02/docker`
(100 GB) via symlink. **No tocar ese symlink.**

**Containers** (prefix `msk-multiagente-`):
- `msk-multiagente-api-1` (FastAPI + LangGraph) → 127.0.0.1:8000
- `msk-multiagente-ui-1` (Next.js 15) → 127.0.0.1:3000
- `msk-multiagente-redis-1` → sin puerto expuesto (uso interno al compose)

**docker-compose.override.yml** (en el repo): bindea puertos a 127.0.0.1 (nginx
del host rutea), `ports: !reset []` en redis, y `mem_limit: 2g` en ui.

**Reverse proxy**: nginx del host →
- `/api/*`, `/widget/*`, `/webhook/*`, `/customer/*`, `/static/*`, `/media/*`,
  `/widget.js`, `/health` → `http://127.0.0.1:8000`
- Resto (UI consola) → `http://127.0.0.1:3000`
- SSL con Let's Encrypt (certs en `/etc/letsencrypt/live/agentes.msklatam.com/`,
  válido hasta 10-jul-2026; certbot renovará auto)

**Healthcheck**: `curl https://agentes.msklatam.com/health` → `{"status":"ok"}`

**.env**: vive solo en el servidor (no en git). Para agregar vars:
`nano /opt/multiagente/.env` + `docker compose -p msk-multiagente restart api`.

## Flujo de deploy estándar

```bash
# 1. LOCAL — commit + push normal
git add . && git commit -m "..." && git push

# 2. SERVER — pull + build + recreate con tmux (no muere si SSH se corta)
ssh -i ~/.ssh/msk_droplet root@129.212.145.193
cd /opt/multiagente
git pull
tmux new-session -d -s deploy '
  docker compose -p msk-multiagente build api ui > /tmp/build.log 2>&1 && \
  docker compose -p msk-multiagente up -d --force-recreate api ui > /dev/null && \
  echo DONE > /tmp/deploy-done.flag
'
# Wait loop
until [ -f /tmp/deploy-done.flag ]; do sleep 10; done
rm /tmp/deploy-done.flag
tail -5 /tmp/build.log
docker ps --filter label=com.docker.compose.project=msk-multiagente

# 3. VERIFICAR
curl -sf https://agentes.msklatam.com/health
```

**Para solo backend**: `docker compose -p msk-multiagente build api && docker compose -p msk-multiagente up -d --force-recreate api`
**Para solo frontend**: reemplazar `api` por `ui` en lo anterior.

⚠️ Siempre usar `-p msk-multiagente` — sin esto el project name default es
"multiagente" y puede colisionar con los containers del otro stack.

⚠️ `--force-recreate` es OBLIGATORIO — compose no recrea si solo cambia la
imagen pero no la config del service.

## Droplet VIEJO (pre-migración, 20-abr-2026)

`root@68.183.156.122` · 2 GB RAM. Containers bajados. DNS ya no apunta ahí.
Gonzalo tiene que destruirlo desde panel DO cuando quiera (libera $12/mes).
No hay nada importante ahí — todo migró al server grande.

## Snapshot / backups

DigitalOcean snapshots automáticos disponibles. Se pueden hacer manuales desde
el panel antes de operaciones de riesgo.

Backup local pre-migración al server grande: `/tmp/msk-env-backup.txt` en mi
laptop (tiene el .env del droplet viejo). No está committeado.

## Troubleshooting común

**"502 Bad Gateway"** después de deploy → el api está arrancando (10-20s).
Esperar. Si persiste, `docker logs msk-multiagente-api-1 --tail 30`.

**Build se cuelga en pasos finales** → plink/ssh cortó mid-build. Re-chequear
`docker images --format '{{.Repository}} {{.CreatedAt}}' | grep multi`. Si la
imagen tiene timestamp reciente, el build sí terminó; solo falta el `up -d`.

**Scheduler no arranca (notifs cron no dispara)** → Redis lock
`scheduler:lock` con TTL 1h puede estar quedado de un restart anterior.
Borrarlo: `docker exec msk-multiagente-redis-1 redis-cli -a $REDIS_PASS del scheduler:lock`
y restart api.

**Widget no aparece en msklatam.tech/.com** → chequear:
1. Script tag en el HTML: `curl https://msklatam.tech | grep widget.js`
2. CORP header en widget.js: `curl -sI https://agentes.msklatam.com/widget.js | grep cross-origin-resource-policy` debe ser `cross-origin`
3. Z-index del FAB no tapado: en DevTools buscar `cm-widget-container`.
