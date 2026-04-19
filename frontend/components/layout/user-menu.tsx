"use client";

/**
 * Dropdown del avatar en el rail — reemplaza el botón de logout directo.
 *
 * Contenido:
 *   - Header con avatar + nombre + rol + email.
 *   - Selector de estado de disponibilidad (Disponible / Break / Almuerzo /
 *     Ausente / Ocupado). Persiste en Redis via /api/v1/auth/agent-status;
 *     afecta el round-robin de asignación automática (solo "available"
 *     recibe convs nuevas).
 *   - Link a Configuración (/settings).
 *   - Cerrar sesión.
 *
 * Estilo inspirado en Botmaker (ver screenshots del usuario): cabecera
 * con avatar grande, separador "Recibe conversaciones / No recibe", lista
 * vertical con íconos coloreados por estado.
 */
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Circle, Coffee, Briefcase, Moon, Ban, Settings, LogOut, Loader2 } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { cn, initials } from "@/lib/utils";

type AgentStatus = "available" | "busy" | "away";

// Las etiquetas que vemos en la UI son más granulares que los 3 valores
// que el backend acepta hoy (`available`/`busy`/`away`). Mapeamos cada UI
// label al status backend correspondiente. Si mañana querés trackear
// "break" vs "almuerzo" como métrica separada, hay que agregar un campo
// `reason` al endpoint y guardar ambos.
type StatusOption = {
  key: "available" | "break" | "lunch" | "away" | "busy";
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  colorClass: string;
  backend: AgentStatus;
  receivesConvs: boolean;
};

const STATUSES: StatusOption[] = [
  {
    key: "available",
    label: "En línea",
    icon: Circle,
    colorClass: "text-success fill-success",
    backend: "available",
    receivesConvs: true,
  },
  { key: "break",  label: "Break",    icon: Coffee,    colorClass: "text-warn",   backend: "away", receivesConvs: false },
  { key: "lunch",  label: "Almuerzo", icon: Briefcase, colorClass: "text-warn",   backend: "away", receivesConvs: false },
  { key: "away",   label: "Ausente",  icon: Moon,      colorClass: "text-fg-dim", backend: "away", receivesConvs: false },
  { key: "busy",   label: "Ocupado",  icon: Ban,       colorClass: "text-danger", backend: "busy", receivesConvs: false },
];

export function UserMenu() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<string>("available");
  const [saving, setSaving] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  // Carga el estado actual al montar
  useEffect(() => {
    if (!user) return;
    api
      .get<{ status: string }>("/auth/agent-status")
      .then((r) => {
        // Mapeo inverso: si backend dice "away", UI muestra "away" por default
        // (el usuario puede haber estado en break o almuerzo pero el backend
        // solo persiste el bucket). Priorizamos mostrar el último elegido
        // si lo tenemos en localStorage.
        const uiKey = typeof window !== "undefined" ? localStorage.getItem("msk_status_ui") : null;
        if (uiKey && STATUSES.find((s) => s.key === uiKey && s.backend === r.status)) {
          setStatus(uiKey);
        } else {
          setStatus(r.status === "busy" ? "busy" : r.status === "away" ? "away" : "available");
        }
      })
      .catch(() => {});
  }, [user]);

  // Cerrar al clickear afuera
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const setAgentStatus = async (opt: StatusOption) => {
    setStatus(opt.key);
    if (typeof window !== "undefined") localStorage.setItem("msk_status_ui", opt.key);
    setSaving(true);
    try {
      await api.post("/auth/agent-status", { status: opt.backend });
    } catch {
      // silent — next mount reload desde el server
    } finally {
      setSaving(false);
    }
  };

  if (!user) return null;

  const current = STATUSES.find((s) => s.key === status) || STATUSES[0];
  const avatarInitials = initials(user.name);
  const receivesByDefault = STATUSES.filter((s) => s.receivesConvs);
  const notReceiving = STATUSES.filter((s) => !s.receivesConvs);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "rail-tooltip-wrap w-9 h-9 rounded-full text-white text-xs font-bold flex items-center justify-center cursor-pointer transition-opacity hover:opacity-90 relative",
          "bg-gradient-to-br from-pink-500 to-fuchsia-600",
        )}
        data-tooltip={`${user.name} · ${user.email}`}
        aria-label="Abrir menú de usuario"
      >
        {avatarInitials}
        {/* Dot de estado siempre visible */}
        <span
          className={cn(
            "absolute bottom-0 right-0 w-3 h-3 rounded-full ring-2 ring-panel",
            status === "available" && "bg-success",
            status === "busy" && "bg-danger",
            (status === "break" || status === "lunch") && "bg-warn",
            status === "away" && "bg-fg-dim",
          )}
        />
      </button>

      {open && (
        <div className="absolute bottom-0 left-12 w-72 bg-panel border border-border rounded-lg shadow-lg z-50 py-1">
          {/* Header */}
          <div className="px-3 pt-3 pb-2 flex items-center gap-3 border-b border-border">
            <div className="w-10 h-10 rounded-full bg-gradient-to-br from-pink-500 to-fuchsia-600 text-white text-sm font-bold flex items-center justify-center">
              {avatarInitials}
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold truncate">{user.name}</div>
              <div className="text-[11px] text-fg-dim capitalize">{user.role}</div>
              <div className="text-[11px] text-fg-dim truncate">{user.email}</div>
            </div>
          </div>

          {/* Estado */}
          <div className="py-1">
            <div className="px-3 py-1 text-[10px] text-fg-dim uppercase tracking-wide">
              Estado
              {saving && <Loader2 className="inline w-2.5 h-2.5 animate-spin ml-1" />}
            </div>
            <div className="px-2 pb-1 text-[10px] text-fg-dim">Recibe conversaciones</div>
            {receivesByDefault.map((opt) => {
              const Icon = opt.icon;
              const active = status === opt.key;
              return (
                <button
                  key={opt.key}
                  onClick={() => setAgentStatus(opt)}
                  className={cn(
                    "w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors text-left",
                    active ? "bg-accent/15" : "hover:bg-hover",
                  )}
                >
                  <Icon className={cn("w-3.5 h-3.5", opt.colorClass)} />
                  <span className="flex-1">{opt.label}</span>
                  {active && <span className="text-[9px] text-accent">actual</span>}
                </button>
              );
            })}
            <div className="px-2 pt-2 pb-1 text-[10px] text-fg-dim">No recibe conversaciones</div>
            {notReceiving.map((opt) => {
              const Icon = opt.icon;
              const active = status === opt.key;
              return (
                <button
                  key={opt.key}
                  onClick={() => setAgentStatus(opt)}
                  className={cn(
                    "w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors text-left",
                    active ? "bg-accent/15" : "hover:bg-hover",
                  )}
                >
                  <Icon className={cn("w-3.5 h-3.5", opt.colorClass)} />
                  <span className="flex-1">{opt.label}</span>
                  {active && <span className="text-[9px] text-accent">actual</span>}
                </button>
              );
            })}
          </div>

          <div className="border-t border-border py-1">
            <Link
              href="/settings"
              onClick={() => setOpen(false)}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-fg-muted hover:bg-hover hover:text-fg transition-colors"
            >
              <Settings className="w-3.5 h-3.5" />
              <span>Configuración</span>
            </Link>
            <button
              onClick={() => {
                setOpen(false);
                logout();
              }}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-danger hover:bg-danger/10 transition-colors"
            >
              <LogOut className="w-3.5 h-3.5" />
              <span>Cerrar sesión</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
