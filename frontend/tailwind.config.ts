import type { Config } from "tailwindcss";

// Los neutrales (bg/panel/card/hover/border/fg) usan CSS variables RGB para
// soportar light/dark theme + opacity modifiers (bg-fg-muted/40, bg-accent/18).
// Los brand colors (accent, success, warn, danger, info) son hex directos —
// no cambian entre temas.
const rgb = (varName: string) => `rgb(var(${varName}) / <alpha-value>)`;

const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg:         rgb("--c-bg"),
        panel:      rgb("--c-panel"),
        card:       rgb("--c-card"),
        hover:      rgb("--c-hover"),
        border:     rgb("--c-border"),
        "border-2": rgb("--c-border-2"),
        fg:         rgb("--c-fg"),
        "fg-muted": rgb("--c-fg-muted"),
        "fg-dim":   rgb("--c-fg-dim"),
        accent:     "#a855f7",
        "accent-2": "#9333ea",
        success:    "#10b981",
        warn:       "#f59e0b",
        danger:     "#ef4444",
        info:       "#3b82f6",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
