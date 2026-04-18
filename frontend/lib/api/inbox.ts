/**
 * Cliente del backend /api/inbox/* — todas las queries y mutations del inbox.
 */
import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type {
  ConversationListItem,
  Channel,
  LifecycleStage,
  ConvStatus,
  Queue,
  InboxView,
  ContactDetail,
  Message,
  Agent,
} from "@/lib/mock-data";

// ─────────────────────────────────────────────────────────────────────
// Types que devuelve el backend (mantén aligned con api/inbox_api.py)
// ─────────────────────────────────────────────────────────────────────

export type ApiConversation = {
  id: string;
  session_id: string;
  channel: Channel;
  name: string;
  email: string;
  phone: string;
  country: string;
  last_message: string;
  last_timestamp: string;
  message_count: number;
  assigned_agent_id: string | null;
  status: ConvStatus;
  lifecycle: LifecycleStage;
  lifecycle_is_manual: boolean;
  queue: Queue;
  bot_paused: boolean;
  needs_human: boolean;
  tags: string[];
  unread: boolean;
};

export type ApiContact = {
  zoho_id: string | null;
  name: string;
  first_name: string;
  last_name: string;
  email: string;
  phone: string;
  country: string;
  country_name: string;
  professional: {
    profession: string | null;
    specialty: string | null;
    cargo: string | null;
    workplace: string | null;
    work_area: string | null;
  };
  jurisdictional_cert: { code: string | null; name: string } | null;
  courses_taken: string[];
  scoring: { profile: number; sales: number };
  cobranzas: ApiCobranzas | null;
};

export type ApiCobranzas = {
  status: "ok" | "due_soon" | "overdue" | "suspended";
  currency: string;
  overdueAmount: number;
  totalDueAmount: number;
  contractAmount: number;
  installmentValue: number;
  lastPaymentAmount: number;
  totalInstallments: number;
  paidInstallments: number;
  overdueInstallments: number;
  pendingInstallments: number;
  daysOverdue: number;
  contractStatus: string;
  paymentMethod: string;
  nextDue: string | null;
  paymentLink: string | null;
};

// ─────────────────────────────────────────────────────────────────────
// Mappers: API → tipos del frontend (mock-data.ts)
// ─────────────────────────────────────────────────────────────────────

const PALETTES = [
  "from-pink-500 to-fuchsia-600",
  "from-blue-500 to-blue-700",
  "from-emerald-500 to-emerald-700",
  "from-zinc-600 to-zinc-800",
  "from-amber-500 to-amber-700",
  "from-purple-500 to-pink-600",
  "from-teal-500 to-cyan-700",
];

function colorFromName(name: string): string {
  const n = (name || "?").charCodeAt(0) || 0;
  return PALETTES[n % PALETTES.length];
}

function fmtTime(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) {
    return d.toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit" });
  }
  if (diff < 1000 * 60 * 60 * 24 * 2) return "ayer";
  return d.toLocaleDateString("es-AR", { day: "2-digit", month: "2-digit" });
}

export function apiToListItem(c: ApiConversation): ConversationListItem {
  return {
    id: c.id,
    contact: {
      name: c.name || "Visitante anónimo",
      initials: (c.name || "?")[0].toUpperCase(),
      avatarColor: colorFromName(c.name),
      country: c.country || "AR",
      email: c.email,
      phone: c.phone,
    },
    lastMessage: c.last_message,
    lastMessageAt: fmtTime(c.last_timestamp),
    lifecycle: c.lifecycle,
    channel: c.channel,
    unread: c.unread,
    assignedTo: c.assigned_agent_id,
    needsHuman: c.needs_human,
    botPaused: c.bot_paused,
    status: c.status,
    tags: c.tags,
    queue: c.queue,
  };
}

