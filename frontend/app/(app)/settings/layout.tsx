"use client";

/**
 * Layout de `/settings/*` — agrega sub-navegación lateral entre secciones.
 *
 * Secciones:
 *   - Agentes humanos  → /settings/agents   (CRUD del equipo)
 *   - Colas de atención → /settings/queues   (ver + asignar agentes)
 *   - Workspace        → /settings/workspace (info backend, integraciones)
 *   - Auditoría        → /settings/audit    (log de acciones humanas)
 *
 * Inspirado en el patrón de Botmaker que vimos en las capturas: nav lateral
 * con títulos + descripciones cortas debajo. Cada sub-página es su propio
 * `page.tsx` en `/settings/<seccion>/`.
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Users, ListTree, Building2, History } from "lucide-react";
import { cn } from "@/lib/utils";
import { useRole } from "@/lib/auth";

type Section = {
  href: string;
  label: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
  adminOnly?: boolean;
};

const SECTIONS: Section[] = [
  {
    href: "/settings/agents",
    label: "Agentes",
    description: "Personas del equipo con acceso al sistema",
    icon: Users,
  },
  {
    href: "/settings/queues",
    label: "Colas de atención",
    description: "Quién atiende qué cola (ventas/cobranzas/post-venta × país)",
    icon: ListTree,
  },
  {
    href: "/settings/audit",
    label: "Historial de cambios",
    description: "Audita acciones humanas del inbox",
    icon: History,
    adminOnly: true,
  },
  {
    href: "/settings/workspace",
    label: "Workspace",
    description: "Info del backend e integraciones",
    icon: Building2,
    adminOnly: true,
  },
];

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { isAdmin } = useRole();
  const visible = SECTIONS.filter((s) => !s.adminOnly || isAdmin);

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* Sub-nav lateral */}
      <aside className="w-64 bg-panel border-r border-border flex flex-col shrink-0">
        <div className="px-4 py-4 border-b border-border">
          <h1 className="text-sm font-semibold">Configuración</h1>
          <p className="text-[11px] text-fg-dim mt-0.5">Equipo, colas y workspace</p>
        </div>
        <nav className="flex-1 overflow-y-auto scroll-thin py-2">
          {visible.map((s) => {
            const active = pathname === s.href || pathname.startsWith(s.href + "/");
            const Icon = s.icon;
            return (
              <Link
                key={s.href}
                href={s.href}
                className={cn(
                  "flex items-start gap-3 px-4 py-2.5 text-xs transition-colors border-l-2",
                  active
                    ? "bg-accent/10 border-l-accent text-fg"
                    : "border-l-transparent text-fg-muted hover:bg-hover hover:text-fg",
                )}
              >
                <Icon className="w-4 h-4 shrink-0 mt-0.5" />
                <div className="min-w-0">
                  <div className="text-sm font-medium">{s.label}</div>
                  <div className="text-[11px] text-fg-dim leading-snug mt-0.5">
                    {s.description}
                  </div>
                </div>
              </Link>
            );
          })}
        </nav>
      </aside>

      {/* Contenido de la sub-página */}
      <div className="flex-1 flex flex-col overflow-hidden">{children}</div>
    </div>
  );
}
