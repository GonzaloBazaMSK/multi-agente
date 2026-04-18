/**
 * Cliente de la API. En dev, las llamadas a /api/* las redirige
 * next.config.mjs (rewrites) al FastAPI local. En prod, Nginx hace lo mismo
 * en agentes.msklatam.com → /api/* → FastAPI.
 *
 * IMPORTANTE: el backend tiene DOS familias de routes:
 *   - /auth/*   → router de auth, montado SIN prefijo /api
 *                  (login, /auth/users, /auth/me, etc.)
 *   - /api/*    → todo el resto (inbox, prompts, channels, courses, ...)
 *
 * Si llamás `api.get("/auth/users")` ingenuamente, queda `/api/auth/users`
 * que NO existe en el backend → 404. Por eso `request()` detecta paths que
 * empiezan con /auth y los rutea sin el prefijo /api. Era el bug que hacía
 * que la pantalla de Configuración mostrara "Necesitás iniciar sesión como
 * admin" aunque estuvieras logueado como admin.
 */

const ADMIN_KEY = process.env.NEXT_PUBLIC_ADMIN_KEY || "change-this-secret";

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("msk_console_token");
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "x-admin-key": ADMIN_KEY,
    ...(init.headers as Record<string, string> || {}),
  };
  if (token) headers["x-session-token"] = token;

  // Routes /auth/* viven en el root, no debajo de /api. Ver header del archivo.
  const url = path.startsWith("/auth/") ? path : `/api${path}`;
  const res = await fetch(url, { ...init, headers });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, `HTTP ${res.status}: ${text || res.statusText}`);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get:    <T,>(path: string) => request<T>(path, { method: "GET" }),
  post:   <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put:    <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body ? JSON.stringify(body) : undefined }),
  delete: <T,>(path: string) => request<T>(path, { method: "DELETE" }),
};

export { ApiError };
