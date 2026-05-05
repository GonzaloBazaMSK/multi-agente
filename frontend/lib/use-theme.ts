"use client";

import { useEffect, useState } from "react";

export type Theme = "dark" | "light";

const STORAGE_KEY = "msk-theme";

function getInitialTheme(): Theme {
  if (typeof window === "undefined") return "dark";
  return document.documentElement.classList.contains("light") ? "light" : "dark";
}

/**
 * Hook minimal para leer/escribir el theme actual.
 * El theme inicial lo aplica un script inline en layout.tsx (evita flash);
 * este hook solo se sincroniza con la clase actual del <html> y la persiste
 * cuando el user togglea.
 */
export function useTheme() {
  const [theme, setThemeState] = useState<Theme>("dark");

  useEffect(() => {
    setThemeState(getInitialTheme());
  }, []);

  function setTheme(next: Theme) {
    const root = document.documentElement;
    root.classList.remove("dark", "light");
    root.classList.add(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // localStorage puede fallar en modo privado o cuotas — ignorar.
    }
    setThemeState(next);
  }

  function toggleTheme() {
    setTheme(theme === "dark" ? "light" : "dark");
  }

  return { theme, setTheme, toggleTheme };
}
