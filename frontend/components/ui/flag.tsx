import { cn } from "@/lib/utils";

/**
 * Bandera de país por código ISO-2.
 * Usa flagcdn.com (SVG, gratis, sin API key) en lugar de emoji porque Windows
 * no renderiza emojis de banderas regionales por política de Microsoft.
 */
interface FlagProps {
  /** ISO-2 (ej: "AR", "MX"). Case-insensitive. */
  iso: string;
  /** Tamaño en px (alto). El ancho se calcula proporcional. */
  size?: number;
  className?: string;
  title?: string;
}

export function Flag({ iso, size = 14, className, title }: FlagProps) {
  if (!iso || iso.length !== 2) return null;
  const code = iso.toLowerCase();
  return (
    <img
      src={`https://flagcdn.com/${code}.svg`}
      alt={iso.toUpperCase()}
      title={title ?? iso.toUpperCase()}
      width={Math.round(size * 1.5)}
      height={size}
      className={cn("inline-block rounded-[2px] object-cover", className)}
      style={{ height: `${size}px`, width: `${Math.round(size * 1.5)}px` }}
    />
  );
}
