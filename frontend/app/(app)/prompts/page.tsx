"use client";

/**
 * /prompts — editor de prompts de agentes.
 *
 * 5 agentes (ventas, cobranzas, post-venta, bienvenida, orquestador) con su
 * system prompt editable. El backend persiste a `agents/<agente>/prompts.py`
 * que se lee en el próximo turno del bot — NO hace hot-reload del módulo
 * Python, solo se relee el archivo cuando el agente re-importa el módulo
 * (típicamente en el siguiente restart del container). Por eso dejamos el
 * warning bien visible.
 *
 * Paridad con widget/admin_prompts.html: mismas 5 tabs, mismo Ctrl+S,
 * mismo toast success/error. Auth: admin-only (require_role en backend).
 */

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, Save } from "lucide-react";
import { RoleGate } from "@/lib/auth";
import { NoAccess } from "@/components/ui/coming-soon";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type Agent = {
  key: "ventas" | "cobranzas" | "post_venta" | "bienvenida" | "orquestador";
  label: string;
  file: string;
  description: string;
};

const AGENTS: Agent[] = [
  { key: "ventas",       label: "Ventas",       file: "agents/sales/prompts.py",          description: "RAG de cursos + cierre con links MP/Rebill" },
  { key: "cobranzas",    label: "Cobranzas",    file: "agents/collections/prompts.py",    description: "Recupero de deuda vencida (Zoho)" },
  { key: "post_venta",   label: "Post-venta",   file: "agents/post_sales/prompts.py",     description: "Soporte LMS + certificados" },
  { key: "bienvenida",   label: "Bienvenida",   file: "agents/routing/greeting_prompt.py", description: "Saludo inicial del widget" },
  { key: "orquestador",  label: "Orquestador",  file: "agents/routing/router_prompt.py",  description: "Clasificador de intent (gpt-4o-mini)" },
];

export default function PromptsPage() {
  return (
    <RoleGate min="admin" denyFallback={<NoAccess requiredRole="admin" />}>
      <PromptsPageInner />
    </RoleGate>
  );
}

function PromptsPageInner() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Agent["key"]>("ventas");
  const [draft, setDraft] = useState("");
  const [toast, setToast] = useState<{ msg: string; kind: "ok" | "err" } | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Carga del prompt actual cuando cambia la tab
  const promptQ = useQuery<{ agent: string; content: string }>({
    queryKey: ["admin", "prompts", selected],
    queryFn: () => api.get(`/admin/prompts/${selected}`),
    staleTime: 0, // siempre fresco — si otro admin edita, que lo veamos
  });

  useEffect(() => {
    if (promptQ.data) setDraft(promptQ.data.content);
  }, [promptQ.data]);

  const save = useMutation({
    mutationFn: () => api.post(`/admin/prompts/${selected}`, { content: draft }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "prompts", selected] });
      showToast("Prompt guardado — se aplica en el próximo mensaje", "ok");
    },
    onError: (e: Error) => showToast(e.message || "Error al guardar", "err"),
  });

  const showToast = (msg: string, kind: "ok" | "err") => {
    setToast({ msg, kind });
    window.setTimeout(() => setToast(null), 3500);
  };

  // Ctrl+S / Cmd+S para guardar (paridad con la UI vieja).
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (!save.isPending && draft !== promptQ.data?.content) save.mutate();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [save, draft, promptQ.data?.content]);

  const dirty = promptQ.data?.content !== undefined && draft !== promptQ.data?.content;
  const activeAgent = AGENTS.find((a) => a.key === selected)!;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 py-4 border-b border-border flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Editor de prompts</h1>
          <p className="text-xs text-fg-dim mt-0.5">
            System prompts de cada agente del bot. Admin-only.
          </p>
        </div>
        <Button
          size="sm"
          disabled={!dirty || save.isPending}
          onClick={() => save.mutate()}
        >
          {save.isPending ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Save className="w-3.5 h-3.5" />
          )}
          {save.isPending ? "Guardando…" : dirty ? "Guardar (Ctrl+S)" : "Sin cambios"}
        </Button>
      </div>

      <div className="flex-1 flex overflow-hidden">
        {/* Sidebar de agentes */}
        <aside className="w-52 border-r border-border bg-panel overflow-y-auto scroll-thin">
          {AGENTS.map((a) => {
            const active = selected === a.key;
            return (
              <button
                key={a.key}
                type="button"
                onClick={() => setSelected(a.key)}
                className={cn(
                  "w-full text-left px-4 py-3 border-b border-border text-xs transition-colors",
                  active ? "bg-accent/15 text-fg" : "text-fg-muted hover:bg-hover hover:text-fg",
                )}
              >
                <div className="font-medium text-sm">{a.label}</div>
                <div className="text-[11px] text-fg-dim mt-0.5 leading-tight">{a.description}</div>
                <div className="font-mono text-[9px] text-fg-dim mt-1 truncate">{a.file}</div>
              </button>
            );
          })}
        </aside>

        {/* Editor */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="bg-warn/10 border-b border-warn/30 px-4 py-2 text-[11px] text-warn flex items-start gap-2">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <div>
              Los cambios se persisten en <span className="font-mono">{activeAgent.file}</span>.
              El bot aplica el nuevo prompt en el próximo turno. Si reconstruís el container
              (<span className="font-mono">docker compose build</span>) sin pushear a git, se
              pierden los cambios.
            </div>
          </div>
          {promptQ.isLoading ? (
            <div className="flex-1 flex items-center justify-center text-xs text-fg-dim">
              <Loader2 className="w-4 h-4 animate-spin mr-2" /> Cargando prompt…
            </div>
          ) : promptQ.error ? (
            <div className="flex-1 flex items-center justify-center text-xs text-danger">
              {(promptQ.error as Error).message || "No se pudo cargar el prompt"}
            </div>
          ) : (
            <textarea
              ref={textareaRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              spellCheck={false}
              className="flex-1 p-4 bg-bg text-fg text-[12px] font-mono leading-relaxed resize-none outline-none scroll-thin"
              placeholder="# System prompt..."
            />
          )}
        </div>
      </div>

      {/* Toast */}
      {toast && (
        <div
          className={cn(
            "fixed bottom-6 right-6 px-4 py-2 rounded-md text-xs font-medium shadow-lg border",
            toast.kind === "ok"
              ? "bg-success/10 border-success/40 text-success"
              : "bg-danger/10 border-danger/40 text-danger",
          )}
        >
          {toast.msg}
        </div>
      )}
    </div>
  );
}
