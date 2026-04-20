"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  PanelRightClose,
  UserPlus,
  Bot,
  Pause,
  Play,
  Check,
  Tag,
  Sparkles,
  Flame,
  Thermometer,
  Snowflake,
  CheckCircle2,
  CreditCard,
  Clock as ClockIcon,
  XCircle,
  Loader2,
} from "lucide-react";
import { Avatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Flag } from "@/components/ui/flag";
import {
  Dropdown,
  DropdownItem,
  DropdownLabel,
  DropdownSeparator,
} from "@/components/ui/dropdown";
import { Composer } from "./composer";
import { MessageBubble } from "./message-bubble";
import { useAgents } from "@/lib/api/inbox";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ContactDetail, ConversationListItem, LifecycleStage, Message } from "@/lib/mock-data";

// ═════════ Etiqueta IA del clasificador (agents/classifier.py) ═════════
// Guardada en Redis `conv_label:{session_id}` — automática + override
// manual vía POST /api/v1/inbox/conversations/{id}/label.
type AILabel =
  | "caliente"
  | "tibio"
  | "frio"
  | "convertido"
  | "esperando_pago"
  | "seguimiento"
  | "no_interesa"
  | "sin_clasificar";

const AI_LABEL_OPTIONS: {
  value: Exclude<AILabel, "sin_clasificar">;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  color: string; // tailwind text color class
}[] = [
  { value: "caliente",       label: "Caliente",       icon: Flame,        color: "text-danger" },
  { value: "tibio",          label: "Tibio",          icon: Thermometer,  color: "text-warn" },
  { value: "frio",           label: "Frío",           icon: Snowflake,    color: "text-info" },
  { value: "esperando_pago", label: "Esperando pago", icon: CreditCard,   color: "text-warn" },
  { value: "convertido",     label: "Convertido",     icon: CheckCircle2, color: "text-success" },
  { value: "seguimiento",    label: "Seguimiento",    icon: ClockIcon,    color: "text-info" },
  { value: "no_interesa",    label: "No le interesa", icon: XCircle,      color: "text-fg-dim" },
];

const LIFECYCLE_OPTIONS: { value: LifecycleStage; label: string; color: string }[] = [
  { value: "new",      label: "New Lead",  color: "bg-info" },
  { value: "hot",      label: "Hot Lead",  color: "bg-warn" },
  { value: "customer", label: "Customer",  color: "bg-success" },
  { value: "cold",     label: "Cold Lead", color: "bg-fg-dim" },
];

interface Props {
  contact: ContactDetail | null;
  conversation: ConversationListItem | null;
  messages: Message[];
  onToggleContactPanel: () => void;
  /** Acciones (mutaciones) sobre la conversación */
  onAssign:        (agentId: string | null) => void;
  onTakeoverHuman: () => void;
  onToggleBot:     () => void;
  onClassify:      (stage: LifecycleStage) => void;
}

