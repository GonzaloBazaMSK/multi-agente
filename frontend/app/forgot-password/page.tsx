"use client";

/**
 * Pantalla "olvidé mi contraseña" — paso 1 de 2.
 *
 * El user mete su email, el backend dispara un mail via Supabase Auth con un
 * link tipo:
 *   https://<project>.supabase.co/auth/v1/verify?token=...&type=recovery&redirect_to=https://agentes.msklatam.com/reset-password
 *
 * Cuando hace click, Supabase redirige a /reset-password con un access_token
 * en el hash fragment de la URL. Ese flujo lo maneja /reset-password/page.tsx.
 *
 * Por seguridad la respuesta del backend SIEMPRE es genérica — no confirma si
 * el email existe (anti user-enumeration).
 */

import { useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Loader2, ArrowLeft, CheckCircle2 } from "lucide-react";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await fetch("/auth/forgot-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(`Error ${res.status}: ${txt}`);
      }
      setSent(true);
    } catch (err) {
      setError((err as Error).message || "Error al enviar el mail");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg p-4">
      <div className="w-full max-w-sm bg-panel border border-border rounded-lg p-6 space-y-5">
        <div className="flex flex-col items-center gap-2 mb-2">
          <img src="/logo.png" alt="MSK" className="w-12 h-12" />
          <div className="text-lg font-semibold">Recuperar contraseña</div>
          <div className="text-xs text-fg-dim text-center">
            Te enviamos un mail con instrucciones para restablecerla.
          </div>
        </div>

        {sent ? (
          <div className="space-y-4">
            <div className="bg-success/10 border border-success/30 rounded-md p-4 flex flex-col items-center gap-2">
              <CheckCircle2 className="w-8 h-8 text-success" />
              <div className="text-sm font-medium text-center">Revisá tu inbox</div>
              <div className="text-[11px] text-fg-dim text-center">
                Si <b>{email}</b> está registrado, te enviamos un mail con un link
                para crear una nueva contraseña. El link expira en 1 hora.
              </div>
              <div className="text-[10px] text-fg-dim text-center mt-2">
                ¿No te llega? Revisá spam, o pedí otro link en unos minutos.
              </div>
            </div>
            <Link
              href="/login"
              className="text-xs text-accent hover:underline flex items-center justify-center gap-1"
            >
              <ArrowLeft className="w-3 h-3" /> Volver a iniciar sesión
            </Link>
          </div>
        ) : (
          <form onSubmit={onSubmit} className="space-y-4">
            <div>
              <label className="block text-xs text-fg-muted mb-1">Email</label>
              <Input
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="tu@msklatam.com"
              />
            </div>

            {error && (
              <div className="bg-danger/10 border border-danger/30 rounded-md px-3 py-2 text-xs text-danger">
                ⚠ {error}
              </div>
            )}

            <Button type="submit" className="w-full" disabled={loading || !email}>
              {loading ? (
                <><Loader2 className="w-4 h-4 animate-spin" /> Enviando...</>
              ) : "Enviar mail de recuperación"}
            </Button>

            <Link
              href="/login"
              className="text-xs text-accent hover:underline flex items-center justify-center gap-1"
            >
              <ArrowLeft className="w-3 h-3" /> Volver a iniciar sesión
            </Link>
          </form>
        )}
      </div>
    </div>
  );
}
