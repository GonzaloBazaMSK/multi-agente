"use client";

import {
  Code2,
  PanelRightClose,
  UserPlus,
  AlarmClock,
  Bot,
  Pause,
  Play,
  Check,
  Tag,
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
import { TEAM, ME } from "@/lib/mock-data";
import type { ContactDetail, ConversationListItem, LifecycleStage, Message } from "@/lib/mock-data";

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
  onSnooze:        (until: string | null) => void;
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
  onSnooze,
  onTakeoverHuman,
  onToggleBot,
  onClassify,
}: Props) {

  if (!contact || !conversation) {
    return (
      <div className="flex-1 flex items-center justify-center bg-bg text-fg-dim text-sm">
        Seleccioná una conversación
      </div>
    );
  }

  const assignedAgent = conversation.assignedTo
    ? TEAM.find((a) => a.id === conversation.assignedTo)
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
          {/* Clasificar (override manual del lifecycle) */}
          <Dropdown
            trigger={
              <Button variant="secondary" size="sm">
                <Tag className="w-3.5 h-3.5" /> Clasificar
              </Button>
            }
          >
            {(close) => (
              <>
                <DropdownLabel>Clasificación manual</DropdownLabel>
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
                  El bot reclasifica auto en cada turno. Tu cambio queda fijo hasta que vos lo cambies.
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
                {TEAM.map((a) => (
                  <DropdownItem
                    key={a.id}
                    onClick={() => { onAssign(a.id); close(); }}
                  >
                    <div className={`w-5 h-5 rounded-full bg-gradient-to-br ${a.color} text-white text-[9px] font-bold flex items-center justify-center`}>
                      {a.initials}
                    </div>
                    <span>{a.name}{a.id === ME.id && " (yo)"}</span>
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

          {/* Snooze */}
          <Dropdown
            trigger={
              <Button variant="secondary" size="sm">
                <AlarmClock className="w-3.5 h-3.5" /> Snooze
              </Button>
            }
          >
            {(close) => (
              <>
                <DropdownLabel>Postergar hasta</DropdownLabel>
                <DropdownItem onClick={() => { onSnooze("en 1 hora");      close(); }}>En 1 hora</DropdownItem>
                <DropdownItem onClick={() => { onSnooze("en 4 horas");     close(); }}>En 4 horas</DropdownItem>
                <DropdownItem onClick={() => { onSnooze("mañana 09:00");   close(); }}>Mañana a la mañana</DropdownItem>
                <DropdownItem onClick={() => { onSnooze("la próxima semana"); close(); }}>La semana que viene</DropdownItem>
                {conversation.snoozedUntil && (
                  <>
                    <DropdownSeparator />
                    <DropdownItem onClick={() => { onSnooze(null); close(); }} variant="danger">
                      Cancelar snooze
                    </DropdownItem>
                  </>
                )}
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
      </div>

      {/* Composer (componente extraído) */}
      <Composer botPaused={conversation.botPaused} onToggleBot={onToggleBot} />
    </div>
  );
}

function MessageBubble({ message, contactInitials }: { message: Message; contactInitials: string }) {
  if (message.role === "user") {
    return (
      <div className="flex gap-3 justify-end">
        <div className="max-w-xl">
          <div className="text-[10px] text-fg-dim mb-1 text-right">Usuario · {message.at}</div>
          <div className="bg-accent/15 border border-accent/30 rounded-lg px-4 py-2.5 text-sm">
            {message.content}
          </div>
        </div>
        <div className="w-8 h-8 rounded-full bg-gradient-to-br from-pink-500 to-fuchsia-600 text-white text-xs font-bold flex items-center justify-center shrink-0">
          {contactInitials}
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-3">
      <div className="w-8 h-8 rounded-full bg-accent/20 text-accent text-xs font-bold flex items-center justify-center shrink-0">
        🤖
      </div>
      <div className="max-w-xl space-y-2">
        <div className="text-[10px] text-fg-dim">Bot · {message.agent} · {message.at}</div>
        {message.toolCall && (
          <div className="bg-info/10 border border-info/30 rounded-lg px-3 py-2 text-xs flex items-center gap-2">
            <Code2 className="w-3.5 h-3.5 text-info shrink-0" />
            <span className="font-mono text-info truncate">
              {message.toolCall.name}({message.toolCall.args})
            </span>
            <span className="ml-auto text-[10px] text-success shrink-0">
              {message.toolCall.status === "ok" ? "200 OK" : "ERR"}
              {message.toolCall.duration && ` · ${message.toolCall.duration}`}
            </span>
          </div>
        )}
        <div className="bg-card rounded-lg px-4 py-2.5 text-sm whitespace-pre-line">
          {message.content}
        </div>
      </div>
    </div>
  );
}
