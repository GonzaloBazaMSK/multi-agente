"use client";

import { useState } from "react";
import {
  ExternalLink, Sparkles, ArrowRight, DollarSign, AlertCircle, Copy,
  ChevronDown, ChevronUp, FileText, Calendar, CreditCard, TrendingUp,
} from "lucide-react";
import { Avatar } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Flag } from "@/components/ui/flag";
import type { ContactDetail, DebtStatus } from "@/lib/mock-data";

const DEBT_STATUS: Record<DebtStatus, { label: string; color: string; dot: string }> = {
  ok:        { label: "Al día",       color: "text-success", dot: "bg-success" },
  due_soon:  { label: "Vence pronto", color: "text-warn",    dot: "bg-warn" },
  overdue:   { label: "En mora",      color: "text-danger",  dot: "bg-danger" },
  suspended: { label: "Suspendido",   color: "text-danger",  dot: "bg-danger" },
};

function formatMoney(amount: number, currency: string): string {
  return new Intl.NumberFormat("es-AR", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
}

interface Props {
  contact: ContactDetail | null;
}

export function ContactPanel({ contact }: Props) {
  const [insightsOpen, setInsightsOpen] = useState(false);

  if (!contact) return null;

  const openZohoContact = () => {
    if (!contact.zohoId) return;
    window.open(`https://crm.zoho.com/crm/tab/Contacts/${contact.zohoId}`, "_blank", "noopener,noreferrer");
  };
  const openZohoCobranzas = () => {
    if (!contact.zohoId) return;
    window.open(`https://crm.zoho.com/crm/tab/Contacts/${contact.zohoId}/SalesOrders`, "_blank", "noopener,noreferrer");
  };

  return (
    <aside className="w-80 bg-panel border-l border-border overflow-y-auto scroll-thin shrink-0">
      {/* HEADER */}
      <div className="p-4 border-b border-border">
        <div className="flex items-center gap-3">
          <Avatar
            initials={contact.name[0]}
            gradient="from-pink-500 to-fuchsia-600"
            size="lg"
          />
          <div className="min-w-0">
            <div className="text-sm font-semibold flex items-center gap-1.5">
              <Flag iso={contact.country} size={12} />
              {contact.name}
            </div>
            <div className="text-[11px] text-fg-dim truncate">{contact.email}</div>
            <div className="text-[11px] text-fg-dim truncate">{contact.phone}</div>
          </div>
        </div>
      </div>

      <div className="p-4 space-y-4 text-xs">
        {/* ============= INSIGHTS IA — Collapsible ============= */}
        <button
          onClick={() => setInsightsOpen((s) => !s)}
          className="w-full border border-accent/40 bg-accent/10 hover:bg-accent/15 rounded-md px-3 py-2 flex items-center justify-between transition-colors group"
        >
          <div className="flex items-center gap-2 text-accent">
            <Sparkles className="w-3.5 h-3.5" />
            <span className="text-[11px] font-semibold uppercase tracking-wider">Insights IA</span>
            <span className="text-[10px] bg-accent/30 text-accent rounded-full w-5 h-5 flex items-center justify-center font-bold animate-pulse">
              {contact.ai.scoringReasons.length}
            </span>
          </div>
          {insightsOpen ? (
            <ChevronUp className="w-3.5 h-3.5 text-accent" />
          ) : (
            <ChevronDown className="w-3.5 h-3.5 text-accent group-hover:translate-y-0.5 transition-transform" />
          )}
        </button>

        {insightsOpen && (
          <div className="border border-accent/20 bg-accent/5 rounded-md p-3 space-y-2.5 -mt-2">
            <div>
              <div className="text-fg-dim text-[10px] mb-0.5">Resumen</div>
              <div className="text-[11px] leading-relaxed">{contact.ai.summary}</div>
            </div>
            <div>
              <div className="text-fg-dim text-[10px] mb-0.5 flex items-center gap-1">
                <ArrowRight className="w-2.5 h-2.5" /> Próximo paso sugerido
              </div>
              <div className="text-[11px] leading-relaxed text-fg">{contact.ai.nextStep}</div>
            </div>
            <div>
              <div className="text-fg-dim text-[10px] mb-0.5">Razones del scoring</div>
              <ul className="space-y-0.5">
                {contact.ai.scoringReasons.map((r, i) => (
                  <li key={i} className="text-[11px] leading-snug flex gap-1.5">
                    <span className="text-accent mt-0.5">•</span>
                    <span>{r}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}

        {/* ============= CARD: CONTACTO ============= */}
        <CardSection
          title="Contacto"
          action={
            <Button
              variant="ghost"
              size="sm"
              onClick={openZohoContact}
              disabled={!contact.zohoId}
              className="text-[10px] h-6"
              title={contact.zohoId ? "Ver contacto en Zoho CRM" : "Sin ID de Zoho"}
            >
              Ver en Zoho <ExternalLink className="w-2.5 h-2.5" />
            </Button>
          }
        >
          <div className="space-y-3">
            <Section title="Profesional">
              {contact.professional.profession && <Row label="Profesión"    value={contact.professional.profession} />}
              {contact.professional.specialty &&  <Row label="Especialidad" value={contact.professional.specialty} />}
              {contact.professional.cargo &&      <Row label="Cargo"        value={contact.professional.cargo} />}
              {contact.professional.workplace &&  <Row label="Lugar"        value={contact.professional.workplace} />}
              {contact.professional.workArea &&   <Row label="Área"         value={contact.professional.workArea} />}
            </Section>

            {contact.jurisdictionalCert && (
              <Section title="Aval jurisdiccional">
                <div className="bg-success/10 border border-success/30 rounded px-2 py-1.5 text-success text-[11px]">
                  ✓ {contact.jurisdictionalCert.code} · {contact.jurisdictionalCert.name}
                </div>
              </Section>
            )}

            {contact.coursesTaken.length > 0 && (
              <Section title={`Cursos cursados (${contact.coursesTaken.length})`}>
                <ul className="space-y-1">
                  {contact.coursesTaken.slice(0, 4).map((c) => (
                    <li key={c} className="flex items-center gap-1.5">
                      <span className="w-1 h-1 bg-success rounded-full" />
                      {c}
                    </li>
                  ))}
                  {contact.coursesTaken.length > 4 && (
                    <li className="text-fg-dim text-[10px] pl-2">
                      + {contact.coursesTaken.length - 4} más
                    </li>
                  )}
                </ul>
              </Section>
            )}

            <Section title="Scoring">
              <ScoreBar label="Perfil" value={contact.scoring.profile} color="bg-success" />
              <ScoreBar label="Venta"  value={contact.scoring.sales}   color="bg-warn" />
            </Section>

            {contact.tags.length > 0 && (
              <Section title="Etiquetas">
                <div className="flex flex-wrap gap-1">
                  {contact.tags.map((t) => (
                    <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-hover">{t}</span>
                  ))}
                </div>
              </Section>
            )}
          </div>
        </CardSection>

        {/* ============= CARD: COBRANZAS RICO ============= */}
        {contact.cobranzas ? (
          <CardSection
            title="Cobranzas"
            action={
              <Button
                variant="ghost"
                size="sm"
                onClick={openZohoCobranzas}
                disabled={!contact.zohoId}
                className="text-[10px] h-6"
                title="Ver cobranzas en Zoho CRM"
              >
                Ver en Zoho <ExternalLink className="w-2.5 h-2.5" />
              </Button>
            }
          >
            {(() => {
              const c = contact.cobranzas!;
              const s = DEBT_STATUS[c.status];
              const progressPct = Math.round((c.paidInstallments / c.totalInstallments) * 100);

              return (
                <div className="space-y-3">
                  {/* Estado destacado */}
                  <div className="flex flex-col items-center py-2">
                    <div className={`w-3 h-3 rounded-full ${s.dot} mb-1.5 shadow-lg`} style={{ boxShadow: `0 0 12px var(--tw-shadow-color)` }} />
                    <div className={`text-base font-bold ${s.color}`}>{s.label}</div>
                  </div>

                  {/* Resumen financiero */}
                  <div>
                    <div className="text-fg-dim text-[10px] uppercase tracking-wider mb-1.5 flex items-center gap-1">
                      <TrendingUp className="w-3 h-3" /> Resumen financiero
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div className={`rounded-md p-2 border ${c.overdueAmount > 0 ? "bg-danger/10 border-danger/30" : "bg-hover border-border"}`}>
                        <div className="text-[9px] text-fg-dim mb-0.5">Saldo vencido</div>
                        <div className={`text-sm font-bold tabular-nums ${c.overdueAmount > 0 ? "text-danger" : ""}`}>
                          {formatMoney(c.overdueAmount, c.currency)}
                        </div>
                      </div>
                      <div className="rounded-md p-2 border border-border bg-hover">
                        <div className="text-[9px] text-fg-dim mb-0.5">Saldo total</div>
                        <div className="text-sm font-bold tabular-nums">
                          {formatMoney(c.totalDueAmount, c.currency)}
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="space-y-1">
                    <Row label="Importe contrato" value={formatMoney(c.contractAmount, c.currency)} mono />
                    <Row label="Valor cuota"      value={formatMoney(c.installmentValue, c.currency)} mono />
                    <Row label="Último pago"      value={formatMoney(c.lastPaymentAmount, c.currency)} mono />
                  </div>

                  {/* Cuotas */}
                  <div>
                    <div className="text-fg-dim text-[10px] uppercase tracking-wider mb-1.5 flex items-center gap-1">
                      <FileText className="w-3 h-3" /> Cuotas
                    </div>
                    <div className="space-y-2">
                      <div>
                        <div className="flex justify-between text-[10px] mb-1">
                          <span className="text-fg-dim">Progreso de pago</span>
                          <span>{c.paidInstallments}/{c.totalInstallments} cuotas ({progressPct}%)</span>
                        </div>
                        <div className="bg-bg rounded-full h-1.5">
                          <div className="bg-success h-1.5 rounded-full" style={{ width: `${progressPct}%` }} />
                        </div>
                      </div>
                      <div className="grid grid-cols-3 gap-1.5">
                        <div className="bg-success/15 border border-success/30 rounded p-2 text-center">
                          <div className="text-success text-base font-bold tabular-nums">{c.paidInstallments}</div>
                          <div className="text-[9px] text-fg-dim mt-0.5">Pagas</div>
                        </div>
                        <div className="bg-danger/15 border border-danger/30 rounded p-2 text-center">
                          <div className="text-danger text-base font-bold tabular-nums">{c.overdueInstallments}</div>
                          <div className="text-[9px] text-fg-dim mt-0.5">Vencidas</div>
                        </div>
                        <div className="bg-info/15 border border-info/30 rounded p-2 text-center">
                          <div className="text-info text-base font-bold tabular-nums">{c.pendingInstallments}</div>
                          <div className="text-[9px] text-fg-dim mt-0.5">Pendientes</div>
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Detalle */}
                  <div>
                    <div className="text-fg-dim text-[10px] uppercase tracking-wider mb-1.5 flex items-center gap-1">
                      <AlertCircle className="w-3 h-3" /> Detalle
                    </div>
                    <div className="space-y-1">
                      <Row
                        label="Días de atraso"
                        value={c.daysOverdue > 0 ? `${c.daysOverdue} días` : "—"}
                        valueClass={c.daysOverdue > 0 ? "text-danger font-semibold" : ""}
                      />
                      <Row label="Estado gestión" value={c.contractStatus} valueClass={c.contractStatus === "Contrato baja" ? "text-warn" : ""} />
                      <Row label="Modo de pago"   value={c.paymentMethod} />
                      {c.nextDue && <Row label="Próximo venc." value={c.nextDue} />}
                    </div>
                  </div>

                  {c.paymentLink && (
                    <div className="flex gap-1 pt-2 border-t border-border">
                      <Button
                        variant="secondary"
                        size="sm"
                        className="flex-1 text-[10px]"
                        onClick={() => window.open(c.paymentLink, "_blank")}
                      >
                        <DollarSign className="w-3 h-3" /> Link de pago
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        title="Copiar link"
                        onClick={() => navigator.clipboard.writeText(c.paymentLink!)}
                      >
                        <Copy className="w-3 h-3" />
                      </Button>
                    </div>
                  )}
                </div>
              );
            })()}
          </CardSection>
        ) : (
          <div className="border border-border border-dashed rounded-md p-3 text-[11px] text-fg-dim text-center">
            Sin información de cobranzas
            <div className="text-[10px] mt-0.5 opacity-70">(sin compras previas)</div>
          </div>
        )}
      </div>
    </aside>
  );
}

function CardSection({
  title, action, children, defaultOpen = true,
}: { title: string; action?: React.ReactNode; children: React.ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border border-border rounded-md overflow-hidden">
      {/* Header — mismo patrón que Insights IA: título a la izquierda, acción y chevron a la derecha */}
      <div className="bg-card px-3 py-2 flex items-center justify-between border-b border-border gap-2">
        <button
          onClick={() => setOpen((s) => !s)}
          className="text-[10px] uppercase tracking-wider font-semibold text-fg-muted hover:text-fg flex-1 text-left"
        >
          {title}
        </button>
        <div className="flex items-center gap-2">
          {action}
          <button
            onClick={() => setOpen((s) => !s)}
            className="text-fg-dim hover:text-fg p-0.5"
            aria-label={open ? "Colapsar" : "Expandir"}
          >
            {open ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>
      {open && <div className="p-3">{children}</div>}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-fg-dim text-[10px] uppercase tracking-wider mb-1.5">{title}</div>
      <div className="space-y-1">{children}</div>
    </div>
  );
}

function Row({
  label, value, mono = false, valueClass = "",
}: { label: string; value: string; mono?: boolean; valueClass?: string }) {
  return (
    <div className="flex justify-between gap-2 items-baseline">
      <span className="text-fg-dim shrink-0 text-[11px]">{label}</span>
      <span className={`text-right text-[11px] ${mono ? "tabular-nums font-mono" : ""} ${valueClass}`}>
        {value}
      </span>
    </div>
  );
}

function ScoreBar({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-[10px]">
        <span>{label}</span>
        <span>{value}/100</span>
      </div>
      <div className="bg-bg rounded-full h-1">
        <div className={`${color} h-1 rounded-full`} style={{ width: `${value}%` }} />
      </div>
    </div>
  );
}
