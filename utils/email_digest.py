"""
Cron: digest diario por email con notificaciones no leídas.

Corre 1 vez por día (9:00 AR) via APScheduler. Para cada usuario que
tenga `notification_preferences.email_digest = true` y tenga notifs sin
leer en las últimas 24h, le manda un mail con el resumen.

Provider de email: Resend (si `RESEND_API_KEY` está seteado) o SMTP
clásico (si `EMAIL_SMTP_HOST/USER/PASSWORD` están seteados). Si NO hay
provider configurado, logea advertencia y retorna sin enviar nada — así
podemos activar el feature más adelante sin redeploy, solo agregando
las env vars.

Diseño del mail (texto plano + HTML simple, sin deps de templating):
    Asunto: "Tenés N novedades en MSK Console"
    Cuerpo:
        Hola {nombre},
        Tenés {N} notificaciones sin leer en la consola desde ayer.

        • [icono] Te asignaron una conversación con Juan Pérez
        • [icono] 3 mensajes nuevos en conversaciones tuyas
        • [icono] 1 conversación sin respuesta hace más de 2h
        ...

        Abrir consola → https://agentes.msklatam.com/inbox
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx
import structlog

from config.settings import get_settings
from memory import postgres_store
from utils.notifications import DEFAULT_PREFERENCES

logger = structlog.get_logger(__name__)


CONSOLE_URL = "https://agentes.msklatam.com"

# Labels legibles por tipo de notif — para armar el cuerpo del mail sin
# traer todo el frontend aquí.
TYPE_LABELS = {
    "conv_assigned": "conversaciones que te asignaron",
    "new_message_mine": "mensajes nuevos en tus conversaciones",
    "conv_stale": "conversaciones sin respuesta hace más de 2h",
    "template_approved": "cambios de estado en plantillas HSM",
}


async def run_email_digest() -> dict:
    """Manda digest a todos los users con prefs.email_digest=true que tengan
    notifs sin leer del último día.

    Devuelve stats: usuarios procesados, emails enviados, errores.
    """
    settings = get_settings()
    provider = _detect_provider(settings)
    if provider == "none":
        logger.warning(
            "email_digest_skipped_no_provider",
            hint="Configurar RESEND_API_KEY o EMAIL_SMTP_* en .env",
        )
        return {"skipped": True, "reason": "no_email_provider_configured"}

    # Traer todos los user_ids que activaron digest
    pool = await postgres_store.get_pool()
    async with pool.acquire() as conn:
        digest_users = await conn.fetch(
            """
            select user_id
            from public.notification_preferences
            where email_digest = true
            """
        )

    if not digest_users:
        logger.info("email_digest_no_recipients")
        return {"sent": 0, "skipped": True, "reason": "no_recipients"}

    # Para cada user, traer notifs unread + info del profile (email, nombre)
    from integrations.supabase_client import list_profiles

    profiles = await list_profiles()
    profile_by_id = {p["id"]: p for p in profiles}

    yesterday = datetime.now(timezone.utc) - timedelta(hours=26)
    sent = 0
    errors = 0

    for row in digest_users:
        user_id = str(row["user_id"])
        profile = profile_by_id.get(user_id)
        if not profile:
            continue
        email = profile.get("email")
        name = profile.get("name") or email or "agente"
        if not email:
            continue

        async with pool.acquire() as conn:
            notifs = await conn.fetch(
                """
                select id, type, data, created_at
                from public.notifications
                where user_id = $1
                  and read_at is null
                  and created_at > $2
                order by created_at desc
                limit 50
                """,
                user_id,
                yesterday,
            )
        if not notifs:
            continue  # nada para reportar — skip

        subject, html_body, text_body = _build_digest_email(name, notifs)
        try:
            await _send_email(provider, settings, email, subject, html_body, text_body)
            sent += 1
            logger.info("email_digest_sent", user=email, notifs=len(notifs))
        except Exception as e:
            errors += 1
            logger.warning("email_digest_send_failed", user=email, error=str(e))

    return {"sent": sent, "errors": errors, "candidates": len(digest_users)}


def _detect_provider(settings) -> str:
    """Elige Resend si está, sino SMTP, sino 'none'."""
    if getattr(settings, "resend_api_key", "") or getattr(settings, "RESEND_API_KEY", ""):
        return "resend"
    if getattr(settings, "email_smtp_host", ""):
        return "smtp"
    return "none"


def _build_digest_email(name: str, notifs: list) -> tuple[str, str, str]:
    """Arma subject + HTML + text bodies desde la lista de notifs."""
    n = len(notifs)
    subject = f"Tenés {n} {'novedad' if n == 1 else 'novedades'} en MSK Console"

    # Agrupar por tipo → bullet list
    by_type: Counter = Counter([nn["type"] for nn in notifs])
    bullets_html = ""
    bullets_text = ""
    for notif_type, count in by_type.most_common():
        label = TYPE_LABELS.get(notif_type, notif_type.replace("_", " "))
        bullets_html += f"<li><b>{count}</b> {label}</li>\n"
        bullets_text += f"  • {count} {label}\n"

    html_body = f"""<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, 'Segoe UI', Roboto, sans-serif; color: #1a1a1a; max-width: 560px; margin: 0 auto; padding: 24px;">
  <h2 style="margin-top: 0; color: #a855f7;">Hola {name} 👋</h2>
  <p>Tenés <b>{n}</b> notificaciones sin leer en la consola desde ayer:</p>
  <ul style="padding-left: 20px; line-height: 1.8;">
    {bullets_html}
  </ul>
  <p style="margin-top: 32px;">
    <a href="{CONSOLE_URL}/inbox" style="display: inline-block; background: #a855f7; color: white; padding: 10px 22px; border-radius: 8px; text-decoration: none; font-weight: 600;">Abrir consola →</a>
  </p>
  <p style="color: #888; font-size: 12px; margin-top: 32px; border-top: 1px solid #eee; padding-top: 16px;">
    Podés desactivar este digest desde <a href="{CONSOLE_URL}/settings/notifications" style="color: #a855f7;">Preferencias</a>.
  </p>
