import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Middleware Next.js — setea Content-Security-Policy con nonces por
 * request. Defensa en profundidad contra XSS:
 *
 *  - `script-src 'self' 'nonce-<random>' 'strict-dynamic'` — solo
 *    scripts con nuestro nonce del request actual. Un XSS inyectado
 *    no tiene el nonce correcto → el browser lo bloquea aunque esté
 *    en el DOM.
 *  - `style-src 'self' 'unsafe-inline'` — necesario por Tailwind que
 *    emite styles inline (utility classes). Idealmente llevar a
 *    `strict-dynamic` con nonces también; Tailwind no lo soporta
 *    completamente todavía.
 *  - `img-src https:` — flags de flagcdn.com, avatars de R2, etc.
 *  - `connect-src 'self'` — fetches solo al mismo origen. Backend y
 *    frontend comparten dominio (agentes.msklatam.com).
 *  - `frame-ancestors 'none'` — ni iframes externos ni nuestros. Mejor
 *    que X-Frame-Options que es legacy.
 *
 * El nonce se propaga a los componentes vía header `x-nonce`. Next.js
 * lo agarra automáticamente y lo inyecta en los <script> que emite al
 * hacer SSR.
 */
export function middleware(request: NextRequest) {
  // Nonce criptográfico por request.
  const nonce = btoa(crypto.randomUUID()).replace(/=+$/, "");

  const isDev = process.env.NODE_ENV === "development";

  const cspHeader = `
    default-src 'self';
    script-src 'self' 'nonce-${nonce}' 'strict-dynamic' ${isDev ? "'unsafe-eval'" : ""};
    style-src 'self' 'unsafe-inline';
    img-src 'self' data: https:;
    font-src 'self' data:;
    connect-src 'self' ${isDev ? "ws://localhost:* http://localhost:*" : ""};
    frame-ancestors 'none';
    base-uri 'self';
    form-action 'self';
    upgrade-insecure-requests;
  `.replace(/\s{2,}/g, " ").trim();

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("content-security-policy", cspHeader);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("content-security-policy", cspHeader);
  return response;
}

// Corre en todo excepto /api (el backend tiene su propio CSP si lo necesita),
// assets estáticos, y archivos generados por Next.
export const config = {
  matcher: [
    {
      source: "/((?!api|_next/static|_next/image|favicon.ico|logo.png|logo.svg).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
