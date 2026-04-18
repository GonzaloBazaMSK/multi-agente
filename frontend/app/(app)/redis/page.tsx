"use client";

/**
 * /redis — visor y gestor de claves Redis.
 *
 * Paridad con widget/redis.html: stats, pattern search, list/inspect/delete
 * de claves, delete-pattern, flush-conversations, nuclear-reset.
 *
 * Todo admin-only (backend chequea require_role("admin") en cada endpoint).
 * Las acciones destructivas tienen doble confirmación — la vieja usaba
 * `confirm()`+`prompt()`, la mantenemos porque es el punto de fricción
 * deseado (nadie quiere borrar 5000 claves por accidente).
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Database,
  HardDrive,
  Loader2,
  RefreshCw,
  Search,
  Trash2,
  Users,
} from "lucide-react";
import { RoleGate } from "@/lib/auth";
import { NoAccess } from "@/components/ui/coming-soon";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type RedisKey = {
  key: string;
  type: string;
  ttl: number;
  size: number;
  preview: string;
};

type RedisStats = {
  dbsize: number;
  used_memory_human: string;
  connected_clients: number;
  uptime_in_seconds: number;
  redis_version: string;
  keyspace: Record<string, unknown>;
};

type KeyValue = {
  key: string;
  type: string;
  ttl: number;
  value: string;
};

// Chips para patrones más comunes — paridad con los de redis.html.
const QUICK_PATTERNS = [
  "conv:*",
  "zoho_cache:*",
  "zoho_cursadas:*",
  "bot_disabled:*",
  "conv_label:*",
  "session:*",
  "flow:*",
  "*",
];

const TYPE_COLOR: Record<string, string> = {
  string: "bg-success/15 text-success",
  list:   "bg-info/15 text-info",
  set:    "bg-accent/15 text-accent",
  hash:   "bg-warn/15 text-warn",
};

function formatTTL(ttl: number): string {
  if (ttl === -1) return "∞";
  if (ttl === -2) return "expirada";
  if (ttl < 60) return `${ttl}s`;
  if (ttl < 3600) return `${Math.round(ttl / 60)}m`;
  if (ttl < 86400) return `${Math.round(ttl / 3600)}h`;
  return `${Math.round(ttl / 86400)}d`;
}

export default function RedisPage() {
  return (
    <RoleGate min="admin" denyFallback={<NoAccess requiredRole="admin" />}>
      <RedisPageInner />
    </RoleGate>
  );
}

function RedisPageInner() {
  const qc = useQueryClient();
  const [pattern, setPattern] = useState("*");
  const [submitted, setSubmitted] = useState("*");
  const [selected, setSelected] = useState<string | null>(null);

  const statsQ = useQuery<RedisStats>({
    queryKey: ["redis", "stats"],
    queryFn: () => api.get("/admin/redis/stats"),
    refetchInterval: 30_000,
  });

  const keysQ = useQuery<{ keys: RedisKey[]; total: number }>({
    queryKey: ["redis", "keys", submitted],
    queryFn: () =>
      api.get(`/admin/redis/keys?pattern=${encodeURIComponent(submitted)}&limit=300`),
    staleTime: 10_000,
  });

  const valueQ = useQuery<KeyValue>({
    queryKey: ["redis", "key", selected],
    queryFn: () => api.get(`/admin/redis/key?key=${encodeURIComponent(selected!)}`),
    enabled: !!selected,
    staleTime: 10_000,
  });

  const deleteKey = useMutation({
    mutationFn: (key: string) =>
      api.delete(`/admin/redis/key?key=${encodeURIComponent(key)}`),
    onSuccess: (_, key) => {
      qc.invalidateQueries({ queryKey: ["redis"] });
      if (selected === key) setSelected(null);
    },
    onError: (e: Error) => alert("Error: " + e.message),
  });

  const deletePattern = useMutation({
    mutationFn: (p: string) =>
      api.post<{ deleted: number }>("/admin/redis/delete-pattern", { pattern: p }),
    onSuccess: (r) => {
      alert(`${r.deleted} claves eliminadas.`);
      qc.invalidateQueries({ queryKey: ["redis"] });
      setSelected(null);
    },
    onError: (e: Error) => alert("Error: " + e.message),
  });

  const flushConvs = useMutation({
    mutationFn: () => api.post<{ message: string }>("/admin/redis/flush-conversations", {}),
    onSuccess: (r) => {
      alert(r.message);
      qc.invalidateQueries({ queryKey: ["redis"] });
      setSelected(null);
    },
    onError: (e: Error) => alert("Error: " + e.message),
  });

  const nukeAll = useMutation({
    mutationFn: () => api.post<{ message: string }>("/admin/redis/nuclear-reset", {}),
    onSuccess: (r) => {
      alert(r.message);
      qc.invalidateQueries({ queryKey: ["redis"] });
      setSelected(null);
    },
    onError: (e: Error) => alert("Error: " + e.message),
  });

  const handleSearch = () => setSubmitted(pattern.trim() || "*");

  const handleDeletePattern = () => {
    const p = prompt(
      "Patrón a eliminar (ej: zoho_cache:*). NO permitimos '*' solo ni patrones que afecten session/flow/widget:config.",
    );
    if (!p) return;
    if (!confirm(`Eliminar TODAS las claves que matchean "${p}"? No se puede deshacer.`)) return;
    deletePattern.mutate(p);
  };

  const handleFlushConvs = () => {
    if (
      !confirm(
        "⚠️ Esto borra TODAS las conversaciones y caches del Redis.\n\n" +
          "Se conservan: widget:config, auth sessions, flows, templates.\n\n" +
          "¿Seguro?",
      )
    )
      return;
    if (!confirm("Última confirmación — ¿procedemos?")) return;
    flushConvs.mutate();
  };

  const handleNuke = () => {
    if (
      !confirm(
        "☢️ RESET NUCLEAR: borra Redis conversations + Postgres conversations/messages + Supabase customers + auth users (excepto perfiles de agentes).\n\n" +
          "¿Seguro?",
      )
    )
      return;
    const input = prompt("Escribí literalmente: RESET NUCLEAR");
    if (input !== "RESET NUCLEAR") return alert("Cancelado (no coincide el texto).");
    nukeAll.mutate();
  };

  const selectedValue = useMemo(() => valueQ.data?.value ?? "", [valueQ.data]);

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 py-4 border-b border-border flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Redis admin</h1>
          <p className="text-xs text-fg-dim mt-0.5">
            Inspección y limpieza de claves. Admin-only. Operaciones destructivas requieren doble
            confirmación.
          </p>
        </div>
        <div className="flex gap-1.5">
          <Button variant="outline" size="sm" onClick={() => qc.invalidateQueries({ queryKey: ["redis"] })}>
            <RefreshCw className="w-3.5 h-3.5" /> Refrescar
          </Button>
          <Button variant="warn" size="sm" onClick={handleDeletePattern} disabled={deletePattern.isPending}>
            <Trash2 className="w-3.5 h-3.5" /> Borrar patrón
          </Button>
          <Button variant="danger" size="sm" onClick={handleFlushConvs} disabled={flushConvs.isPending}>
            Reset conversaciones
          </Button>
          <Button variant="danger" size="sm" onClick={handleNuke} disabled={nukeAll.isPending}>
            ☢️ Reset NUCLEAR
          </Button>
        </div>
      </div>

      {/* Stats */}
      <div className="px-6 py-3 border-b border-border grid grid-cols-4 gap-3">
        <StatCard icon={Database}  label="Claves totales"  value={statsQ.data?.dbsize?.toLocaleString() ?? "…"} />
        <StatCard icon={HardDrive} label="Memoria usada"   value={statsQ.data?.used_memory_human ?? "…"} />
        <StatCard icon={Users}     label="Clientes"        value={String(statsQ.data?.connected_clients ?? "…")} />
        <StatCard icon={RefreshCw} label="Versión Redis"   value={statsQ.data?.redis_version ?? "…"} />
      </div>

      {/* Toolbar de búsqueda */}
      <div className="px-6 py-3 border-b border-border flex items-center gap-2 flex-wrap">
        <Input
          className="max-w-xs"
          placeholder="Patrón (ej: conv:*)"
          value={pattern}
          onChange={(e) => setPattern(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
        />
        <Button size="sm" onClick={handleSearch}>
          <Search className="w-3.5 h-3.5" /> Buscar
        </Button>
        <div className="w-px h-5 bg-border mx-1" />
        {QUICK_PATTERNS.map((q) => (
          <button
            key={q}
            onClick={() => { setPattern(q); setSubmitted(q); }}
            className={cn(
              "text-[11px] px-2 py-0.5 rounded border transition-colors",
              submitted === q
                ? "bg-accent/15 border-accent text-accent"
                : "bg-bg border-border text-fg-muted hover:text-fg",
            )}
          >
            {q}
          </button>
        ))}
      </div>

      {/* Two-pane */}
      <div className="flex-1 flex overflow-hidden">
        {/* Keys list */}
        <div className="w-[44%] border-r border-border flex flex-col">
          <div className="px-4 py-2 border-b border-border text-[11px] text-fg-dim flex justify-between">
            <span>{keysQ.data ? `${keysQ.data.total} claves` : "Cargando…"}</span>
            <span className="font-mono">{submitted}</span>
          </div>
          <div className="flex-1 overflow-y-auto scroll-thin">
            {keysQ.isLoading && (
              <div className="p-4 text-xs text-fg-dim"><Loader2 className="inline w-3 h-3 animate-spin mr-2" />Cargando claves…</div>
            )}
            {keysQ.data?.keys.length === 0 && (
              <div className="p-4 text-xs text-fg-dim">Sin claves para este patrón.</div>
            )}
            {keysQ.data?.keys.map((k) => {
              const active = selected === k.key;
              return (
                <div
                  key={k.key}
                  className={cn(
                    "group px-3 py-2 border-b border-border cursor-pointer text-xs transition-colors",
                    active ? "bg-accent/10 border-l-2 border-l-accent" : "hover:bg-hover",
                  )}
                  onClick={() => setSelected(k.key)}
                >
                  <div className="flex items-center gap-1.5 mb-0.5">
                    <span className="font-mono text-[11px] truncate flex-1">{k.key}</span>
                    <span className={cn("text-[9px] px-1.5 py-0.5 rounded", TYPE_COLOR[k.type] ?? "bg-border text-fg-dim")}>
                      {k.type}
                    </span>
                    <span className="text-[9px] text-fg-dim">{formatTTL(k.ttl)}</span>
                    <button
                      type="button"
                      className="opacity-0 group-hover:opacity-100 transition-opacity text-danger hover:bg-danger/20 w-5 h-5 rounded flex items-center justify-center"
                      onClick={(e) => {
                        e.stopPropagation();
                        if (confirm(`Eliminar la clave "${k.key}"?`)) deleteKey.mutate(k.key);
                      }}
                      title="Eliminar clave"
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                  {k.preview && (
                    <div className="text-[10px] text-fg-dim truncate font-mono">{k.preview}</div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Value panel */}
        <div className="flex-1 flex flex-col">
          {!selected ? (
            <div className="flex-1 flex items-center justify-center text-xs text-fg-dim">
              Seleccioná una clave para ver su valor.
            </div>
          ) : valueQ.isLoading ? (
            <div className="flex-1 flex items-center justify-center text-xs text-fg-dim">
              <Loader2 className="w-4 h-4 animate-spin mr-2" /> Cargando valor…
            </div>
          ) : valueQ.error ? (
            <div className="flex-1 flex items-center justify-center text-xs text-danger">
              {(valueQ.error as Error).message}
            </div>
          ) : valueQ.data ? (
            <>
              <div className="px-4 py-2 border-b border-border flex items-center gap-3">
                <span className="font-mono text-xs truncate flex-1">{valueQ.data.key}</span>
                <span className={cn("text-[9px] px-1.5 py-0.5 rounded", TYPE_COLOR[valueQ.data.type] ?? "bg-border text-fg-dim")}>
                  {valueQ.data.type}
                </span>
                <span className="text-[9px] text-fg-dim">TTL {formatTTL(valueQ.data.ttl)}</span>
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => {
                    if (confirm(`Eliminar la clave "${valueQ.data.key}"?`)) deleteKey.mutate(valueQ.data.key);
                  }}
                  disabled={deleteKey.isPending}
                >
                  <Trash2 className="w-3 h-3" /> Eliminar
                </Button>
              </div>
              <pre className="flex-1 p-4 overflow-auto scroll-thin text-[11px] font-mono whitespace-pre-wrap break-all">
                {selectedValue}
              </pre>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string;
}) {
  return (
    <div className="bg-card border border-border rounded-lg px-3 py-2 flex items-center gap-3">
      <Icon className="w-4 h-4 text-fg-muted" />
      <div>
        <div className="text-[10px] text-fg-dim uppercase">{label}</div>
        <div className="text-sm font-mono">{value}</div>
      </div>
    </div>
  );
}
