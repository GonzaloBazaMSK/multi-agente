"""
Mapeo canónico de motivos de rechazo de pago a explicaciones humanas
**ampliadas**.

Las claves coinciden 1-a-1 con los `PaymentErrorStatus` del frontend
(`msk-front/src/app/[lang]/checkout/utils/paymentErrorMessages.ts`):
    insufficient_funds | card_declined | expired_card | invalid_card |
    processing_error   | fraud_high_risk | invalid_session | rejected

⚠️ IMPORTANTE: el `userMessage` que el frontend muestra en la pantalla de
rechazo es **muy corto** (1 línea). El widget tiene que aportar valor real,
no repetir lo mismo. Las "explicaciones" de este archivo son **ampliadas**
respecto al texto del checkout — incluyen:
  - Causas posibles concretas (varias opciones)
  - Pasos para diagnosticar (qué chequear primero)
  - Contexto sobre quién es responsable (banco vs procesadora vs user)
  - Tiempos esperados de resolución cuando aplica

El frontend ya hace el mapping de los códigos crudos de cada gateway
(MP statusDetail, Rebill error code, Stripe error code) a estos status
canónicos. El widget recibe el código YA mapeado vía `msk:paymentRejected`.

Si llega un código que no está en el dict, se hace fallback al `message`
crudo del gateway.
"""

