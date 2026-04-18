"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";

type User = {
  id: string;
  email: string;
  name: string;
  role: string;
};

type AuthCtx = {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthCtx | null>(null);
const TOKEN_KEY = "msk_console_token";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const pathname = usePathname();

  // Load token from localStorage on mount + validate with /auth/me
  // En dev, si no hay token NO redirige — usa el admin key del env como
  // fallback para que el equipo pueda ver el inbox sin loguearse.
  useEffect(() => {
    const t = typeof window !== "undefined" ? localStorage.getItem(TOKEN_KEY) : null;
    if (!t) {
      setLoading(false);
      // No redirect — el user puede ver el inbox con admin key
      return;
    }
    setToken(t);
    fetch("/auth/me", { headers: { "x-session-token": t } })
      .then(async (r) => {
        if (!r.ok) throw new Error("Token inválido");
        const data = await r.json();
        setUser(data);
      })
      .catch(() => {
        localStorage.removeItem(TOKEN_KEY);
        setToken(null);
        // Tampoco redirect en error — solo cae al fallback
      })
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const login = async (email: string, password: string) => {
    const res = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      throw new Error(`Login falló: ${res.status} ${txt}`);
    }
    const data = await res.json();
    localStorage.setItem(TOKEN_KEY, data.token);
    setToken(data.token);
    setUser(data.user);
    router.replace("/inbox");
  };

  const logout = () => {
    if (token) {
      fetch("/auth/logout", { method: "POST", headers: { "x-session-token": token } }).catch(() => {});
    }
    localStorage.removeItem(TOKEN_KEY);
    setUser(null);
    setToken(null);
    router.replace("/login");
  };

  return (
    <AuthContext.Provider value={{ user, token, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth fuera de AuthProvider");
  return ctx;
}

/**
 * Lee el token actual sincronicamente para usar fuera de React (ej en api.ts).
 */
export function getAuthToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}
