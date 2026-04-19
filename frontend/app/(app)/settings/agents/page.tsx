"use client";

/**
 * /settings/agents — CRUD del equipo humano (agentes, supervisores, admins).
 *
 * NO confundir con /agents (info de los agentes IA — bots). Acá son
 * personas que se loguean a la consola para atender conversaciones.
 *
 * Permisos:
 *   - supervisor: lista + edita colas de otros users (PATCH)
 *   - admin: todo lo anterior + crear + borrar
 */

import { AlertCircle, Loader2, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { NoAccess } from "@/components/ui/coming-soon";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { RoleGate, useRole } from "@/lib/auth";

const ROLES = [
  { value: "agente", label: "Agente" },
  { value: "supervisor", label: "Supervisor" },
  { value: "admin", label: "Admin" },
] as const;

type Profile = {
  id: string;
  email: string;
  name: string | null;
  role: "admin" | "supervisor" | "agente";
  queues: string[];
  created_at?: string;
};

const EMPTY_DRAFT = {
  email: "",
  password: "",
  name: "",
  role: "agente" as Profile["role"],
  queues: [] as string[],
};

export default function SettingsAgentsPage() {
  return (
    <RoleGate
      min="supervisor"
      denyFallback={<NoAccess requiredRole="supervisor o admin" />}
    >
      <Inner />
    </RoleGate>
  );
}

function Inner() {
  const qc = useQueryClient();
  const { user: me, isAdmin } = useRole();

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

  const [showForm, setShowForm] = useState(false);
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => api.post("/auth/users", draft),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["auth", "users"] });
      qc.invalidateQueries({ queryKey: ["inbox", "agents"] });
      setShowForm(false);
      setDraft(EMPTY_DRAFT);
      setError(null);
    },
    onError: (e: Error) => setError(e.message || "Error al crear usuario"),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/auth/users/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["auth", "users"] });
      qc.invalidateQueries({ queryKey: ["inbox", "agents"] });
    },
  });

  const canCreate = isAdmin && draft.email && draft.password && draft.name;

  const toggleQueue = (q: string) =>
    setDraft((d) => ({
      ...d,
      queues: d.queues.includes(q)
        ? d.queues.filter((x) => x !== q)
        : [...d.queues, q],
    }));

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 py-4 border-b border-border flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Agentes humanos</h1>
          <p className="text-xs text-fg-dim mt-0.5">
            Personas del equipo con acceso al sistema — crear uno lo agrega a
            Supabase Auth y aparece en el dropdown de "Asignar a" del inbox.
          </p>
        </div>
        {isAdmin && (
          <Button
            size="sm"
            onClick={() => {
              setShowForm(true);
              setError(null);
            }}
          >
            <Plus className="w-3.5 h-3.5" /> Nuevo agente
          </Button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto scroll-thin p-6 space-y-4 max-w-3xl">
        {showForm && isAdmin && (
          <div className="bg-card border border-border rounded-lg p-4 space-y-3">
            {error && (
              <div className="bg-danger/10 border border-danger/30 rounded px-3 py-2 text-[11px] text-danger flex items-start gap-2">
                <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                <span className="break-all">{error}</span>
              </div>
            )}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-[10px] text-fg-muted uppercase">
                  Nombre completo
                </label>
                <Input
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  placeholder="Nombre Apellido"
                />
              </div>
              <div>
                <label className="text-[10px] text-fg-muted uppercase">Email</label>
                <Input
                  type="email"
                  value={draft.email}
                  onChange={(e) => setDraft({ ...draft, email: e.target.value })}
                  placeholder="agente@msklatam.com"
                />
              </div>
              <div>
                <label className="text-[10px] text-fg-muted uppercase">
                  Password inicial
                </label>
                <Input
                  type="password"
                  value={draft.password}
                  onChange={(e) =>
                    setDraft({ ...draft, password: e.target.value })
                  }
                  placeholder="mínimo 8 caracteres"
                />
              </div>
              <div>
                <label className="text-[10px] text-fg-muted uppercase">Rol</label>
                <select
                  value={draft.role}
                  onChange={(e) =>
                    setDraft({ ...draft, role: e.target.value as Profile["role"] })
                  }
                  className="w-full h-8 px-2 bg-bg border border-border rounded text-sm focus:outline-none focus:ring-1 focus:ring-accent"
                >
                  {ROLES.map((r) => (
                    <option key={r.value} value={r.value}>
                      {r.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div>
              <label className="text-[10px] text-fg-muted uppercase mb-1.5 block">
                Colas asignadas
                {draft.queues.length > 0 && (
                  <span className="text-fg-dim normal-case ml-2">
                    ({draft.queues.length} seleccionadas)
                  </span>
                )}
              </label>
              <div className="flex flex-wrap gap-1">
                {(queuesQ.data ?? []).map((q) => {
                  const sel = draft.queues.includes(q);
                  return (
                    <button
                      key={q}
                      type="button"
                      onClick={() => toggleQueue(q)}
                      className={`text-[11px] px-2 py-0.5 rounded border transition-colors ${
                        sel
                          ? "bg-accent/15 border-accent text-accent"
                          : "bg-bg border-border text-fg-muted hover:text-fg"
                      }`}
                    >
                      {q}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2 border-t border-border">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setShowForm(false);
                  setError(null);
                }}
              >
                Cancelar
              </Button>
              <Button
                size="sm"
                onClick={() => create.mutate()}
                disabled={create.isPending || !canCreate}
              >
                {create.isPending ? (
                  <Loader2 className="w-3 h-3 animate-spin" />
                ) : (
                  "Crear"
                )}
              </Button>
            </div>
          </div>
        )}

        <div className="space-y-1.5">
          {usersQ.isLoading && (
            <div className="text-xs text-fg-dim">Cargando equipo…</div>
          )}
          {usersQ.error && (
            <div className="text-xs text-danger">
              No se pudo cargar el equipo. {isAdmin ? "" : "Necesitás rol supervisor+."}
            </div>
          )}
          {usersQ.data?.map((u) => {
            const isMe = me?.id === u.id;
            return (
              <div
                key={u.id}
                className="bg-card border border-border rounded-lg p-3 flex items-center gap-3"
              >
                <div className="w-9 h-9 rounded-full bg-gradient-to-br from-pink-500 to-fuchsia-600 text-white text-xs font-bold flex items-center justify-center">
                  {(u.name || u.email).slice(0, 2).toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium flex items-center gap-2">
                    {u.name || u.email}
                    {isMe && <span className="text-[10px] text-accent">(vos)</span>}
                    <span
                      className={`text-[9px] px-1.5 py-0.5 rounded ${
                        u.role === "admin"
                          ? "bg-danger/15 text-danger"
                          : u.role === "supervisor"
                          ? "bg-warn/15 text-warn"
                          : "bg-info/15 text-info"
                      }`}
                    >
                      {u.role}
                    </span>
                  </div>
                  <div className="text-[11px] text-fg-dim truncate">
                    {u.email}
                    {u.queues?.length > 0 && (
                      <span className="ml-2">
                        · {u.queues.length} cola
                        {u.queues.length === 1 ? "" : "s"}
                      </span>
                    )}
                  </div>
                </div>
                {isAdmin && !isMe && (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => {
                      if (
                        confirm(
                          `Borrar a ${u.name || u.email}? Las conversaciones que tenga asignadas quedarán libres.`,
                        )
                      ) {
                        remove.mutate(u.id);
                      }
                    }}
                    disabled={remove.isPending}
                    title="Borrar agente"
                  >
                    <Trash2 className="w-3.5 h-3.5 text-danger" />
                  </Button>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