</body>
</html>"""

    text_body = f"""Hola {name},

Tenés {n} notificaciones sin leer en la consola desde ayer:

{bullets_text}
Abrir consola: {CONSOLE_URL}/inbox

Desactivar digest: {CONSOLE_URL}/settings/notifications
"""
    return subject, html_body, text_body


async def _send_email(
    provider: str,
    settings,
    to: str,
    subject: str,
    html: str,
    text: str,
) -> None:
    if provider == "resend":
        await _send_via_resend(settings, to, subject, html, text)
    elif provider == "smtp":
        await _send_via_smtp(settings, to, subject, html, text)


async def _send_via_resend(settings, to: str, subject: str, html: str, text: str):
    api_key = getattr(settings, "resend_api_key", "") or getattr(settings, "RESEND_API_KEY", "")
    from_addr = getattr(settings, "email_from", "") or "MSK Console <notifs@agentes.msklatam.com>"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": from_addr,
                "to": [to],
                "subject": subject,
                "html": html,
                "text": text,
            },
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"Resend API {resp.status_code}: {resp.text[:200]}")


async def _send_via_smtp(settings, to: str, subject: str, html: str, text: str):
    # SMTP clásico — sincrono, lo corremos en threadpool para no bloquear.
    import asyncio
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    def _send():
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = getattr(settings, "email_from", "notifs@agentes.msklatam.com")
        msg["To"] = to
        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))

        host = getattr(settings, "email_smtp_host", "")
        port = int(getattr(settings, "email_smtp_port", 587))
        user = getattr(settings, "email_smtp_user", "")
        password = getattr(settings, "email_smtp_password", "")
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls()
            if user and password:
                s.login(user, password)
            s.send_message(msg)

    await asyncio.to_thread(_send)
