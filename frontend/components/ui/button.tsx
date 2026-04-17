import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent disabled:opacity-50 disabled:pointer-events-none",
  {
    variants: {
      variant: {
        default: "bg-accent text-white hover:bg-accent-2",
        secondary: "bg-hover text-fg-muted hover:bg-border hover:text-fg",
        ghost: "text-fg-muted hover:bg-hover hover:text-fg",
        danger: "bg-danger/15 text-danger hover:bg-danger/25",
        warn: "bg-warn/15 text-warn hover:bg-warn/25",
        outline: "border border-border bg-transparent text-fg hover:bg-hover",
      },
      size: {
        default: "h-8 px-3 text-xs",
        sm: "h-7 px-2 text-xs",
        lg: "h-9 px-4 text-sm",
        icon: "h-8 w-8",
        "icon-sm": "h-7 w-7",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(buttonVariants({ variant, size, className }))} {...props} />
  )
);
Button.displayName = "Button";

export { buttonVariants };