# Mapeo: status canónico → {titulo, explicacion (ampliada), accion}.
# La "explicacion" debe darle al user info NUEVA respecto del checkout.
PAYMENT_REJECTIONS: dict[str, dict[str, str]] = {
    "insufficient_funds": {
        "titulo": "Fondos insuficientes",
        "explicacion": (
            "El banco emisor de la tarjeta rechazó el cobro porque la cuenta "
            "no tiene saldo o cupo disponible para este monto. Esto puede pasar "
            "por tres razones típicas:\n"
            "1. **Saldo real**: la cuenta no llega al monto del curso (revisalo "
            "en homebanking).\n"
            "2. **Límite de la tarjeta de crédito**: aunque tengas saldo, el "
            "cupo mensual de la tarjeta no alcanza para este consumo.\n"
            "3. **Pagos online bloqueados**: muchos bancos vienen con un cupo "
            "menor para compras online; podés ampliarlo desde la app del banco "
            "en menos de 1 minuto."
        ),
        "accion": (
            "Lo más rápido es probar con una tarjeta distinta volviendo al "
            "checkout. Si querés usar esta misma, ampliá el cupo de compras "
            "online desde la app del banco y reintentá. Si no podés "
            "resolverlo, te derivo con un asesor académico que te ayuda."
        ),
    },
    "card_declined": {
        "titulo": "Tarjeta rechazada por el banco",
        "explicacion": (
            "El banco emisor rechazó el cobro pero no comunicó el motivo "
            "exacto a la procesadora — esto es lo más común y suele significar "
            "una de tres cosas:\n"
            "1. **Sistema antifraude del banco** bloqueó el consumo por ser "
            "online o por monto inusual. Suele resolverse autorizándolo desde "
            "la app del banco.\n"
            "2. **Datos del titular** no coinciden (nombre completo, DNI/RFC/"
            "CURP según país). Verificá que coincidan con los del frente de "
            "la tarjeta.\n"
            "3. **Restricción interna del banco**: tarjeta nueva sin activar, "
            "límite agotado, deuda atrasada. Esto solo lo resuelve el banco."
        ),
        "accion": (
            "Te recomiendo dos cosas en paralelo: (1) revisá la app del banco "
            "y autorizá el consumo si te aparece como pendiente, y (2) mientras "
            "tanto probá con otra tarjeta para no perder tiempo. Si sigue sin "
            "andar, llamá al número del dorso de la tarjeta — los operadores "
            "suelen autorizar el pago en el momento."
        ),
    },
    "expired_card": {
        "titulo": "Tarjeta vencida",
        "explicacion": (
            "La fecha de vencimiento que ingresaste o la que el sistema tiene "
            "guardada ya pasó. Tené en cuenta dos detalles:\n"
            "1. La tarjeta vence el **último día del mes** indicado, no el "
            "primero. Si tu vencimiento es 06/26, sirve hasta el 30 de junio.\n"
            "2. Si te llegó el plástico nuevo pero todavía no lo activaste, el "
            "banco rechaza pagos online aunque la fecha esté vigente."
        ),
        "accion": (
            "Si tenés el plástico nuevo, activalo desde la app del banco y "
            "reintentá desde el checkout. Si no, usá otra tarjeta vigente. "
            "Si necesitás ayuda extra, te derivo con un asesor académico."
        ),
    },
    "invalid_card": {
        "titulo": "Datos de la tarjeta no válidos",
        "explicacion": (
            "El sistema rechazó los datos antes de llegar al banco. El error "
            "está casi siempre en uno de estos tres campos:\n"
            "1. **Número de tarjeta**: 16 dígitos seguidos (sin espacios ni "
            "guiones). Un solo dígito mal lo invalida.\n"
            "2. **Fecha de vencimiento**: formato MM/AA (ej. 09/27, no 9/2027).\n"
            "3. **CVV**: los 3 dígitos del dorso (4 si es Amex, en el frente). "
            "No es el PIN ni la clave de cajero."
        ),
        "accion": (
            "Lo más útil es volver a tipear los 3 campos mirando la tarjeta "
            "física (no copiando del homebanking, que a veces formatea distinto). "
            "Si seguís con el mismo error, probá con otra tarjeta."
        ),
    },
    "processing_error": {
        "titulo": "Error de procesamiento",
        "explicacion": (
            "No es un problema con tu tarjeta — algo falló en la red bancaria "
            "que conecta a la procesadora con el banco. Suele ser:\n"
            "1. **Caída momentánea** del banco emisor (típico fines de semana "
            "o feriados, dura entre 5 y 30 min).\n"
            "2. **Timeout** en la respuesta: la solicitud llegó pero el banco "
            "tardó mucho en contestar y se cortó.\n"
            "3. Más raro: problema entre la procesadora y la red de tarjetas "
            "(Visa/Mastercard)."
        ),
        "accion": (
            "Lo más probable es que andá en 5-10 minutos sin tocar nada. Si "
            "querés, esperá un cafecito y reintentá. Si después de 30 min "
            "sigue, te derivo con un asesor académico para revisar."
        ),
    },
    "fraud_high_risk": {
        "titulo": "Bloqueado por sistema antifraude",
        "explicacion": (
            "El sistema antifraude (de la procesadora o del banco) marcó la "
            "operación como riesgosa y la bloqueó **antes** de que el banco la "
            "rechazara. **No es un problema con tu tarjeta** — son controles "
            "automáticos que evalúan dispositivo, IP, monto, hora y patrón de "
            "compra.\n\n"
            "Causas típicas: estás comprando desde una IP nueva, una red "
            "pública (wifi de hotel/aeropuerto), un dispositivo distinto al "
            "habitual, o el monto es alto para tu perfil de la tarjeta."
        ),
        "accion": (
            "Probá tres cosas en este orden: (1) cambiá a una red conocida "
            "(wifi de casa o datos móviles), (2) si seguís en problema, "
            "autorizá el consumo desde la app del banco (a veces el banco "
            "manda push de confirmación), (3) usá otra tarjeta. No insistas "
            "más de 2 veces seguidas o el sistema bloquea más fuerte."
        ),
    },
    "invalid_session": {
        "titulo": "Sesión de pago expirada",
        "explicacion": (
            "El link/sesión del checkout tiene vida útil corta (10-15 min) y "
            "ya expiró. Esto suele pasar si:\n"
            "1. Dejaste la pestaña abierta un rato largo antes de pagar.\n"
            "2. Reintentaste varias veces y el sistema invalidó la sesión "
            "anterior por seguridad.\n"
            "3. Volviste con el botón ‘atrás’ del navegador a una sesión vieja.\n\n"
            "**No se cobró nada** — la sesión simplemente caducó."
        ),
        "accion": (
            "Refrescá la página del checkout (F5) y la sesión se regenera "
            "automáticamente. Si después de refrescar sigue sin andar, te "
            "derivo con un asesor académico para que te asista."
        ),
    },
    "rejected": {
        "titulo": "Pago rechazado",
        "explicacion": (
            "La procesadora rechazó la operación pero no devolvió un código "
            "específico que nos permita decirte el motivo exacto. Estadísticamente "
            "los motivos más probables son, en orden:\n"
            "1. **Antifraude del banco** (≈40%): el banco bloqueó el consumo "
            "online sin avisar el motivo.\n"
            "2. **Cupo o saldo insuficiente** (≈30%): aunque parezca que tenés "
            "saldo, el cupo online puede ser menor.\n"
            "3. **Datos no coinciden** (≈20%): nombre, DNI o CVV con un dato mal.\n"
            "4. Otros (≈10%): tarjeta nueva sin activar, restricción interna."
        ),
        "accion": (
            "Lo más eficiente: probá con otra tarjeta de entrada — si esa "
            "anda, era problema del banco emisor de la primera. Si querés "
            "diagnosticar la original, llamá al banco al número del dorso. "
            "Si preferís que te ayude un asesor académico, te derivo."
        ),
    },
}


