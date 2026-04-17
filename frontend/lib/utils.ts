import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** ISO-2 a emoji bandera (Unicode regional indicators). */
export function countryFlag(iso2: string): string {
  if (!iso2 || iso2.length !== 2) return "";
  const A = 0x1f1e6;
  const codePoints = iso2
    .toUpperCase()
    .split("")
    .map((c) => A + c.charCodeAt(0) - 65);
  return String.fromCodePoint(...codePoints);
}

/** Iniciales para avatares. "Gonzalo Baza" -> "GB" */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
