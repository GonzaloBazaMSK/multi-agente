"use client";

/**
 * /analytics — Dashboard operativo del contact center.
 *
 * Secciones:
 *   1. KPIs macro (conversaciones, mensajes, activas, resueltas, hot leads)
 *   2. Estado ACTUAL (convs abiertas ahora, necesitan humano, stale >2h)
 *   3. SLA / TMR (takeover rate, % respondidas <15m, <1h, TMR p50/p90)
 *   4. Volumen diario (barras)
 *   5. Heatmap 7×24 (hora×día de la semana) — útil para decidir turnos
 *   6. Leaderboard de agentes humanos (convs atendidas + TMR individual)
 *   7. Breakdowns (canal, cola, país, lifecycle)
 *
 * Todas las métricas vienen de GET /api/v1/inbox/analytics?days=N.
 * La query pesa: percentile_cont + DISTINCT ON → cacheamos 60s client-side
 * (staleTime).
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Loader2,
  MessageSquare,
  Users,
  TrendingUp,
  CheckCircle2,
  Flame,
  Clock,
  AlertTriangle,
  UserCog,
  Gauge,
  ChevronRight,
} from "lucide-react";
import { api } from "@/lib/api";
import { Flag } from "@/components/ui/flag";
import { cn } from "@/lib/utils";

type AgentRow = {
  agent_id: string;
  agent_name: string;
  agent_email: string | null;
  convs_handled: number;
  tmr_p50_min: number | null;
  active_convs: number;
};

type Analytics = {
  totals: {
    conversations: number;
    messages: number;
    active_today: number;
    resolved: number;
    hot_leads: number;
    open_now: number;
    needs_human_now: number;
    stale_now: number;
  };
  sla: {
    answered_human: number;
    total_with_user: number;
    takeover_rate_pct: number;
    bot_only_rate_pct: number;
    under_15m_pct: number;
    under_60m_pct: number;
    tmr_p50_min: number;
    tmr_p90_min: number;
  };
  daily: { day: string; count: number }[];
  heatmap: { dow: number; hour: number; count: number }[];
  leaderboard: AgentRow[];
  by_channel: Record<string, number>;
  by_queue: Record<string, number>;
  by_country: Record<string, number>;
  by_lifecycle: Record<string, number>;
};

// Labels/colores locales para breakdowns
const QUEUE_LABELS: Record<string, string> = {
  sales: "Ventas",
  billing: "Cobranzas",
  "post-sales": "Post-venta",
  support: "Soporte",
};
const LIFECYCLE_LABELS: Record<string, string> = {
  new: "Nuevo",
  hot: "Hot",
  customer: "Cliente",
  cold: "Cold",
};
const CHANNEL_LABELS: Record<string, string> = {
  whatsapp: "WhatsApp",
  widget: "Widget web",
  botmaker: "Botmaker (legacy)",
  twilio: "Twilio",
};

export default function AnalyticsPage() {
  const [days, setDays] = useState(30);
  const q = useQuery<Analytics>({
    queryKey: ["analytics", days],
    queryFn: () => api.get(`/inbox/analytics?days=${days}`),
    staleTime: 60_000,
  });

  if (q.isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center text-fg-dim">
        <Loader2 className="w-5 h-5 animate-spin" />
      </div>
    );
  }
  if (!q.data)
    return (
      <div className="flex-1 flex items-center justify-center text-fg-dim">Sin datos</div>
    );
  const d = q.data;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 py-4 border-b border-border flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Analytics</h1>
          <p className="text-xs text-fg-dim mt-0.5">
            Dashboard operativo — SLA, TMR, leaderboard y volumen del contact center.
          </p>
        </div>
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="bg-bg border border-border rounded-md px-3 py-1.5 text-sm focus:outline-none focus:border-accent"
        >
          <option value={7}>Últimos 7 días</option>
          <option value={30}>Últimos 30 días</option>
          <option value={90}>Últimos 90 días</option>
        </select>
      </div>

      <div className="flex-1 overflow-y-auto scroll-thin p-6 space-y-6">
        {/* ═══════════ 1. KPIs macro (volumen total del período) ═══════════ */}
        <section>
          <h2 className="text-[10px] uppercase tracking-wider text-fg-muted mb-2">
            Volumen del período
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <KPI icon={<MessageSquare className="w-4 h-4 text-accent" />} label="Conversaciones" value={d.totals.conversations} />
            <KPI icon={<Users className="w-4 h-4 text-info" />} label="Mensajes" value={d.totals.messages} />
            <KPI icon={<TrendingUp className="w-4 h-4 text-warn" />} label="Activas hoy" value={d.totals.active_today} />
            <KPI icon={<CheckCircle2 className="w-4 h-4 text-success" />} label="Resueltas" value={d.totals.resolved} />
            <KPI icon={<Flame className="w-4 h-4 text-danger" />} label="Hot leads" value={d.totals.hot_leads} />
          </div>
        </section>

        {/* ═══════════ 2. Estado AHORA (snapshot live) ═══════════ */}
        <section>
          <h2 className="text-[10px] uppercase tracking-wider text-fg-muted mb-2">
            Estado ahora
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <KPI
              icon={<MessageSquare className="w-4 h-4 text-info" />}
              label="Convs abiertas"
              value={d.totals.open_now}
              sublabel="status open o pending"
            />
            <KPI
              icon={<UserCog className="w-4 h-4 text-warn" />}
              label="Necesitan humano"
              value={d.totals.needs_human_now}
              sublabel="flag needs_human activo"
              accent={d.totals.needs_human_now > 0 ? "warn" : undefined}
            />
            <KPI
              icon={<AlertTriangle className="w-4 h-4 text-danger" />}
              label="Sin respuesta >2h"
              value={d.totals.stale_now}
              sublabel="el agente asignado no respondió"
              accent={d.totals.stale_now > 0 ? "danger" : undefined}
            />
          </div>
        </section>

        {/* ═══════════ 3. SLA + TMR ═══════════ */}
        <section>
          <h2 className="text-[10px] uppercase tracking-wider text-fg-muted mb-2 flex items-center gap-1.5">
            <Gauge className="w-3 h-3" />
            SLA & tiempo de respuesta humana
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <SlaCard
              label="Takeover rate"
              value={`${d.sla.takeover_rate_pct}%`}
              sublabel={`${d.sla.answered_human}/${d.sla.total_with_user} convs con humano`}
              help="De todas las convs con al menos un mensaje del cliente, ¿en qué % intervino un humano?"
            />
            <SlaCard
              label="Bot-only resolution"
              value={`${d.sla.bot_only_rate_pct}%`}
              sublabel={`${d.sla.total_with_user - d.sla.answered_human} convs`}
              help="% de convs donde el bot solo resolvió (ningún humano respondió)"
              good={d.sla.bot_only_rate_pct > 50}
            />
            <SlaCard
              label="SLA <15 min"
              value={`${d.sla.under_15m_pct}%`}
              sublabel="de las atendidas por humano"
              help="% de convs con TMR ≤ 15 min (de las que recibieron respuesta humana)"
              good={d.sla.under_15m_pct >= 80}
              bad={d.sla.under_15m_pct < 50}
            />
            <SlaCard
              label="SLA <1h"
              value={`${d.sla.under_60m_pct}%`}
              sublabel="de las atendidas por humano"
              help="% de convs con TMR ≤ 60 min"
              good={d.sla.under_60m_pct >= 95}
              bad={d.sla.under_60m_pct < 80}
            />
          </div>

          <div className="grid grid-cols-2 gap-3 mt-3">
            <SlaCard
              label="TMR mediana (p50)"
              value={formatMinutes(d.sla.tmr_p50_min)}
              sublabel="de primer msg del cliente al primer msg humano"
              help="Mediana del tiempo a primera respuesta humana"
              good={d.sla.tmr_p50_min > 0 && d.sla.tmr_p50_min <= 15}
              bad={d.sla.tmr_p50_min > 60}
            />
            <SlaCard
              label="TMR p90"
              value={formatMinutes(d.sla.tmr_p90_min)}
              sublabel="9 de cada 10 responden en menos de esto"
              help="Percentil 90 del tiempo de respuesta humana"
              bad={d.sla.tmr_p90_min > 120}
            />
          </div>
        </section>

        {/* ═══════════ 4. Volumen diario ═══════════ */}
        <Card title="Conversaciones por día">
          <DailyChart daily={d.daily} />
        </Card>

        {/* ═══════════ 5. Heatmap 7×24 ═══════════ */}
        <Card title="Volumen por hora y día (hora local AR)">
          <Heatmap data={d.heatmap} />
        </Card>

        {/* ═══════════ 6. Leaderboard agentes ═══════════ */}
        <Card title="Leaderboard de agentes (del período)">
          <Leaderboard rows={d.leaderboard} />
        </Card>

        {/* ═══════════ 7. Breakdowns ═══════════ */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Card title="Por canal">
            <BreakdownList data={d.by_channel} labels={CHANNEL_LABELS} />
          </Card>
          <Card title="Por cola de atención">
            <BreakdownList data={d.by_queue} labels={QUEUE_LABELS} />
          </Card>
          <Card title="Por país (top 10)">
            <BreakdownList
              data={d.by_country}
              renderKey={(k) => (
                <span className="flex items-center gap-1.5">
                  <Flag iso={k} size={10} /> {k}
                </span>
              )}
            />
          </Card>
          <Card title="Por lifecycle">
            <BreakdownList data={d.by_lifecycle} labels={LIFECYCLE_LABELS} />
          </Card>
        </div>
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════
// Subcomponents
// ══════════════════════════════════════════════════════════════════════

function KPI({
  icon,
  label,
  value,
  sublabel,
  accent,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  sublabel?: string;
  accent?: "warn" | "danger";
}) {
  return (
    <div
      className={cn(
        "bg-card border border-border rounded-lg p-4",
        accent === "warn" && "border-warn/40 bg-warn/5",
        accent === "danger" && "border-danger/40 bg-danger/5",
      )}
    >
      <div className="flex items-center gap-2 text-xs text-fg-dim">
        {icon} {label}
      </div>
      <div className="text-2xl font-bold mt-1 tabular-nums">
        {value.toLocaleString("es-AR")}
      </div>
      {sublabel && <div className="text-[10px] text-fg-dim mt-0.5">{sublabel}</div>}
    </div>
  );
}

function SlaCard({
  label,
  value,
  sublabel,
  help,
  good,
  bad,
}: {
  label: string;
  value: string;
  sublabel?: string;
  help?: string;
  good?: boolean;
  bad?: boolean;
}) {
  return (
    <div
      className={cn(
        "bg-card border border-border rounded-lg p-4",
        good && "border-success/40 bg-success/5",
        bad && "border-danger/40 bg-danger/5",
      )}
      title={help}
    >
      <div className="text-[10px] uppercase tracking-wider text-fg-muted">{label}</div>
      <div
        className={cn(
          "text-2xl font-bold mt-1 tabular-nums",
          good && "text-success",
          bad && "text-danger",
        )}
      >
        {value}
      </div>
      {sublabel && (
        <div className="text-[10px] text-fg-dim mt-0.5 leading-tight">{sublabel}</div>
      )}
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-card border border-border rounded-lg p-4">
      <div className="text-[10px] uppercase tracking-wider text-fg-muted mb-3">{title}</div>
      {children}
    </div>
  );
}

function DailyChart({ daily }: { daily: { day: string; count: number }[] }) {
  const max = Math.max(1, ...daily.map((x) => x.count));
  if (daily.length === 0)
    return <div className="text-fg-dim text-xs italic">Sin datos en el período</div>;
  return (
    <>
      <div className="flex items-end gap-0.5 h-40">
        {daily.map((day) => (
          <div
            key={day.day}
            className="flex-1 flex flex-col items-center gap-1 group min-w-0"
            title={`${day.day}: ${day.count} convs`}
          >
            <div className="text-[9px] text-fg-dim opacity-0 group-hover:opacity-100 transition-opacity tabular-nums">
              {day.count}
            </div>
            <div
              className="w-full bg-accent/30 group-hover:bg-accent rounded-t transition-colors"
              style={{ height: `${(day.count / max) * 100}%`, minHeight: 2 }}
            />
          </div>
        ))}
      </div>
      <div className="text-[10px] text-fg-dim mt-1 flex justify-between">
        <span>{daily[0]?.day}</span>
        <span>{daily[daily.length - 1]?.day}</span>
      </div>
    </>
  );
}

function Heatmap({ data }: { data: { dow: number; hour: number; count: number }[] }) {
  // DOW postgres: 0=domingo, 6=sábado. Reordenamos a semana laboral: Lun-Dom.
  const DOW_LABELS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];
  const DOW_ORDER = [1, 2, 3, 4, 5, 6, 0]; // Lun → Dom
  const grid: Record<string, number> = {};
  for (const r of data) grid[`${r.dow}-${r.hour}`] = r.count;
  const max = Math.max(1, ...data.map((r) => r.count));

  return (
    <>
      <div className="flex gap-0.5 items-center text-[9px] text-fg-dim">
        <div className="w-8" />
        {Array.from({ length: 24 }, (_, h) => (
          <div key={h} className="flex-1 text-center tabular-nums">
            {h % 3 === 0 ? h : ""}
          </div>
        ))}
      </div>
      {DOW_ORDER.map((dow, i) => (
        <div key={dow} className="flex gap-0.5 items-center mt-0.5">
          <div className="w-8 text-[10px] text-fg-dim">{DOW_LABELS[i]}</div>
          {Array.from({ length: 24 }, (_, h) => {
            const v = grid[`${dow}-${h}`] ?? 0;
            const intensity = v === 0 ? 0 : Math.max(0.1, v / max);
            return (
              <div
                key={h}
                className="flex-1 h-5 rounded-[2px] relative"
                style={{
                  backgroundColor:
                    v === 0 ? "rgba(255,255,255,0.04)" : `rgba(168, 85, 247, ${intensity})`,
                }}
                title={`${DOW_LABELS[i]} ${h}:00 · ${v} conv${v === 1 ? "" : "s"}`}
              />
            );
          })}
        </div>
      ))}
      <div className="text-[9px] text-fg-dim mt-2 flex items-center gap-1">
        Menos
        <div className="flex gap-0.5">
          {[0.1, 0.3, 0.5, 0.7, 1].map((i) => (
            <div
              key={i}
              className="w-3 h-3 rounded-[2px]"
              style={{ backgroundColor: `rgba(168, 85, 247, ${i})` }}
            />
          ))}
        </div>
        Más · máx {max} convs/hora
      </div>
    </>
  );
}

