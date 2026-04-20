"use client";

/**
 * /pipeline — Kanban de clasificación de leads por el agente IA.
 *
 * Columnas: 7 etiquetas del clasificador IA (agents/classifier.py):
 *   caliente / tibio / frio / convertido / esperando_pago / seguimiento /
 *   no_interesa  (+ "sin_clasificar" para convs sin label aún)
 *
 * El clasificador corre automáticamente después de cada respuesta del bot
 * y guarda la etiqueta en Redis (conv_label:{session_id}). Este kanban:
 *   - Lista todas las convs abiertas/pendientes agrupadas por label
 *   - Permite DRAG & DROP entre columnas → dispara POST
 *     /api/v1/inbox/conversations/{id}/label con el nuevo label
 *     (override manual, se pisa al automático en Redis)
 *   - Menú "..." en cada card como fallback accesible
 *
 * Auto-refresh 30s via TanStack Query.
 */

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  useDraggable,
  useDroppable,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import {
  Loader2,
  RefreshCw,
  MoreVertical,
  ArrowRight,
  UserCog,
  Flame,
  Snowflake,
  Thermometer,
  CheckCircle2,
  Clock,
  Phone,
  Globe,
  Pause,
  GripVertical,
  CreditCard,
  XCircle,
  HelpCircle,
  MessageSquare,
} from "lucide-react";

import { api } from "@/lib/api";
import { useAgents } from "@/lib/api/inbox";
import { useAuth } from "@/lib/auth";
import { Flag } from "@/components/ui/flag";
import { Button } from "@/components/ui/button";
import { RoleGate } from "@/lib/auth";
import { NoAccess } from "@/components/ui/coming-soon";
import { cn } from "@/lib/utils";

// ═══════════════════════════════════════════════════════════════════════
// Types y constantes
// ═══════════════════════════════════════════════════════════════════════

type ConvLabel =
  | "caliente"
  | "tibio"
  | "frio"
  | "convertido"
  | "esperando_pago"
  | "seguimiento"
  | "no_interesa"
  | "sin_clasificar";

type PipelineConv = {
  id: string;
  session_id: string;
  channel: "whatsapp" | "widget" | string;
  name: string;
  email: string;
  country: string;
  last_timestamp: string;
  assigned_agent_id: string | null;
  status: "open" | "pending" | "resolved";
  queue: "sales" | "billing" | "post-sales";
  needs_human: boolean;
  bot_paused: boolean;
  label: ConvLabel;
};

type PipelineResponse = {
  grouped: Record<string, PipelineConv[]>;
  counts: Record<string, number>;
  total: number;
};

const COLUMNS: {
  key: ConvLabel;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  color: string; // tailwind color-name (accent/warn/danger/success/info)
  description: string;
}[] = [
  {
    key: "caliente",
    label: "Caliente",
    icon: Flame,
    color: "danger",
    description: "Quiere inscribirse ya. Pregunta por precio/fechas/pago.",
  },
  {
    key: "tibio",
    label: "Tibio",
    icon: Thermometer,
    color: "warn",
    description: "Interesado con dudas. Pide más info.",
  },
  {
    key: "frio",
    label: "Frío",
    icon: Snowflake,
    color: "info",
    description: "Pasivo, sin preguntas. Solo mira.",
  },
  {
    key: "esperando_pago",
    label: "Esperando pago",
    icon: CreditCard,
    color: "warn",
    description: "Tiene link de pago, aún no pagó.",
  },
  {
    key: "convertido",
    label: "Convertido",
    icon: CheckCircle2,
    color: "success",
    description: "Pagó o confirmó inscripción.",
  },
  {
    key: "seguimiento",
    label: "Seguimiento",
    icon: Clock,
    color: "info",
    description: "Pidió que lo contacten después.",
  },
  {
    key: "no_interesa",
    label: "No le interesa",
    icon: XCircle,
    color: "fg-dim",
    description: "Dijo explícitamente que no le interesa.",
  },
  {
    key: "sin_clasificar",
    label: "Sin clasificar",
    icon: HelpCircle,
    color: "fg-muted",
    description: "El agente IA todavía no clasificó (pocos mensajes).",
  },
];

