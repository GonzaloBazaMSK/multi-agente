"use client";

import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

interface DropdownProps {
  trigger: React.ReactNode;
  children: (close: () => void) => React.ReactNode;
  align?: "left" | "right";
  /** Dirección vertical: "down" (default) abajo del trigger, "up" arriba. */
  side?: "down" | "up";
  className?: string;
}

/**
 * Dropdown simple sin libs externas. Cierra al click afuera o ESC.
 * Render prop expone `close()` para que los items lo invoquen.
 */
export function Dropdown({
  trigger,
  children,
  align = "right",
  side = "down",
  className,
}: DropdownProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const close = () => setOpen(false);

  const positionClass =
    side === "up"
      ? "bottom-full mb-1"
      : "top-full mt-1";

  return (
    <div ref={ref} className="relative inline-block">
      <div onClick={() => setOpen((s) => !s)}>{trigger}</div>
      {open && (
        <div
          className={cn(
            "absolute z-30 min-w-[200px] bg-card border border-border rounded-md shadow-xl py-1",
            positionClass,
            align === "right" ? "right-0" : "left-0",
            className
          )}
        >
          {children(close)}
        </div>
      )}
    </div>
  );
}

export function DropdownItem({
  children,
  onClick,
  variant = "default",
  disabled = false,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  variant?: "default" | "danger";
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "w-full text-left px-3 py-1.5 text-xs hover:bg-hover transition-colors flex items-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed",
        variant === "danger" ? "text-danger hover:bg-danger/10" : "text-fg"
      )}
    >
      {children}
    </button>
  );
}

export function DropdownLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-wider text-fg-dim">
      {children}
    </div>
  );
}

export function DropdownSeparator() {
  return <div className="my-1 h-px bg-border" />;
}
