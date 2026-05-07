"use client";

/**
 * /templates — gestión de plantillas HSM de WhatsApp (Meta Cloud API).
 *
 * Paridad total con widget/templates.html (excepto el preview WA-like, que
 * acá es más minimalista):
 *   - Lista con stats por estado + filtros + search
 *   - Ver detalle (header/body/footer/buttons/rejected_reason)
 *   - Eliminar (admin)
 *   - Modal crear con:
 *       · header opcional (texto | imagen | video | documento)
 *       · media upload via /templates/hsm/upload-media (devuelve handle)
 *       · body con {{N}} variables
 *       · footer opcional
 *       · hasta 3 botones (QUICK_REPLY | URL | PHONE_NUMBER)
 *
 * Auth: leer = supervisor+; crear/borrar = admin.
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
  Upload,
  X,
  XCircle,
} from "lucide-react";
import { RoleGate, useRole } from "@/lib/auth";
import { NoAccess } from "@/components/ui/coming-soon";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type HSMButton = {
  type: "QUICK_REPLY" | "URL" | "PHONE_NUMBER";
  text: string;
  url?: string;
  phone_number?: string;
};

type HeaderType = "" | "TEXT" | "IMAGE" | "VIDEO" | "DOCUMENT";

type Template = {
  id: string;
  name: string;
  language: string;
  category: "MARKETING" | "UTILITY" | "AUTHENTICATION";
  status: "APPROVED" | "PENDING" | "REJECTED" | "PAUSED";
  body: string;
  header: { format: string; text: string } | null;
  footer: string;
  buttons: HSMButton[];
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

type Draft = {
  name: string;
  category: Template["category"];
  language: (typeof LANGUAGES)[number];
  header_type: HeaderType;
  header_text: string;
  header_handle: string;
  header_file_label: string;
  body_text: string;
  footer_text: string;
  buttons: HSMButton[];
};

const EMPTY_DRAFT: Draft = {
  name: "",
  category: "MARKETING",
  language: "es_AR",
  header_type: "",
  header_text: "",
  header_handle: "",
  header_file_label: "",
  body_text: "",
  footer_text: "",
  buttons: [],
};

export default function TemplatesPage() {
  return (
    <RoleGate min="admin" denyFallback={<NoAccess requiredRole="admin" />}>
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
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [formError, setFormError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  const q = useQuery<{ templates: Template[]; error?: string }>({
    queryKey: ["templates", "all"],
    queryFn: () => api.get("/templates/hsm/all"),
    staleTime: 60_000,
  });

  const create = useMutation({
    mutationFn: () => {
      const payload = {
        name: draft.name,
        category: draft.category,
        language: draft.language,
        body_text: draft.body_text,
        header_text: draft.header_type === "TEXT" ? draft.header_text : "",
        header_type:
          draft.header_type === "IMAGE" || draft.header_type === "VIDEO" || draft.header_type === "DOCUMENT"
            ? draft.header_type
            : "",
        header_handle: draft.header_handle,
        footer_text: draft.footer_text,
        buttons: draft.buttons,
      };
      return api.post("/templates/hsm/create", payload);
    },
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
    const query = search.trim().toLowerCase();
    return templates.filter((t) => {
      if (filter !== "ALL" && t.status !== filter) return false;
      if (!query) return true;
      return t.name.toLowerCase().includes(query) || (t.body || "").toLowerCase().includes(query);
    });
  }, [templates, filter, search]);

  const canCreate =
    isAdmin &&
    /^[a-z0-9_]+$/.test(draft.name) &&
    draft.body_text.trim().length > 0 &&
    draft.body_text.length <= 1024 &&
    !uploading &&
    // Si hay tipo media, tenemos que tener handle
    (!["IMAGE", "VIDEO", "DOCUMENT"].includes(draft.header_type) || draft.header_handle !== "");

  const handleDelete = (name: string) => {
    if (!confirm(`Eliminar la plantilla "${name}"? No se puede deshacer.`)) return;
    remove.mutate(name);
  };

  const uploadMedia = async (file: File) => {
    setUploading(true);
    setFormError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      // Cookie httpOnly va automática en same-origin. El prefix /api/v1
      // es el canónico desde el refactor de versioning.
      const res = await fetch("/api/v1/templates/hsm/upload-media", {
        method: "POST",
        body: form,
        credentials: "include",
      });
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(`Upload falló: ${text || res.status}`);
      }
      const data: { handle: string; media_type: string; filename: string } = await res.json();
      setDraft((d) => ({
        ...d,
        header_handle: data.handle,
        header_file_label: `${data.filename} (${data.media_type})`,
      }));
    } catch (e) {
      setFormError((e as Error).message);
    } finally {
      setUploading(false);
    }
  };

  const addButton = () => {
    if (draft.buttons.length >= 3) return;
    setDraft({
      ...draft,
      buttons: [...draft.buttons, { type: "QUICK_REPLY", text: "" }],
    });
  };

  const updateButton = (i: number, patch: Partial<HSMButton>) => {
    const next = [...draft.buttons];
    next[i] = { ...next[i], ...patch };
    setDraft({ ...draft, buttons: next });
  };

  const removeButton = (i: number) => {
    setDraft({ ...draft, buttons: draft.buttons.filter((_, idx) => idx !== i) });
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
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
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
                    {t.header?.format && t.header.format !== "TEXT" && (
                      <span className="text-[10px] text-info">· header {t.header.format.toLowerCase()}</span>
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
          <div className="bg-panel border border-border rounded-lg w-full max-w-2xl max-h-[90vh] flex flex-col">
            <div className="px-4 py-3 border-b border-border flex items-center justify-between">
              <div className="text-sm font-semibold">Nueva plantilla HSM</div>
              <button
                onClick={() => { setShowForm(false); setFormError(null); }}
                className="text-fg-muted hover:text-fg text-lg leading-none"
              >
                ×
              </button>
            </div>
            <div className="p-4 space-y-4 overflow-y-auto scroll-thin">
              {formError && (
                <div className="bg-danger/10 border border-danger/30 text-danger text-[11px] px-3 py-2 rounded">
                  {formError}
                </div>
              )}

              {/* Basics */}
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
                    Solo minúsculas, números y guión bajo.
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

              {/* Header */}
              <div className="border-t border-border pt-3">
                <div className="text-[11px] font-semibold mb-1.5">Header (opcional)</div>
                <div className="flex gap-1 mb-2 flex-wrap">
                  {(
                    [
                      { v: "",         label: "Sin header" },
                      { v: "TEXT",     label: "Texto" },
                      { v: "IMAGE",    label: "Imagen" },
                      { v: "VIDEO",    label: "Video" },
                      { v: "DOCUMENT", label: "PDF" },
                    ] as { v: HeaderType; label: string }[]
                  ).map(({ v, label }) => (
                    <button
                      key={label}
                      type="button"
                      onClick={() =>
                        setDraft({ ...draft, header_type: v, header_handle: "", header_file_label: "", header_text: "" })
                      }
                      className={cn(
                        "text-[11px] px-2 py-1 rounded border transition-colors",
                        draft.header_type === v
                          ? "bg-accent/15 border-accent text-accent"
                          : "bg-bg border-border text-fg-muted",
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                {draft.header_type === "TEXT" && (
                  <Input
                    value={draft.header_text}
                    onChange={(e) => setDraft({ ...draft, header_text: e.target.value })}
                    placeholder="Título del mensaje (max 60 chars)"
                    maxLength={60}
                  />
                )}
                {(draft.header_type === "IMAGE" ||
                  draft.header_type === "VIDEO" ||
                  draft.header_type === "DOCUMENT") && (
                  <div className="space-y-2">
                    <label className="inline-flex items-center gap-2 text-xs cursor-pointer bg-bg border border-border rounded px-3 py-1.5 hover:bg-hover">
                      {uploading ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Upload className="w-3.5 h-3.5" />
                      )}
                      {draft.header_handle ? "Cambiar archivo" : "Subir archivo"}
                      <input
                        type="file"
                        className="hidden"
                        accept={
                          draft.header_type === "IMAGE"
                            ? "image/jpeg,image/png,image/webp"
                            : draft.header_type === "VIDEO"
                            ? "video/mp4,video/3gpp"
                            : "application/pdf"
                        }
                        onChange={(e) => {
                          const file = e.target.files?.[0];
                          if (file) uploadMedia(file);
                        }}
                      />
                    </label>
                    {draft.header_file_label && (
                      <div className="text-[10px] text-success flex items-center gap-1.5">
                        <CheckCircle2 className="w-3 h-3" /> {draft.header_file_label}
                      </div>
                    )}
                    <div className="text-[10px] text-fg-dim">
                      Max 16MB.{" "}
                      {draft.header_type === "IMAGE"
                        ? "JPG / PNG / WebP"
                        : draft.header_type === "VIDEO"
                        ? "MP4 / 3GPP"
                        : "PDF"}.
                    </div>
                  </div>
                )}
              </div>

              {/* Body */}
              <div className="border-t border-border pt-3">
                <label className="text-[11px] font-semibold block mb-1.5">
                  Cuerpo ({draft.body_text.length}/1024) <span className="text-danger">*</span>
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

              {/* Footer */}
              <div>
                <label className="text-[10px] text-fg-muted uppercase">Footer (opcional)</label>
                <Input
                  value={draft.footer_text}
                  onChange={(e) => setDraft({ ...draft, footer_text: e.target.value })}
                  placeholder="MSK Latam · msklatam.com"
                  maxLength={60}
                />
              </div>

              {/* Buttons */}
              <div className="border-t border-border pt-3">
                <div className="flex items-center justify-between mb-2">
                  <div className="text-[11px] font-semibold">Botones ({draft.buttons.length}/3)</div>
                  {draft.buttons.length < 3 && (
                    <button
                      type="button"
                      onClick={addButton}
                      className="text-[11px] text-accent hover:underline flex items-center gap-1"
                    >
                      <Plus className="w-3 h-3" /> Agregar
                    </button>
                  )}
                </div>
                <div className="space-y-2">
                  {draft.buttons.map((b, i) => (
                    <div
                      key={i}
                      className="bg-bg border border-border rounded p-2 space-y-2 relative"
                    >
                      <button
                        type="button"
                        onClick={() => removeButton(i)}
                        className="absolute top-1 right-1 text-fg-dim hover:text-danger"
                      >
                        <X className="w-3 h-3" />
                      </button>
                      <div className="grid grid-cols-3 gap-2">
                        <select
                          value={b.type}
                          onChange={(e) =>
                            updateButton(i, { type: e.target.value as HSMButton["type"] })
                          }
                          className="h-7 px-1 bg-bg border border-border rounded text-xs"
                        >
                          <option value="QUICK_REPLY">Respuesta rápida</option>
                          <option value="URL">URL</option>
                          <option value="PHONE_NUMBER">Teléfono</option>
                        </select>
                        <Input
                          className="col-span-2"
                          value={b.text}
                          onChange={(e) => updateButton(i, { text: e.target.value })}
                          placeholder="Texto del botón"
                          maxLength={25}
                        />
                      </div>
                      {b.type === "URL" && (
                        <Input
                          value={b.url || ""}
                          onChange={(e) => updateButton(i, { url: e.target.value })}
                          placeholder="https://msklatam.com/..."
                        />
                      )}
                      {b.type === "PHONE_NUMBER" && (
                        <Input
                          value={b.phone_number || ""}
                          onChange={(e) => updateButton(i, { phone_number: e.target.value })}
                          placeholder="+5491134567890"
                        />
                      )}
                    </div>
                  ))}
                </div>
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
