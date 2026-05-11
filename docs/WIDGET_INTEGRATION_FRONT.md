# Integración del widget de chat en msk-front

Doc para el equipo de **msk-front** (Ariel). Define las 3 integraciones
que tienen que cablearse del lado del frontend para que el widget de soporte
(servido desde `agentes.msklatam.com`) reaccione bien al checkout.

> **Quién mantiene esto**: backend del widget vive en
> `multi-agente/widget/static/chat.js` (el bundle es `widget.js` que ya cargás
> con `<script src="https://agentes.msklatam.com/widget.js" ...>`).

---

## TL;DR — qué tiene que hacer el front

1. **En `/checkout` mobile**: el widget esconde solo su FAB; renderizá un
   botón propio "Solicitar asistencia" que llame `window.MSKChat.open()`.
2. **Cuando el gateway rechaza un pago**: dispatch `msk:paymentRejected` con
   el código de error → el widget se abre solo en desktop y el bot arranca
   explicando el motivo del rechazo.
3. **Apertura automática por inactividad (5 min)**: ya funciona sola en
   desktop, no necesitás hacer nada.

---

## 1. Botón "Solicitar asistencia" en mobile + /checkout

**Por qué:** el widget oculta su FAB en mobile cuando la URL matchea
`/checkout` (porque el banner sticky de pago lo tapaba y porque queremos un
CTA más explícito en ese momento). En desktop el FAB sigue visible.

**Qué tenés que hacer:** renderizar un botón propio en la página del checkout
en mobile, que llame al método público que expone el widget.

```tsx
// Ejemplo simplificado — adaptalo al diseño de Ariel
function CheckoutMobileSupportButton() {
  const handleClick = () => {
    if (typeof window !== 'undefined' && window.MSKChat) {
      window.MSKChat.open();
    }
  };
  return (
    <button onClick={handleClick} className="msk-help-fab-mobile">
      💬 Solicitar asistencia
    </button>
  );
}
```

**Ubicación sugerida:** dentro de `src/app/[lang]/checkout/[curso]/page.tsx`
o donde tengan el layout del checkout, condicional a mobile (`useMediaQuery`
o equivalente). Basta con renderizarlo siempre — en desktop pueden ocultarlo
porque ya tenés el FAB del widget.

**API completa de `window.MSKChat`:**

```ts
declare global {
  interface Window {
    MSKChat?: {
      open():   void;     // abre el panel
      close():  void;     // lo cierra
      toggle(): void;     // toggle
      isOpen(): boolean;  // estado
      reportPaymentRejection(detail: PaymentRejectionDetail): void;
                          // alias de dispatchEvent — ver punto 2
    };
  }
}
```

---

## 2. Dispatch del evento `msk:paymentRejected` cuando hay rechazo

**Por qué:** queremos que cuando el gateway rechaza un pago, el chat se abra
**solo en desktop** y arranque explicando POR QUÉ se rechazó (con info
ampliada del motivo, no solo "rechazado"). En mobile el chat no se auto-abre
(usuario tiene que tocar el botón "Solicitar asistencia"), pero el motivo
queda igualmente registrado para cuando el user abra el chat.

### Shape del evento

```ts
window.dispatchEvent(new CustomEvent('msk:paymentRejected', {
  detail: {
    code:    string,  // código canónico — usar PaymentErrorStatus existente
    message: string,  // userMessage (el mismo que ya muestran a Ariel)
    reason:  string,  // alias de message, opcional (para fallback)
    gateway: string,  // 'rebill' | 'stripe' | 'mercadopago'
  }
}));
```

### Códigos válidos en `code`

Mismos `PaymentErrorStatus` que ya usan en
`src/app/[lang]/checkout/utils/paymentErrorMessages.ts`:

| Código              | Cuándo                                          |
|---------------------|-------------------------------------------------|
| `insufficient_funds`| Sin fondos / sin cupo                            |
| `card_declined`     | Tarjeta rechazada por el banco                   |
| `expired_card`      | Tarjeta vencida                                  |
| `invalid_card`      | Datos mal (incluye `invalid_card_number`/`_cvc`) |
| `processing_error`  | Error de red / procesamiento                     |
| `fraud_high_risk`   | Antifraude bloqueó                               |
| `invalid_session`   | Sesión expirada                                  |
| `rejected`          | Genérico — usá este si no sabés el motivo exacto |

> El backend del widget tiene un dict canónico con explicaciones humanas y
> próximo paso recomendado para cada uno
> ([`integrations/payment_rejections.py`](../integrations/payment_rejections.py)).
> Si pasás un código desconocido, cae en fallback usando el `message`.

### Dónde dispararlo en msk-front

