@echo off
REM ============================================================================
REM Flush de cache Redis de Zoho Contacts + Area Cobranzas para un email dado.
REM
REM Uso:
REM   flush_zoho_cache.bat                    (usa el email por defecto)
REM   flush_zoho_cache.bat otro@email.com     (flushea el email indicado)
REM
REM Qué borra:
REM   - zoho_cursadas:{email}   (perfil Zoho Contacts + cursadas, TTL 24h)
REM   - datos_deudor:{email}    (ficha Zoho Area Cobranzas, TTL 2h)
REM   - sales_profile:{email}   (perfil cacheado para el agente de ventas)
REM
REM Prerequisito:
REM   SSH key auth configurado. Coloca tu clave privada PuTTY (.ppk) en la ruta
REM   indicada en SSH_KEY_PATH, o establece la variable de entorno SSH_KEY_PATH.
REM ============================================================================

setlocal

REM Email por defecto — cambialo si querés otro default
set "DEFAULT_EMAIL=gbaza2612@gmail.com"

REM SSH key path — override via environment variable if needed
if "%SSH_KEY_PATH%"=="" (
    set "SSH_KEY_PATH=%USERPROFILE%\.ssh\multiagente_deploy.ppk"
)

REM Si vino arg → usarlo; si no → default
if "%~1"=="" (
    set "EMAIL=%DEFAULT_EMAIL%"
) else (
    set "EMAIL=%~1"
)

echo.
echo ===============================================================
echo  Flush Redis cache para: %EMAIL%
echo ===============================================================
echo.

REM Verify key file exists
if not exist "%SSH_KEY_PATH%" (
    echo ERROR: SSH key not found at %SSH_KEY_PATH%
    echo Set the SSH_KEY_PATH environment variable to the path of your .ppk key file.
    echo.
    pause
    exit /b 1
)

REM Use SSH key auth (-i) instead of password (-pw)
echo y | plink -i "%SSH_KEY_PATH%" -hostkey "SHA256:oiCZ7kfsEDCMfu442Uq2xxl8U/rebAVs3x6gpJaXbgI" root@68.183.156.122 "docker exec multiagente-api-1 python -c \"import redis,os; r=redis.from_url(os.getenv('REDIS_URL')); email='%EMAIL%'; keys=[f'zoho_cursadas:{email}', f'datos_deudor:{email}', f'sales_profile:{email}']; d=r.delete(*keys); print(f'Flushed {d} key(s) for {email}'); [print(f'  - {k}: {\\\"gone\\\" if not r.exists(k) else \\\"still there\\\"}') for k in keys]\""

echo.
echo ===============================================================
echo  Listo. Ahora podes recargar el widget para pegarle fresh a Zoho.
echo ===============================================================
echo.

pause
