"use client";

/**
 * Hook de notificaciones in-app.
 *
 * Responsabilidades:
 *   1. Lista inicial via GET /api/v1/notifications
 *   2. Conteo de no leídas para el badge (cacheado via TanStack Query)
 *   3. SSE a /api/v1/notifications/stream para push en tiempo real
 *   4. Reproducir un beep si `sound_enabled` en las prefs
 *   5. Mutations: markRead, markAllRead
 *
 * El stream reconnecta automáticamente porque EventSource hace eso nativo
 * si el endpoint manda `retry: 5000` (lo hacemos en api/notifications.py).
 *
 * El beep usa la Web Audio API con una frecuencia corta — no requiere un
 * archivo externo ni permisos extras.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Notification, Preferences } from "@/lib/notifications";

type ListResponse = { notifications: Notification[]; count: number };
type CountResponse = { count: number };

// Mantenemos un AudioContext único entre beeps. Si lo creamos en cada playBeep,
// Chrome lo crea en estado "suspended" hasta la primera interacción del usuario,
// y los beeps siguientes pueden seguir suspendidos.
let _sharedAudioCtx: AudioContext | null = null;

function getAudioCtx(): AudioContext | null {
  if (typeof window === "undefined") return null;
  if (_sharedAudioCtx) return _sharedAudioCtx;
  try {
    const Ctx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    _sharedAudioCtx = new Ctx();
    return _sharedAudioCtx;
  } catch {
    return null;
  }
}

function playBeep() {
  const ctx = getAudioCtx();
  if (!ctx) return;
  // Chrome bloquea audio hasta que el user interactúe con la página. resume()
  // lo despierta. Si nunca hubo click, queda suspended y no suena (limitación
  // del navegador, no nuestra). Una vez que el user clickea cualquier cosa,
  // los beeps siguientes ya funcionan.
  if (ctx.state === "suspended") ctx.resume().catch(() => {});
  try {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.3);
  } catch {
    // Silently ignore.
  }
}

/**
 * Muestra una notificación push del navegador (la que aparece en la barra del
 * SO incluso si el navegador está minimizado / sin foco). Requiere permiso
 * explícito del usuario — la primera vez la pedimos con requestPermission().
 */
function showBrowserNotification(title: string, body: string) {
  if (typeof window === "undefined" || !("Notification" in window)) return;
  if (Notification.permission !== "granted") return;
  try {
    const n = new Notification(title, {
      body,
      icon: "/logo.png",
      tag: "msk-inbox", // mismo tag → reemplaza la anterior, no se acumulan
      silent: false, // que use el sonido del SO; nuestro beep es complementario
    });
    n.onclick = () => {
      window.focus();
      n.close();
    };
  } catch {
    // Algunos navegadores tiran si Notification se llama desde service worker
    // context. Si pasa, ignoramos — el beep + el badge de la consola son fallback.
  }
}

/**
 * Pide permiso para notifs push si todavía no se decidió. Idempotente — si ya
 * está "granted" o "denied", no hace nada. Conviene llamarlo en respuesta a
 * un click del user (Chrome rechaza requestPermission sin user gesture).
 */
function ensureNotificationPermission() {
  if (typeof window === "undefined" || !("Notification" in window)) return;
  if (Notification.permission === "default") {
    Notification.requestPermission().catch(() => {});
  }
}