export function apiToContactDetail(c: ApiContact): ContactDetail {
  return {
    id: c.zoho_id || c.email,
    zohoId: c.zoho_id || undefined,
    name: c.name,
    email: c.email,
    phone: c.phone,
    country: c.country || "AR",
    countryName: c.country_name || "Argentina",
    channel: "whatsapp", // se completa con la conversation
    pageContext: undefined,
    lifecycle: "new",
    professional: {
      profession: c.professional.profession || undefined,
      specialty:  c.professional.specialty  || undefined,
      cargo:      c.professional.cargo      || undefined,
      workplace:  c.professional.workplace  || undefined,
      workArea:   c.professional.work_area  || undefined,
    },
    jurisdictionalCert: c.jurisdictional_cert?.code
      ? { code: c.jurisdictional_cert.code, name: c.jurisdictional_cert.name }
      : undefined,
    coursesTaken: c.courses_taken,
    scoring: c.scoring,
    tags: [],
    ai: {
      summary: "—",
      nextStep: "—",
      scoringReasons: [],
    },
    cobranzas: c.cobranzas as any,
  };
}

// ─────────────────────────────────────────────────────────────────────
// Hooks
// ─────────────────────────────────────────────────────────────────────

/**
 * Conteo de conversaciones por (queue, country) — para el filtro Cola → País.
 * Devuelve { sales: { AR: 12, MX: 3, ... }, billing: {...}, "post-sales": {...} }
 */
export function useQueueStats() {
  return useQuery<Record<string, Record<string, number>>>({
    queryKey: ["inbox", "queue-stats"],
    queryFn: async () => api.get(`/inbox/queue-stats`),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

export function useAgents() {
  return useQuery<Agent[]>({
    queryKey: ["inbox", "agents"],
    queryFn: async () => {
      const data = await api.get<{ id: string; name: string; initials: string; color: string }[]>("/inbox/agents");
      return data.map((a) => ({ id: a.id, name: a.name, initials: a.initials, color: a.color }));
    },
    staleTime: 5 * 60_000,
  });
}

export type ConversationsParams = {
  view?: InboxView;
  lifecycle?: LifecycleStage | null;
  channel?: Channel | null;
  queue?: Queue | null;
  country?: string | null;
  search?: string;
  limit?: number;
};

export function useConversations(params: ConversationsParams) {
  const qs = new URLSearchParams();
  if (params.view && params.view !== "all") qs.set("view", params.view);
  if (params.lifecycle) qs.set("lifecycle", params.lifecycle);
  if (params.channel)   qs.set("channel", params.channel);
  if (params.queue)     qs.set("queue", params.queue);
  if (params.country)   qs.set("country", params.country);
  if (params.search)    qs.set("search", params.search);
  qs.set("limit", String(params.limit ?? 100));

  const key: QueryKey = ["inbox", "conversations", Object.fromEntries(qs)];

  return useQuery({
    queryKey: key,
    queryFn: async () => {
      const data = await api.get<ApiConversation[]>(`/inbox/conversations?${qs}`);
      return data.map(apiToListItem);
    },
    staleTime: 5_000,
    refetchInterval: 15_000, // polling de 15s para nuevas conversaciones
  });
}

export function useMessages(conversationId: string | null) {
  return useQuery({
    queryKey: ["inbox", "messages", conversationId],
    queryFn: async () => {
      if (!conversationId) return [] as Message[];
      const rows = await api.get<{
        id: string; role: string; content: string;
        agent: string | null;
        attachments?: { url: string; filename?: string; content_type?: string; size?: number }[];
        at: string;
      }[]>(
        `/inbox/conversations/${conversationId}/messages`
      );
      return rows.map((m) => ({
        id: m.id,
        role: m.role as Message["role"],
        content: m.content,
        at: new Date(m.at).toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit" }),
        agent: m.agent || undefined,
        attachments: m.attachments || [],
      }));
    },
    enabled: !!conversationId,
  });
}

export function useAIInsights(conversationId: string | null) {
  return useQuery<{ summary: string; nextStep: string; scoringReasons: string[] }>({
    queryKey: ["inbox", "ai-insights", conversationId],
    queryFn: () => api.get(`/inbox/conversations/${conversationId}/ai-insights`),
    enabled: !!conversationId,
    staleTime: 5 * 60_000,
  });
}

export function useContact(email: string | null) {
  return useQuery({
    queryKey: ["inbox", "contact", email],
    queryFn: async () => {
      if (!email) return null;
      try {
        const c = await api.get<ApiContact>(`/inbox/contacts/${encodeURIComponent(email)}`);
        return apiToContactDetail(c);
      } catch {
        return null;
      }
    },
    enabled: !!email,
    staleTime: 60_000,
  });
}

// ─── Mutations ──────────────────────────────────────────────────────

function useInvalidateConversations() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ["inbox", "conversations"] });
}