function Leaderboard({ rows }: { rows: AgentRow[] }) {
  if (rows.length === 0)
    return (
      <div className="text-fg-dim text-xs italic">
        Nadie respondió convs en este período.
      </div>
    );
  const maxHandled = Math.max(1, ...rows.map((r) => r.convs_handled));
  return (
    <div className="space-y-1">
      <div className="grid grid-cols-[1fr_80px_80px_80px] gap-2 text-[10px] text-fg-muted uppercase tracking-wider pb-1 border-b border-border">
        <div>Agente</div>
        <div className="text-right">Atendidas</div>
        <div className="text-right">TMR</div>
        <div className="text-right">Activas</div>
      </div>
      {rows.map((r) => (
        <div
          key={r.agent_id}
          className="grid grid-cols-[1fr_80px_80px_80px] gap-2 text-xs items-center py-1 hover:bg-hover rounded px-1 transition-colors"
        >
          <div className="flex items-center gap-2 min-w-0">
            <div className="w-6 h-6 rounded-full bg-gradient-to-br from-pink-500 to-fuchsia-600 text-white text-[9px] font-bold flex items-center justify-center shrink-0">
              {(r.agent_name || "??").slice(0, 2).toUpperCase()}
            </div>
            <div className="min-w-0">
              <div className="truncate font-medium">{r.agent_name}</div>
              {r.agent_email && (
                <div className="text-[10px] text-fg-dim truncate">{r.agent_email}</div>
              )}
            </div>
          </div>
          <div className="text-right relative tabular-nums">
            <div
              className="absolute inset-y-0 right-0 bg-accent/15 rounded"
              style={{ width: `${(r.convs_handled / maxHandled) * 100}%` }}
            />
            <span className="relative z-10 pr-1">{r.convs_handled}</span>
          </div>
          <div className="text-right tabular-nums">
            {r.tmr_p50_min !== null ? formatMinutes(r.tmr_p50_min) : "—"}
          </div>
          <div className="text-right tabular-nums">{r.active_convs}</div>
        </div>
      ))}
    </div>
  );
}