// Colores helpers por label — genera clases de tailwind reales (no dinámicas)
const COLOR_CLASSES: Record<string, { bg: string; text: string; border: string; dot: string; bgSoft: string }> = {
  danger: {
    bg: "bg-danger",
    text: "text-danger",
    border: "border-danger/30",
    dot: "bg-danger",
    bgSoft: "bg-danger/5",
  },
  warn: {
    bg: "bg-warn",
    text: "text-warn",
    border: "border-warn/30",
    dot: "bg-warn",
    bgSoft: "bg-warn/5",
  },
  info: {
    bg: "bg-info",
    text: "text-info",
    border: "border-info/30",
    dot: "bg-info",
    bgSoft: "bg-info/5",
  },
  success: {
    bg: "bg-success",
    text: "text-success",
    border: "border-success/30",
    dot: "bg-success",
    bgSoft: "bg-success/5",
  },
  accent: {
    bg: "bg-accent",
    text: "text-accent",
    border: "border-accent/30",
    dot: "bg-accent",
    bgSoft: "bg-accent/5",
  },
  "fg-dim": {
    bg: "bg-fg-dim",
    text: "text-fg-dim",
    border: "border-fg-dim/20",
    dot: "bg-fg-dim",
    bgSoft: "bg-fg-dim/5",
  },
  "fg-muted": {
    bg: "bg-fg-muted",
    text: "text-fg-muted",
    border: "border-fg-muted/20",
    dot: "bg-fg-muted",
    bgSoft: "bg-fg-muted/5",
  },
};

function formatRelative(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const diffSec = Math.round((Date.now() - d.getTime()) / 1000);
  if (diffSec < 60) return "ahora";
  if (diffSec < 3600) return `${Math.round(diffSec / 60)}m`;
  if (diffSec < 86400) return `${Math.round(diffSec / 3600)}h`;
  return `${Math.round(diffSec / 86400)}d`;
}

function initials(name: string): string {
  if (!name) return "??";
  const parts = name.trim().split(/\s+/).slice(0, 2);
  return parts.map((p) => p[0]?.toUpperCase() || "").join("") || "??";
}

// ═══════════════════════════════════════════════════════════════════════
// Root
// ═══════════════════════════════════════════════════════════════════════

export default function PipelinePage() {
  return (
    <RoleGate min="supervisor" denyFallback={<NoAccess requiredRole="supervisor o admin" />}>
      <Inner />
    </RoleGate>
  );
}

