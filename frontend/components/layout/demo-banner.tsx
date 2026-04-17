"use client";

import { useState } from "react";
import { AlertTriangle, X } from "lucide-react";

export function DemoBanner() {
  const [closed, setClosed] = useState(false);
  if (closed) return null;
  return (
    <div className="bg-warn/15 border-b border-warn/30 px-4 py-1.5 flex items-center gap-2 text-[11px] text-warn">
      <AlertTriangle className="w-3 h-3 shrink-0" />
      <span>
        <strong>Modo demo</strong> · Toda la data es mock local. Asignar / clasificar / cobranzas
        no persisten en backend (recargar = reset).
      </span>
      <button
        onClick={() => setClosed(true)}
        className="ml-auto p-0.5 hover:bg-warn/20 rounded"
        title="Cerrar"
      >
        <X className="w-3 h-3" />
      </button>
    </div>
  );
}
