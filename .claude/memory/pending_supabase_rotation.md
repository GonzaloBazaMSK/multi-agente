---
name: Pendiente — rotar Supabase Secret Key
description: La Supabase Secret Key estuvo hardcodeada en código; falta rotarla
type: project
---
La clave `SUPABASE_SECRET_KEY` estuvo hardcodeada en `integrations/supabase_client.py` hasta el 2026-04-14. Se movió a variable de entorno y se agregó al `/opt/multiagente/.env` del servidor.

**Pendiente:** rotar la key en el dashboard de Supabase (proyecto `gfvmexzejtlhuxljywbr`). Pasos:
1. Supabase → Settings → API → regenerar `secret key`
2. Actualizar `/opt/multiagente/.env` en el server con la nueva key
3. `docker compose -p msk-multiagente restart api`
4. Verificar `curl https://agentes.msklatam.com/health` y probar un login

**Why:** La key nunca llegó a GitHub (push protection la bloqueó), pero vivió en texto plano en el servidor y cualquiera con acceso root al droplet la pudo haber visto. Mejor rotar por higiene de seguridad.

**How to apply:** Si Gonzalo confirma que ya rotó, borrar esta memoria. Hasta entonces, recordárselo si surge algo relacionado con auth/Supabase.