Hay 3 puntos en los que el frontend ya procesa el rechazo (los identifiqué
explorando el repo en read-only). En cada uno hay que agregar el dispatch
**inmediatamente después** del `setPaymentStatus(...)`:

#### 2.1 — Rebill

`src/app/[lang]/checkout/CheckoutPaymentRebill.tsx` — dentro de `handleError()`
(~línea 264-319), después del `mapRebillErrorToStatus(...)` + `setPaymentStatus(specificStatus)`:

```ts
const handleError = (event: any) => {
  const statusDetail = event?.detail?.result?.statusDetail
    || event?.detail?.data?.result?.statusDetail;
  const errorStatus = mapRebillErrorToStatus(statusDetail || errorType);
  setPaymentStatus(errorStatus);
  window.location.hash = '#rechazado';

  // ▶ AGREGAR: notificar al widget de chat
  window.dispatchEvent(new CustomEvent('msk:paymentRejected', {
    detail: {
      code: errorStatus,
      message: getPaymentErrorMessage(errorStatus),
      gateway: 'rebill',
    },
  }));
};
```

#### 2.2 — MercadoPago

`src/app/[lang]/checkout/CheckoutPaymentMercadoPago.tsx` (~línea 383-392):

```ts
} else {
  setPaymentStatus('rejected');
  window.location.hash = '#rechazado';

  // ▶ AGREGAR
  window.dispatchEvent(new CustomEvent('msk:paymentRejected', {
    detail: {
      code: 'rejected',
      message: getPaymentErrorMessage('rejected'),
      gateway: 'mercadopago',
    },
  }));
}
```

> Si MP devuelve algún `status_detail` más específico (ej. `cc_rejected_*`),
> mapealo antes a un `PaymentErrorStatus` y pasá ese código en lugar de
> `'rejected'`. La granularidad la elige Ariel.

#### 2.3 — Stripe

`src/app/[lang]/checkout/CheckoutPaymentStripe.tsx` (~línea 170-176):

```ts
if (error) {
  setStripeError(error.message);
  logServer('error creando metodo de pago en stripe', { error });

  // ▶ AGREGAR (Stripe expone error.code y error.decline_code)
  const stripeStatus = mapStripeErrorToStatus(error); // crear helper si no existe
  window.dispatchEvent(new CustomEvent('msk:paymentRejected', {
    detail: {
      code: stripeStatus,
      message: error.message,
      gateway: 'stripe',
    },
  }));
  return;
}
```

> Sugerencia para `mapStripeErrorToStatus`: mapear `error.code` con la misma
> tabla de Rebill — los códigos coinciden bastante (`card_declined`,
> `expired_card`, `processing_error`, etc.).

### Alternativa: `window.MSKChat.reportPaymentRejection(detail)`

Si preferís llamada directa a método en lugar de `dispatchEvent`, es
equivalente:

```ts
window.MSKChat?.reportPaymentRejection({
  code: errorStatus,
  message: getPaymentErrorMessage(errorStatus),
  gateway: 'rebill',
});
```

Hace exactamente lo mismo internamente. Usá la que te resulte más cómoda.

---

## 3. Apertura automática por inactividad (5 min)

**Funciona sola, no tenés que hacer nada.** El widget detecta inactividad
(sin mousemove/keydown/scroll/click/touchstart) durante 5 minutos en la misma
URL y, **solo en desktop**, abre el panel automáticamente.

Reglas:
- Solo desktop (≥769px). En mobile no se auto-abre — la UX sería invasiva.
- **Una sola vez por URL × sesión** (flag en `sessionStorage`). Si el user
  cierra el panel después del auto-open, no vuelve a abrirse en esa misma
  página.
- Cambiar de URL (SPA o navegación) resetea el timer y la flag.

---

## Verificación

Cuando termines de cablear los 3 puntos:

1. **Desktop, en home**: dejá la pestaña inactiva 5 min → debería abrirse el
   chat solo. Refrescá la página y comprobá que NO se abre dos veces seguidas
   en la misma URL.
2. **Mobile, /checkout**: el FAB del widget no debería aparecer; el botón
   "Solicitar asistencia" tuyo sí.
3. **Desktop, simular rechazo de pago**: forzá un pago rechazado con tarjeta
   de prueba. El chat debería abrirse solo y el bot arrancar con algo del
   estilo *"Vi que tuviste un problema con el pago — la tarjeta fue rechazada
   por el banco. Probá con otra…"*.
4. **Mobile, simular rechazo de pago**: el chat NO se debería abrir solo, pero
   si tocás el botón "Solicitar asistencia", el bot ya tiene el contexto del
   rechazo y arranca explicando el motivo.

Cualquier duda sobre el shape exacto del payload o si Stripe/MP devuelven
códigos que no están en la tabla, pasame los códigos crudos y los agrego al
mapeo del backend.

— Gonza
