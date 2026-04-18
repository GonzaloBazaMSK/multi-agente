"use client";

/**
 * /channels — canales de entrada, integraciones externas y apariencia del
 * widget embebible.
 *
 * Secciones:
 *   1. Canales de entrada (WhatsApp Meta/Botmaker/Twilio/widget)
 *   2. Integraciones externas (Zoho/MP/Rebill/OpenAI/Pinecone/R2/Sentry/Slack)
 *   3. Apariencia del widget embebible (título, color, saludo, avatar,
 *      quick replies, posición) — consumido por chat.js al cargarse en
 *      sitios externos como msklatam.tech.
 *
 * La 3° sección venía del Drawflow builder viejo (widget/flows.html). El
 * builder se eliminó (code-death: 0 sesiones lo ejecutaban) pero la config
 * del widget se mantuvo porque SÍ se usa. Vive en /admin/widget-config del
 * backend (antes en /admin/flows/widget-config).
 *
 * Auth: admin.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Loader2,
  Save,
  XCircle,
} from "lucide-react";
import { RoleGate } from "@/lib/auth";
import { NoAccess } from "@/components/ui/coming-soon";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type ChannelKey =
  | "whatsapp_meta"
  | "botmaker"
  | "twilio"
  | "widget"
  | "zoho"
  | "mercadopago"
  | "rebill"
  | "openai"
  | "pinecone"
  | "cloudflare_r2"
  | "sentry"
  | "slack";

type ChannelStatus = {
  [K in ChannelKey]: {
    configured: boolean;
    [extra: string]: unknown;
  };
};

type WidgetConfig = {
  title: string;
  color: string;
  greeting: string;
  avatar: string;
  bubble_icon: string;
  position: "right" | "left";
  quick_replies: string;
};

const LABELS: Record<ChannelKey, { label: string; group: "canales" | "integraciones" }> = {
  whatsapp_meta: { label: "WhatsApp Cloud API (Meta)", group: "canales" },
  botmaker:      { label: "Botmaker",                  group: "canales" },
  twilio:        { label: "Twilio WhatsApp",           group: "canales" },
  widget:        { label: "Widget web embebible",      group: "canales" },
  zoho:          { label: "Zoho CRM",                  group: "integraciones" },
  mercadopago:   { label: "MercadoPago",               group: "integraciones" },
  rebill:        { label: "Rebill",                    group: "integraciones" },
  openai:        { label: "OpenAI (LLM + Whisper + TTS)", group: "integraciones" },
  pinecone:      { label: "Pinecone (RAG)",            group: "integraciones" },
  cloudflare_r2: { label: "Cloudflare R2 (media)",     group: "integraciones" },
  sentry:        { label: "Sentry (errores)",          group: "integraciones" },
  slack:         { label: "Slack (alertas)",           group: "integraciones" },
};

export default function ChannelsPage() {
  return (
    <RoleGate min="admin" denyFallback={<NoAccess requiredRole="admin" />}>
      <ChannelsInner />
    </RoleGate>
  );
}

function ChannelsInner() {
  const q = useQuery<ChannelStatus>({
    queryKey: ["channels", "status"],
    queryFn: () => api.get("/admin/channels-status"),
    refetchInterval: 60_000,
  });

  const keys = Object.keys(LABELS) as ChannelKey[];
  const canales       = keys.filter((k) => LABELS[k].group === "canales");
  const integraciones = keys.filter((k) => LABELS[k].group === "integraciones");
  const configCount = q.data ? keys.filter((k) => q.data![k]?.configured).length : 0;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 py-4 border-b border-border">
        <h1 className="text-lg font-semibold">Canales e integraciones</h1>
        <p className="text-xs text-fg-dim mt-0.5">
          Estado de conexión de cada canal y apariencia del widget embebible. Los secretos se
          editan por SSH en <span className="font-mono">/opt/multiagente/.env</span>.
        </p>
      </div>

      <div className="flex-1 overflow-y-auto scroll-thin p-6 space-y-6 max-w-3xl">
        {q.isLoading && (
          <div className="text-xs text-fg-dim">
            <Loader2 className="inline w-3.5 h-3.5 animate-spin mr-2" />Cargando estado…
          </div>
        )}
        {q.error && <div className="text-xs text-danger">{(q.error as Error).message}</div>}

        {q.data && (
          <>
            <div className="bg-card border border-border rounded-lg px-3 py-2 flex items-center gap-3 text-xs">
              <CheckCircle2 className="w-4 h-4 text-success" />
              <div>
                <span className="font-semibold">{configCount} de {keys.length}</span>{" "}
                <span className="text-fg-dim">integraciones configuradas.</span>
              </div>
            </div>

            <Section title="Canales de entrada" keys={canales} data={q.data} />
            <Section title="Integraciones externas" keys={integraciones} data={q.data} />
          </>
        )}

        <WidgetConfigSection />

        <div className="text-[11px] text-fg-dim pt-4 border-t border-border">
          <strong>Cómo rotar credenciales:</strong>{" "}
          <code className="bg-bg px-1 rounded">ssh root@68.183.156.122</code> →{" "}
          <code className="bg-bg px-1 rounded">nano /opt/multiagente/.env</code> →{" "}
          <code className="bg-bg px-1 rounded">docker compose restart api</code>. La UI no lo hace
          para no exponer secretos en el bundle JS.
        </div>
      </div>
    </div>
  );
}

function Section({
  title,
  keys,
  data,
}: {
  title: string;
  keys: ChannelKey[];
  data: ChannelStatus;
}) {
  return (
    <section>
      <h2 className="text-sm font-semibold mb-2">{title}</h2>
      <div className="space-y-1.5">
        {keys.map((k) => {
          const meta = data[k] || { configured: false };
          const cfg = !!meta.configured;
          const extras: [string, string][] = Object.entries(meta)
            .filter(([key, val]) => key !== "configured" && val)
            .map(([key, val]) => [key, String(val)]);
          return (
            <div key={k} className="bg-card border border-border rounded-lg p-3 flex items-start gap-3">
              {cfg ? (
                <CheckCircle2 className="w-4 h-4 text-success shrink-0 mt-0.5" />
              ) : (
                <XCircle className="w-4 h-4 text-fg-dim shrink-0 mt-0.5" />
              )}
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium flex items-center gap-2">
                  {LABELS[k].label}
                  <span
                    className={cn(
                      "text-[9px] px-1.5 py-0.5 rounded",
                      cfg ? "bg-success/15 text-success" : "bg-border text-fg-dim",
                    )}
                  >
                    {cfg ? "configurado" : "no configurado"}
                  </span>
                </div>
                {extras.length > 0 && (
                  <div className="flex gap-3 mt-1 flex-wrap">
                    {extras.map(([key, val]) => (
                      <span key={key} className="text-[10px] text-fg-dim">
                        <span className="font-mono">{key}</span>:{" "}
                        <span className="font-mono text-fg-muted">{val}</span>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function WidgetConfigSection() {
  const qc = useQueryClient();
  const cfgQ = useQuery<WidgetConfig>({
    queryKey: ["widget-config"],
    queryFn: () => api.get("/admin/widget-config"),
    staleTime: 60_000,
  });

  const [draft, setDraft] = useState<WidgetConfig | null>(null);
  const effective = draft ?? cfgQ.data ?? null;
  const dirty =
    draft && cfgQ.data && JSON.stringify(draft) !== JSON.stringify(cfgQ.data);

  const save = useMutation({
    mutationFn: (c: WidgetConfig) => api.post("/admin/widget-config", c),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["widget-config"] });
      setDraft(null);
    },
    onError: (e: Error) => alert(e.message),
  });

  return (
    <section className="pt-4 border-t border-border">
      <div className="flex items-center justify-between mb-2">
        <div>
          <h2 className="text-sm font-semibold">Widget embebible: apariencia</h2>
          <p className="text-[11px] text-fg-dim">
            Esta config la carga <span className="font-mono">chat.js</span> cuando se embebe en
            sitios externos (msklatam.com, msklatam.tech, etc).
          </p>
        </div>
        {dirty && (
          <Button
            size="sm"
            onClick={() => effective && save.mutate(effective)}
            disabled={save.isPending}
          >
            {save.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            Guardar cambios
          </Button>
        )}
      </div>

      {cfgQ.isLoading && !effective ? (
        <div className="text-xs text-fg-dim">
          <Loader2 className="inline w-3 h-3 animate-spin mr-1" />Cargando…
        </div>
      ) : effective ? (
        <div className="bg-card border border-border rounded-lg p-4 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-[10px] text-fg-muted uppercase">Título</label>
              <Input
                value={effective.title}
                onChange={(e) => setDraft({ ...effective, title: e.target.value })}
              />
            </div>
            <div>
              <label className="text-[10px] text-fg-muted uppercase">Color primario</label>
              <div className="flex gap-2">
                <input
                  type="color"
                  value={effective.color}
                  onChange={(e) => setDraft({ ...effective, color: e.target.value })}
                  className="h-8 w-12 bg-bg border border-border rounded cursor-pointer"
                />
                <Input
                  value={effective.color}
                  onChange={(e) => setDraft({ ...effective, color: e.target.value })}
                  className="flex-1 font-mono"
                />
              </div>
            </div>
            <div className="col-span-2">
              <label className="text-[10px] text-fg-muted uppercase">Saludo inicial</label>
              <Input
                value={effective.greeting}
                onChange={(e) => setDraft({ ...effective, greeting: e.target.value })}
                placeholder="¡Hola! Soy tu asesor de cursos, ¿en qué te ayudo?"
              />
            </div>
            <div>
              <label className="text-[10px] text-fg-muted uppercase">Avatar (emoji o URL)</label>
              <Input
                value={effective.avatar}
                onChange={(e) => setDraft({ ...effective, avatar: e.target.value })}
                placeholder="🩺"
              />
            </div>
            <div>
              <label className="text-[10px] text-fg-muted uppercase">Posición</label>
              <select
                value={effective.position}
                onChange={(e) => setDraft({ ...effective, position: e.target.value as "right" | "left" })}
                className="w-full h-8 px-2 bg-bg border border-border rounded text-sm"
              >
                <option value="right">Derecha</option>
                <option value="left">Izquierda</option>
              </select>
            </div>
            <div className="col-span-2">
              <label className="text-[10px] text-fg-muted uppercase">
                Quick replies (separadas por <span className="font-mono">|</span>)
              </label>
              <Input
                value={effective.quick_replies}
                onChange={(e) => setDraft({ ...effective, quick_replies: e.target.value })}
                placeholder="Cursos online|Asesoramiento|Cobranzas"
              />
            </div>
          </div>

          {/* Preview */}
          <div className="pt-3 border-t border-border">
            <div className="text-[10px] text-fg-dim uppercase mb-1">Preview</div>
            <div className="bg-bg border border-border rounded-lg p-3 flex items-start gap-2">
              <div
                className="w-9 h-9 rounded-full flex items-center justify-center text-white text-lg shrink-0"
                style={{ backgroundColor: effective.color }}
              >
                {effective.avatar || "💬"}
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-xs font-semibold">{effective.title || "—"}</div>
                <div className="text-[11px] text-fg-muted mt-0.5">
                  {effective.greeting || "—"}
                </div>
                {effective.quick_replies && (
                  <div className="flex gap-1 mt-2 flex-wrap">
                    {effective.quick_replies.split("|").map((qr, i) => (
                      <span
                        key={i}
                        className="text-[10px] px-2 py-0.5 rounded-full border"
                        style={{ borderColor: effective.color, color: effective.color }}
                      >
                        {qr.trim()}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
