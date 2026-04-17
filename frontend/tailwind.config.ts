import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg:        "#0a0a0a",
        panel:     "#111111",
        card:      "#171717",
        hover:     "#1f1f1f",
        border:    "#262626",
        "border-2":"#3a3a3a",
        fg:        "#fafafa",
        "fg-muted":"#a1a1aa",
        "fg-dim":  "#71717a",
        accent:    "#a855f7",
        "accent-2":"#9333ea",
        success:   "#10b981",
        warn:      "#f59e0b",
        danger:    "#ef4444",
        info:      "#3b82f6",
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
