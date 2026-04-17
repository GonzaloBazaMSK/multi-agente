/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // Desactiva el indicador flotante de Next en dev (rayito abajo izq que
  // se pisaba con el avatar del usuario).
  devIndicators: {
    appIsrStatus: false,
    buildActivity: false,
  },

  // Reverse proxy local: en dev, las rutas /api/* las redirige al FastAPI.
  // En prod (mismo dominio agentes.msklatam.com), Nginx hace el routing.
  // OJO: preservar el prefix /api/ porque el backend tiene endpoints /api/inbox/*
  async rewrites() {
    const apiBase = process.env.API_BASE_URL || "http://localhost:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${apiBase}/api/:path*`,
      },
    ];
  },

  // Permitir cargar SVGs de banderas desde flagcdn.com
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "flagcdn.com" },
    ],
  },

  // Output standalone para que el Dockerfile saque solo lo necesario
  output: "standalone",
};

export default nextConfig;
