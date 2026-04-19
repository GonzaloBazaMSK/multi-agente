"use client";

/**
 * /settings/audit — log de acciones humanas del inbox.
 *
 * Muestra `inbox_audit_log` (populado por `log_action` en utils/inbox_jobs.py).
 * Cada fila = una acción manual: asignar, cambiar estado, pausar bot, enviar
 * plantilla, etc. Útil para:
 *   - Debug "quién resolvió esta conversación"
 *   - Compliance "quién pausó el bot del cliente X"
 *   - Detectar patrones (un agente asigna todo a sí mismo, etc.)
 *
 * Backend: GET /api/v1/inbox/audit-log?limit=&actor_id=&conversation_id=
 *
 * Deliberadamente simple — no paginación ni búsqueda fuzzy. Si el equipo
 * crece y esto se vuelve útil, se refactorea con TanStack Table +
 * virtualización. Hoy con 100 filas es suficiente.
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import {
  Loader2,
  History,
  UserCircle2,
  ArrowRight,
  Filter,
  RefreshCw,
} from "lucide-react";

import { NoAccess } from "@/components/ui/coming-soon";
import { RoleGate } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type AuditEntry = {
  id: string;
  actor_id: string | null;
  action: string;
  conversation_id: string | null;
  detail: Record<string, unknown>;
  created_at: string;
};

type Profile = {
  id: string;
  email: string;
  name: string | null;
  role: string;
};

// Labels legibles para las acciones más comunes. Si no está en este mapa,
// mostramos el raw action con formato "snake_case" como fallback.
const ACTION_LABELS: Record<string, { label: string; color: string }> = {
  assign: { label: "Asignó conversación", color: "text-info" },
  unassign: { label: "Liberó conversación", color: "text-fg-muted" },
  status: { label: "Cambió estado", color: "text-warn" },
  classify: { label: "Clasificó", color: "text-info" },
  bot_pause: { label: "Pausó bot", color: "text-warn" },
  bot_resume: { label: "Reanudó bot", color: "text-success" },
  template_send: { label: "Envió plantilla HSM", color: "text-info" },
  reply: { label: "Respondió manualmente", color: "text-fg" },
  snooze: { label: "Pospuso conversación", color: "text-fg-muted" },
  bulk_assign: { label: "Asignación masiva", color: "text-info" },
  bulk_status: { label: "Cambio masivo de estado", color: "text-warn" },
};

function formatAction(a: string) {
  return ACTION_LABELS[a] || { label: a.replace(/_/g, " "), color: "text-fg-muted" };
}

function formatRelative(iso: string) {
  const d = new Date(iso);
  const diffSec = Math.round((Date.now() - d.getTime()) / 1000);
  if (diffSec < 60) return "hace unos segundos";
  if (diffSec < 3600) return `hace ${Math.round(diffSec / 60)}m`;
  if (diffSec < 86400) return `hace ${Math.round(diffSec / 3600)}h`;
  return d.toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" });
}

export default function AuditPage() {
  return (
    <RoleGate min="admin" denyFallback={<NoAccess requiredRole="admin" />}>
      <Inner />
    </RoleGate>
  );
}

function Inner() {
  const [actorFilter, setActorFilter] = useState<string>("");
  const [actionFilter, setActionFilter] = useState<string>("");

  // Todos los users — para mapear actor_id → nombre legible
  const usersQ = useQuery<Profile[]>({
    queryKey: ["auth", "users"],
    queryFn: () => api.get("/auth/users"),
    staleTime: 60_000,
  });

  const userById = useMemo(() => {
    const idx = new Map<string, Profile>();
    for (const u of usersQ.data ?? []) idx.set(u.id, u);
    return idx;
  }, [usersQ.data]);

  const logQ = useQuery<AuditEntry[]>({
    queryKey: ["inbox", "audit-log", actorFilter],
    queryFn: () => {
      const params = new URLSearchParams({ limit: "100" });
      if (actorFilter) params.set("actor_id", actorFilter);
      return api.get(`/inbox/audit-log?${params.toString()}`);
    },
    staleTime: 10_000,
  });

  const filtered = useMemo(() => {
    if (!logQ.data) return [];
    if (!actionFilter) return logQ.data;
    return logQ.data.filter((e) => e.action === actionFilter);
  }, [logQ.data, actionFilter]);

  // Opciones de acción únicas detectadas en el log cargado
  const actionOptions = useMemo(() => {
    const s = new Set<string>();
    for (const e of logQ.data ?? []) s.add(e.action);
    return Array.from(s).sort();
  }, [logQ.data]);

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 py-4 border-b border-border flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Historial de cambios</h1>
          <p className="text-xs text-fg-dim mt-0.5">
            Últimas 100 acciones manuales del inbox — asignaciones, cambios de estado,
            pausas de bot.
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => logQ.refetch()}
          disabled={logQ.isFetching}
        >
          <RefreshCw
            className={cn("w-3.5 h-3.5", logQ.isFetching && "animate-spin")}
          />
          Refrescar
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto scroll-thin p-6 max-w-4xl">
        {/* Filtros */}
        <div className="flex items-center gap-2 mb-4">
          <Filter className="w-3.5 h-3.5 text-fg-dim" />
          <select
            value={actorFilter}
            onChange={(e) => setActorFilter(e.target.value)}
            className="h-7 px-2 bg-bg border border-border rounded text-xs focus:outline-none focus:ring-1 focus:ring-accent"
          >
            <option value="">Todos los usuarios</option>
            {(usersQ.data ?? []).map((u) => (
              <option key={u.id} value={u.id}>
                {u.name || u.email}
              </option>
            ))}
          </select>
          <select
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
            className="h-7 px-2 bg-bg border border-border rounded text-xs focus:outline-none focus:ring-1 focus:ring-accent"
          >
            <option value="">Todas las acciones</option>
            {actionOptions.map((a) => (
              <option key={a} value={a}>
                {formatAction(a).label}
              </option>
            ))}
          </select>
          {(actorFilter || actionFilter) && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setActorFilter("");
                setActionFilter("");
              }}
            >
              Limpiar
            </Button>
          )}
          <span className="text-[11px] text-fg-dim ml-auto">
            {filtered.length} {filtered.length === 1 ? "entrada" : "entradas"}
          </span>
        </div>

        {logQ.isLoading && (
          <div className="text-xs text-fg-dim flex items-center gap-2">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Cargando historial…
          </div>
        )}
        {logQ.error && (
          <div className="text-xs text-danger">
            No se pudo cargar el historial. {(logQ.error as Error).message}
          </div>
        )}

        {!logQ.isLoading && filtered.length === 0 && (
          <div className="text-center py-12 text-fg-dim">
            <History className="w-8 h-8 mx-auto mb-2 opacity-50" />
            <p className="text-sm">Sin registros</p>
            <p className="text-[11px] mt-1">
              {actorFilter || actionFilter
                ? "Probá sacar los filtros."
                : "El log se genera cuando hay acciones manuales en el inbox."}
            </p>
          </div>
        )}

        <div className="space-y-1">
          {filtered.map((entry) => {
            const actor = entry.actor_id ? userById.get(entry.actor_id) : null;
            const action = formatAction(entry.action);
            return (
              <div
                key={entry.id}
                className="bg-card border border-border rounded-lg p-3 flex items-start gap-3"
              >
                <div className="w-7 h-7 rounded-full bg-gradient-to-br from-pink-500 to-fuchsia-600 text-white text-[10px] font-bold flex items-center justify-center shrink-0">
                  {actor ? (
                    (actor.name || actor.email).slice(0, 2).toUpperCase()
                  ) : (
                    <UserCircle2 className="w-3.5 h-3.5" />
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-xs flex flex-wrap items-center gap-x-2 gap-y-0.5">
                    <span className="font-medium">
                      {actor?.name || actor?.email || "Sistema"}
                    </span>
                    <span className={cn("font-medium", action.color)}>
                      {action.label}
                    </span>
                    {entry.conversation_id && (
                      <Link
                        href={`/inbox?c=${entry.conversation_id}`}
                        className="text-[11px] text-accent hover:underline inline-flex items-center gap-0.5"
                      >
                        ver conversación
                        <ArrowRight className="w-3 h-3" />
                      </Link>
                    )}
                  </div>
                  {Object.keys(entry.detail || {}).length > 0 && (
                    <div className="text-[11px] text-fg-dim mt-0.5 font-mono break-all">
                      {formatDetail(entry.detail)}
                    </div>
                  )}
                </div>
                <div className="text-[10px] text-fg-dim shrink-0 whitespace-nowrap">
                  {formatRelative(entry.created_at)}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// Detail es un JSON libre — renderizamos inline las claves cortas. Si es muy
// largo, truncamos.
function formatDetail(detail: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(detail)) {
    const val = typeof v === "string" ? v : JSON.stringify(v);
    parts.push(`${k}=${val.length > 40 ? val.slice(0, 40) + "…" : val}`);
  }
  return parts.join(" · ");
}
