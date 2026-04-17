import * as React from "react";
import { cn } from "@/lib/utils";

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type = "text", ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(
        "w-full bg-bg border border-border rounded-md px-3 py-1.5 text-sm placeholder-fg-dim focus:outline-none focus:border-accent",
        className
      )}
      {...props}
    />
  )
);
Input.displayName = "Input";
