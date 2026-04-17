# MSK Console — Frontend

UI de operaciones del bot multi-agente MSK. Consume la API FastAPI bajo
`/api/*` (mismo dominio en prod, proxy via `next.config.mjs` en dev).

## Stack

- **Next.js 15** (App Router) + **React 19** + **TypeScript**
- **Tailwind CSS** (paleta custom MSK, dark mode por default)
- **TanStack Query** para data fetching
- **Lucide** para iconos
- **shadcn-style** primitives (botón, badge, input, avatar) sin runtime

## Desarrollo local

Requiere **Node.js 20+** y **pnpm o npm**.

```bash
cd frontend
npm install
npm run dev
```

Luego abrí <http://localhost:3000>. Por defecto redirige a `/inbox`.

### Variables de entorno

Crear `.env.local` (ignorado por git):

```bash
# A dónde apunta el proxy /api/*  (default: http://localhost:8000)
API_BASE_URL=http://localhost:8000

# Admin key para los endpoints protegidos del API (mismo que en el server)
NEXT_PUBLIC_ADMIN_KEY=change-this-secret
```

## Estructura

```
frontend/
├── app/                       # App Router pages
│   ├── layout.tsx             # Root con QueryProvider
│   ├── page.tsx               # /  → redirect a /inbox
│   └── (app)/                 # Layout con sidebar rail
│       ├── layout.tsx
│       ├── inbox/page.tsx     # Inbox (lista + detalle + contacto)
│       ├── contacts/page.tsx  # placeholder
│       ├── courses/page.tsx
│       ├── agents/page.tsx
│       ├── prompts/page.tsx
│       ├── channels/page.tsx
│       ├── analytics/page.tsx
│       └── settings/page.tsx
├── components/
│   ├── layout/rail.tsx        # Sidebar estrecha estilo respond.io
│   ├── inbox/                 # 3 sub-componentes del Inbox
│   └── ui/                    # primitives (button, input, badge, avatar)
├── lib/
│   ├── api.ts                 # fetch wrapper a /api
│   ├── mock-data.ts           # data mock mientras se conecta la API real
│   ├── utils.ts               # cn(), countryFlag(), initials()
│   └── query-provider.tsx
└── ...
```

## Deploy en producción

Va junto al backend en el server actual (DigitalOcean). Se agrega como
servicio al `docker-compose.yml` y se enruta vía Nginx existente:

- `agentes.msklatam.com/`        → este Next.js (puerto 3000 interno)
- `agentes.msklatam.com/api/*`   → FastAPI existente (puerto 8000 interno)

Ver instrucciones de deploy en el `docker-compose.yml` y el bloque Nginx
correspondiente.
