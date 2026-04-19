/**
 * Cliente HTTP del backend.
 *
 * Auth: la sesión vive en una cookie `msk_session` httpOnly, emitida por
 * `POST /api/v1/auth/login`. Como es httpOnly NO es leíble desde JS (eso
 * es el punto — un XSS vía npm comprometida no puede robar el token).
 * El browser la manda automáticamente en cada fetch al mismo origen, no
 * hay que tocarla acá.
 *
 * `credentials: "include"` fuerza que se mande incluso si el frontend
 * está en otro origen (dev: localhost:3000 → api:8000). En prod mismo
 * origen alcanzaría con "same-origin" pero "include" es equivalente.
 *
 * Antes usábamos `x-session-token` header + localStorage. Se migró a
 * cookies httpOnly porque el header era vulnerable a XSS — una dep npm
 * comprometida podía leer `localStorage.getItem("msk_console_token")`.
 */

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init.headers as Record<string, string>) || {}),
  };

  const url = `/api/v1${path}`;
  const res = await fetch(url, {
    ...init,
    headers,
    credentials: "include", // manda la cookie msk_session automáticamente
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, `HTTP ${res.status}: ${text || res.statusText}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T,>(path: string) => request<T>(path, { method: "GET" }),
  post: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body ? JSON.stringify(body) : undefined }),
  patch: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }),
  delete: <T,>(path: string) => request<T>(path, { method: "DELETE" }),
};

export { ApiError };