export function ConversationDetail({
  contact,
  conversation,
  messages,
  onToggleContactPanel,
  onAssign,
  onTakeoverHuman,
  onToggleBot,
  onClassify,
}: Props) {
  const qc = useQueryClient();

  // Etiqueta IA actual (Redis) — se refetchea cuando cambia la conv o cuando
  // otro evento la actualice. El classifier del bot la actualiza después de
  // cada respuesta; este GET trae el estado más reciente.
  const labelQ = useQuery<{ label: AILabel }>({
    queryKey: ["inbox", "conv", conversation?.id, "label"],
    queryFn: () => api.get(`/inbox/conversations/${conversation!.id}/label`),
    enabled: !!conversation?.id,
    staleTime: 30_000,
  });

  const setLabelMutation = useMutation({
    mutationFn: (label: Exclude<AILabel, "sin_clasificar">) =>
      api.post(`/inbox/conversations/${conversation!.id}/label`, { label }),
    onMutate: async (label) => {
      await qc.cancelQueries({
        queryKey: ["inbox", "conv", conversation?.id, "label"],
      });
      const prev = qc.getQueryData(["inbox", "conv", conversation?.id, "label"]);
      qc.setQueryData(["inbox", "conv", conversation?.id, "label"], { label });
      return { prev };
    },
    onError: (_e, _vars, ctx) => {
      if (ctx?.prev) {
        qc.setQueryData(["inbox", "conv", conversation?.id, "label"], ctx.prev);
      }
    },
    onSettled: () => {
      qc.invalidateQueries({
        queryKey: ["inbox", "conv", conversation?.id, "label"],
      });
      // El pipeline también se beneficia — lo invalidamos para que la card
      // se mueva de columna si el user tiene el kanban abierto en otra tab.
      qc.invalidateQueries({ queryKey: ["pipeline", "leads"] });
    },
  });

  const currentLabel = labelQ.data?.label ?? "sin_clasificar";
  const currentLabelMeta = AI_LABEL_OPTIONS.find((o) => o.value === currentLabel);

  // Auto-scroll al último mensaje cuando llega uno nuevo o se cambia de conv
  const messagesEndRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, conversation?.id]);

  // Equipo real desde el backend (profiles con role agente/supervisor/admin)
  const { data: agents = [] } = useAgents();
  const { user } = useAuth();
  const meId = user?.id ?? null;

  if (!contact || !conversation) {
    return (
      <div className="flex-1 flex items-center justify-center bg-bg text-fg-dim text-sm">
        Seleccioná una conversación
      </div>
    );
  }

  const assignedAgent = conversation.assignedTo
    ? agents.find((a) => a.id === conversation.assignedTo)
    : null;

  return (
    <div className="flex-1 flex flex-col bg-bg min-w-0">
      {/* Header */}
      <div className="px-6 py-3 border-b border-border flex items-center justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <Avatar
            initials={contact.name[0]}
            gradient="from-pink-500 to-fuchsia-600"
          />
          <div className="min-w-0">
            <div className="text-sm font-semibold flex items-center gap-2 flex-wrap">
              <Flag iso={contact.country} size={13} />
              <span className="truncate">{contact.name}</span>
              {/* Badge sincronizado con el lifecycle ACTUAL de la conversación
                  (no del contacto) — para que el cambio en "Clasificar" se vea acá */}
              <Badge variant={conversation.lifecycle}>
                {conversation.lifecycle === "hot" ? "Hot Lead"
                  : conversation.lifecycle === "new" ? "New Lead"
                  : conversation.lifecycle === "customer" ? "Customer"
                  : "Cold Lead"}
              </Badge>
              {conversation.botPaused && (
                <Badge variant="warn"><Bot className="w-2.5 h-2.5" /> Bot pausado</Badge>
              )}
            </div>
            <div className="text-[11px] text-fg-dim truncate">
              {contact.phone} · {contact.channel === "whatsapp" ? "WhatsApp" : "Web Widget"}
              {contact.pageContext && ` · ${contact.pageContext}`}
              {assignedAgent && ` · asignada a ${assignedAgent.name}`}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          {/* Etiqueta IA (clasificador): caliente/tibio/frío/... */}
          <Dropdown
            trigger={
              <Button
                variant="secondary"
                size="sm"
                title={currentLabelMeta ? currentLabelMeta.label : "Sin clasificar aún"}
              >
                {currentLabelMeta ? (
                  <>
                    <currentLabelMeta.icon className={cn("w-3.5 h-3.5", currentLabelMeta.color)} />
                    <span>{currentLabelMeta.label}</span>
                  </>
                ) : (
                  <>
                    <Sparkles className="w-3.5 h-3.5 text-fg-dim" />
                    <span className="text-fg-dim">Etiqueta IA</span>
                  </>
                )}
                {setLabelMutation.isPending && (
                  <Loader2 className="w-3 h-3 animate-spin ml-1" />
                )}
              </Button>
            }
          >
            {(close) => (
              <>
                <DropdownLabel>Etiqueta IA del lead</DropdownLabel>
                {AI_LABEL_OPTIONS.map((opt) => {
                  const OptIcon = opt.icon;
                  return (
                    <DropdownItem
                      key={opt.value}
                      onClick={() => {
                        setLabelMutation.mutate(opt.value);
                        close();
                      }}
                    >
                      <OptIcon className={cn("w-3.5 h-3.5", opt.color)} />
                      <span>{opt.label}</span>
                      {currentLabel === opt.value && (
                        <Check className="w-3 h-3 ml-auto text-accent" />
                      )}
                    </DropdownItem>
                  );
                })}
                <DropdownSeparator />
                <div className="px-3 py-1.5 text-[10px] text-fg-dim leading-snug">
                  El agente IA reclasifica después de cada respuesta del bot.
                  Tu cambio se mantiene hasta que el bot lo pise.
                </div>
              </>
            )}
          </Dropdown>

          {/* Clasificar lifecycle (new/hot/customer/cold — escalón del funnel) */}
          <Dropdown
            trigger={
              <Button variant="secondary" size="sm">
                <Tag className="w-3.5 h-3.5" /> Lifecycle
              </Button>
            }
          >
            {(close) => (
              <>
                <DropdownLabel>Etapa del funnel</DropdownLabel>
                {LIFECYCLE_OPTIONS.map((opt) => (
                  <DropdownItem
                    key={opt.value}
                    onClick={() => { onClassify(opt.value); close(); }}
                  >
                    <span className={`w-2 h-2 rounded-full ${opt.color}`} />
                    <span>{opt.label}</span>
                    {conversation.lifecycle === opt.value && (
                      <Check className="w-3 h-3 ml-auto text-accent" />
                    )}
                  </DropdownItem>
                ))}
                <DropdownSeparator />
                <div className="px-3 py-1.5 text-[10px] text-fg-dim">
                  Distinto de Etiqueta IA — esto es la etapa del funnel.
                </div>
              </>
            )}
          </Dropdown>

          {/* Asignar */}
          <Dropdown
            trigger={
              <Button variant="secondary" size="sm">
                <UserPlus className="w-3.5 h-3.5" /> Asignar
              </Button>
            }
          >
            {(close) => (
              <>
                <DropdownLabel>Asignar a</DropdownLabel>
                {agents.length === 0 && (
                  <div className="px-3 py-2 text-[11px] text-fg-dim">Cargando equipo…</div>
                )}
                {agents.map((a) => (
                  <DropdownItem
                    key={a.id}
                    onClick={() => { onAssign(a.id); close(); }}
                  >
                    <div className={`w-5 h-5 rounded-full bg-gradient-to-br ${a.color} text-white text-[9px] font-bold flex items-center justify-center`}>
                      {a.initials}
                    </div>
                    <span>{a.name}{a.id === meId && " (yo)"}</span>
                    {conversation.assignedTo === a.id && <Check className="w-3 h-3 ml-auto text-accent" />}
                  </DropdownItem>
                ))}
                <DropdownSeparator />
                <DropdownItem onClick={() => { onAssign(null); close(); }}>
                  Quitar asignación
                </DropdownItem>
              </>
            )}
          </Dropdown>

          {/* Tomar control / Reactivar bot */}
          {conversation.botPaused ? (
            <Button variant="secondary" size="sm" onClick={onToggleBot}>
              <Play className="w-3.5 h-3.5" /> Reactivar bot
            </Button>
          ) : (
            <Button variant="warn" size="sm" onClick={onTakeoverHuman}>
              <Pause className="w-3.5 h-3.5" /> Tomar control
            </Button>
          )}

          <Button variant="ghost" size="icon-sm" onClick={onToggleContactPanel} title="Mostrar/ocultar panel">
            <PanelRightClose className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>

      {/* Mensajes */}
      <div className="flex-1 overflow-y-auto scroll-thin p-6 space-y-4">
        <div className="flex justify-center">
          <span className="text-[10px] text-fg-dim">Hoy · 10:30</span>
        </div>

        {messages.length === 0 ? (
          <div className="text-center text-fg-dim text-xs pt-10">
            Sin mensajes en esta conversación todavía.
          </div>
        ) : (
          messages.map((m) => (
            <MessageBubble key={m.id} message={m} contactInitials={contact.name[0]} />
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Composer (componente extraído) */}
      <Composer
        botPaused={conversation.botPaused}
        onToggleBot={onToggleBot}
        conversationId={conversation.id}
        channel={conversation.channel}
        agentId={user?.id ?? ""}
        agentName={user?.name ?? "Agente"}
      />
    </div>
  );
}

// MessageBubble exportado a su propio archivo (./message-bubble.tsx)
// con soporte de markdown + audio player + adjuntos.
