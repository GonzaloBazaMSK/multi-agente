"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Inbox,
  BookOpen,
  Bot,
  FileCode,
  Plug,
  BarChart3,
  Settings,
  Bell,
} from "lucide-react";
import { cn } from "@/lib/utils";

type RailLink = {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  badge?: number;
};

const NAV: RailLink[] = [
  { href: "/inbox",     label: "Inbox",                icon: Inbox,    badge: 12 },
  { href: "/courses",   label: "Catálogo de cursos",   icon: BookOpen },
  { href: "/agents",    label: "Agentes IA",           icon: Bot },
  { href: "/prompts",   label: "Editor de prompts",    icon: FileCode },
  { href: "/channels",  label: "Canales",              icon: Plug },
  { href: "/analytics", label: "Analytics",            icon: BarChart3 },
];

export function Rail() {
  const pathname = usePathname();

  return (
    <aside className="w-14 bg-panel border-r border-border flex flex-col items-center py-2 shrink-0">
      {/* Logo MSK */}
      <div className="w-9 h-9 flex items-center justify-center mb-3">
        <img src="/logo.png" alt="MSK" className="w-9 h-9 object-contain" />
      </div>

      <div className="w-8 h-px bg-border mb-3" />

      {/* Nav */}
      <nav className="flex flex-col items-center gap-1 flex-1">
        {NAV.map((item) => {
          const active = pathname.startsWith(item.href);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "rail-tooltip-wrap relative w-9 h-9 rounded-md flex items-center justify-center transition-colors",
                active ? "bg-accent/18 text-fg" : "text-fg-muted hover:bg-hover hover:text-fg"
              )}
              data-tooltip={item.label}
            >
              <Icon className="w-[18px] h-[18px]" />
              {item.badge !== undefined && (
                <span className="absolute -top-0.5 -right-0.5 w-4 h-4 rounded-full bg-accent text-white text-[9px] flex items-center justify-center font-bold">
                  {item.badge}
                </span>
              )}
              {active && (
                <span className="absolute left-[-8px] top-1/2 -translate-y-1/2 w-[3px] h-[22px] bg-accent rounded-r" />
              )}
            </Link>
          );
        })}
      </nav>

      {/* Bottom */}
      <div className="flex flex-col items-center gap-1 mt-auto">
        <Link
          href="/settings"
          className={cn(
            "rail-tooltip-wrap relative w-9 h-9 rounded-md flex items-center justify-center",
            pathname.startsWith("/settings")
              ? "bg-accent/18 text-fg"
              : "text-fg-muted hover:bg-hover hover:text-fg"
          )}
          data-tooltip="Settings"
        >
          <Settings className="w-[18px] h-[18px]" />
        </Link>
        <button
          className="rail-tooltip-wrap relative w-9 h-9 rounded-md flex items-center justify-center text-fg-muted hover:bg-hover hover:text-fg"
          data-tooltip="Notificaciones"
        >
          <Bell className="w-[18px] h-[18px]" />
          <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-danger ring-2 ring-panel" />
        </button>
        <div
          className="rail-tooltip-wrap w-9 h-9 rounded-full bg-gradient-to-br from-pink-500 to-fuchsia-600 text-white text-xs font-bold flex items-center justify-center cursor-pointer ring-2 ring-success/40"
          data-tooltip="Gonzalo Baza · gonzalobaza@msklatam.com"
        >
          G
        </div>
      </div>

      <style jsx global>{`
        .rail-tooltip-wrap::after {
          content: attr(data-tooltip);
          position: absolute;
          left: calc(100% + 10px);
          top: 50%;
          transform: translateY(-50%);
          background: #1f1f1f;
          color: #fafafa;
          padding: 4px 8px;
          border-radius: 6px;
          font-size: 11px;
          white-space: nowrap;
          opacity: 0;
          pointer-events: none;
          transition: opacity 0.15s;
          z-index: 50;
          border: 1px solid #3a3a3a;
        }
        .rail-tooltip-wrap:hover::after {
          opacity: 1;
        }
      `}</style>
    </aside>
  );
}
