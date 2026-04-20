"use client";

import { Search, Filter, RefreshCw, Bot, MessageSquare, Smartphone, X, Inbox as InboxIcon, Mail, UserCheck, Hourglass, User, CheckCircle2 } from "lucide-react";
import { Avatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Flag } from "@/components/ui/flag";
import { Dropdown, DropdownLabel, DropdownItem, DropdownSeparator } from "@/components/ui/dropdown";
import { CollapsibleSection } from "@/components/ui/collapsible-section";
import { cn } from "@/lib/utils";
import {
  type ConversationListItem,
  type LifecycleStage,
  type Channel,
  type InboxView,
  type Queue,
  QUEUE_LABEL,
  QUEUE_COLOR,
} from "@/lib/mock-data";
import { useAgents } from "@/lib/api/inbox";
import { useRole } from "@/lib/auth";

interface Props {
  items: ConversationListItem[];
  selectedId?: string;
  onSelect: (id: string) => void;

  /** Multi-selección para bulk actions */
  bulkSelected: Set<string>;
  onBulkToggle: (id: string) => void;
  onBulkSelectAll: () => void;
  onBulkClear: () => void;
  onBulkAssign: (agentId: string | null) => void;
  onBulkResolve: () => void;

  view: InboxView;
  onViewChange: (v: InboxView) => void;

  lifecycle: LifecycleStage | null;
  onLifecycleChange: (l: LifecycleStage | null) => void;

  channel: Channel | null;
  onChannelChange: (c: Channel | null) => void;

  queue: Queue | null;
  onQueueChange: (q: Queue | null) => void;

  country: string | null;
  onCountryChange: (c: string | null) => void;
  /** stats real backend: { sales: { AR: 12, MX: 3 }, billing: {...} } */
  queueStats?: Record<string, Record<string, number>>;

  search: string;
  onSearchChange: (s: string) => void;

  counts: {
    total: number;
    unread: number;
    mine: number;
    queue: number;
    humanAttn: number;
    withBot: number;
    resolved: number;
    byLifecycle: Record<LifecycleStage, number>;
    byChannel: Record<Channel, number>;
    byQueue: Record<Queue, number>;
  };
}

const LIFECYCLE_LABEL: Record<ConversationListItem["lifecycle"], string> = {
  new: "New Lead", hot: "Hot Lead", customer: "Customer", cold: "Cold Lead",
};

// Vistas que aparecen como chips visibles (las más usadas)
const PRIMARY_VIEWS: {
  value: InboxView;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  countKey: keyof Props["counts"];
}[] = [
  { value: "all",    label: "Todas",     icon: InboxIcon, countKey: "total" },
  { value: "unread", label: "No leídas", icon: Mail,      countKey: "unread" },
  { value: "mine",   label: "Mías",      icon: UserCheck, countKey: "mine" },
];

// Vistas adicionales en el dropdown del embudo (lista vertical)
const SECONDARY_VIEWS: {
  value: InboxView;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  countKey: keyof Props["counts"];
}[] = [
  { value: "queue",      label: "En cola (esperando agente)", icon: Hourglass,    countKey: "queue" },
  { value: "human-attn", label: "En atención humana",         icon: User,         countKey: "humanAttn" },
  { value: "with-bot",   label: "Con bot",                    icon: Bot,          countKey: "withBot" },
  { value: "resolved",   label: "Resueltas",                  icon: CheckCircle2, countKey: "resolved" },
];

