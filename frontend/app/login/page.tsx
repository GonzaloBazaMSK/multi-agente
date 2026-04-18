"use client";

import { useState } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Loader2 } from "lucide-react";

export default function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(email, password);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg p-4">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm bg-panel border border-border rounded-lg p-6 space-y-5"
      >
        <div className="flex flex-col items-center gap-2 mb-2">
          <img src="/logo.png" alt="MSK" className="w-12 h-12" />
          <div className="text-lg font-semibold">MSK Console</div>
          <div className="text-xs text-fg-dim">Operaciones del bot multi-agente</div>
        </div>

        <div className="space-y-3">
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
          <div>
            <div className="flex justify-between items-center mb-1">
              <label className="block text-xs text-fg-muted">Contraseña</label>
              <Link
                href="/forgot-password"
                className="text-[11px] text-accent hover:underline"
              >
                ¿La olvidaste?
              </Link>
            </div>
            <Input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
        </div>

        {error && (
          <div className="bg-danger/10 border border-danger/30 rounded-md px-3 py-2 text-xs text-danger">
            ⚠ {error}
          </div>
        )}

        <Button type="submit" className="w-full" disabled={loading || !email || !password}>
          {loading ? (
            <><Loader2 className="w-4 h-4 animate-spin" /> Ingresando...</>
          ) : "Iniciar sesión"}
        </Button>
      </form>
    </div>
  );
}
