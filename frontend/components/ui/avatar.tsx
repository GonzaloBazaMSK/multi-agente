import { cn } from "@/lib/utils";
import { Flag } from "./flag";

interface AvatarProps {
  initials: string;
  /** Tailwind classes para el gradient. Ej: "from-pink-500 to-fuchsia-600" */
  gradient?: string;
  /** ISO-2 del país. Si está, muestra una mini-bandera abajo a la derecha. */
  country?: string;
  size?: "sm" | "md" | "lg";
  className?: string;
}

const SIZES = {
  sm: { box: "w-8 h-8 text-xs",  flagSize: 11 },
  md: { box: "w-9 h-9 text-sm",  flagSize: 12 },
  lg: { box: "w-12 h-12 text-lg", flagSize: 14 },
};

export function Avatar({
  initials,
  gradient = "from-pink-500 to-fuchsia-600",
  country,
  size = "md",
  className,
}: AvatarProps) {
  const sz = SIZES[size];
  return (
    <div className={cn("relative shrink-0", className)}>
      <div
        className={cn(
          "rounded-full bg-gradient-to-br text-white font-bold flex items-center justify-center",
          gradient,
          sz.box
        )}
      >
        {initials}
      </div>
      {country && (
        <Flag
          iso={country}
          size={sz.flagSize}
          className="absolute -bottom-0.5 -right-0.5 ring-2 ring-bg shadow"
        />
      )}
    </div>
  );
}
