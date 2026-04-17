/**
 * Datos mock para desarrollo del UI mientras se implementan los endpoints REST.
 * Se reemplazan por llamadas reales a la API a medida que estén listos.
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
  | "snoozed"
  | "resolved";

/** Filtro legacy (lo dejo por compat) */
export type AssignmentFilter = "all" | "mine" | "unassigned" | "needs-human";

export type Agent = {
  id: string;
  name: string;
  initials: string;
  color: string;
};

export const ME: Agent = {
  id: "u-gbaza",
  name: "Gonzalo Baza",
  initials: "G",
  color: "from-pink-500 to-fuchsia-600",
};

export const TEAM: Agent[] = [
  ME,
  { id: "u-msoto",  name: "Marina Soto",  initials: "MS", color: "from-blue-500 to-indigo-600" },
  { id: "u-jrios",  name: "Julián Ríos",  initials: "JR", color: "from-emerald-500 to-teal-600" },
  { id: "u-vmari",  name: "Valeria Marí", initials: "VM", color: "from-amber-500 to-orange-600" },
];

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
  /** true si está snoozed hasta cierto momento (UI lo grisa) */
  snoozedUntil: string | null;
  /** true si el bot está pausado en esta conversación */
  botPaused: boolean;
  /** estado de la conversación (abierta/pendiente/resuelta) */
  status: ConvStatus;
  /** lista de tags aplicados a la conversación (no al contacto) */
  tags?: string[];
  /** cola de atención (asignada por el bot router según contenido + país) */
  queue: Queue;
};

