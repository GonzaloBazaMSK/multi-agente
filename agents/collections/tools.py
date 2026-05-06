"""
Herramientas del agente de cobranzas — replicando lógica del BOT n8n.
Tools: buscar_alumno_mail_adc, buscar_suscripcion_rebill, generar_insta_link_rebill
"""

import json

import structlog
from langchain_core.tools import tool

from integrations.payments.rebill import RebillClient
from integrations.zoho.area_cobranzas import ZohoAreaCobranzas

logger = structlog.get_logger(__name__)

CHANNEL_MAP = {
    "Argentina": "medicalscientificknowledge-whatsapp-5491139007715",
    "Colombia": "medicalscientificknowledge-whatsapp-5753161349",
    "Mexico": "medicalscientificknowledge-whatsapp-5215599904940",
    "Ecuador": "medicalscientificknowledge-whatsapp-593998158115",
    "Chile": "medicalscientificknowledge-whatsapp-56224875300",
    "Uruguay": "medicalscientificknowledge-whatsapp-5491152170771",
}

# Mapeo nombre de país (como viene de Zoho) → código ISO usado por Rebill
# para decidir las cuotas habilitadas (`INSTALLMENTS_BY_COUNTRY`).
COUNTRY_ISO_MAP = {
    "Argentina": "AR",
    "Colombia": "CO",
    "Mexico": "MX",
    "México": "MX",
    "Ecuador": "EC",
    "Chile": "CL",
    "Uruguay": "UY",
    "Peru": "PE",
    "Perú": "PE",
    "Bolivia": "BO",
    "Paraguay": "PY",
}


def _to_iso_country(pais: str) -> str:
    """Devuelve el código ISO-2 del país. Si ya viene corto, lo deja."""
    if not pais:
        return "AR"
    if len(pais) == 2:
        return pais.upper()
    return COUNTRY_ISO_MAP.get(pais, "AR")


@tool
async def buscar_alumno_mail_adc(email: str) -> str:
    """
    Busca la ficha completa del alumno en el módulo Area_de_cobranzas de Zoho por email.
    Usar cuando el alumno proporciona su email y no se tiene la ficha cargada.

    Args:
        email: Email del alumno con el que se registró en el campus.
    """
    zoho = ZohoAreaCobranzas()
    ficha = await zoho.search_by_email(email)

    if not ficha:
        return f"No encontré ningún alumno registrado con el email {email}. ¿Podría verificar que sea el correo con el que accede al campus?"

    return _formatear_ficha(ficha)


@tool
async def buscar_suscripcion_rebill(cobranza_id: str, phone: str, pais: str = "Argentina") -> str:
    """
    Reintenta el cobro de la suscripción Rebill ACTIVA del alumno.
    Devuelve el retry-payment-link de la suscripción (mantiene el ciclo recurrente
    intacto — el cobro de este link cuenta como el cobro mensual del débito automático).

    Usar SOLO cuando metodoPago == 'Rebill' y el alumno debe EXACTAMENTE 1 cuota
    (saldoPendiente == valorCuota).

    Si esta tool falla o no hay suscripción activa: NO llamés otra tool de link.
    Derivá al alumno con un asesor de cobranzas.

    Args:
        cobranza_id: ID del registro en Area_de_cobranzas de Zoho.
        phone: Teléfono del alumno (para monitoreo posterior).
        pais: País del alumno (para tags/logs).
    """
    zoho = ZohoAreaCobranzas()
    ficha = await zoho.get_by_id(cobranza_id)
    if not ficha:
        return "No pude obtener los datos del alumno. Derivá con HANDOFF_REQUIRED: error_tool."

    customer_id = ficha.get("ID_Cliente", "")
    if not customer_id:
        return (
            "El alumno no tiene ID_Cliente registrado para Rebill. "
            "Derivá con HANDOFF_REQUIRED: error_tool."
        )

    rebill = RebillClient()
    try:
        result = await rebill.get_active_subscription_link(customer_id)
    except Exception as e:
        logger.warning("rebill_subscription_lookup_failed", error=str(e), cobranza_id=cobranza_id)
        return (
            "Hubo un error consultando la suscripción Rebill. "
            "Derivá con HANDOFF_REQUIRED: error_tool."
        )

    url = result.get("checkout_url", result.get("url", ""))
    if not url:
        return (
            "No encontré una suscripción Rebill activa para este alumno. "
            "Derivá con HANDOFF_REQUIRED: error_tool."
        )

    return (
        f"[REBILL_DATA:{json.dumps({'cobranzaId': cobranza_id, 'phone': phone, 'pais': pais})}]\n"
        f"Aquí tiene el enlace para abonar su cuota:\n{url}\n[LINK_REBILL_ENVIADO]"
    )


