"""
Configuraciones de campaña/cupón por canal+país.

Cada config define cómo se renderiza el bloque de promo dentro del system
prompt de ventas. Permite que el mismo prompt base sirva para múltiples
canales (widget, whatsapp, email digest, etc.) y campañas (Hot Sale,
Black Friday, cupones permanentes del bot, etc.) **sin duplicar el prompt**.

Cómo se usa:
    from agents.sales.channel_configs import get_campaign_config
    config = get_campaign_config(country="AR", channel="whatsapp")
    prompt = build_sales_prompt(country="AR", channel="whatsapp",
                                campaign_config=config)

Tipos de promo soportados (`promo_type`):
    - "hot_sale_block": promo única tipo Hot Sale (1 código, %, vigencia).
      Se MENCIONA en apertura. Usado en widget de campaña pública.
    - "scaled_coupons": 2 niveles de cupón con escalado por objeción.
      NO se menciona en apertura — aparece solo si hay objeción real
      de precio. Usado en WhatsApp (lead ya calificado, mejor margen).
    - "none": sin promo. El bot no menciona descuentos ni cupones
      en absoluto. Usado para canales/países sin campaña activa.

Para agregar un canal/campaña nuevo:
    1. Sumá el config al dict `_CONFIGS` con key (country.upper(), channel).
    2. (Opcional) Si es un `promo_type` nuevo, agregá un renderer en
       `agents/sales/prompts.py` → `_render_promo_block(config)`.
"""

from __future__ import annotations

from typing import Any


# ── PROMO ACTIVA — editar acá cuando cambie ─────────────────────────────────
# Widget AR — Hot Sale pública del sitio msklatam.com
WIDGET_AR: dict[str, Any] = {
    "promo_type": "hot_sale_block",
    "code": "HOY30",
    "pct": 30,
    "factor": 0.70,  # cuota × 0.70 = post-descuento
    "until": "17 de mayo 2026",
    "name": "Hot Sale",
}

# WhatsApp AR/LATAM — cupones permanentes del bot vendedor, escalado por objeción
WHATSAPP_DEFAULT: dict[str, Any] = {
    "promo_type": "scaled_coupons",
    "levels": [
        # (código, % descuento, factor cuota original, descripción nivel)
        ("BOT15", 15, 0.85, "Nivel 1 — primera objeción real de precio"),
        ("BOT20", 20, 0.80, "Nivel 2 — segunda objeción. Techo absoluto."),
    ],
}

# Países/canales sin campaña activa.
NO_PROMO: dict[str, Any] = {"promo_type": "none"}


# Mapa principal: (country_uppercase, channel_lowercase) → config.
# Si una combinación no está, se usa el default por canal (ver get_campaign_config).
_CONFIGS: dict[tuple[str, str], dict[str, Any]] = {
    # Widget
    ("AR", "widget"): WIDGET_AR,
    # WhatsApp (todos los países usan el mismo BOT15/BOT20 por ahora)
    ("AR", "whatsapp"): WHATSAPP_DEFAULT,
    ("MX", "whatsapp"): WHATSAPP_DEFAULT,
    ("CL", "whatsapp"): WHATSAPP_DEFAULT,
    ("CO", "whatsapp"): WHATSAPP_DEFAULT,
    ("PE", "whatsapp"): WHATSAPP_DEFAULT,
    ("UY", "whatsapp"): WHATSAPP_DEFAULT,
    ("BO", "whatsapp"): WHATSAPP_DEFAULT,
    ("PY", "whatsapp"): WHATSAPP_DEFAULT,
    ("EC", "whatsapp"): WHATSAPP_DEFAULT,
    ("VE", "whatsapp"): WHATSAPP_DEFAULT,
    ("CR", "whatsapp"): WHATSAPP_DEFAULT,
    ("GT", "whatsapp"): WHATSAPP_DEFAULT,
    ("HN", "whatsapp"): WHATSAPP_DEFAULT,
    ("NI", "whatsapp"): WHATSAPP_DEFAULT,
    ("PA", "whatsapp"): WHATSAPP_DEFAULT,
    ("SV", "whatsapp"): WHATSAPP_DEFAULT,
    ("ES", "whatsapp"): WHATSAPP_DEFAULT,
    ("INT", "whatsapp"): WHATSAPP_DEFAULT,
}


# Fallbacks por canal (cuando no hay match exacto país+canal).
_DEFAULTS_BY_CHANNEL: dict[str, dict[str, Any]] = {
    "widget": NO_PROMO,  # widget sin Hot Sale activa → sin promo
    "whatsapp": WHATSAPP_DEFAULT,  # cualquier país en WA → BOT15/BOT20
}


def get_campaign_config(country: str, channel: str) -> dict[str, Any]:
    """
    Devuelve el config de campaña/cupón para el par (country, channel).

    Resolución:
        1. Match exacto (country.upper(), channel.lower()) en _CONFIGS.
        2. Fallback por canal en _DEFAULTS_BY_CHANNEL.
        3. NO_PROMO (sin promo) si ninguno aplica.
    """
    key = ((country or "").upper().strip(), (channel or "").lower().strip())
    if key in _CONFIGS:
        return _CONFIGS[key]
    return _DEFAULTS_BY_CHANNEL.get(key[1], NO_PROMO)
