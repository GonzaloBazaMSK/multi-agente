"use client";

/**
 * Pantalla "olvidé mi contraseña" — paso 2 de 2.
 *
 * El user llega acá desde el mail que envió Supabase. Supabase agrega los
 * tokens en el HASH FRAGMENT (no query params) — algo así:
 *   /reset-password#access_token=eyJ...&refresh_token=...&type=recovery&expires_in=3600
 *
 * Es importante que sea HASH y no query: hash NO se manda al server, se queda
 * en el cliente. Eso es lo que hace seguro este flow (el access_token nunca
 * se loguea en logs de servidor / proxy / referer).
 *
 * Si no hay access_token (el user llegó acá directo escribiendo la URL),
 * mostramos un mensaje pidiendo que pida un link nuevo.
 */

import { useState, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Loader2, CheckCircle2, AlertCircle } from "lucide-react";

export default function ResetPasswordPage() {
  const router = useRouter();
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  // Extraer access_token del hash al montar. Hash NO se manda al server.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const hash = window.location.hash.startsWith("#")
      ? window.location.hash.slice(1)
      : window.location.hash;
    const params = new URLSearchParams(hash);
    const token = params.get("access_token");
    const type = params.get("type");
    const errCode = params.get("error_code") || params.get("error");

    if (errCode) {
      // Supabase rebota con error en el hash: ej. otp_expired
      setTokenError(
        params.get("error_description") ||
        "El link de recuperación expiró o ya fue usado. Pedí uno nuevo."
      );
      return;
    }
    if (!token || type !== "recovery") {
      setTokenError(
        "Esta página solo se accede desde el link del mail de recuperación. " +
        "Si estás intentando cambiar tu contraseña, pedí un mail nuevo."
      );
      return;
    }
    setAccessToken(token);
    // Por higiene visual, limpiamos el hash (queda en localStorage del state).
    // Esto evita que si el user comparte la URL, comparta también el token.
    history.replaceState(null, "", window.location.pathname);
  }, []);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (password.length < 8) {
      setError("La contraseña debe tener al menos 8 caracteres");
      return;
    }
    if (password !== confirm) {
      setError("Las contraseñas no coinciden");
      return;
    }
    if (!accessToken) {
      setError("No se encontró el token de recuperación. Pedí un link nuevo.");
      return;
    }
    setLoading(true);
    try {
      const res = await fetch("/auth/reset-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ access_token: accessToken, new_password: password }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Error ${res.status}`);
      }
      setDone(true);
      // Auto-redirect al login después de 3s
      setTimeout(() => router.replace("/login"), 3000);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg p-4">
      <div className="w-full max-w-sm bg-panel border border-border rounded-lg p-6 space-y-5">
        <div className="flex flex-col items-center gap-2 mb-2">
          <img src="/logo.png" alt="MSK" className="w-12 h-12" />
          <div className="text-lg font-semibold">Nueva contraseña</div>
        </div>

        {tokenError ? (
          <div className="space-y-4">
            <div className="bg-danger/10 border border-danger/30 rounded-md p-4 flex flex-col items-center gap-2">
              <AlertCircle className="w-8 h-8 text-danger" />
              <div className="text-xs text-fg-dim text-center">{tokenError}</div>
            </div>
            <Link
              href="/forgot-password"
              className="block text-center text-xs text-accent hover:underline"
            >
              Pedir nuevo link de recuperación
            </Link>
          </div>
        ) : done ? (
          <div className="space-y-4">
            <div className="bg-success/10 border border-success/30 rounded-md p-4 flex flex-col items-center gap-2">
              <CheckCircle2 className="w-8 h-8 text-success" />
              <div className="text-sm font-medium text-center">¡Contraseña actualizada!</div>
              <div className="text-[11px] text-fg-dim text-center">
                Te redirigimos al login en 3 segundos...
              </div>
            </div>
            <Link
              href="/login"
              className="block text-center text-xs text-accent hover:underline"
            >
              Ir ahora →
            </Link>
          </div>
        ) : (
          <form onSubmit={onSubmit} className="space-y-4">
            <div>
              <label className="block text-xs text-fg-muted mb-1">
                Nueva contraseña
              </label>
              <Input
                type="password"
                autoComplete="new-password"
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="mínimo 8 caracteres"
              />
            </div>
            <div>
              <label className="block text-xs text-fg-muted mb-1">
                Repetí la contraseña
              </label>
              <Input
                type="password"
                autoComplete="new-password"
                required
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
              />
            </div>

            {error && (
              <div className="bg-danger/10 border border-danger/30 rounded-md px-3 py-2 text-xs text-danger">
                ⚠ {error}
              </div>
            )}

            <Button
              type="submit"
              className="w-full"
              disabled={loading || !accessToken || !password || !confirm}
            >
              {loading ? (
                <><Loader2 className="w-4 h-4 animate-spin" /> Actualizando...</>
              ) : "Actualizar contraseña"}
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
