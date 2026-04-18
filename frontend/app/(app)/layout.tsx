"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Rail } from "@/components/layout/rail";
import { useAuth } from "@/lib/auth";

/**
 * Guard de cliente para todo el segmento (app)/*. Si no hay user después
 * de cargar, redirect a /login. Mientras carga muestra un splash mínimo —
 * sin esto se vería un flash del inbox vacío antes del redirect.
 *
 * Esto NO reemplaza la auth del backend (cada endpoint chequea x-session-token
 * vía verify_session), es defensa en profundidad para que la UI no muestre
 * nada de la app a un visitante anónimo.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  if (loading) {
    return (
      <div className="h-screen flex items-center justify-center bg-bg text-fg-dim text-sm">
        Cargando…
      </div>
    );
  }

  if (!user) {
    // Se va a redirigir en el effect de arriba; mientras no renderizamos nada
    // sensible.
    return null;
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <Rail />
      <main className="flex-1 flex overflow-hidden">{children}</main>
    </div>
  );
}