def explain_rejection(
    code: str = "",
    raw_message: str = "",
    reason: str = "",
) -> dict[str, str]:
    """
    Devuelve la explicación humana ampliada para un código de rechazo.

    Prioridad:
      1. Match exacto del `code` en el dict canónico → texto ampliado.
      2. Si no matchea pero hay `raw_message`/`reason` → fallback con texto crudo.
      3. Genérico.

    Returns: {titulo, explicacion, accion, code}
    """
    code_norm = (code or "").strip().lower()
    if code_norm in PAYMENT_REJECTIONS:
        info = PAYMENT_REJECTIONS[code_norm]
        return {
            "titulo": info["titulo"],
            "explicacion": info["explicacion"],
            "accion": info["accion"],
            "code": code_norm,
        }

    fallback_text = (raw_message or reason or "").strip()
    if fallback_text:
        return {
            "titulo": "Pago rechazado",
            "explicacion": (
                f"La procesadora devolvió este motivo: «{fallback_text[:300]}». "
                "Sin más detalle del banco, las causas más comunes son antifraude "
                "del banco, datos del titular que no coinciden, o cupo online "
                "insuficiente."
            ),
            "accion": (
                "Te puedo ayudar a diagnosticar — contame qué tarjeta usaste "
                "(crédito o débito, banco) y vemos. En paralelo podés probar "
                "con otra tarjeta o contactar al banco emisor."
            ),
            "code": code_norm or "unknown",
        }

    return {
        "titulo": "Pago rechazado",
        "explicacion": (
            "La procesadora rechazó la operación sin devolver un motivo "
            "específico. Las causas más probables son antifraude del banco, "
            "cupo online insuficiente, o un dato del titular que no coincide."
        ),
        "accion": (
            "Probá con otra tarjeta como primer paso — si esa anda, era "
            "problema del banco de la primera. Si preferís, te derivo con "
            "un asesor académico."
        ),
        "code": code_norm or "unknown",
    }


def build_context_block(rejection: dict) -> str:
    """
    Toma el payload `payment_rejection` (que viene del widget vía
    `msk:paymentRejected`) y devuelve un bloque markdown para inyectar al
    contexto del agente sales/closer. El agente lo lee como instrucción de
    cómo arrancar la conversación tras un rechazo.

    `rejection` debe tener la forma:
        {reason: str, code: str, message: str, gateway?: str}

    Si todos los campos están vacíos, devuelve "" (no se inyecta nada).
    """
    if not rejection or not isinstance(rejection, dict):
        return ""

    code = rejection.get("code") or ""
    raw_message = rejection.get("message") or ""
    reason = rejection.get("reason") or ""
    gateway = rejection.get("gateway") or ""

    if not (code or raw_message or reason):
        return ""

    info = explain_rejection(code=code, raw_message=raw_message, reason=reason)

    return (
        "## ⚠️ CONTEXTO CRÍTICO — RECHAZO DE PAGO RECIENTE\n\n"
        "El usuario acaba de tener un pago rechazado en el checkout y el chat se "
        "abrió automáticamente para ayudarlo. **Tu primer turno DEBE arrancar "
        "explicando el motivo en lenguaje claro y aportando información AMPLIADA "
        "respecto al banner que ya vio en el checkout.** El user ya leyó algo "
        "como «Tu tarjeta fue rechazada por el banco» — repetir eso sería ruido. "
        "Tu valor está en explicar **causas posibles + cómo diagnosticar + "
        "qué hacer**.\n\n"
        f"**Motivo del rechazo: {info['titulo']}**\n"
        f"  - Código del gateway: `{info['code']}`{(' (' + gateway + ')') if gateway else ''}\n\n"
        "**Información ampliada (parafraseala — NO la pegues literal):**\n"
        f"{info['explicacion']}\n\n"
        "**Acción que el usuario puede intentar por su cuenta (sugeríla):**\n"
        f"{info['accion']}\n\n"
        "## INSTRUCCIONES PARA ESTE TURNO\n"
        "1. Empezá con empatía breve (1 línea, sin sobreactuar): «Vi que tuviste "
        "un problema con el pago — te explico qué pasó».\n"
        "2. Explicá el motivo del rechazo aportando las **causas posibles** "
        "del bloque ampliado (las 3 razones típicas, no solo el título). "
        "Adaptá el tono al país y resumí en 4-6 líneas máximo — no peques de "
        "manual.\n"
        "3. Sugerí la **acción que el usuario puede intentar por su cuenta** "
        "(otra tarjeta, autorizar desde la app del banco, etc.).\n"
        "4. **🚫 PROHIBIDO regenerar links de pago.** NO uses `create_payment_link`. "
        "NO digas «te genero un link nuevo», «te paso un link directo» ni nada "
        "parecido. El reintento del pago se hace desde el checkout original — "
        "el usuario refresca o vuelve a la página y reintenta.\n"
        "5. **Si el usuario insiste en reintentar el pago, no puede resolverlo "
        "solo, o pide hablar con alguien → derivá SIEMPRE con "
        "HANDOFF_REQUIRED.** Un asesor académico puede asistirlo personalmente "
        "(verificar datos, generar manualmente un link distinto, ofrecer "
        "alternativas caso a caso).\n"
        "6. **NO inventes** otros métodos que MSK no acepta (solo tarjeta "
        "crédito/débito — ver Regla #7).\n"
        "7. **NO leas el código crudo** (`cc_rejected_*`, `card_declined`) — "
        "es ruido para el usuario."
    )
