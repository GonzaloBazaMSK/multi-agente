"use client";

import { useState, useEffect } from "react";
import { Bell, Volume2, Mail, Loader2, UserPlus, MessageCircle, Clock, CheckCircle2, BellRing, BellOff, BellDot } from "lucide-react";

import { useAuth } from "@/lib/auth";
import { useNotifications } from "@/lib/use-notifications";
import { DEFAULT_PREFERENCES, type Preferences } from "@/lib/notifications";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

export default function NotificationsSettingsPage() {
  const { preferences, updatePreferences, isSaving, isLoading } = useNotifications();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

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
            {isAdmin && (
              <Toggle
                icon={CheckCircle2}
                iconColor="text-success"
                title="Plantilla HSM aprobada"
                description="Meta aprobó una plantilla que subiste — ya podés usarla."
                checked={draft.template_approved}
                onChange={() => toggle("template_approved")}
                disabled={isLoading}
              />
            )}
          </div>
        </section>

        {/* Permisos del navegador */}
        <section>
          <h2 className="text-sm font-semibold mb-3">Permisos del navegador</h2>
          <BrowserPermissionCard />
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
          <button
            onClick={playTestSound}
            className="mt-2 text-[11px] text-fg-dim hover:text-fg underline underline-offset-2"
          >
            ▶ Probar sonido ahora
          </button>
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

function playTestSound() {
  const audio = new Audio("/notif.wav");
  audio.volume = 0.7;
  audio.play().catch((e) => console.warn("[test] audio blocked:", e?.message));
}

async function sendDebugNotif() {
  try {
    await api.post("/notifications/debug-notify-me", {
      type: "new_message_mine",
      data: { client_name: "Prueba", preview: "Esto es una notificación de prueba" },
    });
  } catch (e) {
    console.warn("[test] debug-notify-me failed:", e);
  }
}

function BrowserPermissionCard() {
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">("default");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<"ok" | "err" | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!("Notification" in window)) {
      setPermission("unsupported");
      return;
    }
    setPermission(Notification.permission);
  }, []);

  const request = async () => {
    if (typeof window === "undefined" || !("Notification" in window)) return;
    const result = await Notification.requestPermission();
    setPermission(result);
    if (result === "granted") {
      playTestSound();
      new Notification("MSK Console", {
        body: "¡Notificaciones activadas! Así se verán.",
        icon: "/logo.png",
        tag: "msk-perm-test",
      });
    }
  };

  const testAll = async () => {
    setTesting(true);
    setTestResult(null);
    playTestSound();
    if (typeof window !== "undefined" && "Notification" in window && Notification.permission === "granted") {
      new Notification("MSK Console", {
        body: "Notificación de prueba — sonido + badge en campana",
        icon: "/logo.png",
        tag: "msk-test",
      });
    }
    try {
      await sendDebugNotif();
      setTestResult("ok");
    } catch {
      setTestResult("err");
    } finally {
      setTesting(false);
    }
  };

  if (permission === "unsupported") {
    return (
      <div className="flex items-start gap-3 p-3 rounded-lg border border-border bg-card">
        <BellOff className="w-4 h-4 shrink-0 mt-0.5 text-fg-muted" />
        <div className="text-sm text-fg-dim">Tu navegador no soporta notificaciones push.</div>
      </div>
    );
  }

  if (permission === "granted") {
    return (
      <div className="flex items-start gap-3 p-3 rounded-lg border border-success/40 bg-success/5">
        <BellRing className="w-4 h-4 shrink-0 mt-0.5 text-success" />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-success">Activadas ✓</div>
          <div className="text-[11px] text-fg-dim mt-0.5">
            Chrome mostrará alertas del sistema aunque la ventana esté minimizada.
          </div>
          {testResult === "ok" && (
            <div className="text-[11px] text-success mt-1">Notificación enviada — deberías ver el popup y escuchar el sonido.</div>
          )}
          {testResult === "err" && (
            <div className="text-[11px] text-destructive mt-1">Error al enviar — revisá la consola del navegador.</div>
          )}
        </div>
        <button
          onClick={testAll}
          disabled={testing}
          className="shrink-0 text-[12px] font-medium bg-accent text-white px-3 py-1.5 rounded-md hover:bg-accent/90 transition-colors disabled:opacity-50"
        >
          {testing ? "…" : "Probar todo"}
        </button>
      </div>
    );
  }

  if (permission === "denied") {
    return (
      <div className="flex items-start gap-3 p-3 rounded-lg border border-destructive/40 bg-destructive/5">
        <BellOff className="w-4 h-4 shrink-0 mt-0.5 text-destructive" />
        <div className="text-sm">
          <span className="font-medium text-destructive">Bloqueadas por el navegador.</span>
          <p className="text-[11px] text-fg-dim mt-1 leading-snug">
            Hacé clic en el candado en la barra de dirección → <strong>Notificaciones</strong> → <strong>Permitir</strong>, y recargá la página.
          </p>
        </div>
      </div>
    );
  }

  // default — nunca se pidió permiso
  return (
    <div className="flex items-start gap-3 p-3 rounded-lg border border-warn/40 bg-warn/5">
      <BellDot className="w-4 h-4 shrink-0 mt-0.5 text-warn" />
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium">Permiso no solicitado</div>
        <div className="text-[11px] text-fg-dim mt-0.5 leading-snug">
          Activá las notificaciones del sistema para recibir alertas cuando Chrome esté minimizado.
        </div>
      </div>
      <button
        onClick={request}
        className="shrink-0 text-[12px] font-medium bg-accent text-white px-3 py-1.5 rounded-md hover:bg-accent/90 transition-colors"
      >
        Activar
      </button>
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
  // Toda la fila es clickeable. Antes usábamos <label> + <button> role=switch
  // pero el <label> estaba tragando el click (no hay <input> asociado — label
  // semántico solo tiene sentido cuando envuelve o apunta a un input), así
  // que el onChange jamás se disparaba. Lo cambiamos a <div role=switch>.
  const handleToggle = () => {
    if (!disabled) onChange();
  };

  return (
    <div
      role="switch"
      aria-checked={checked}
      aria-disabled={disabled}
      tabIndex={disabled ? -1 : 0}
      onClick={handleToggle}
      onKeyDown={(e) => {
        if (e.key === " " || e.key === "Enter") {
          e.preventDefault();
          handleToggle();
        }
      }}
      className={cn(
        "flex items-start gap-3 p-3 rounded-lg border border-border bg-card transition-colors outline-none",
        disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer hover:border-accent/40 focus:border-accent",
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
      <div
        aria-hidden="true"
        className={cn(
          "shrink-0 mt-0.5 w-9 h-5 rounded-full relative transition-colors",
          checked ? "bg-accent" : "bg-border",
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform",
            checked ? "translate-x-[18px]" : "translate-x-0.5",
          )}
        />
      </div>
    </div>
  );
}