@tool
async def generar_insta_link_rebill(
    cobranza_id: str,
    phone: str,
    monto: float,
    moneda: str,
    descripcion: str,
    pais: str = "Argentina",
) -> str:
    """
    Genera un link de pago Rebill por un monto específico (múltiples cuotas, saldo total, o monto parcial).
    Usar cuando metodoPago == 'Rebill' y se deben cobrar varias cuotas o el saldo total.

    Args:
        cobranza_id: ID del registro en Area_de_cobranzas de Zoho.
        phone: Teléfono del alumno.
        monto: Monto exacto a cobrar.
        moneda: Código de moneda (ARS, MXN, COP, CLP, UYU).
        descripcion: Descripción del pago (ej: "2 cuotas vencidas").
        pais: País del alumno.
    """
    rebill = RebillClient()
    iso_country = _to_iso_country(pais)
    # Para enriquecer email/nombre del cliente buscamos la ficha por cobranza_id.
    # Si falla, igual creamos el link sin prefilled fields.
    customer_email = ""
    customer_name = ""
    try:
        zoho = ZohoAreaCobranzas()
        ficha = await zoho.get_by_id(cobranza_id)
        if ficha:
            customer_email = ficha.get("email", "") or ""
            customer_name = ficha.get("alumno", "") or ""
    except Exception as e:
        logger.warning("zoho_lookup_for_insta_link_failed", error=str(e), cobranza_id=cobranza_id)

    result = await rebill.create_payment_link(
        title=descripcion,
        amount=monto,
        currency=moneda,
        country=iso_country,
        customer_email=customer_email,
        customer_name=customer_name,
        is_single_use=True,
    )
    url = result.get("checkout_url", "")
    if not url:
        return "No pude generar el link de pago en este momento. Por favor intentá en unos minutos."

    return (
        f"[REBILL_DATA:{json.dumps({'cobranzaId': cobranza_id, 'phone': phone, 'pais': pais, 'monto': monto})}]\n"
        f"Aquí tiene el enlace para regularizar su cuenta:\n{url}\n[LINK_REBILL_ENVIADO]"
    )


def _formatear_ficha(ficha: dict) -> str:
    """Formatea la ficha del alumno para que el agente la incorpore al contexto."""
    pais = ficha.get("pais", "")
    moneda = ficha.get("moneda", "ARS")

    def fmt(n):
        if pais == "Argentina":
            return f"{moneda} {n:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{moneda} {n:,.2f}"

    lines = [
        "FICHA_ALUMNO_ENCONTRADA:",
        f"- Nombre: {ficha.get('alumno')} (País: {pais})",
        f"- Email: {ficha.get('email')}",
        f"- ID Cobranza: {ficha.get('cobranzaId')}",
        f"- Estado: {ficha.get('estadoGestion')} / Mora: {ficha.get('estadoMora')}",
        f"- Método de pago: {ficha.get('metodoPago')} ({ficha.get('modoPago')})",
        f"- Contrato: {fmt(ficha.get('importeContrato', 0))} total",
        f"- Cuotas: {ficha.get('cuotasPagas')}/{ficha.get('cuotasTotales')} pagas ({ficha.get('cuotasVencidas')} vencidas)",
        f"- Valor cuota: {fmt(ficha.get('valorCuota', 0))}",
        f"- Deuda vencida: {fmt(ficha.get('saldoPendiente', 0))}",
        f"- Saldo total pendiente: {fmt(ficha.get('saldoTotal', 0))}",
        f"- Días de atraso: {ficha.get('diasAtraso')}",
        f"- Último pago: {fmt(ficha.get('importeUltimoPago', 0))} el {ficha.get('fechaUltimoPago')}",
        f"- Próximo vencimiento: {ficha.get('fechaProximoPago')}",
        f"- Fecha contrato efectivo: {ficha.get('fechaContratoEfectivo')}",
    ]
    return "\n".join(lines)
