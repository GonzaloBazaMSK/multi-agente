from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Redis (cache caliente)
    redis_url: str = "redis://localhost:6379/0"

    # Postgres (durable store, Supabase) — empty disables dual-write
    database_url: str = ""

    # Zoho
    zoho_client_id: str = ""
    zoho_client_secret: str = ""
    zoho_refresh_token: str = ""
    zoho_redirect_uri: str = ""
    zoho_base_url: str = "https://www.zohoapis.com/crm/v6"
    zoho_accounts_url: str = "https://accounts.zoho.com"

    # Botmaker
    botmaker_client_id: str = ""
    botmaker_secret_id: str = ""
    botmaker_refresh_token: str = ""
    botmaker_api_key: str = ""          # access-token estático (fallback / dev)
    botmaker_base_url: str = "https://go.botmaker.com/api/v1.0"
    botmaker_webhook_secret: str = ""

    # WhatsApp Meta Cloud API (directo, sin Botmaker)
    whatsapp_token: str = ""                  # Token de acceso permanente
    whatsapp_phone_number_id: str = ""        # ID del número en Meta
    whatsapp_waba_id: str = ""                # WhatsApp Business Account ID (para listar templates)
    whatsapp_verify_token: str = ""  # Token de verificación del webhook — must be set explicitly
    whatsapp_app_secret: str = ""             # App Secret para verificar firmas
    whatsapp_app_id: str = ""                 # App ID de Meta (para Resumable Upload API)

    # Twilio WhatsApp Sandbox / API
    twilio_account_sid: str = ""              # ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    twilio_auth_token: str = ""               # Token de autenticación
    twilio_whatsapp_from: str = "whatsapp:+14155238886"  # Sandbox number

    # MercadoPago
    mp_access_token: str = ""
    mp_public_key: str = ""
    mp_webhook_secret: str = ""

    # Rebill
    rebill_api_key: str = ""
    rebill_base_url: str = "https://api.rebill.to/v2"

    # Business hours
    business_hours_start: int = 9       # 9:00 AM
    business_hours_end: int = 18        # 6:00 PM
    business_timezone: str = "America/Argentina/Buenos_Aires"
    business_days: str = "0,1,2,3,4"    # Mon-Fri (0=Monday)
    off_hours_message: str = "Gracias por escribirnos. Nuestro horario de atención es de Lunes a Viernes de 9:00 a 18:00 hs (Argentina). Te responderemos a la brevedad."

    # App
    app_env: str = "development"
    app_secret_key: str = "change-this-secret"
    app_base_url: str = "http://localhost:8000"
    allowed_origins: str = "http://localhost:3000"

    # Sentry (error tracking) — empty string disables it
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.1  # 10% of requests traced

    # Cloudflare R2 (object storage, S3-compatible) — empty disables, falls back to filesystem
    r2_endpoint: str = ""           # https://<ACCOUNT_ID>.r2.cloudflarestorage.com
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""             # msk-multiagente-media
    r2_public_url: str = ""         # https://pub-XXXX.r2.dev

    # Supabase
    supabase_url: str = "https://ubycfticfuatoafzsrfv.supabase.co"
    supabase_service_role_key: str = ""

    # Notifications
    slack_webhook_url: str = ""

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
