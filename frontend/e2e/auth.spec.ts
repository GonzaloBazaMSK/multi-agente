/**
 * Tests E2E del flow de auth.
 *
 * Requiere E2E_ADMIN_EMAIL + E2E_ADMIN_PASSWORD en el env.
 * Si no están, se skippean automáticamente vía fixture.
 */
import { expect, test } from "./fixtures";

test.describe("auth flow", () => {
  test("login con cookie httpOnly lleva al inbox", async ({ authenticatedPage: page }) => {
    // authenticatedPage ya hizo login — verificamos estado post-login
    expect(page.url()).toContain("/inbox");

    // La cookie msk_session tiene que estar seteada, httpOnly
    const cookies = await page.context().cookies();
    const session = cookies.find((c) => c.name === "msk_session");
    expect(session).toBeDefined();
    expect(session?.httpOnly).toBe(true);
  });

  test("/api/v1/auth/me con sesión devuelve el user", async ({ authenticatedPage: page }) => {
    const res = await page.request.get("/api/v1/auth/me");
    expect(res.status()).toBe(200);
    const user = await res.json();
    expect(user).toHaveProperty("email");
    expect(user).toHaveProperty("role");
    expect(["admin", "supervisor", "agente"]).toContain(user.role);
  });

  test("logout borra la sesión", async ({ authenticatedPage: page }) => {
    await page.request.post("/api/v1/auth/logout");
    // Después del logout, /me debe fallar
    const res = await page.request.get("/api/v1/auth/me");
    expect(res.status()).toBe(401);
  });
});
