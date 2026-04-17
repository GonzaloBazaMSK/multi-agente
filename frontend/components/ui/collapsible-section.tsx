"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

interface Props {
  title: React.ReactNode;
  defaultOpen?: boolean;
  children: React.ReactNode;
  /** Subtítulo o badge a la derecha del título */
  rightAccessory?: React.ReactNode;
}

export function CollapsibleSection({
  title,
  defaultOpen = false,
  children,
  rightAccessory,
}: Props) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button
        onClick={() => setOpen((s) => !s)}
        className="w-full px-3 py-1.5 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-fg-dim hover:text-fg hover:bg-hover transition-colors"
      >
        {open ? (
          <ChevronDown className="w-3 h-3" />
        ) : (
          <ChevronRight className="w-3 h-3" />
        )}
        <span className="flex-1 text-left font-semibold">{title}</span>
        {rightAccessory}
      </button>
      {open && <div className="pb-1">{children}</div>}
    </div>
  );
}
