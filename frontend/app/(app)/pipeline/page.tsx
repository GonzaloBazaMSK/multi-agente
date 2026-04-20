"use client";

/**
 * /pipeline — Kanban drag-and-drop de conversaciones agrupadas por cola IA.
 *
 * Columnas: Ventas / Cobranzas / Post-venta (las 3 queues del router IA).
 * Cards draggables via @dnd-kit/core. onDragEnd dispara PATCH a
 * /api/v1/inbox/conversations/{id}/queue y actualiza optimístamente la UI
 * (si falla el backend, revert).
 *
 * Además del drag, cada card tiene un menú "..." como fallback (acceso a
 * teclado, mobile sin drag bien soportado, etc).
 *
 * Auto-refresh: 30s via TanStack Query. Si otro agente movió una conv
 * mientras estabas viendo, el polling lo refleja.
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
  Sparkles,
  Award,
  MessageSquare,
  Phone,
  Globe,
  Pause,
  GripVertical,
} from "lucide-react";

import { api } from "@/lib/api";
import { useConversations } from "@/lib/api/inbox";
import { Flag } from "@/components/ui/flag";
import { Button } from "@/components/ui/button";
import { RoleGate } from "@/lib/auth";
import { NoAccess } from "@/components/ui/coming-soon";
import {
  type ConversationListItem,
  type LifecycleStage,
  type Queue,
} from "@/lib/mock-data";
import { cn } from "@/lib/utils";

const COLUMNS: {
  queue: Queue;
  label: string;
  color: string;
  borderColor: string;
  bgHover: string;
}[] = [
  {
    queue: "sales",
    label: "Ventas",
    color: "text-accent",
    borderColor: "border-accent/30",
    bgHover: "bg-accent/5",
  },
  {
    queue: "billing",
    label: "Cobranzas",
    color: "text-warn",
    borderColor: "border-warn/30",
    bgHover: "bg-warn/5",
  },
  {
    queue: "post-sales",
    label: "Post-venta",
    color: "text-info",
    borderColor: "border-info/30",
    bgHover: "bg-info/5",
  },
];

const LIFECYCLE_META: Record<
  LifecycleStage,
  { label: string; color: string; icon: React.ComponentType<{ className?: string }> }
> = {
  new: { label: "Nuevo", color: "text-info bg-info/15", icon: Sparkles },
  hot: { label: "Hot", color: "text-danger bg-danger/15", icon: Flame },
  customer: { label: "Cliente", color: "text-success bg-success/15", icon: Award },
  cold: { label: "Cold", color: "text-fg-dim bg-fg-dim/15", icon: Snowflake },
};

function formatRelative(iso: string): string {
  const d = new Date(iso);
  const diffSec = Math.round((Date.now() - d.getTime()) / 1000);
  if (diffSec < 60) return "ahora";
  if (diffSec < 3600) return `${Math.round(diffSec / 60)}m`;
  if (diffSec < 86400) return `${Math.round(diffSec / 3600)}h`;
  return `${Math.round(diffSec / 86400)}d`;
}

export default function PipelinePage() {
  return (
    <RoleGate min="supervisor" denyFallback={<NoAccess requiredRole="supervisor o admin" />}>
      <Inner />
    </RoleGate>
  );
}

function Inner() {
  const qc = useQueryClient();
  const [lifecycleFilter, setLifecycleFilter] = useState<LifecycleStage | "all">("all");
  const [statusFilter, setStatusFilter] = useState<"open" | "pending" | "all">("open");
  const [activeConv, setActiveConv] = useState<ConversationListItem | null>(null);

  // Reusamos el mismo hook que usa /inbox — así garantizamos que el shape
  // de cada conversación es el ConversationListItem normalizado (contact.*,
  // lifecycle, channel, etc) en vez del shape plano del backend.
  const convsQ = useConversations({ limit: 200 });

  // Mutation para mover conversación de cola. El optimistic update lo
  // hacemos invalidando el cache de useConversations (la query key la
  // define el hook — ["inbox", "conversations", {...}]).
  const moveQueue = useMutation({
    mutationFn: ({ convId, queue }: { convId: string; queue: Queue }) =>
      api.post(`/inbox/conversations/${convId}/queue`, { queue }),
    onError: (e: Error) => {
      alert(`No se pudo mover: ${e.message}`);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["inbox", "conversations"] });
    },
  });

  const grouped = useMemo(() => {
    const byQueue: Record<Queue, ConversationListItem[]> = {
      sales: [],
      billing: [],
      "post-sales": [],
    };
    for (const c of convsQ.data ?? []) {
      // Defensivo: si falta contact (bug del backend o shape viejo en caché),
      // skipea la card en vez de crashear todo el kanban.
      if (!c || !c.contact) continue;
      if (statusFilter !== "all" && c.status !== statusFilter) continue;
      if (lifecycleFilter !== "all" && c.lifecycle !== lifecycleFilter) continue;
      const q = c.queue || "sales";
      if (!byQueue[q]) continue;
      byQueue[q].push(c);
    }
    return byQueue;
  }, [convsQ.data, lifecycleFilter, statusFilter]);

  const totalFiltered =
    grouped.sales.length + grouped.billing.length + grouped["post-sales"].length;

  // Sensors — solo PointerSensor con distance=6 para no confundir click
  // casual con drag. Drag activa al mover 6px+.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  );

  const handleDragStart = (e: DragStartEvent) => {
    const conv = (convsQ.data ?? []).find((c) => c.id === e.active.id);
    if (conv) setActiveConv(conv);
  };

  const handleDragEnd = (e: DragEndEvent) => {
    setActiveConv(null);
    const { active, over } = e;
    if (!over) return;
    const convId = String(active.id);
    const targetQueue = String(over.id) as Queue;
    const currentConv = (convsQ.data ?? []).find((c) => c.id === convId);
    if (!currentConv || currentConv.queue === targetQueue) return;
    if (!COLUMNS.some((c) => c.queue === targetQueue)) return;
    moveQueue.mutate({ convId, queue: targetQueue });
  };

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 py-4 border-b border-border flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-lg font-semibold">Pipeline</h1>
          <p className="text-xs text-fg-dim mt-0.5">
            Kanban de conversaciones — arrastrá las cards entre columnas para
            cambiar su cola de atención.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={lifecycleFilter}
            onChange={(e) => setLifecycleFilter(e.target.value as LifecycleStage | "all")}
            className="bg-bg border border-border rounded-md px-3 py-1.5 text-xs focus:outline-none focus:border-accent"
          >
            <option value="all">Todos los lifecycle</option>
            <option value="hot">🔥 Hot leads</option>
            <option value="new">✨ Nuevos</option>
            <option value="customer">🏆 Clientes</option>
            <option value="cold">❄ Cold</option>
          </select>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as "open" | "pending" | "all")}
            className="bg-bg border border-border rounded-md px-3 py-1.5 text-xs focus:outline-none focus:border-accent"
          >
            <option value="open">Abiertas</option>
            <option value="pending">Pendientes</option>
            <option value="all">Todas</option>
          </select>
          <Button
            size="sm"
            variant="outline"
            onClick={() => convsQ.refetch()}
            disabled={convsQ.isFetching}
          >
            <RefreshCw className={cn("w-3.5 h-3.5", convsQ.isFetching && "animate-spin")} />
            Refrescar
          </Button>
        </div>
      </div>

      <div className="px-6 py-2 border-b border-border text-[11px] text-fg-dim flex items-center gap-4">
        <span>
          <span className="font-medium text-fg">{totalFiltered}</span> conversaciones
          {lifecycleFilter !== "all" && (
            <span>
              {" "}
              · lifecycle:{" "}
              <span className="text-fg">{LIFECYCLE_META[lifecycleFilter].label}</span>
            </span>
          )}
        </span>
        <span>· Auto-refresh 30s · Drag & drop entre columnas</span>
      </div>

      {convsQ.isLoading ? (
        <div className="flex-1 flex items-center justify-center text-fg-dim">
          <Loader2 className="w-5 h-5 animate-spin" />
        </div>
      ) : (
        <DndContext
          sensors={sensors}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
          onDragCancel={() => setActiveConv(null)}
        >
          <div className="flex-1 overflow-x-auto scroll-thin">
            <div className="flex gap-4 p-4 min-h-full" style={{ minWidth: "1200px" }}>
              {COLUMNS.map((col) => (
                <Column
                  key={col.queue}
                  queue={col.queue}
                  label={col.label}
                  color={col.color}
                  borderColor={col.borderColor}
                  bgHover={col.bgHover}
                  convs={grouped[col.queue]}
                  onMove={(convId, q) => moveQueue.mutate({ convId, queue: q })}
                  moving={moveQueue.isPending}
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

function Column({
  queue,
  label,
  color,
  borderColor,
  bgHover,
  convs,
  onMove,
  moving,
}: {
  queue: Queue;
  label: string;
  color: string;
  borderColor: string;
  bgHover: string;
  convs: ConversationListItem[];
  onMove: (convId: string, queue: Queue) => void;
  moving: boolean;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: queue });
  return (
    <div
      ref={setNodeRef}
      className={cn(
        "flex-1 min-w-[320px] bg-card rounded-lg border flex flex-col overflow-hidden transition-colors",
        borderColor,
        isOver && bgHover,
        isOver && "border-2",
      )}
    >
      <div className={cn("px-3 py-2.5 border-b flex items-center justify-between", borderColor)}>
        <div className="flex items-center gap-2">
          <span className={cn("w-2 h-2 rounded-full", color.replace("text-", "bg-"))} />
          <span className="text-sm font-semibold">{label}</span>
          <span className="text-[10px] text-fg-dim tabular-nums">({convs.length})</span>
        </div>
        {isOver && (
          <span className="text-[10px] text-accent animate-pulse">Soltar aquí</span>
        )}
      </div>
      <div className="flex-1 overflow-y-auto scroll-thin p-2 space-y-2">
        {convs.length === 0 && (
          <div className="text-[11px] text-fg-dim italic text-center py-8">
            Sin conversaciones
            {isOver && <div className="text-accent mt-1">Soltar aquí para mover</div>}
          </div>
        )}
        {convs.map((c) => (
          <Card
            key={c.id}
            conv={c}
            onMove={onMove}
            moving={moving}
            currentQueue={queue}
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
  currentQueue,
}: {
  conv: ConversationListItem;
  onMove: (convId: string, queue: Queue) => void;
  moving: boolean;
  currentQueue: Queue;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: conv.id,
  });
  const lifecycleMeta = LIFECYCLE_META[conv.lifecycle] || LIFECYCLE_META.new;
  const LifecycleIcon = lifecycleMeta.icon;
  const targetQueues = COLUMNS.filter((c) => c.queue !== currentQueue);

  return (
    <div
      ref={setNodeRef}
      className={cn(
        "bg-bg border border-border rounded-md p-2.5 transition-all relative group",
        isDragging ? "opacity-40" : "hover:border-accent/50",
      )}
    >
      <div className="flex items-start gap-2 mb-1.5">
        {/* Handle de drag — cursor grab, solo acá el pointer puede arrastrar */}
        <button
          {...attributes}
          {...listeners}
          className="touch-none cursor-grab active:cursor-grabbing text-fg-dim hover:text-fg shrink-0 mt-0.5"
          title="Arrastrar para mover"
          aria-label="Arrastrar para mover"
        >
          <GripVertical className="w-3.5 h-3.5" />
        </button>
        <div className="w-7 h-7 rounded-full bg-gradient-to-br from-pink-500 to-fuchsia-600 text-white text-[9px] font-bold flex items-center justify-center shrink-0">
          {conv.contact.initials}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1">
            <Flag iso={conv.contact.country} size={10} />
            <span className="text-xs font-medium truncate">{conv.contact.name}</span>
          </div>
          <div className="text-[10px] text-fg-dim tabular-nums">
            {formatRelative(conv.lastMessageAt)}
          </div>
        </div>
        <button
          onClick={() => setMenuOpen(!menuOpen)}
          className="opacity-0 group-hover:opacity-100 text-fg-dim hover:text-fg p-0.5"
          title="Mover / acciones"
          aria-label="Menú de acciones"
        >
          <MoreVertical className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="flex items-center gap-1 mb-1.5 flex-wrap pl-6">
        <span
          className={cn(
            "text-[9px] px-1.5 py-0.5 rounded flex items-center gap-0.5",
            lifecycleMeta.color,
          )}
        >
          <LifecycleIcon className="w-2.5 h-2.5" /> {lifecycleMeta.label}
        </span>
        {conv.needsHuman && (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-warn/15 text-warn flex items-center gap-0.5">
            <UserCog className="w-2.5 h-2.5" /> Necesita humano
          </span>
        )}
        {conv.botPaused && (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-fg-dim/15 text-fg-dim flex items-center gap-0.5">
            <Pause className="w-2.5 h-2.5" /> Bot pausado
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

      <div className="text-[11px] text-fg-dim line-clamp-2 leading-snug mb-1.5 pl-6">
        {conv.lastMessage}
      </div>

      <Link
        href={`/inbox?c=${conv.id}`}
        className="inline-flex items-center gap-1 text-[10px] text-accent hover:underline pl-6"
      >
        Abrir en inbox <ArrowRight className="w-2.5 h-2.5" />
      </Link>

      {menuOpen && (
        <div
          className="absolute top-7 right-2 z-20 bg-panel border border-border rounded-md shadow-lg py-1 w-44"
          onMouseLeave={() => setMenuOpen(false)}
        >
          <div className="px-2 py-1 text-[9px] uppercase tracking-wider text-fg-muted">
            Mover a cola
          </div>
          {targetQueues.map((tq) => (
            <button
              key={tq.queue}
              onClick={() => {
                onMove(conv.id, tq.queue);
                setMenuOpen(false);
              }}
              disabled={moving}
              className="w-full text-left px-2 py-1.5 text-[11px] hover:bg-hover transition-colors flex items-center gap-2 disabled:opacity-50"
            >
              <span className={cn("w-2 h-2 rounded-full", tq.color.replace("text-", "bg-"))} />
              {tq.label}
            </button>
          ))}
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

/**
 * DragOverlay — preview fantasma que sigue al cursor durante el drag.
 * Lo mantenemos simple: solo el nombre del cliente + lifecycle badge.
 */
function DraggingCard({ conv }: { conv: ConversationListItem }) {
  const lifecycleMeta = LIFECYCLE_META[conv.lifecycle];
  const LifecycleIcon = lifecycleMeta.icon;
  return (
    <div className="bg-card border-2 border-accent shadow-lg rounded-md p-2.5 w-[280px] rotate-2">
      <div className="flex items-center gap-2 mb-1">
        <div className="w-7 h-7 rounded-full bg-gradient-to-br from-pink-500 to-fuchsia-600 text-white text-[9px] font-bold flex items-center justify-center shrink-0">
          {conv.contact.initials}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1">
            <Flag iso={conv.contact.country} size={10} />
            <span className="text-xs font-medium truncate">{conv.contact.name}</span>
          </div>
        </div>
      </div>
      <span
        className={cn(
          "text-[9px] px-1.5 py-0.5 rounded flex items-center gap-0.5 w-fit",
          lifecycleMeta.color,
        )}
      >
        <LifecycleIcon className="w-2.5 h-2.5" /> {lifecycleMeta.label}
      </span>
    </div>
  );
}
