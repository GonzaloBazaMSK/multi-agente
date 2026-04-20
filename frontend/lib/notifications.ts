/**
 * Tipos + helpers compartidos para notificaciones in-app.
 *
 * Mapa central de `type` del backend → metadatos de presentación (icono,
 * color, link). Agregar un tipo nuevo:
 *   1) en backend, sumar a VALID_TYPES de utils/notifications.py
 *   2) acá, agregar caso en NOTIFICATION_MAP
 */

import {
  Bell,
  UserPlus,
  MessageCircle,
  Clock,
  CheckCircle2,
  type LucideIcon,
} from "lucide-react";

export type NotificationType =
  | "conv_assigned"
  | "new_message_mine"
  | "conv_stale"
  | "template_approved";

export type Notification = {
  id: string;
  user_id: string;
  type: NotificationType;
  data: {
    conversation_id?: string;
    client_name?: string;
    preview?: string;
    queue?: string;
    channel?: string;
    template_name?: string;
    [k: string]: unknown;
  };
  created_at: string;
  read_at: string | null;
};

export type Preferences = {
  conv_assigned: boolean;
  new_message_mine: boolean;
  conv_stale: boolean;
  template_approved: boolean;
  sound_enabled: boolean;
  email_digest: boolean;
};

export const DEFAULT_PREFERENCES: Preferences = {
  conv_assigned: true,
  new_message_mine: true,
  conv_stale: true,
  template_approved: true,
  sound_enabled: false,
  email_digest: false,
};

export const NOTIFICATION_MAP: Record<
  NotificationType,
  {
    icon: LucideIcon;
    color: string;
    title: (data: Notification["data"]) => string;
    preview: (data: Notification["data"]) => string;
    href: (data: Notification["data"]) => string | null;
  }
> = {
  conv_assigned: {
    icon: UserPlus,
    color: "text-accent",
    title: () => "Te asignaron una conversación",
    preview: (d) => d.client_name || "cliente",
    href: (d) => (d.conversation_id ? `/inbox?c=${d.conversation_id}` : null),
  },
  new_message_mine: {
    icon: MessageCircle,
    color: "text-info",
    title: (d) => `Mensaje de ${d.client_name || "cliente"}`,
    preview: (d) => (d.preview as string) || "…",
    href: (d) => (d.conversation_id ? `/inbox?c=${d.conversation_id}` : null),
  },
  conv_stale: {
    icon: Clock,
    color: "text-warn",
    title: (d) => `Sin respuesta: ${d.client_name || "cliente"}`,
    preview: () => "Pasaron más de 2h desde el último mensaje",
    href: (d) => (d.conversation_id ? `/inbox?c=${d.conversation_id}` : null),
  },
  template_approved: {
    icon: CheckCircle2,
    color: "text-success",
    title: (d) => `Plantilla aprobada: ${d.template_name || ""}`,
    preview: () => "Ya podés usarla para reabrir conversaciones de WhatsApp",
    href: () => "/templates",
  },
};

// Fallback si el type del backend no está mapeado (deprecated / nuevo sin migrar UI)
export const FALLBACK_META = {
  icon: Bell,
  color: "text-fg-muted",
};

export function formatRelative(iso: string): string {
  const d = new Date(iso);
  const diffSec = Math.round((Date.now() - d.getTime()) / 1000);
  if (diffSec < 60) return "ahora";
  if (diffSec < 3600) return `${Math.round(diffSec / 60)}m`;
  if (diffSec < 86400) return `${Math.round(diffSec / 3600)}h`;
  if (diffSec < 86400 * 7) return `${Math.round(diffSec / 86400)}d`;
  return d.toLocaleDateString("es-AR", { day: "2-digit", month: "short" });
}
