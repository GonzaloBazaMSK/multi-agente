#!/bin/bash
# Deploy script — reconstruye la imagen con el código más reciente
# Uso: ssh root@68.183.156.122 "cd /opt/multiagente && bash deploy.sh"
set -e

echo "=== 1. Git pull ==="
git pull origin main

echo ""
echo "=== 2. Docker build (con código actualizado) ==="
docker compose build --no-cache api

echo ""
echo "=== 3. Docker up (recrea container con imagen nueva) ==="
docker compose up -d

echo ""
echo "=== 4. Verificación ==="
sleep 3
docker logs multiagente-api-1 --tail 5

echo ""
echo "=== DEPLOY COMPLETO ==="