function Inner() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const { data: agents = [] } = useAgents();

  // Filtros del kanban
  const [daysFilter, setDaysFilter] = useState<number>(30);
  const [channelFilter, setChannelFilter] = useState<string>("all");
  const [agentFilter, setAgentFilter] = useState<string>("all"); // "all" | "me" | "unassigned" | <uuid>
  const [activeConv, setActiveConv] = useState<PipelineConv | null>(null);

  // Mapa id → nombre para mostrar en las cards
  const agentById = useMemo(() => {
    const m = new Map<string, { name: string }>();
    for (const a of agents) m.set(a.id, { name: a.name });
    return m;
  }, [agents]);

  const pipeQ = useQuery<PipelineResponse>({
    queryKey: ["pipeline", "leads", daysFilter, channelFilter, agentFilter, user?.id],
    queryFn: () => {
      const params = new URLSearchParams({
        limit: "300",
        days: String(daysFilter),
      });
      if (channelFilter !== "all") params.set("channel", channelFilter);
      if (agentFilter === "me" && user?.id) {
        params.set("agent_id", user.id);
      } else if (agentFilter === "unassigned") {
        params.set("unassigned", "true");
      } else if (agentFilter !== "all") {
        params.set("agent_id", agentFilter);
      }
      return api.get(`/inbox/pipeline?${params.toString()}`);
    },
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  const moveLabel = useMutation({
    mutationFn: ({ convId, label }: { convId: string; label: ConvLabel }) =>
      api.post(`/inbox/conversations/${convId}/label`, { label }),
    onMutate: async ({ convId, label }) => {
      await qc.cancelQueries({ queryKey: ["pipeline", "leads"] });
      const previous = qc.getQueryData<PipelineResponse>(["pipeline", "leads"]);
      if (previous) {
        // Optimistic: movemos la card de su columna actual a la nueva
        const newGrouped: Record<string, PipelineConv[]> = {};
        let moved: PipelineConv | null = null;
        for (const [col, convs] of Object.entries(previous.grouped)) {
          newGrouped[col] = [];
          for (const c of convs) {
            if (c.id === convId) {
              moved = { ...c, label };
            } else {
              newGrouped[col].push(c);
            }
          }
        }
        if (moved) {
          newGrouped[label] = [moved, ...(newGrouped[label] || [])];
        }
        const newCounts: Record<string, number> = {};
        for (const [col, convs] of Object.entries(newGrouped)) {
          newCounts[col] = convs.length;
        }
        qc.setQueryData<PipelineResponse>(["pipeline", "leads"], {
          ...previous,
          grouped: newGrouped,
          counts: newCounts,
        });
      }
      return { previous };
    },
    onError: (e: Error, _vars, ctx) => {
      if (ctx?.previous) {
        qc.setQueryData(["pipeline", "leads"], ctx.previous);
      }
      alert(`No se pudo reclasificar: ${e.message}`);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["pipeline", "leads"] });
    },
  });

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  );

  // Los filtros (days/channel/agent) van al server — filteredGrouped
  // es simplemente pipeQ.data.grouped ya filtrado por el backend. Si hay
  // filtros client-side adicionales en el futuro, van acá.
  const filteredGrouped = pipeQ.data?.grouped ?? {};

  const handleDragStart = (e: DragStartEvent) => {
    const all = Object.values(pipeQ.data?.grouped ?? {}).flat();
    const conv = all.find((c) => c.id === e.active.id);
    if (conv) setActiveConv(conv);
  };

  const handleDragEnd = (e: DragEndEvent) => {
    setActiveConv(null);
    const { active, over } = e;
    if (!over) return;
    const convId = String(active.id);
    const targetLabel = String(over.id) as ConvLabel;
    if (!COLUMNS.some((c) => c.key === targetLabel)) return;
    if (targetLabel === "sin_clasificar") return; // no permitir mover A "sin clasificar"
    const all = Object.values(pipeQ.data?.grouped ?? {}).flat();
    const conv = all.find((c) => c.id === convId);
    if (!conv || conv.label === targetLabel) return;
    moveLabel.mutate({ convId, label: targetLabel });
  };

  const totalFiltered = Object.values(filteredGrouped).reduce(
    (sum, arr) => sum + arr.length,
    0,
  );

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 py-4 border-b border-border flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-lg font-semibold">Pipeline de leads</h1>
          <p className="text-xs text-fg-dim mt-0.5">
            Clasificación automática del agente IA. Arrastrá cards entre
            columnas para reclasificar manualmente.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {/* Ventana de tiempo */}
          <select
            value={daysFilter}
            onChange={(e) => setDaysFilter(Number(e.target.value))}
            className="bg-bg border border-border rounded-md px-3 py-1.5 text-xs focus:outline-none focus:border-accent"
            title="Últimos N días (filtra por última actividad)"
          >
            <option value={1}>Hoy</option>
            <option value={7}>Últimos 7 días</option>
            <option value={30}>Últimos 30 días</option>
            <option value={90}>Últimos 90 días</option>
            <option value={365}>Último año</option>
          </select>

          {/* Canal */}
          <select
            value={channelFilter}
            onChange={(e) => setChannelFilter(e.target.value)}
            className="bg-bg border border-border rounded-md px-3 py-1.5 text-xs focus:outline-none focus:border-accent"
          >
            <option value="all">Todos los canales</option>
            <option value="widget">Widget</option>
            <option value="whatsapp">WhatsApp</option>
          </select>

          {/* Agente asignado */}
          <select
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            className="bg-bg border border-border rounded-md px-3 py-1.5 text-xs focus:outline-none focus:border-accent"
          >
            <option value="all">Todos los agentes</option>
            <option value="me">Mías</option>
            <option value="unassigned">Sin asignar</option>
            <optgroup label="Agentes">
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </optgroup>
          </select>

          <Button
            size="sm"
            variant="outline"
            onClick={() => pipeQ.refetch()}
            disabled={pipeQ.isFetching}
          >
            <RefreshCw className={cn("w-3.5 h-3.5", pipeQ.isFetching && "animate-spin")} />
            Refrescar
          </Button>
        </div>
      </div>

      <div className="px-6 py-2 border-b border-border text-[11px] text-fg-dim flex items-center gap-4 flex-wrap">
        <span>
          <span className="font-medium text-fg">{totalFiltered}</span> conversaciones
        </span>
        <span>· Auto-refresh 30s</span>
        <span>· Arrastrá con el handle ⋮⋮ de la izquierda</span>
      </div>

      {pipeQ.isLoading ? (
        <div className="flex-1 flex items-center justify-center text-fg-dim">
          <Loader2 className="w-5 h-5 animate-spin" />
        </div>
      ) : pipeQ.error ? (
        <div className="flex-1 flex items-center justify-center text-fg-dim text-sm">
          No se pudo cargar el pipeline. {(pipeQ.error as Error).message}
        </div>
      ) : (
        <DndContext
          sensors={sensors}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
          onDragCancel={() => setActiveConv(null)}
        >
          <div className="flex-1 overflow-x-auto scroll-thin">
            <div
              className="flex gap-3 p-4 min-h-full"
              style={{ minWidth: `${COLUMNS.length * 300}px` }}
            >
              {COLUMNS.map((col) => (
                <Column
                  key={col.key}
                  colDef={col}
                  convs={filteredGrouped[col.key] ?? []}
                  onMove={(convId, label) => moveLabel.mutate({ convId, label })}
                  moving={moveLabel.isPending}
                  agentById={agentById}
                />
              ))}
            </div>
          </div>
          <DragOverlay dropAnimation={null}>
            {activeConv ? <DraggingCard conv={activeConv} /> : null}
          </DragOverlay>
        </DndContext>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// Column + Card
// ═══════════════════════════════════════════════════════════════════════

function Column({
  colDef,
  convs,
  onMove,
  moving,
  agentById,
}: {
  colDef: (typeof COLUMNS)[number];
  convs: PipelineConv[];
  onMove: (convId: string, label: ConvLabel) => void;
  moving: boolean;
  agentById: Map<string, { name: string }>;
}) {
  const { setNodeRef, isOver } = useDroppable({
    id: colDef.key,
    disabled: colDef.key === "sin_clasificar",
  });
  const c = COLOR_CLASSES[colDef.color] || COLOR_CLASSES["fg-muted"];
  const Icon = colDef.icon;

  return (
    <div
      ref={setNodeRef}
      className={cn(
        "w-[290px] shrink-0 bg-card rounded-lg border flex flex-col overflow-hidden transition-colors",
        c.border,
        isOver && c.bgSoft,
        isOver && "ring-2 ring-offset-0",
        isOver && (c.border === "border-danger/30" ? "ring-danger/40"
          : c.border === "border-warn/30" ? "ring-warn/40"
          : c.border === "border-success/30" ? "ring-success/40"
          : c.border === "border-info/30" ? "ring-info/40"
          : "ring-accent/40"),
      )}
    >
      <div className={cn("px-3 py-2.5 border-b", c.border)}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Icon className={cn("w-3.5 h-3.5", c.text)} />
            <span className="text-sm font-semibold">{colDef.label}</span>
            <span className="text-[10px] text-fg-dim tabular-nums">({convs.length})</span>
          </div>
          {isOver && colDef.key !== "sin_clasificar" && (
            <span className={cn("text-[10px] animate-pulse", c.text)}>Soltar aquí</span>
          )}
        </div>
        <div className="text-[10px] text-fg-dim mt-0.5 leading-snug">
          {colDef.description}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto scroll-thin p-2 space-y-2">
        {convs.length === 0 && (
          <div className="text-[11px] text-fg-dim italic text-center py-6">
            Sin conversaciones
            {isOver && colDef.key !== "sin_clasificar" && (
              <div className={cn("mt-1", c.text)}>Soltar aquí para reclasificar</div>
            )}
          </div>
        )}
        {convs.map((c) => (
          <Card
            key={c.id}
            conv={c}
            onMove={onMove}
            moving={moving}
            agentName={
              c.assigned_agent_id ? agentById.get(c.assigned_agent_id)?.name ?? null : null
            }
          />
        ))}
      </div>
    </div>
  );
}

function Card({
  conv,
  onMove,
  moving,
  agentName,
}: {
  conv: PipelineConv;
  onMove: (convId: string, label: ConvLabel) => void;
  moving: boolean;
  agentName: string | null;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: conv.id,
  });
  // Destinos válidos: todas las columnas excepto la actual y "sin_clasificar"
  const targetLabels = COLUMNS.filter(
    (c) => c.key !== conv.label && c.key !== "sin_clasificar",
  );

  return (
    <div
      ref={setNodeRef}
      className={cn(
        "bg-bg border border-border rounded-md p-2.5 transition-all relative group",
        isDragging ? "opacity-40" : "hover:border-accent/50",
      )}
    >
      <div className="flex items-start gap-2 mb-1.5">
        <button
          {...attributes}
          {...listeners}
          className="touch-none cursor-grab active:cursor-grabbing text-fg-dim hover:text-fg shrink-0 mt-0.5"
          title="Arrastrar para reclasificar"
          aria-label="Arrastrar para reclasificar"
        >
          <GripVertical className="w-3.5 h-3.5" />
        </button>
        <div className="w-7 h-7 rounded-full bg-gradient-to-br from-pink-500 to-fuchsia-600 text-white text-[9px] font-bold flex items-center justify-center shrink-0">
          {initials(conv.name)}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1">
            <Flag iso={conv.country} size={10} />
            <span className="text-xs font-medium truncate">{conv.name}</span>
          </div>
          <div className="text-[10px] text-fg-dim tabular-nums">
            {formatRelative(conv.last_timestamp)}
          </div>
        </div>
        <button
          onClick={() => setMenuOpen(!menuOpen)}
          className="opacity-0 group-hover:opacity-100 text-fg-dim hover:text-fg p-0.5"
          title="Más acciones"
          aria-label="Menú de acciones"
        >
          <MoreVertical className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="flex items-center gap-1 mb-1.5 flex-wrap pl-6">
        {conv.needs_human && (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-warn/15 text-warn flex items-center gap-0.5">
            <UserCog className="w-2.5 h-2.5" /> Humano
          </span>
        )}
        {conv.bot_paused && (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-fg-dim/15 text-fg-dim flex items-center gap-0.5">
            <Pause className="w-2.5 h-2.5" /> Pausa
          </span>
        )}
        {conv.channel === "whatsapp" ? (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-success/10 text-success flex items-center gap-0.5">
            <Phone className="w-2.5 h-2.5" /> WhatsApp
          </span>
        ) : (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-info/10 text-info flex items-center gap-0.5">
            <Globe className="w-2.5 h-2.5" /> Widget
          </span>
        )}
      </div>

      {conv.email && (
        <div className="text-[10px] text-fg-dim truncate pl-6 mb-0.5">{conv.email}</div>
      )}

      {/* Agente asignado — si lo hay */}
      {agentName && (
        <div className="text-[10px] pl-6 mb-1 flex items-center gap-1">
          <UserCog className="w-2.5 h-2.5 text-accent" />
          <span className="text-fg-dim">Asignada a</span>{" "}
          <span className="text-fg truncate">{agentName}</span>
        </div>
      )}

      <Link
        href={`/inbox?c=${conv.id}`}
        className="inline-flex items-center gap-1 text-[10px] text-accent hover:underline pl-6"
      >
        Abrir en inbox <ArrowRight className="w-2.5 h-2.5" />
      </Link>

      {menuOpen && (
        <div
          className="absolute top-7 right-2 z-20 bg-panel border border-border rounded-md shadow-lg py-1 w-48"
          onMouseLeave={() => setMenuOpen(false)}
        >
          <div className="px-2 py-1 text-[9px] uppercase tracking-wider text-fg-muted">
            Reclasificar como
          </div>
          {targetLabels.map((tl) => {
            const TlIcon = tl.icon;
            const color = COLOR_CLASSES[tl.color] || COLOR_CLASSES["fg-muted"];
            return (
              <button
                key={tl.key}
                onClick={() => {
                  onMove(conv.id, tl.key);
                  setMenuOpen(false);
                }}
                disabled={moving}
                className="w-full text-left px-2 py-1.5 text-[11px] hover:bg-hover transition-colors flex items-center gap-2 disabled:opacity-50"
              >
                <TlIcon className={cn("w-3 h-3", color.text)} />
                {tl.label}
              </button>
            );
          })}
          <div className="border-t border-border mt-1 pt-1 px-2 py-1">
            <Link
              href={`/inbox?c=${conv.id}`}
              className="text-[11px] text-accent hover:underline flex items-center gap-1"
              onClick={() => setMenuOpen(false)}
            >
              <MessageSquare className="w-2.5 h-2.5" /> Abrir en inbox
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}

function DraggingCard({ conv }: { conv: PipelineConv }) {
  return (
    <div className="bg-card border-2 border-accent shadow-lg rounded-md p-2.5 w-[260px] rotate-2">
      <div className="flex items-center gap-2">
        <div className="w-7 h-7 rounded-full bg-gradient-to-br from-pink-500 to-fuchsia-600 text-white text-[9px] font-bold flex items-center justify-center shrink-0">
          {initials(conv.name)}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1">
            <Flag iso={conv.country} size={10} />
            <span className="text-xs font-medium truncate">{conv.name}</span>
          </div>
          <div className="text-[10px] text-fg-dim">Arrastrar para reclasificar</div>
        </div>
      </div>
    </div>
  );
}
