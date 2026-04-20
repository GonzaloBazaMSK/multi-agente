/**
 * Tipos compartidos del dominio inbox.
 *
 * NOTA HISTÓRICA: este archivo originalmente contenía datos mock (ME, TEAM,
 * MOCK_CONVERSATIONS, MOCK_MESSAGES, MOCK_CONTACTS) usados durante el desarrollo
 * inicial del UI. Esos mocks fueron eliminados cuando se conectó todo a la API
 * real (ver migration 004 + /auth/users + /inbox/* endpoints).
 *
 * Conservamos:
 *  - Tipos del dominio (Agent, Message, ConversationListItem, ContactDetail, etc).
 *  - Maps de UI puramente presentacionales (QUEUE_LABEL, QUEUE_COLOR, COUNTRY_NAMES).
 *
 * Si necesitás un agente o lista de equipo en runtime, usá `useAgents()` de
 * `@/lib/api/inbox` o `useAuth()` de `@/lib/auth`.
 */

export type LifecycleStage = "new" | "hot" | "customer" | "cold";
export type Channel = "whatsapp" | "widget";
export type ConvStatus = "open" | "pending" | "resolved";

/**
 * Cola de atención asignada por el bot router según TEMA + país.
 * El "humano" NO es una cola — es un flag ortogonal (needs_human/bot_paused).
 * Una conversación de ventas con humano interviniendo sigue siendo "sales".
 */
export type Queue = "sales" | "billing" | "post-sales";

export const QUEUE_LABEL: Record<Queue, string> = {
  sales:       "Ventas",
  billing:     "Cobranzas",
  "post-sales": "Post-venta",
};

export const QUEUE_COLOR: Record<Queue, string> = {
  sales:       "bg-accent/15 text-accent",
  billing:     "bg-warn/15 text-warn",
  "post-sales": "bg-info/15 text-info",
};

/** Vistas principales de la inbox (igual que respond.io) */
export type InboxView =
  | "all"
  | "unread"
  | "mine"
  | "queue"          // esperando agente humano
  | "human-attn"    // en atención humana (bot pausado)
  | "with-bot"      // con bot activo
  | "resolved";

/** Filtro legacy (lo dejo por compat) */
export type AssignmentFilter = "all" | "mine" | "unassigned" | "needs-human";

export type Agent = {
  id: string;
  name: string;
  initials: string;
  color: string;
};

export type ConversationListItem = {
  id: string;
  contact: {
    name: string;
    initials: string;
    avatarColor: string;
    country: string;
    email?: string;
    phone?: string;
  };
  lastMessage: string;
  lastMessageAt: string;
  lifecycle: LifecycleStage;
  channel: Channel;
  unread: boolean;
  /** id del agente humano asignado, o null si nadie */
  assignedTo: string | null;
  /** true si requiere atención humana (escaló del bot) */
  needsHuman: boolean;
  /** true si el bot está pausado en esta conversación */
  botPaused: boolean;
  /** estado de la conversación (abierta/pendiente/resuelta) */
  status: ConvStatus;
  /** lista de tags aplicados a la conversación (no al contacto) */
  tags?: string[];
  /** cola de atención (asignada por el bot router según contenido + país) */
  queue: Queue;
};

export type MessageAttachment = {
  url: string;
  filename?: string;
  content_type?: string;
  size?: number;
};

export type Message = {
  id: string;
  role: "user" | "bot" | "human" | "system" | "assistant";
  content: string;
  at: string;
  agent?: string;
  attachments?: MessageAttachment[];
  toolCall?: {
    name: string;
    args: string;
    duration?: string;
    status?: "ok" | "error";
  };
};

export type AIInsight = {
  /** Próximo paso sugerido por el agente */
  nextStep: string;
  /** Razones detrás del scoring (3-5 bullets) */
  scoringReasons: string[];
  /** Resumen breve de la conversación */
  summary: string;
};

export type DebtStatus = "ok" | "due_soon" | "overdue" | "suspended";

export type CobranzasInfo = {
  status: DebtStatus;
  currency: string;

  // Resumen financiero
  overdueAmount: number;       // Saldo vencido
  totalDueAmount: number;      // Saldo total adeudado
  contractAmount: number;      // Importe del contrato
  installmentValue: number;    // Valor de cada cuota
  lastPaymentAmount: number;   // Último pago realizado

  // Cuotas
  totalInstallments: number;   // Total de cuotas del contrato (ej: 12)
  paidInstallments: number;    // Cuotas pagas
  overdueInstallments: number; // Cuotas vencidas (mora)
  pendingInstallments: number; // Cuotas pendientes (futuras)

  // Detalle
  daysOverdue: number;         // Días de atraso de la cuota más antigua vencida
  contractStatus: string;      // "Activo", "Contrato baja", "Suspendido", "Cancelado"
  paymentMethod: string;       // "Tarjeta de crédito", "Débito automático", "Transferencia", etc

  // Próximo vencimiento (humano-legible)
  nextDue?: string;
  paymentLink?: string;

  // ID del registro en el módulo Area_de_cobranzas (CustomModule20) del
  // CRM. Se usa para armar el link directo "Ver en Zoho" al detalle del
  // registro en crm.zoho.com. Opcional — si el backend todavía no lo
  // trae, el link cae a la lista del módulo.
  cobranzaZohoId?: string;
};

export type ContactDetail = {
  id: string;
  zohoId?: string;
  name: string;
  email: string;
  phone: string;
  country: string;
  countryName: string;
  channel: Channel;
  pageContext?: string;
  lifecycle: LifecycleStage;
  professional: {
    profession?: string;
    specialty?: string;
    cargo?: string;
    workplace?: string;
    workArea?: string;
  };
  jurisdictionalCert?: { code: string; name: string };
  coursesTaken: string[];
  scoring: { profile: number; sales: number };
  tags: string[];
  ai: AIInsight;
  cobranzas?: CobranzasInfo;
};

export const COUNTRY_NAMES: Record<string, string> = {
  AR: "Argentina",
  MX: "México",
  CL: "Chile",
  CO: "Colombia",
  PE: "Perú",
  UY: "Uruguay",
  EC: "Ecuador",
  ES: "España",
  BO: "Bolivia",
  PY: "Paraguay",
  VE: "Venezuela",
  CR: "Costa Rica",
  GT: "Guatemala",
  HN: "Honduras",
  NI: "Nicaragua",
  PA: "Panamá",
  SV: "El Salvador",
};
