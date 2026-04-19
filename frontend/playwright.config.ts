import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E contra el ambiente que elijas.
 *
 * Por default testea contra prod (agentes.msklatam.com) — asume que los
 * commits a main pasan por CI antes. Para dev local, setear BASE_URL.
 *
 * Corré:
 *   npx playwright test              # todos los tests
 *   npx playwright test --ui         # modo UI interactivo
 *   npx playwright test --headed     # ver el browser
 *   npx playwright test -g "login"   # solo los que matchean
 *
 * Requiere credenciales de un user admin válido — las pasamos por env
 * para no commitearlas:
 *   E2E_ADMIN_EMAIL=...
 *   E2E_ADMIN_PASSWORD=...
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [["github"], ["html"]] : "html",

  use: {
    baseURL: process.env.BASE_URL || "https://agentes.msklatam.com",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    // Firefox + WebKit se pueden agregar si el riesgo de cross-browser
    // empieza a importar. Hoy sobra Chromium (mismo engine que ~90% de
    // usuarios del panel admin).
  ],
});