export function useNotifications() {
  const qc = useQueryClient();
  const esRef = useRef<EventSource | null>(null);

  const prefsQ = useQuery<Preferences>({
    queryKey: ["notifications", "preferences"],
    queryFn: () => api.get("/notifications/preferences"),
    staleTime: 60_000,
  });

  const listQ = useQuery<ListResponse>({
    queryKey: ["notifications", "list"],
    queryFn: () => api.get("/notifications?limit=50"),
    staleTime: 30_000,
  });

  const countQ = useQuery<CountResponse>({
    queryKey: ["notifications", "unread-count"],
    queryFn: () => api.get("/notifications/unread-count"),
    staleTime: 30_000,
    refetchInterval: 60_000, // fallback polling por si SSE falla silencioso
  });

  const markRead = useMutation({
    mutationFn: (id: string) => api.post(`/notifications/${id}/read`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications", "list"] });
      qc.invalidateQueries({ queryKey: ["notifications", "unread-count"] });
    },
  });

  const markAllRead = useMutation({
    mutationFn: () => api.post<{ ok: true; marked: number }>("/notifications/mark-all-read"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications", "list"] });
      qc.invalidateQueries({ queryKey: ["notifications", "unread-count"] });
    },
  });

  const updatePreferences = useMutation({
    mutationFn: (p: Partial<Preferences>) =>
      api.patch<Preferences>("/notifications/preferences", p),
    onSuccess: (data) => {
      qc.setQueryData(["notifications", "preferences"], data);
    },
  });

  // Pedir permiso de notifs del navegador en el primer mount. Idempotente —
  // si ya hay decisión guardada (granted/denied) no molesta. Chrome solo
  // acepta la pedida en respuesta a un user gesture, pero si la pestaña se
  // abre via click la primera invocación suele andar igual.
  useEffect(() => {
    ensureNotificationPermission();
  }, []);

  // Connect SSE cuando hay prefs cargadas (necesitamos saber `sound_enabled`)
  useEffect(() => {
    if (!prefsQ.data) return;
    const prefs = prefsQ.data;

    // Si ya hay una conexión, la cerramos antes de crear otra (evita leaks)
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }

    const url = "/api/v1/notifications/stream";
    const es = new EventSource(url, { withCredentials: true });
    esRef.current = es;

    es.onmessage = (ev) => {
      try {
        const payload = JSON.parse(ev.data);
        if (payload.event === "new" && payload.notification) {
          // Actualiza lista + count localmente sin refetch full
          qc.setQueryData<ListResponse>(["notifications", "list"], (old) => {
            if (!old) return { notifications: [payload.notification], count: 1 };
            return {
              notifications: [payload.notification, ...old.notifications].slice(0, 50),
              count: Math.min(old.count + 1, 50),
            };
          });
          qc.setQueryData<CountResponse>(["notifications", "unread-count"], (old) => ({
            count: (old?.count ?? 0) + 1,
          }));
          if (prefs.sound_enabled) playBeep();
          // Push del navegador — visible aunque el browser esté minimizado.
          // Solo si hay permiso (request al mount). Mensaje contextualizado
          // por tipo de notif.
          const n = payload.notification as {
            title?: string;
            body?: string;
            type?: string;
            data?: { client_name?: string; preview?: string };
          };
          const title = n.title || "MSK Console";
          const body =
            n.body ||
            (n.type === "new_message_mine"
              ? `${n.data?.client_name ?? "Cliente"}: ${n.data?.preview ?? ""}`
              : n.type === "conv_assigned"
                ? `Te asignaron: ${n.data?.client_name ?? "una conversación"}`
                : n.type === "conv_stale"
                  ? `Sin respuesta hace 2h: ${n.data?.client_name ?? "cliente"}`
                  : "Tenés una novedad");
          showBrowserNotification(title, body);
        }
      } catch {
        // payload inválido, ignorar
      }
    };

    es.onerror = () => {
      // EventSource reconecta solo — solo necesitamos que no explote.
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [prefsQ.data, qc]);

  const notifications = listQ.data?.notifications ?? [];
  const unreadCount = countQ.data?.count ?? 0;

  return {
    notifications,
    unreadCount,
    preferences: prefsQ.data,
    isLoading: listQ.isLoading || prefsQ.isLoading,
    markRead: useCallback((id: string) => markRead.mutate(id), [markRead]),
    markAllRead: useCallback(() => markAllRead.mutate(), [markAllRead]),
    updatePreferences: useCallback(
      (p: Partial<Preferences>) => updatePreferences.mutate(p),
      [updatePreferences],
    ),
    isSaving: markRead.isPending || markAllRead.isPending || updatePreferences.isPending,
  };
}
