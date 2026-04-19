"use client";

/**
 * /settings/workspace — info del workspace + estado de integraciones.
 *
 * Es la "pantalla About" del admin: qué canales están configurados, qué
 * servicios remotos responden, y en qué environment corre. No permite
 * editar nada desde acá — para gestionar canales hay página dedicada
 * (/channels). Esto es solo lectura, orientado a que el admin detecte
 * rápido "¿por qué no salen WhatsApp?" → miro acá y veo que Meta no
 * está configurado.
 *
 * Fuente: GET /api/v1/admin/channels-status (accesible a supervisor+).
 */

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  XCircle,
  MessageSquare,
  Phone,
  CreditCard,
  Globe,
  Bot,
  Database,
  FileText,
  Slack,
  ShieldAlert,
  ArrowRight,
} from "lucide-react";

import { NoAccess } from "@/components/ui/coming-soon";
import { RoleGate } from "@/lib/auth";
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

type ChannelsStatus = Record<ChannelKey, { configured: boolean } & Record<string, unknown>>;

// Lista ordenada por importancia: primero los canales que el cliente ve
// (WhatsApp), después pagos, después infra interna.
const CHANNELS: {
  key: ChannelKey;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  description: string;
  category: "messaging" | "payments" | "infra";
}[] = [
  {
    key: "whatsapp_meta",
    label: "WhatsApp Cloud API",
    icon: MessageSquare,
    description: "Canal principal — mensajes oficiales vía Meta",
    category: "messaging",
  },
  {
    key: "botmaker",
    label: "Botmaker",
    icon: Bot,
    description: "Proxy legacy — heredamos conversaciones históricas",
    category: "messaging",
  },
  {
    key: "twilio",
    label: "Twilio SMS/WhatsApp",
    icon: Phone,
    description: "Fallback SMS + sandbox de WhatsApp para testing",
    category: "messaging",
  },
  {
    key: "widget",
    label: "Widget embebido (web)",
    icon: Globe,
    description: "Chat en msklatam.com — lo sirve FastAPI directo",
    category: "messaging",
  },
  {
    key: "slack",
    label: "Notificaciones Slack",
    icon: Slack,
    description: "Alertas de oportunidades calientes al equipo",
    category: "messaging",
  },
  {
    key: "mercadopago",
    label: "Mercado Pago",
    icon: CreditCard,
    description: "Cobranza de cursos — webhook + SDK",
    category: "payments",
  },
  {
    key: "rebill",
    label: "Rebill",
    icon: CreditCard,
    description: "Suscripciones recurrentes para cursos premium",
    category: "payments",
  },
  {
    key: "zoho",
    label: "Zoho CRM",
    icon: Database,
    description: "Sincronización de contactos y pedidos",
    category: "infra",
  },
  {
    key: "openai",
    label: "OpenAI",
    icon: Bot,
    description: "LLM que alimenta los agentes IA",
    category: "infra",
  },
  {
    key: "pinecone",
    label: "Pinecone",
    icon: FileText,
    description: "Vector DB para RAG del catálogo de cursos",
    category: "infra",
  },
  {
    key: "cloudflare_r2",
    label: "Cloudflare R2",
    icon: Database,
    description: "Storage de media (audio/imagen) y adjuntos",
    category: "infra",
  },
  {
    key: "sentry",
    label: "Sentry",
    icon: ShieldAlert,
    description: "Monitoreo de errores frontend + backend",
    category: "infra",
  },
];

const CATEGORY_LABELS = {
  messaging: "Mensajería y canales",
  payments: "Pagos",
  infra: "Infraestructura y servicios",
} as const;

export default function WorkspacePage() {
  return (
    <RoleGate min="admin" denyFallback={<NoAccess requiredRole="admin" />}>
      <Inner />
    </RoleGate>
  );
}