export function ConversationList({
  items,
  selectedId,
  onSelect,
  bulkSelected,
  onBulkToggle,
  onBulkSelectAll,
  onBulkClear,
  onBulkAssign,
  onBulkResolve,
  view,
  onViewChange,
  lifecycle,
  onLifecycleChange,
  channel,
  onChannelChange,
  queue,
  onQueueChange,
  country,
  onCountryChange,
  queueStats,
  search,
  onSearchChange,
  counts,
}: Props) {
  // Bulk actions solo para supervisor+. Para un agente común ocultamos los
  // checkbox y la toolbar — el backend también rechaza bulk ops con 403 si
  // intenta forzarlo, esto es defensa en profundidad + UX.
  const { isSupervisor } = useRole();
  const canBulk = isSupervisor;
  const bulkMode = canBulk && bulkSelected.size > 0;
  const allVisibleSelected = items.length > 0 && items.every((i) => bulkSelected.has(i.id));

  // Equipo real desde backend (profiles con role agente/supervisor/admin).
  // Usado en bulk assign + sidebar "Asignado a".
  const { data: agents = [] } = useAgents();

  return (
    <div className="w-[340px] bg-panel border-r border-border flex flex-col shrink-0">
      {/* ===== BULK ACTIONS BAR (cuando hay selección) ===== */}
      {bulkMode && (
        <div className="border-b border-accent/40 bg-accent/10 px-3 py-2">
          <div className="flex items-center justify-between mb-2">
            <div className="text-[11px] font-semibold text-accent">
              {bulkSelected.size} seleccionada{bulkSelected.size === 1 ? "" : "s"}
            </div>
            <button
              onClick={onBulkClear}
              className="text-[11px] text-fg-muted hover:text-fg flex items-center gap-1"
            >
              <X className="w-3 h-3" /> Cancelar
            </button>
          </div>
          <div className="flex gap-1 flex-wrap">
            <Dropdown
              align="left"
              trigger={
                <Button variant="default" size="sm">
                  <UserCheck className="w-3 h-3" /> Asignar a…
                </Button>
              }
            >
              {(close) => (
                <>
                  <DropdownLabel>Reasignar {bulkSelected.size} a</DropdownLabel>
                  {agents.length === 0 && (
                    <div className="px-3 py-2 text-[11px] text-fg-dim">Cargando equipo…</div>
                  )}
                  {agents.map((a) => (
                    <DropdownItem key={a.id} onClick={() => { onBulkAssign(a.id); close(); }}>
                      <div className={`w-5 h-5 rounded-full bg-gradient-to-br ${a.color} text-white text-[9px] font-bold flex items-center justify-center`}>
                        {a.initials}
                      </div>
                      {a.name}
                    </DropdownItem>
                  ))}
                  <DropdownSeparator />
                  <DropdownItem onClick={() => { onBulkAssign(null); close(); }} variant="danger">
                    Quitar asignación a todas
                  </DropdownItem>
                </>
              )}
            </Dropdown>
            <Button variant="secondary" size="sm" onClick={onBulkResolve}>
              <CheckCircle2 className="w-3 h-3" /> Cerrar
            </Button>
            <Button variant="ghost" size="sm" onClick={onBulkSelectAll}>
              {allVisibleSelected ? "Quitar todas" : "Seleccionar todas"}
            </Button>
          </div>
        </div>
      )}

      {/* ============================ Header ============================ */}
      <div className="border-b border-border">
        <div className="flex items-center justify-between px-4 pt-3 pb-2">
          <div className="text-sm font-semibold">Inbox</div>
          <div className="flex items-center gap-1">
            <Dropdown
              align="right"
              trigger={
                <Button variant="ghost" size="icon-sm" title="Filtros avanzados">
                  <Filter className="w-3.5 h-3.5" />
                </Button>
              }
              className="w-72"
            >
              {(close) => (
                <>
                  <DropdownLabel>Vistas</DropdownLabel>
                  {SECONDARY_VIEWS.map((v) => {
                    const Icon = v.icon;
                    const count = counts[v.countKey] as number;
                    const active = view === v.value;
                    return (
                      <DropdownItem
                        key={v.value}
                        onClick={() => { onViewChange(v.value); close(); }}
                      >
                        <Icon className="w-3.5 h-3.5 text-fg-muted" />
                        <span className="flex-1">{v.label}</span>
                        <span className={`text-[10px] ${active ? "text-accent font-bold" : "text-fg-dim"}`}>
                          {count}
                        </span>
                      </DropdownItem>
                    );
                  })}

                  <DropdownSeparator />
                  <DropdownLabel>
                    Por cola de atención
                  </DropdownLabel>
                  {/* Siempre las mismas 6 sub-opciones por cola: AR, CL, EC, MX, CO, MP */}
                  {(["sales", "billing", "post-sales"] as Queue[]).map((q) => {
                    const active = queue === q;
                    const stats = queueStats?.[q] ?? {};
                    const totalForQueue = Object.values(stats).reduce((a, b) => a + b, 0);
                    const COUNTRIES_ORDER = ["AR", "CL", "EC", "MX", "CO", "MP"] as const;
                    return (
                      <CollapsibleSection
                        key={q}
                        title={
                          <span className="flex items-center gap-1.5">
                            <span className={cn("w-2 h-2 rounded-full", QUEUE_COLOR[q].split(" ")[0])} />
                            {QUEUE_LABEL[q]}
                          </span>
                        }
                        // Por default cerrada — si se abre queda el país en pantalla
                        // con 7 sub-filas y se vuelve invasivo visualmente.
                        // El user la abre cuando quiere filtrar por país.
                        defaultOpen={false}
                        rightAccessory={
                          <span className="text-[10px] text-fg-dim">
                            {totalForQueue}
                            {active && country && (
                              <span className="ml-1 text-accent">· {country}</span>
                            )}
                          </span>
                        }
                      >
                        <button
                          onClick={() => { onQueueChange(active ? null : q); onCountryChange(null); close(); }}
                          className={cn(
                            "w-full px-9 py-1 text-[11px] flex items-center gap-2 hover:bg-hover transition-colors",
                            active && country === null && "bg-accent/10 text-accent"
                          )}
                        >
                          <span className="flex-1 text-left">Todos los países</span>
                          <span className="text-[10px] text-fg-dim">{totalForQueue}</span>
                        </button>
                        {COUNTRIES_ORDER.map((cc) => {
                          const cActive = active && country === cc;
                          const n = stats[cc] ?? 0;
                          return (
                            <button
                              key={cc}
                              onClick={() => { onQueueChange(q); onCountryChange(cc); close(); }}
                              className={cn(
                                "w-full px-9 py-1 text-[11px] flex items-center gap-2 hover:bg-hover transition-colors",
                                cActive && "bg-accent/10 text-accent",
                                n === 0 && !cActive && "opacity-50"
                              )}
                              title={cc === "MP" ? "Multi-país (resto de países que no son AR/CL/EC/MX/CO)" : undefined}
                            >
                              {cc === "MP" ? (
                                <span className="text-[9px] font-bold text-fg-dim w-[15px] text-center">MP</span>
                              ) : (
                                <Flag iso={cc} size={10} />
                              )}
                              <span className="flex-1 text-left">
                                {cc === "MP" ? "Multi-país" : cc}
                              </span>
                              <span className="text-[10px] text-fg-dim">{n}</span>
                            </button>
                          );
                        })}
                      </CollapsibleSection>
                    );
                  })}

                  {/* === LIFECYCLE — collapsible === */}
                  <CollapsibleSection
                    title="Por lifecycle"
                    defaultOpen={false}
                    rightAccessory={lifecycle ? <span className="text-[9px] text-accent">{lifecycle}</span> : null}
                  >
                    {(["new", "hot", "customer", "cold"] as LifecycleStage[]).map((l) => {
                      const active = lifecycle === l;
                      const dot: Record<LifecycleStage, string> = { new: "bg-info", hot: "bg-warn", customer: "bg-success", cold: "bg-fg-dim" };
                      const label: Record<LifecycleStage, string> = { new: "New Lead", hot: "Hot Lead", customer: "Customer", cold: "Cold Lead" };
                      return (
                        <button
                          key={l}
                          onClick={() => { onLifecycleChange(active ? null : l); close(); }}
                          className={cn(
                            "w-full px-9 py-1 text-[11px] flex items-center gap-2 hover:bg-hover transition-colors",
                            active && "bg-accent/10 text-accent"
                          )}
                        >
                          <span className={cn("w-1.5 h-1.5 rounded-full", dot[l])} />
                          <span className="flex-1 text-left">{label[l]}</span>
                          <span className="text-[10px] text-fg-dim">{counts.byLifecycle[l]}</span>
                        </button>
                      );
                    })}
                  </CollapsibleSection>

                  {/* === CANAL — collapsible === */}
                  <CollapsibleSection
                    title="Por canal"
                    defaultOpen={false}
                    rightAccessory={channel ? <span className="text-[9px] text-accent">{channel}</span> : null}
                  >
                    <button
                      onClick={() => { onChannelChange(channel === "whatsapp" ? null : "whatsapp"); close(); }}
                      className={cn(
                        "w-full px-9 py-1 text-[11px] flex items-center gap-2 hover:bg-hover",
                        channel === "whatsapp" && "bg-accent/10 text-accent"
                      )}
                    >
                      <Smartphone className="w-3 h-3 text-success" />
                      <span className="flex-1 text-left">WhatsApp</span>
                      <span className="text-[10px] text-fg-dim">{counts.byChannel.whatsapp}</span>
                    </button>
                    <button
                      onClick={() => { onChannelChange(channel === "widget" ? null : "widget"); close(); }}
                      className={cn(
                        "w-full px-9 py-1 text-[11px] flex items-center gap-2 hover:bg-hover",
                        channel === "widget" && "bg-accent/10 text-accent"
                      )}
                    >
                      <MessageSquare className="w-3 h-3 text-accent" />
                      <span className="flex-1 text-left">Widget</span>
                      <span className="text-[10px] text-fg-dim">{counts.byChannel.widget}</span>
                    </button>
                  </CollapsibleSection>

                  {/* === ASIGNADO A — collapsible === */}
                  <CollapsibleSection
                    title="Asignado a"
                    defaultOpen={false}
                    rightAccessory={<span className="text-[9px] text-fg-dim">{agents.length}</span>}
                  >
                    {agents.map((a) => (
                      <button
                        key={a.id}
                        onClick={() => { close(); }}
                        className="w-full px-9 py-1 text-[11px] flex items-center gap-2 hover:bg-hover"
                      >
                        <div className={`w-4 h-4 rounded-full bg-gradient-to-br ${a.color} text-white text-[8px] font-bold flex items-center justify-center`}>
                          {a.initials}
                        </div>
                        <span className="flex-1 text-left truncate">{a.name}</span>
                      </button>
                    ))}
                  </CollapsibleSection>

                  {/* === TAGS — collapsible === */}
                  <CollapsibleSection
                    title="Por tag"
                    defaultOpen={false}
                  >
                    <div className="px-9 py-1.5 flex flex-wrap gap-1">
                      {["cardio", "amir-interest", "italiano-staff", "residente", "primer-contacto", "urgente", "objeción-precio", "follow-up"].map((t) => (
                        <button
                          key={t}
                          onClick={() => { close(); }}
                          className="text-[10px] px-1.5 py-0.5 rounded bg-hover hover:bg-border text-fg-muted hover:text-fg"
                        >
                          {t}
                        </button>
                      ))}
                    </div>
                  </CollapsibleSection>

                  <DropdownSeparator />
                  <DropdownItem onClick={() => { onViewChange("all"); onLifecycleChange(null); onChannelChange(null); onQueueChange(null); onCountryChange(null); close(); }} variant="danger">
                    <X className="w-3 h-3" /> Limpiar todos los filtros
                  </DropdownItem>
                </>
              )}
            </Dropdown>
            <Button variant="ghost" size="icon-sm" title="Refrescar">
              <RefreshCw className="w-3.5 h-3.5" />
            </Button>
          </div>
        </div>

        {/* Search */}
        <div className="px-4 pb-2 relative">
          <Input
            className="pl-8"
            placeholder="Buscar por nombre, email o mensaje..."
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
          />
          <Search className="w-3.5 h-3.5 absolute left-6 top-2.5 text-fg-dim pointer-events-none" />
        </div>

        {/* === VISTAS PRINCIPALES (solo 3 chips) === */}
        <div className="px-2 pb-3 flex gap-1">
          {PRIMARY_VIEWS.map((v) => {
            const Icon = v.icon;
            const count = counts[v.countKey] as number;
            const active = view === v.value;
            return (
              <button
                key={v.value}
                onClick={() => onViewChange(v.value)}
                className={cn(
                  "flex-1 text-[11px] px-2 py-1.5 rounded transition-colors flex items-center justify-center gap-1.5",
                  active
                    ? "bg-accent/15 text-accent"
                    : "text-fg-muted hover:bg-hover hover:text-fg"
                )}
              >
                <Icon className="w-3 h-3" />
                <span>{v.label}</span>
                <span className={cn("text-[10px]", active ? "opacity-90" : "opacity-50")}>
                  {count}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* ============================ Lista ============================ */}
      <div className="flex-1 overflow-y-auto scroll-thin">
        {items.length === 0 ? (
          <div className="p-8 text-center text-fg-dim text-xs">
            No hay conversaciones que coincidan con los filtros.
          </div>
        ) : (
          items.map((item) => {
            const active = item.id === selectedId;
            const isChecked = bulkSelected.has(item.id);
            return (
              <div
                key={item.id}
                className={cn(
                  "group w-full text-left border-b border-border transition-colors flex",
                  active ? "bg-card border-l-2 border-l-accent" : "hover:bg-hover",
                  isChecked && "bg-accent/10",
                  !item.unread && "opacity-75"
                )}
              >
                {/* Columna del checkbox — siempre 24px, evita pisarse con avatar.
                    Para rol agente el checkbox no aparece (no puede hacer bulk). */}
                {canBulk && (
                  <div
                    className={cn(
                      "shrink-0 flex items-center justify-center transition-all",
                      bulkMode || isChecked ? "w-7 opacity-100" : "w-0 opacity-0 group-hover:w-7 group-hover:opacity-100"
                    )}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <input
                      type="checkbox"
                      checked={isChecked}
                      onChange={() => onBulkToggle(item.id)}
                      className="w-3.5 h-3.5 rounded border-border bg-bg text-accent focus:ring-accent focus:ring-1 cursor-pointer"
                    />
                  </div>
                )}

                <button
                  onClick={() => onSelect(item.id)}
                  className="flex-1 text-left px-4 py-3 min-w-0"
                >
                <div className="flex items-start gap-3">
                  <Avatar
                    initials={item.contact.initials}
                    gradient={item.contact.avatarColor}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline justify-between gap-2">
                      <div className="text-sm font-medium truncate flex items-center gap-1.5">
                        <Flag iso={item.contact.country} size={11} />
                        {item.contact.name}
                        {item.unread && <span className="w-1.5 h-1.5 rounded-full bg-accent" />}
                      </div>
                      <div className="text-[10px] text-fg-dim shrink-0">{item.lastMessageAt}</div>
                    </div>
                    <div className="text-xs text-fg-muted truncate mt-0.5">{item.lastMessage}</div>
                    <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                      <span className={cn("text-[10px] px-1.5 py-0.5 rounded font-medium", QUEUE_COLOR[item.queue])}>
                        {QUEUE_LABEL[item.queue]}
                      </span>
                      <Badge variant={item.lifecycle}>{LIFECYCLE_LABEL[item.lifecycle]}</Badge>
                      <Badge variant={item.channel}>{item.channel === "whatsapp" ? "WA" : "Widget"}</Badge>
                      {item.needsHuman && <Badge variant="warn">⚠ Humano</Badge>}
                      {item.botPaused && (
                        <Badge variant="muted" title="Bot pausado">
                          <Bot className="w-2.5 h-2.5" /> Off
                        </Badge>
                      )}
                      {item.status === "resolved" && (
                        <Badge variant="success">
                          <CheckCircle2 className="w-2.5 h-2.5" /> Resuelta
                        </Badge>
                      )}
                    </div>
                  </div>
                </div>
                </button>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

function ChannelChip({
  active, onClick, label, count, icon, color = "muted",
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count: number;
  icon: React.ReactNode | null;
  color?: "success" | "accent" | "muted";
}) {
  const map: Record<string, string> = {
    success: active ? "bg-success/20 text-success" : "text-fg-muted hover:bg-success/10 hover:text-success",
    accent:  active ? "bg-accent/20 text-accent"   : "text-fg-muted hover:bg-accent/10 hover:text-accent",
    muted:   active ? "bg-fg/10 text-fg"           : "text-fg-muted hover:bg-hover",
  };
  return (
    <button
      onClick={onClick}
      className={cn(
        "text-[10px] px-2 py-0.5 rounded-full whitespace-nowrap flex items-center gap-1 transition-colors",
        map[color]
      )}
    >
      {icon}
      {label} <span className="opacity-60">{count}</span>
    </button>
  );
}

function LifecycleChip({
  active, onClick, color, label, count,
}: {
  active: boolean;
  onClick: () => void;
  color: "info" | "warn" | "success" | "muted" | "neutral";
  label: string;
  count: number;
}) {
  const map: Record<string, string> = {
    info:    active ? "bg-info/20 text-info" : "bg-info/10 text-info hover:bg-info/15",
    warn:    active ? "bg-warn/20 text-warn" : "bg-warn/10 text-warn hover:bg-warn/15",
    success: active ? "bg-success/20 text-success" : "bg-success/10 text-success hover:bg-success/15",
    muted:   active ? "bg-zinc-700/60 text-fg" : "bg-zinc-700/40 text-fg-dim hover:bg-zinc-700/60",
    neutral: active ? "bg-fg/10 text-fg" : "bg-hover text-fg-muted hover:bg-border",
  };
  const dot: Record<string, string> = {
    info: "bg-info", warn: "bg-warn", success: "bg-success", muted: "bg-fg-dim", neutral: "bg-fg-muted",
  };
  return (
    <button
      onClick={onClick}
      className={cn(
        "text-[10px] px-2 py-0.5 rounded-full whitespace-nowrap flex items-center gap-1.5 transition-colors",
        map[color]
      )}
    >
      <span className={cn("w-1.5 h-1.5 rounded-full", dot[color])} />
      {label} {count}
    </button>
  );
}
