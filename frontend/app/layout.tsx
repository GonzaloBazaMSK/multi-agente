import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { QueryProvider } from "@/lib/query-provider";
import { AuthProvider } from "@/lib/auth";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "MSK Console",
  description: "Operaciones del bot multi-agente MSK Latam",
};

// Script que corre SINCRÓNICO en el head antes del primer paint para evitar
// flash de tema incorrecto. Lee localStorage ("msk-theme": 'light' | 'dark')
// y aplica la clase al <html>. Si no hay nada guardado, queda 'dark' (default).
const themeInitScript = `(function(){try{var t=localStorage.getItem('msk-theme');if(t==='light'){document.documentElement.classList.remove('dark');document.documentElement.classList.add('light');}}catch(e){}})();`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="es" className={`dark ${inter.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>
        <QueryProvider>
          <AuthProvider>{children}</AuthProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
