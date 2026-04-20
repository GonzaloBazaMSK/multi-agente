"use client";

/**
 * Dropdown del ícono campana en el rail lateral.
 *
 * Muestra:
 *   - Header con contador + botón "Marcar todas"
 *   - Lista scrolleable de las últimas 50 notifs
 *   - Click en una notif → la marca leída y navega a su `href`
 *   - Footer con link a /settings/notifications para las preferencias
 *
 * El badge rojo del botón (ícono de campana) muestra el conteo real de no
 * leídas via `useNotifications()`. El push real llega por SSE — el hook ya
 * se encarga; este componente solo renderiza.
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Bell, CheckCheck, Settings, Inbox } from "lucide-react";

import { useNotifications } from "@/lib/use-notifications";
import {
  NOTIFICATION_MAP,
  FALLBACK_META,
  formatRelative,
  type Notification,
} from "@/lib/notifications";
import { cn } from "@/lib/utils";

export function NotificationsDropdown() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const { notifications, unreadCount, markRead, markAllRead, isLoading } = useNotifications();
  const router = useRouter();

  // Cerrar al clickear afuera
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const handleClick = (n: Notification) => {
    const meta = NOTIFICATION_MAP[n.type];
    if (!n.read_at) markRead(n.id);
    const href = meta?.href(n.data);
    if (href) {
      router.push(href);
      setOpen(false);
    }
  };

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "rail-tooltip-wrap relative w-9 h-9 rounded-md flex items-center justify-center transition-colors",
          open ? "bg-accent/18 text-fg" : "text-fg-muted hover:bg-hover hover:text-fg",
        )}
        data-tooltip="Notificaciones"
        aria-label="Abrir notificaciones"
      >
        <Bell className="w-[18px] h-[18px]" />
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-4 h-4 px-1 rounded-full bg-danger ring-2 ring-panel text-white text-[9px] font-bold flex items-center justify-center tabular-nums">
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute bottom-0 left-12 w-80 bg-panel border border-border rounded-lg shadow-lg z-50 flex flex-col max-h-[70vh]">
          {/* Header */}
          <div className="px-3 py-2 border-b border-border flex items-center justify-between gap-2">
            <div className="text-sm font-semibold flex items-center gap-2">
              Notificaciones
              {unreadCount > 0 && (
                <span className="text-[10px] text-fg-dim font-normal">
                  ({unreadCount} sin leer)
                </span>
              )}
            </div>
            {unreadCount > 0 && (
              <button
                onClick={() => markAllRead()}
                className="text-[10px] text-fg-dim hover:text-accent flex items-center gap-1"
                title="Marcar todas como leídas"
              >
                <CheckCheck className="w-3 h-3" /> Marcar todas
              </button>
            )}
          </div>

          {/* Lista */}
          <div className="flex-1 overflow-y-auto scroll-thin min-h-[120px]">
            {isLoading && (
              <div className="p-6 text-center text-[11px] text-fg-dim">Cargando…</div>
            )}
            {!isLoading && notifications.length === 0 && (
              <div className="p-6 text-center">
                <Inbox className="w-6 h-6 mx-auto mb-2 text-fg-dim opacity-60" />
                <div className="text-[12px] text-fg-dim">Al día 🎉</div>
                <div className="text-[10px] text-fg-dim mt-0.5">Sin notificaciones</div>
              </div>
            )}
            {notifications.map((n) => {
              const meta = NOTIFICATION_MAP[n.type];
              const Icon = meta?.icon || FALLBACK_META.icon;
              const color = meta?.color || FALLBACK_META.color;
              const title = meta?.title?.(n.data) || n.type;
              const preview = meta?.preview?.(n.data) || "";
              return (
                <button
                  key={n.id}
                  onClick={() => handleClick(n)}
                  className={cn(
                    "w-full text-left px-3 py-2 flex items-start gap-2.5 border-b border-border/50 transition-colors",
                    n.read_at ? "opacity-70" : "bg-accent/[0.03]",
                    "hover:bg-hover",
                  )}
                >
                  <Icon className={cn("w-4 h-4 mt-0.5 shrink-0", color)} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-baseline gap-2">
                      <span className="text-xs font-medium truncate">{title}</span>
                      <span className="text-[10px] text-fg-dim shrink-0">
                        {formatRelative(n.created_at)}
                      </span>
                    </div>
                    {preview && (
                      <div className="text-[11px] text-fg-dim truncate mt-0.5">
                        {preview}
                      </div>
                    )}
                  </div>
                  {!n.read_at && (
                    <span
                      className="w-1.5 h-1.5 rounded-full bg-accent shrink-0 mt-1.5"
                      aria-label="No leída"
                    />
                  )}
                </button>
              );
            })}
          </div>

          {/* Footer */}
          <div className="border-t border-border px-2 py-1">
            <Link
              href="/settings/notifications"
              onClick={() => setOpen(false)}
              className="w-full flex items-center gap-2 px-2 py-1.5 text-xs text-fg-muted hover:bg-hover hover:text-fg rounded transition-colors"
            >
              <Settings className="w-3.5 h-3.5" />
              <span>Preferencias</span>
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