export type Message = {
  id: string;
  role: "user" | "bot" | "human" | "system";
  content: string;
  at: string;
  agent?: string;
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

export const MOCK_CONVERSATIONS: ConversationListItem[] = [
  {
    id: "c1",
    contact: { name: "Gonzalo Baza", initials: "G", avatarColor: "from-pink-500 to-fuchsia-600", country: "AR" },
    lastMessage: "Pasame el link de pago AMIR para empezar",
    lastMessageAt: "10:34",
    lifecycle: "hot",
    channel: "whatsapp",
    unread: true,
    assignedTo: null,
    needsHuman: false,
    snoozedUntil: null,
    botPaused: false,
    status: "open",
    tags: ["cardio", "amir-interest"],
    queue: "sales",
  },
  {
    id: "c2",
    contact: { name: "Laura Martínez", initials: "L", avatarColor: "from-blue-500 to-blue-700", country: "MX" },
    lastMessage: "¿El curso AMIR tiene cuotas sin interés?",
    lastMessageAt: "10:21",
    lifecycle: "new",
    channel: "whatsapp",
    unread: true,
    assignedTo: ME.id,
    needsHuman: false,
    snoozedUntil: null,
    botPaused: false,
    status: "open",
    tags: ["residente", "primer-contacto"],
    queue: "sales",
  },
  {
    id: "c3",
    contact: { name: "Martín Suárez", initials: "M", avatarColor: "from-emerald-500 to-emerald-700", country: "CL" },
    lastMessage: "Necesito hablar con un humano por favor",
    lastMessageAt: "09:58",
    lifecycle: "hot",
    channel: "widget",
    unread: true,
    assignedTo: null,
    needsHuman: true,
    snoozedUntil: null,
    botPaused: true,
    status: "open",
    tags: ["urgente", "objeción-precio"],
    queue: "sales",
  },
  {
    id: "c4",
    contact: { name: "Ana Rodríguez", initials: "A", avatarColor: "from-zinc-600 to-zinc-800", country: "CO" },
    lastMessage: "Gracias, lo voy a pensar y vuelvo",
    lastMessageAt: "ayer",
    lifecycle: "cold",
    channel: "whatsapp",
    unread: false,
    assignedTo: "u-msoto",
    needsHuman: false,
    snoozedUntil: "mañana 10:00",
    botPaused: false,
    status: "pending",
    tags: ["follow-up"],
    queue: "billing",
  },
  {
    id: "c5",
    contact: { name: "Federico Núñez", initials: "F", avatarColor: "from-amber-500 to-amber-700", country: "UY" },
    lastMessage: "¡Increíble el curso! Ya estoy haciendo el módulo 3",
    lastMessageAt: "ayer",
    lifecycle: "customer",
    channel: "whatsapp",
    unread: false,
    assignedTo: ME.id,
    needsHuman: false,
    snoozedUntil: null,
    botPaused: false,
    status: "resolved",
    tags: ["customer-active"],
    queue: "post-sales",
  },
];

export const MOCK_MESSAGES: Record<string, Message[]> = {
  c1: [
    {
      id: "m1",
      role: "bot",
      agent: "Sales Agent",
      at: "10:30",
      content:
        "¡Hola Gonzalo! 👋 Veo que mirás **Cardiología AMIR**. Como asistente en cardiología en el Hospital Italiano, este curso te apunta directo al día a día clínico — ECG, eco, manejo de SCA y arritmias. ¿Vemos el temario o cómo es la inscripción?",
    },
    {
      id: "m2",
      role: "user",
      at: "10:32",
      content: "Pasame el link de pago AMIR para empezar",
    },
    {
      id: "m3",
      role: "bot",
      agent: "Sales Agent",
      at: "10:33",
      content:
        "¡Genial Gonzalo! Te dejo el link de pago acá:\nhttps://mp.com/p/AMIR-AR-29948\nCompletando el pago queda confirmada tu inscripción 🎉",
      toolCall: {
        name: "create_payment_link",
        args: 'course="cardiologia-amir", country="AR"',
        duration: "1.2s",
        status: "ok",
      },
    },
  ],
};

export const MOCK_CONTACTS: Record<string, ContactDetail> = {
  c1: {
    id: "c1",
    zohoId: "5344455000160260053",
    name: "Gonzalo Baza",
    email: "gbaza2612@gmail.com",
    phone: "+54 11 2887 8717",
    country: "AR",
    countryName: "Argentina",
    channel: "whatsapp",
    pageContext: "viendo Cardiología AMIR",
    lifecycle: "hot",
    professional: {
      profession: "Personal médico",
      specialty: "Cardiología",
      cargo: "Auxiliar - Asistente",
      workplace: "Hospital Italiano",
      workArea: "Cardiología",
    },
    jurisdictionalCert: { code: "COLEMEMI", name: "Misiones" },
    coursesTaken: [
      "Medicina Intensiva AMIR",
      "Burnout en salud",
      "Ecografía clínica",
      "Nutrición",
      "Estudios de imágenes en atención primaria",
      "Actualización en dengue, Zika y Chikunguña",
    ],
    scoring: { profile: 87, sales: 72 },
    tags: ["cardio", "amir-interest", "italiano-staff"],
    ai: {
      summary:
        "Cliente recurrente (10 cursos previos) que pidió link de pago de Cardiología AMIR. Bot ya generó el link. Esperando pago.",
      nextStep:
        "Esperar pago. Si en 24h no completa, follow-up con cupón BOT20 (20% off) — tiene historial de compra alto, justifica empujar.",
      scoringReasons: [
        "10 cursos comprados previamente (alta intención de compra)",
        "Pidió link de pago explícito (señal fuerte)",
        "Perfil profesional completo (cargo + lugar + colegio)",
        "Aval COLEMEMI activable sin costo extra",
      ],
    },
    cobranzas: {
      status: "overdue",
      currency: "ARS",
      overdueAmount:    262_132.66,
      totalDueAmount:   1_441_729.67,
      contractAmount:   1_572_796.00,
      installmentValue: 131_066.33,
      lastPaymentAmount: 131_066.33,
      totalInstallments: 12,
      paidInstallments: 1,
      overdueInstallments: 2,
      pendingInstallments: 11,
      daysOverdue: 34,
      contractStatus: "Contrato baja",
      paymentMethod: "Tarjeta de crédito",
      nextDue: "20/Abr (vencida hace 3 días)",
      paymentLink: "https://mp.com/p/AMIR-AR-29948",
    },
  },
  c2: {
    id: "c2",
    zohoId: "5344455000160260999",
    name: "Laura Martínez",
    email: "laura.mtz@hosp.mx",
    phone: "+52 55 5511 8800",
    country: "MX",
    countryName: "México",
    channel: "whatsapp",
    lifecycle: "new",
    professional: {
      profession: "Residente",
      specialty: "Cardiología",
      cargo: "Personal de área",
      workplace: "Hospital Ángeles",
    },
    coursesTaken: [],
    scoring: { profile: 65, sales: 48 },
    tags: ["residente", "cardio", "primer-contacto"],
    ai: {
      summary:
        "Lead nuevo, residente de cardiología. Preguntó por cuotas sin interés del Cardiología AMIR.",
      nextStep:
        "Confirmar 12 cuotas sin interés y mencionar que el AMIR le sirve especialmente para consolidar bases de residencia.",
      scoringReasons: [
        "Profesión + especialidad cargados (residente cardio)",
        "Preguntó por precio (señal de intención)",
        "Sin compras previas — primer contacto",
        "Hospital reconocido (Ángeles)",
      ],
    },
    // sin cobranzas: nunca compró
  },
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
