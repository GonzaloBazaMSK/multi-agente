"use client";

import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getAuthToken } from "@/lib/auth";

/**
 * Suscribe al SSE del inbox y refetcha conversaciones + mensajes
 * cuando llega un evento nuevo (sin esperar polling de 15s).
 *
 * Auth: EventSource no permite headers custom, así que mandamos el JWT como
 * query param `?token=`. El backend lo valida igual que `x-session-token`.
 * (Usábamos el admin key acá pero estaba expuesto en el bundle del browser
 * y bypaseaba el login — ver lib/api.ts para contexto.)
 */
export function useInboxSSE(currentConversationId: string | null) {
  const qc = useQueryClient();

  useEffect(() => {
    const token = getAuthToken();
    if (!token) return; // sin token no hay SSE — el guard ya redirige a /login
    const url = `/api/inbox/stream?token=${encodeURIComponent(token)}`;

    let es: EventSource | null = null;
    let retryTimeout: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      es = new EventSource(url);

      es.onmessage = (e) => {
        try {
          const evt = JSON.parse(e.data);
          // refetch lista (todas las conversaciones se invalidan)
          qc.invalidateQueries({ queryKey: ["inbox", "conversations"] });
          // si el mensaje es de la conv abierta, refetch sus mensajes
          if (evt.type === "new_message" && evt.session_id) {
            // podemos invalidar TODOS los messages porque los queries son por id
            qc.invalidateQueries({ queryKey: ["inbox", "messages"] });
          }
        } catch { /* ignore */ }
      };

      es.onerror = () => {
        es?.close();
        retryTimeout = setTimeout(connect, 3000);
      };
    };

    connect();
    return () => {
      es?.close();
      if (retryTimeout) clearTimeout(retryTimeout);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
