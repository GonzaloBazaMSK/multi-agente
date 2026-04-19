"use client";

/**
 * /settings/queues — Colas de atención.
 *
 * Hoy las colas están hardcoded en `api/auth.py:ALL_QUEUES` (18
 * combinaciones: {ventas, cobranzas, post_venta} × {AR, MX, CL, CO, EC,
 * MP, UY, PE}). Por cada cola listamos los agentes que la tienen en su
 * `profiles.queues` y permitimos agregar/quitar con un click.
 *
 * El modelo es "el agente suscribe a colas" (1:N), no "la cola tiene
 * agentes asignados". El CRUD real pasa por PATCH /auth/users/{id}
 * — esta pantalla es una vista pivoteada.
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ListTree, Loader2, Plus, ShoppingCart, X, Wallet, Headphones } from "lucide-react";

import { NoAccess } from "@/components/ui/coming-soon";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { RoleGate, useRole } from "@/lib/auth";
import { cn } from "@/lib/utils";

type Profile = {
  id: string;
  email: string;
  name: string | null;
  role: "admin" | "supervisor" | "agente";
  queues: string[];
};

// Mapeo de prefijo → info de presentación (icono + label + color).
// Las colas reales son tipo "ventas_AR" — acá la parte antes del "_".
const QUEUE_GROUPS = [
  { prefix: "ventas",     label: "Ventas",       icon: ShoppingCart, color: "text-info" },
  { prefix: "cobranzas",  label: "Cobranzas",    icon: Wallet,       color: "text-warn" },
  { prefix: "post_venta", label: "Post-venta",   icon: Headphones,   color: "text-success" },
] as const;

// Flag 🇦🇷 style — reusamos el mapeo de países del proyecto
const COUNTRY_NAMES: Record<string, string> = {
  AR: "Argentina",
  MX: "México",
  CL: "Chile",
  CO: "Colombia",
  EC: "Ecuador",
  UY: "Uruguay",
  PE: "Perú",
  MP: "Multi-país",
};

export default function SettingsQueuesPage() {
  return (
    <RoleGate min="supervisor" denyFallback={<NoAccess requiredRole="supervisor o admin" />}>
      <Inner />
    </RoleGate>
  );
}

function Inner() {
  const qc = useQueryClient();
  const { isAdmin, isSupervisor } = useRole();
  const canEdit = isSupervisor; // supervisor edita queues (backend enforce), admin también

  const usersQ = useQuery<Profile[]>({
    queryKey: ["auth", "users"],
    queryFn: () => api.get("/auth/users"),
    staleTime: 30_000,
  });
  const queuesQ = useQuery<string[]>({
    queryKey: ["auth", "queues"],
    queryFn: () => api.get("/auth/queues"),
    staleTime: 5 * 60_000,
  });

  const updateUser = useMutation({
    mutationFn: ({ id, queues }: { id: string; queues: string[] }) =>
      api.patch<Profile>(`/auth/users/${id}`, { queues }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["auth", "users"] });
    },
    onError: (e: Error) => alert(e.message),
  });

  // Índice: para cada queue_name ("ventas_AR"), qué users la tienen.
  const byQueue = useMemo(() => {
    const idx: Record<string, Profile[]> = {};
    for (const q of queuesQ.data ?? []) idx[q] = [];
    for (const u of usersQ.data ?? []) {
      for (const q of u.queues ?? []) {
        if (idx[q]) idx[q].push(u);
      }
    }
    return idx;
  }, [usersQ.data, queuesQ.data]);

  // Agrupar colas por prefijo (ventas_*, cobranzas_*, post_venta_*)
  const grouped = useMemo(() => {
    const out: Record<string, string[]> = {};
    for (const g of QUEUE_GROUPS) out[g.prefix] = [];
    for (const q of queuesQ.data ?? []) {
      const prefix = q.split("_")[0] === "post" ? "post_venta" : q.split("_")[0];
      if (out[prefix]) out[prefix].push(q);
    }
    return out;
  }, [queuesQ.data]);

  const loading = usersQ.isLoading || queuesQ.isLoading;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 py-4 border-b border-border">
        <h1 className="text-lg font-semibold">Colas de atención</h1>
        <p className="text-xs text-fg-dim mt-0.5">
          Qué agentes atienden cada cola. Un agente puede estar en varias colas; el round-robin
          de asignación automática solo le manda conversaciones de las que tiene marcadas.
        </p>
      </div>

      <div className="flex-1 overflow-y-auto scroll-thin p-6 space-y-6 max-w-4xl">
        {loading && (
          <div className="text-xs text-fg-dim flex items-center gap-2">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Cargando colas y agentes…
          </div>
        )}

        {!loading &&
          QUEUE_GROUPS.map((group) => {
            const queuesInGroup = grouped[group.prefix] || [];
            if (queuesInGroup.length === 0) return null;
            const Icon = group.icon;
            return (
              <section key={group.prefix}>
                <div className="flex items-center gap-2 mb-3">
                  <Icon className={cn("w-4 h-4", group.color)} />
                  <h2 className="text-sm font-semibold">{group.label}</h2>
                  <span className="text-[10px] text-fg-dim">
                    {queuesInGroup.length} colas
                  </span>
                </div>

                <div className="space-y-2">
                  {queuesInGroup.map((queueName) => {
                    const country = queueName.split("_").slice(-1)[0];
                    const agents = byQueue[queueName] || [];
                    return (
                      <QueueRow
                        key={queueName}
                        queueName={queueName}
                        countryName={COUNTRY_NAMES[country] || country}
                        agents={agents}
                        allUsers={usersQ.data ?? []}
                        canEdit={canEdit}
                        onToggleUser={(user, nowHas) => {
                          const nextQueues = nowHas
                            ? [...(user.queues || []), queueName]
                            : (user.queues || []).filter((x) => x !== queueName);
                          updateUser.mutate({ id: user.id, queues: nextQueues });
                        }}
                        saving={updateUser.isPending}
                      />
                    );
                  })}
                </div>
              </section>
            );
          })}

        {!isAdmin && !isSupervisor && (
          <div className="text-xs text-fg-dim bg-card border border-border rounded p-3">
            Necesitás rol supervisor+ para modificar asignaciones de cola.
          </div>
        )}
      </div>
    </div>
  );
}

function QueueRow({
  queueName,
  countryName,
  agents,
  allUsers,
  canEdit,
  onToggleUser,
  saving,
}: {
  queueName: string;
  countryName: string;
  agents: Profile[];
  allUsers: Profile[];
  canEdit: boolean;
  onToggleUser: (u: Profile, nowHas: boolean) => void;
  saving: boolean;
}) {
  const [expanded, setExpanded] = useState(false);

  // Candidatos para agregar: users que NO están en esta queue
  const candidates = allUsers.filter(
    (u) => u.role === "agente" || u.role === "supervisor" || u.role === "admin",
  );
  const notYetInQueue = candidates.filter((u) => !agents.some((a) => a.id === u.id));

  return (
    <div className="bg-card border border-border rounded-lg">
      <div className="flex items-center gap-3 p-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm">{queueName}</span>
            <span className="text-[10px] text-fg-dim">· {countryName}</span>
          </div>
          <div className="flex gap-1.5 flex-wrap mt-1.5 min-h-[20px]">
            {agents.length === 0 && (
              <span className="text-[11px] text-fg-dim italic">Sin agentes asignados</span>
            )}
            {agents.map((u) => (
              <span
                key={u.id}
                className="inline-flex items-center gap-1 text-[11px] bg-accent/10 text-accent px-1.5 py-0.5 rounded"
              >
                {u.name || u.email}
                {canEdit && (
                  <button
                    type="button"
                    onClick={() => onToggleUser(u, false)}
                    disabled={saving}
                    className="hover:text-danger"
                    title="Quitar de la cola"
                  >
                    <X className="w-3 h-3" />
                  </button>
                )}
              </span>
            ))}
          </div>
        </div>
        {canEdit && notYetInQueue.length > 0 && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setExpanded((v) => !v)}
          >
            <Plus className="w-3.5 h-3.5" />
            Agregar
          </Button>
        )}
      </div>

      {expanded && canEdit && (
        <div className="border-t border-border p-3 bg-bg/50">
          <div className="text-[10px] text-fg-dim uppercase mb-2">
            Agregar agente a esta cola
          </div>
          <div className="flex flex-wrap gap-1">
            {notYetInQueue.map((u) => (
              <button
                key={u.id}
                type="button"
                onClick={() => {
                  onToggleUser(u, true);
                  // Opcional: cerrar el expander si ya agregamos todos los disponibles
                }}
                disabled={saving}
                className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded border border-border bg-bg hover:bg-hover hover:border-accent hover:text-accent transition-colors"
              >
                <Check className="w-3 h-3" />
                {u.name || u.email}
                <span
                  className={cn(
                    "text-[9px] px-1 py-px rounded",
                    u.role === "admin"
                      ? "bg-danger/15 text-danger"
                      : u.role === "supervisor"
                      ? "bg-warn/15 text-warn"
                      : "bg-info/15 text-info",
                  )}
                >
                  {u.role}
                </span>
              </button>
            ))}
            {notYetInQueue.length === 0 && (
              <span className="text-[11px] text-fg-dim italic">
                Todos los agentes ya están en esta cola
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