export function useAssign() {
  const invalidate = useInvalidateConversations();
  return useMutation({
    mutationFn: ({ id, agentId }: { id: string; agentId: string | null }) =>
      api.post(`/inbox/conversations/${id}/assign`, { agent_id: agentId }),
    onSuccess: invalidate,
  });
}

export function useClassify() {
  const invalidate = useInvalidateConversations();
  return useMutation({
    mutationFn: ({ id, lifecycle }: { id: string; lifecycle: LifecycleStage }) =>
      api.post(`/inbox/conversations/${id}/classify`, { lifecycle }),
    onSuccess: invalidate,
  });
}

export function useToggleBot() {
  const invalidate = useInvalidateConversations();
  return useMutation({
    mutationFn: ({ id, paused }: { id: string; paused: boolean }) =>
      api.post(`/inbox/conversations/${id}/bot`, { paused }),
    onSuccess: invalidate,
  });
}

export function useTakeover() {
  const invalidate = useInvalidateConversations();
  return useMutation({
    mutationFn: ({ id, agentId }: { id: string; agentId: string }) =>
      api.post(`/inbox/conversations/${id}/takeover`, { agent_id: agentId }),
    onSuccess: invalidate,
  });
}

export function useSetStatus() {
  const invalidate = useInvalidateConversations();
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: ConvStatus }) =>
      api.post(`/inbox/conversations/${id}/status`, { status }),
    onSuccess: invalidate,
  });
}

export function useBulkAssign() {
  const invalidate = useInvalidateConversations();
  return useMutation({
    mutationFn: ({ ids, agentId }: { ids: string[]; agentId: string | null }) =>
      api.post(`/inbox/bulk/assign`, { ids, agent_id: agentId }),
    onSuccess: invalidate,
  });
}

export function useBulkResolve() {
  const invalidate = useInvalidateConversations();
  return useMutation({
    mutationFn: ({ ids }: { ids: string[] }) =>
      api.post(`/inbox/bulk/status`, { ids, status: "resolved" }),
    onSuccess: invalidate,
  });
}

export function useCorrectSpelling() {
  return useMutation({
    mutationFn: ({ text }: { text: string }) =>
      api.post<{ corrected: string; changed: boolean }>(`/inbox/llm/correct-spelling`, { text }),
  });
}

export type UploadedAttachment = {
  url: string;
  filename?: string;
  content_type?: string;
  size?: number;
};

export function useSendMessage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ conversationId, text, agentId, agentName, attachments }: {
      conversationId: string;
      text: string;
      agentId?: string;
      agentName?: string;
      attachments?: UploadedAttachment[];
    }) =>
      api.post<{ ok: boolean; delivered: boolean; channel: string }>(
        `/inbox/conversations/${conversationId}/send`,
        {
          text,
          agent_id: agentId,
          agent_name: agentName ?? "Agente",
          attachments: attachments ?? [],
        }
      ),
    onSuccess: (_, vars) => {
      qc.invalidateQueries({ queryKey: ["inbox", "messages", vars.conversationId] });
      qc.invalidateQueries({ queryKey: ["inbox", "conversations"] });
    },
  });
}

/**
 * Sube un archivo a R2 vía /api/inbox/upload. Devuelve la URL pública.
 * Se usa antes de useSendMessage para preparar los attachments.
 */
export async function uploadFile(file: File): Promise<UploadedAttachment> {
  const fd = new FormData();
  fd.append("file", file);

  // Auth: solo session token, NO admin key. Ver lib/api.ts.
  const token = typeof window !== "undefined" ? localStorage.getItem("msk_console_token") : null;
  const headers: Record<string, string> = {};
  if (token) headers["x-session-token"] = token;
  const res = await fetch(`/api/inbox/upload`, {
    method: "POST",
    headers,
    body: fd,
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`upload HTTP ${res.status}: ${txt}`);
  }
  const data = await res.json();
  return {
    url: data.url,
    filename: data.filename,
    content_type: data.content_type,
    size: data.size,
  };
}
