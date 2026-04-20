"use client";

/**
 * /settings/notifications — preferencias del usuario logueado.
 *
 * No es admin-only — cada usuario decide qué notifs recibir.
 *
 * Hoy se persiste por-user en `notification_preferences`. La creación de
 * la fila es lazy (al primer GET /preferences). No hace falta seed.
 */

import { useState, useEffect } from "react";
import { Bell, Volume2, Mail, Loader2, UserPlus, MessageCircle, Clock, CheckCircle2 } from "lucide-react";

import { useNotifications } from "@/lib/use-notifications";
import { DEFAULT_PREFERENCES, type Preferences } from "@/lib/notifications";
import { cn } from "@/lib/utils";

export default function NotificationsSettingsPage() {
  const { preferences, updatePreferences, isSaving, isLoading } = useNotifications();

  // Estado local para edición — cuando llega `preferences` del backend, lo
  // sincronizamos. Si el user cambia algo mientras llega otra invalidación,
  // el local gana.
  const [draft, setDraft] = useState<Preferences>(DEFAULT_PREFERENCES);
  useEffect(() => {
    if (preferences) setDraft(preferences);
  }, [preferences]);

  const toggle = (key: keyof Preferences) => {
    const next = !draft[key];
    setDraft((d) => ({ ...d, [key]: next }));
    updatePreferences({ [key]: next });
  };

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 py-4 border-b border-border flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Notificaciones</h1>
          <p className="text-xs text-fg-dim mt-0.5">
            Qué alertas querés recibir en el ícono de la campana.
          </p>
        </div>
        {isSaving && (
          <div className="text-[11px] text-fg-dim flex items-center gap-1">
            <Loader2 className="w-3 h-3 animate-spin" /> Guardando…
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto scroll-thin p-6 space-y-6 max-w-2xl">
        {isLoading && (
          <div className="text-xs text-fg-dim flex items-center gap-2">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Cargando preferencias…
          </div>
        )}

        {/* Tipos de notificación */}
        <section>
          <h2 className="text-sm font-semibold mb-3">Qué eventos me notifican</h2>
          <div className="space-y-1">
            <Toggle
              icon={UserPlus}
              iconColor="text-accent"
              title="Te asignan una conversación"
              description="Cuando vos o un supervisor te marcan como responsable de un chat."
              checked={draft.conv_assigned}
              onChange={() => toggle("conv_assigned")}
              disabled={isLoading}
            />
            <Toggle
              icon={MessageCircle}
              iconColor="text-info"
              title="Mensaje nuevo en tus conversaciones"
              description="El cliente te escribió en una conversación que ya tenés asignada."
              checked={draft.new_message_mine}
              onChange={() => toggle("new_message_mine")}
              disabled={isLoading}
            />
            <Toggle
              icon={Clock}
              iconColor="text-warn"
              title="Conversación sin respuesta hace 2h"
              description="Alerta de SLA — el cliente te escribió hace más de 2h y no respondiste."
              checked={draft.conv_stale}
              onChange={() => toggle("conv_stale")}
              disabled={isLoading}
            />
            <Toggle
              icon={CheckCircle2}
              iconColor="text-success"
              title="Plantilla HSM aprobada"
              description="Meta aprobó una plantilla que subiste — ya podés usarla."
              checked={draft.template_approved}
              onChange={() => toggle("template_approved")}
              disabled={isLoading}
            />
          </div>
        </section>

        {/* Preferencias UX */}
        <section>
          <h2 className="text-sm font-semibold mb-3">Cómo las recibo</h2>
          <div className="space-y-1">
            <Toggle
              icon={Volume2}
              iconColor="text-info"
              title="Sonido"
              description="Beep corto al llegar una notificación nueva."
              checked={draft.sound_enabled}
              onChange={() => toggle("sound_enabled")}
              disabled={isLoading}
            />
            <Toggle
              icon={Mail}
              iconColor="text-warn"
              title="Digest por email"
              description="Resumen diario de notificaciones que no leíste. (Próximamente)"
              checked={draft.email_digest}
              onChange={() => toggle("email_digest")}
              disabled
              comingSoon
            />
          </div>
        </section>

        <div className="bg-card border border-border rounded-lg p-3 text-[11px] text-fg-dim leading-relaxed">
          <div className="flex items-start gap-2">
            <Bell className="w-3.5 h-3.5 shrink-0 mt-0.5 text-fg-muted" />
            <div>
              Las notificaciones se guardan por 90 días en la base y se acceden vía el ícono
              de la campana en el rail. El push en tiempo real usa SSE —{" "}
              <span className="text-fg">si tu conexión cae</span>, se reconecta automáticamente.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Toggle({
  icon: Icon,
  iconColor,
  title,
  description,
  checked,
  onChange,
  disabled,
  comingSoon,
}: {
  icon: React.ComponentType<{ className?: string }>;
  iconColor: string;
  title: string;
  description: string;
  checked: boolean;
  onChange: () => void;
  disabled?: boolean;
  comingSoon?: boolean;
}) {
  return (
    <label
      className={cn(
        "flex items-start gap-3 p-3 rounded-lg border border-border bg-card cursor-pointer transition-colors",
        disabled ? "opacity-50 cursor-not-allowed" : "hover:border-accent/40",
      )}
    >
      <Icon className={cn("w-4 h-4 shrink-0 mt-0.5", iconColor)} />
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium flex items-center gap-2">
          {title}
          {comingSoon && (
            <span className="text-[9px] uppercase tracking-wider bg-warn/15 text-warn px-1.5 py-0.5 rounded">
              Próximamente
            </span>
          )}
        </div>
        <div className="text-[11px] text-fg-dim leading-snug mt-0.5">{description}</div>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={(e) => {
          e.preventDefault();
          if (!disabled) onChange();
        }}
        className={cn(
          "shrink-0 mt-0.5 w-9 h-5 rounded-full relative transition-colors",
          checked ? "bg-accent" : "bg-border",
          disabled && "cursor-not-allowed",
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform",
            checked ? "translate-x-[18px]" : "translate-x-0.5",
          )}
        />
      </button>
    </label>
  );
}