function Inner() {
  const statusQ = useQuery<ChannelsStatus>({
    queryKey: ["admin", "channels-status"],
    queryFn: () => api.get("/admin/channels-status"),
    staleTime: 30_000,
  });

  const data = statusQ.data;
  const configuredCount = data
    ? CHANNELS.filter((c) => data[c.key]?.configured).length
    : 0;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 py-4 border-b border-border">
        <h1 className="text-lg font-semibold">Workspace</h1>
        <p className="text-xs text-fg-dim mt-0.5">
          Estado de integraciones y servicios. Para editar tokens o credenciales vas a{" "}
          <Link href="/channels" className="text-accent hover:underline">
            Canales
          </Link>
          .
        </p>
      </div>

      <div className="flex-1 overflow-y-auto scroll-thin p-6 space-y-6 max-w-3xl">
        {/* Resumen */}
        <div className="bg-card border border-border rounded-lg p-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs text-fg-dim uppercase tracking-wide">
                Integraciones activas
              </div>
              <div className="text-2xl font-semibold mt-1">
                {statusQ.isLoading ? "…" : `${configuredCount} / ${CHANNELS.length}`}
              </div>
              <div className="text-[11px] text-fg-dim mt-1">
                Servicios con credenciales configuradas
              </div>
            </div>
            <Link
              href="/channels"
              className="inline-flex items-center gap-1.5 text-xs text-accent hover:underline"
            >
              Gestionar canales
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>
        </div>

        {statusQ.isLoading && (
          <div className="text-xs text-fg-dim">Cargando estado de servicios…</div>
        )}
        {statusQ.error && (
          <div className="text-xs text-danger">
            No se pudo cargar el estado. {(statusQ.error as Error).message}
          </div>
        )}

        {data &&
          (Object.keys(CATEGORY_LABELS) as Array<keyof typeof CATEGORY_LABELS>).map(
            (cat) => {
              const items = CHANNELS.filter((c) => c.category === cat);
              if (items.length === 0) return null;
              return (
                <section key={cat}>
                  <h2 className="text-sm font-semibold mb-2">
                    {CATEGORY_LABELS[cat]}
                  </h2>
                  <div className="space-y-1.5">
                    {items.map((c) => {
                      const s = data[c.key];
                      const configured = s?.configured ?? false;
                      const Icon = c.icon;
                      return (
                        <div
                          key={c.key}
                          className="bg-card border border-border rounded-lg p-3 flex items-center gap-3"
                        >
                          <div
                            className={cn(
                              "w-8 h-8 rounded flex items-center justify-center shrink-0",
                              configured
                                ? "bg-success/10 text-success"
                                : "bg-fg-dim/10 text-fg-dim",
                            )}
                          >
                            <Icon className="w-4 h-4" />
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="text-sm font-medium">{c.label}</div>
                            <div className="text-[11px] text-fg-dim truncate">
                              {c.description}
                            </div>
                          </div>
                          <div
                            className={cn(
                              "text-[11px] flex items-center gap-1 shrink-0",
                              configured ? "text-success" : "text-fg-dim",
                            )}
                          >
                            {configured ? (
                              <>
                                <CheckCircle2 className="w-3.5 h-3.5" />
                                Configurado
                              </>
                            ) : (
                              <>
                                <XCircle className="w-3.5 h-3.5" />
                                No configurado
                              </>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </section>
              );
            },
          )}

        {/* Info del workspace */}
        <section>
          <h2 className="text-sm font-semibold mb-2">Sobre este workspace</h2>
          <div className="bg-card border border-border rounded-lg p-4 text-xs text-fg-dim space-y-1">
            <div>
              <span className="text-fg">Organización:</span> MSK Latam — cursos médicos
            </div>
            <div>
              <span className="text-fg">Backend API:</span>{" "}
              <code className="text-[10px] bg-bg px-1 py-0.5 rounded">/api/v1</code>
            </div>
            <div>
              <span className="text-fg">Docs OpenAPI:</span>{" "}
              <a
                href="/api/v1/docs"
                target="_blank"
                rel="noreferrer"
                className="text-accent hover:underline"
              >
                /api/v1/docs
              </a>{" "}
              (requiere admin-key)
            </div>
            <div>
              <span className="text-fg">Soporte:</span>{" "}
              <a
                href="mailto:soporte@msklatam.com"
                className="text-accent hover:underline"
              >
                soporte@msklatam.com
              </a>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
