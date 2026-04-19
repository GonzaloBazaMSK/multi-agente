/**
 * Fixtures compartidos entre tests E2E.
 *
 * `authenticatedPage` hace login con las creds del env y devuelve la
 * page ya dentro de la consola. Evita repetir el flow de login en
 * cada test.
 */
import { test as base, expect, type Page } from "@playwright/test";

const ADMIN_EMAIL = process.env.E2E_ADMIN_EMAIL || "";
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || "";

async function loginAs(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel(/email/i).fill(email);
  await page.getByLabel(/contraseña|password/i).fill(password);
  await page.getByRole("button", { name: /ingresar|iniciar|login/i }).click();
  // Espera redirect al inbox (login exitoso)
  await page.waitForURL(/\/inbox/, { timeout: 10_000 });
}

type Fixtures = {
  authenticatedPage: Page;
};

export const test = base.extend<Fixtures>({
  authenticatedPage: async ({ page }, use) => {
    if (!ADMIN_EMAIL || !ADMIN_PASSWORD) {
      test.skip(true, "E2E_ADMIN_EMAIL/PASSWORD no están seteadas");
    }
    await loginAs(page, ADMIN_EMAIL, ADMIN_PASSWORD);
    await use(page);
  },
});

export { expect };
