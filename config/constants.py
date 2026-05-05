from enum import StrEnum


class AgentType(StrEnum):
    SALES = "ventas"
    COLLECTIONS = "cobranzas"
    POST_SALES = "post_venta"
    CLOSER = "closer"
    HUMAN = "humano"


class Country(StrEnum):
    """Paises soportados por el bot.

    Alineado con `integrations/msk_courses.LANG_BY_COUNTRY` (los 17 paises
    con catalogo WP propio) + `INTERNATIONAL` como fallback — mismo criterio
    que usa Wordpress: cualquier pais sin ficha propia cae en INT con precio
    fijo USD.

    Ojo: es distinto de las colas de atencion humana. Para `ventas_XX` /
    `cobranzas_XX` / `post_venta_XX` se usa `MP` (multi-pais) — el enum
    `Country` solo modela el pais de origen del usuario, no la cola.
    """

    ARGENTINA = "AR"
    BOLIVIA = "BO"
    CHILE = "CL"
    COLOMBIA = "CO"
    COSTA_RICA = "CR"
    ECUADOR = "EC"
    SPAIN = "ES"
    GUATEMALA = "GT"
    HONDURAS = "HN"
    MEXICO = "MX"
    NICARAGUA = "NI"
    PANAMA = "PA"
    PARAGUAY = "PY"
    PERU = "PE"
    EL_SALVADOR = "SV"
    URUGUAY = "UY"
    VENEZUELA = "VE"
    INTERNATIONAL = "INT"  # fallback para paises sin catalogo propio


def normalize_country(raw: object) -> Country:
    """Normaliza un string de pais al enum Country.

    Si es un codigo ISO-2 que tenemos soportado → devuelve el enum correspondiente.
    Cualquier otro valor (None, string desconocido, raw != str) → INTERNATIONAL.

    Usado como BeforeValidator en `UserProfile.country` para que un widget
    embebido en un dominio con un usuario de un pais no listado (ej: Nigeria,
    USA, etc.) NO tumbe la request con 500 — en su lugar cae en INT y sigue
    el flow normal con precios USD.
    """
    if isinstance(raw, Country):
        return raw
    if not isinstance(raw, str) or not raw:
        return Country.INTERNATIONAL
    code = raw.strip().upper()
    try:
        return Country(code)
    except ValueError:
        return Country.INTERNATIONAL


COUNTRY_PHONE_PREFIXES: dict[str, Country] = {
    "54": Country.ARGENTINA,
    "591": Country.BOLIVIA,
    "56": Country.CHILE,
    "57": Country.COLOMBIA,
    "506": Country.COSTA_RICA,
    "593": Country.ECUADOR,
    "34": Country.SPAIN,
    "502": Country.GUATEMALA,
    "504": Country.HONDURAS,
    "52": Country.MEXICO,
    "505": Country.NICARAGUA,
    "507": Country.PANAMA,
    "595": Country.PARAGUAY,
    "51": Country.PERU,
    "503": Country.EL_SALVADOR,
    "598": Country.URUGUAY,
    "58": Country.VENEZUELA,
    # INT no tiene prefix — es fallback para paises sin mapeo explicito
}

COUNTRY_CURRENCY: dict[Country, str] = {
    Country.ARGENTINA: "ARS",
    Country.BOLIVIA: "BOB",
    Country.CHILE: "CLP",
    Country.COLOMBIA: "COP",
    Country.COSTA_RICA: "CRC",
    Country.ECUADOR: "USD",  # dolarizado
    Country.SPAIN: "EUR",
    Country.GUATEMALA: "GTQ",
    Country.HONDURAS: "HNL",
    Country.MEXICO: "MXN",
    Country.NICARAGUA: "NIO",
    Country.PANAMA: "USD",  # balboa 1:1 USD, de facto USD
    Country.PARAGUAY: "PYG",
    Country.PERU: "PEN",
    Country.EL_SALVADOR: "USD",  # dolarizado
    Country.URUGUAY: "UYU",
    Country.VENEZUELA: "USD",  # operan en USD por hiperinflacion
    Country.INTERNATIONAL: "USD",  # mismo criterio que WP para paises sin ficha
}

# Conversation TTL in Redis (seconds)
CONVERSATION_TTL = 60 * 60 * 24 * 7  # 7 days

# Max conversation history to send to LLM
MAX_HISTORY_MESSAGES = 20

# Handoff triggers
HANDOFF_KEYWORDS = [
    "hablar con una persona",
    "agente humano",
    "persona real",
    "quiero hablar con alguien",
    "operador",
    "asesor",
]


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    WIDGET = "widget"


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    HANDED_OFF = "handed_off"
    CLOSED = "closed"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    IN_ARREARS = "in_arrears"


# ── Másters: NO se venden por checkout, derivar a asesor humano ──────────────
# Identificación: product_id en el rango [8000000, 8999999] (los 6 másters
# actuales son 8000000–8000005). El criterio coincide 1:1 con `title LIKE
# 'Máster%'`. Slug NO sirve (los slugs son tipo "cuidados-paliativos" sin
# prefijo).
#
# Política: el bot NUNCA debe pitchear, listar ni dar link de checkout para
# estos cursos. Si el user los menciona, derivar a asesor académico humano
# (handoff).
MASTER_PRODUCT_ID_MIN = 8000000
MASTER_PRODUCT_ID_MAX = 8999999

MASTER_SLUGS = frozenset(
    {
        "clinica-infanto-juvenil",
        "cuidados-paliativos",
        "imagen-clinica-y-ecografia",
        "nutricion-antiaging-microbiota-y-glp",
        "rehabilitacion-y-fisioterapia-del-deporte",
        "urgencias-y-emergencias",
    }
)


def is_master_slug(slug: str) -> bool:
    """True si el slug corresponde a un Máster (NO vendible por checkout)."""
    return (slug or "").strip().lower() in MASTER_SLUGS


def is_master_product_id(product_id: int | None) -> bool:
    """True si el product_id está en el rango de Másters."""
    if product_id is None:
        return False
    return MASTER_PRODUCT_ID_MIN <= int(product_id) <= MASTER_PRODUCT_ID_MAX
