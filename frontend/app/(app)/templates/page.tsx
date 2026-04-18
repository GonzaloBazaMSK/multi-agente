"use client";

/**
 * /templates — gestión de plantillas HSM de WhatsApp (Meta Cloud API).
 *
 * Paridad con widget/templates.html: lista con stats por estado (APPROVED /
 * PENDING / REJECTED), filtro por estado + búsqueda, creación y eliminación.
 *
 * Media uploads para headers (image/video/document) todavía NO migrados —
 * requieren upload resumible a Meta + handle. Para eso, por ahora, usar la
 * UI vieja (/admin/templates-ui). El core del CRUD texto + botones quick
 * reply / URL / phone ya está acá.
 *
 * Auth: leer = supervisor+; crear/borrar = admin (enforced en backend).
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Clock,
  Loader2,
  Plus,
  Search,
  Trash2,
  XCircle,
} from "lucide-react";
import { RoleGate, useRole } from "@/lib/auth";
import { NoAccess } from "@/components/ui/coming-soon";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type Button_ = {
  type: "QUICK_REPLY" | "URL" | "PHONE_NUMBER";
  text: string;
  url?: string;
  phone_number?: string;
};

type Template = {
  id: string;
  name: string;
  language: string;
  category: "MARKETING" | "UTILITY" | "AUTHENTICATION";
  status: "APPROVED" | "PENDING" | "REJECTED" | "PAUSED";
  body: string;
  header: { format: string; text: string } | null;
  footer: string;
  buttons: Button_[];
  body_var_count: number;
  rejected_reason: string;
};

type StatusFilter = "ALL" | "APPROVED" | "PENDING" | "REJECTED";

const STATUS_COLOR: Record<Template["status"], string> = {
  APPROVED: "bg-success/15 text-success",
  PENDING:  "bg-warn/15 text-warn",
  REJECTED: "bg-danger/15 text-danger",
  PAUSED:   "bg-border text-fg-dim",
};

const LANGUAGES = ["es_AR", "es_MX", "es", "en_US", "pt_BR"] as const;
const CATEGORIES: Template["category"][] = ["MARKETING", "UTILITY", "AUTHENTICATION"];

const EMPTY_DRAFT = {
  name: "",
  category: "MARKETING" as Template["category"],
  language: "es_AR" as (typeof LANGUAGES)[number],
  body_text: "",
  footer_text: "",
  buttons: [] as Button_[],
};

export default function TemplatesPage() {
  return (
    <RoleGate min="supervisor" denyFallback={<NoAccess requiredRole="supervisor o admin" />}>
      <TemplatesPageInner />
    </RoleGate>
  );
}

function TemplatesPageInner() {
  const qc = useQueryClient();
  const { isAdmin } = useRole();
  const [filter, setFilter] = useState<StatusFilter>("ALL");
  const [search, setSearch] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const [formError, setFormError] = useState<string | null>(null);

  const q = useQuery<{ templates: Template[]; error?: string }>({
    queryKey: ["templates", "all"],
    queryFn: () => api.get("/templates/hsm/all"),
    staleTime: 60_000,
  });

  const create = useMutation({
    mutationFn: () => api.post("/templates/hsm/create", draft),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["templates"] });
      setShowForm(false);
      setDraft(EMPTY_DRAFT);
      setFormError(null);
      alert("Plantilla enviada. Meta la revisa en 24-48h.");
    },
    onError: (e: Error) => setFormError(e.message || "Error creando plantilla"),
  });

  const remove = useMutation({
    mutationFn: (name: string) =>
      api.delete(`/templates/hsm/${encodeURIComponent(name)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["templates"] }),
    onError: (e: Error) => alert(e.message || "Error eliminando"),
  });

  const templates = q.data?.templates ?? [];
  const stats = useMemo(() => {
    const s = { ALL: templates.length, APPROVED: 0, PENDING: 0, REJECTED: 0 };
    for (const t of templates) {
      if (t.status === "APPROVED") s.APPROVED++;
      else if (t.status === "PENDING") s.PENDING++;
      else if (t.status === "REJECTED") s.REJECTED++;
    }
    return s;
  }, [templates]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return templates.filter((t) => {
      if (filter !== "ALL" && t.status !== filter) return false;
      if (!q) return true;
      return t.name.toLowerCase().includes(q) || (t.body || "").toLowerCase().includes(q);
    });
  }, [templates, filter, search]);

  const canCreate =
    isAdmin &&
    /^[a-z0-9_]+$/.test(draft.name) &&
    draft.body_text.trim().length > 0 &&
    draft.body_text.length <= 1024;

  const handleDelete = (name: string) => {
    if (!confirm(`Eliminar la plantilla "${name}"? No se puede deshacer.`)) return;
    remove.mutate(name);
  };

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 py-4 border-b border-border flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Plantillas HSM</h1>
          <p className="text-xs text-fg-dim mt-0.5">
            Mensajes pre-aprobados por Meta para abrir ventanas de 24h fuera del marco de
            respuesta libre.
          </p>
        </div>
        {isAdmin && (
          <Button size="sm" onClick={() => { setShowForm(true); setFormError(null); }}>
            <Plus className="w-3.5 h-3.5" /> Nueva plantilla
          </Button>
        )}
      </div>

      {/* Stats */}
      <div className="px-6 py-3 border-b border-border grid grid-cols-4 gap-3">
        <StatPill active={filter === "ALL"}       onClick={() => setFilter("ALL")}       label="Total"     count={stats.ALL} />
        <StatPill active={filter === "APPROVED"}  onClick={() => setFilter("APPROVED")}  label="Aprobadas" count={stats.APPROVED} icon={CheckCircle2} color="text-success" />
        <StatPill active={filter === "PENDING"}   onClick={() => setFilter("PENDING")}   label="Pendientes" count={stats.PENDING} icon={Clock} color="text-warn" />
        <StatPill active={filter === "REJECTED"}  onClick={() => setFilter("REJECTED")}  label="Rechazadas" count={stats.REJECTED} icon={XCircle} color="text-danger" />
      </div>

      {/* Search */}
      <div className="px-6 py-3 border-b border-border flex items-center gap-2">
        <Search className="w-4 h-4 text-fg-muted" />
        <Input
          className="max-w-sm"
          placeholder="Buscar por nombre o cuerpo..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {q.data?.error && (
          <div className="text-[11px] text-danger ml-auto">
            {q.data.error} — revisá WHATSAPP_WABA_ID en el .env del servidor
          </div>
        )}
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto scroll-thin">
        {q.isLoading && (
          <div className="p-8 text-center text-fg-dim text-xs">
            <Loader2 className="inline w-4 h-4 animate-spin mr-2" />Cargando plantillas…
          </div>
        )}
        {q.error && (
          <div className="p-8 text-center text-danger text-xs">
            {(q.error as Error).message}
          </div>
        )}
        {!q.isLoading && filtered.length === 0 && (
          <div className="p-8 text-center text-fg-dim text-xs">
            {search || filter !== "ALL"
              ? "Sin plantillas que matcheen los filtros."
              : "Todavía no hay plantillas creadas."}
          </div>
        )}

        <div className="divide-y divide-border">
          {filtered.map((t) => (
            <div key={t.id || t.name} className="px-6 py-3 hover:bg-hover transition-colors">
              <div className="flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-mono text-sm">{t.name}</span>
                    <span className={cn("text-[9px] px-1.5 py-0.5 rounded", STATUS_COLOR[t.status])}>
                      {t.status}
                    </span>
                    <span className="text-[10px] text-fg-dim">{t.category}</span>
                    <span className="text-[10px] text-fg-dim">·</span>
                    <span className="text-[10px] text-fg-dim font-mono">{t.language}</span>
                    {t.body_var_count > 0 && (
                      <span className="text-[10px] text-accent">· {t.body_var_count} variables</span>
                    )}
                  </div>
                  {t.header?.text && (
                    <div className="text-[11px] font-semibold text-fg mb-0.5">{t.header.text}</div>
                  )}
                  <div className="text-[12px] text-fg-muted whitespace-pre-wrap break-words">
                    {t.body}
                  </div>
                  {t.footer && (
                    <div className="text-[10px] text-fg-dim mt-1">— {t.footer}</div>
                  )}
                  {t.buttons?.length > 0 && (
                    <div className="flex gap-1 mt-2 flex-wrap">
                      {t.buttons.map((b, i) => (
                        <span
                          key={i}
                          className="text-[10px] px-1.5 py-0.5 rounded border border-border bg-bg"
                        >
                          {b.type === "URL" ? "🔗 " : b.type === "PHONE_NUMBER" ? "📞 " : "💬 "}
                          {b.text}
                        </span>
                      ))}
                    </div>
                  )}
                  {t.status === "REJECTED" && t.rejected_reason && (
                    <div className="text-[10px] text-danger mt-1">
                      Motivo: {t.rejected_reason}
                    </div>
                  )}
                </div>
                {isAdmin && (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    disabled={remove.isPending}
                    onClick={() => handleDelete(t.name)}
                    title="Eliminar plantilla"
                  >
                    <Trash2 className="w-3.5 h-3.5 text-danger" />
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Modal crear */}
      {showForm && isAdmin && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-panel border border-border rounded-lg w-full max-w-2xl max-h-[85vh] flex flex-col">
            <div className="px-4 py-3 border-b border-border flex items-center justify-between">
              <div className="text-sm font-semibold">Nueva plantilla HSM</div>
              <button
                onClick={() => { setShowForm(false); setFormError(null); }}
                className="text-fg-muted hover:text-fg text-lg leading-none"
              >
                ×
              </button>
            </div>
            <div className="p-4 space-y-3 overflow-y-auto scroll-thin">
              {formError && (
                <div className="bg-danger/10 border border-danger/30 text-danger text-[11px] px-3 py-2 rounded">
                  {formError}
                </div>
              )}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-[10px] text-fg-muted uppercase">Nombre</label>
                  <Input
                    value={draft.name}
                    onChange={(e) =>
                      setDraft({ ...draft, name: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })
                    }
                    placeholder="saludo_inicial_ar"
                  />
                  <div className="text-[10px] text-fg-dim mt-1">
                    Solo letras minúsculas, números y guión bajo.
                  </div>
                </div>
                <div>
                  <label className="text-[10px] text-fg-muted uppercase">Idioma</label>
                  <select
                    value={draft.language}
                    onChange={(e) => setDraft({ ...draft, language: e.target.value as (typeof LANGUAGES)[number] })}
                    className="w-full h-8 px-2 bg-bg border border-border rounded text-sm"
                  >
                    {LANGUAGES.map((l) => <option key={l} value={l}>{l}</option>)}
                  </select>
                </div>
                <div className="col-span-2">
                  <label className="text-[10px] text-fg-muted uppercase">Categoría</label>
                  <div className="flex gap-1">
                    {CATEGORIES.map((c) => (
                      <button
                        key={c}
                        type="button"
                        onClick={() => setDraft({ ...draft, category: c })}
                        className={cn(
                          "text-[11px] px-2 py-1 rounded border transition-colors flex-1",
                          draft.category === c
                            ? "bg-accent/15 border-accent text-accent"
                            : "bg-bg border-border text-fg-muted",
                        )}
                      >
                        {c}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
              <div>
                <label className="text-[10px] text-fg-muted uppercase">
                  Cuerpo ({draft.body_text.length}/1024)
                </label>
                <textarea
                  value={draft.body_text}
                  onChange={(e) => setDraft({ ...draft, body_text: e.target.value })}
                  maxLength={1024}
                  rows={5}
                  placeholder="Hola {{1}}, te avisamos que..."
                  className="w-full p-2 bg-bg border border-border rounded text-sm font-mono resize-none"
                />
                <div className="text-[10px] text-fg-dim mt-1">
                  Usá <span className="font-mono">{"{{1}}"}</span>, <span className="font-mono">{"{{2}}"}</span>... para variables.
                </div>
              </div>
              <div>
                <label className="text-[10px] text-fg-muted uppercase">Footer (opcional)</label>
                <Input
                  value={draft.footer_text}
                  onChange={(e) => setDraft({ ...draft, footer_text: e.target.value })}
                  placeholder="MSK Latam · msklatam.com"
                  maxLength={60}
                />
              </div>
              <div className="text-[11px] text-fg-dim border-t border-border pt-2">
                Headers con imagen/video/PDF y botones se siguen gestionando desde la{" "}
                <a href="/admin/templates-ui" className="text-accent hover:underline">
                  UI vieja
                </a>
                . Esta migración cubre el 80% de los casos (texto puro).
              </div>
            </div>
            <div className="px-4 py-3 border-t border-border flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => { setShowForm(false); setFormError(null); }}>
                Cancelar
              </Button>
              <Button
                size="sm"
                disabled={!canCreate || create.isPending}
                onClick={() => create.mutate()}
              >
                {create.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : "Crear"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatPill({
  active,
  onClick,
  label,
  count,
  icon: Icon,
  color,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count: number;
  icon?: React.ComponentType<{ className?: string }>;
  color?: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "bg-card border rounded-lg px-3 py-2 flex items-center gap-3 transition-colors text-left",
        active ? "border-accent bg-accent/10" : "border-border hover:bg-hover",
      )}
    >
      {Icon && <Icon className={cn("w-4 h-4", color)} />}
      <div className="flex-1">
        <div className="text-[10px] text-fg-dim uppercase">{label}</div>
        <div className="text-sm font-semibold">{count}</div>
      </div>
    </button>
  );
}
