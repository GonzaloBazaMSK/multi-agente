import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium",
  {
    variants: {
      variant: {
        new:      "bg-info/15 text-info",
        hot:      "bg-warn/15 text-warn",
        customer: "bg-success/15 text-success",
        cold:     "bg-zinc-700/40 text-fg-muted",
        whatsapp: "bg-success/15 text-success",
        widget:   "bg-accent/15 text-accent",
        muted:    "bg-hover text-fg-muted",
        accent:   "bg-accent/15 text-accent",
        success:  "bg-success/15 text-success",
        warn:     "bg-warn/15 text-warn",
        danger:   "bg-danger/15 text-danger",
        info:     "bg-info/15 text-info",
      },
    },
    defaultVariants: { variant: "muted" },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant, className }))} {...props} />;
}