function BreakdownList({
  data,
  labels,
  renderKey,
}: {
  data: Record<string, number>;
  labels?: Record<string, string>;
  renderKey?: (k: string) => React.ReactNode;
}) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  if (entries.length === 0)
    return <div className="text-fg-dim text-xs italic">Sin datos</div>;
  const total = entries.reduce((s, [, v]) => s + v, 0);
  return (
    <div className="space-y-1.5">
      {entries.map(([k, v]) => (
        <div key={k} className="flex items-center gap-3">
          <div className="text-xs flex-1 truncate">
            {renderKey ? renderKey(k) : labels?.[k] ?? k}
          </div>
          <div className="flex-1 bg-bg rounded h-1.5 relative">
            <div
              className="bg-accent h-1.5 rounded"
              style={{ width: `${(v / max) * 100}%` }}
            />
          </div>
          <div className="text-xs tabular-nums text-fg w-12 text-right">{v}</div>
          <div className="text-[10px] tabular-nums text-fg-dim w-10 text-right">
            {Math.round((v / total) * 100)}%
          </div>
        </div>
      ))}
    </div>
  );
}

function formatMinutes(mins: number | null | undefined): string {
  if (mins === null || mins === undefined || mins === 0) return "—";
  if (mins < 1) return `${Math.round(mins * 60)}s`;
  if (mins < 60) return `${Math.round(mins)}m`;
  const h = Math.floor(mins / 60);
  const m = Math.round(mins - h * 60);
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}
