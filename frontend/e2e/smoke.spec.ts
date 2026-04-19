/**
 * Smoke tests E2E — lo que NO puede romperse entre deploys.
 *
 * Corre contra prod (o el BASE_URL que pases). Cada test es
 * independiente y rápido. Si alguno falla el deploy está roto.
 */
import { expect, test } from "./fixtures";

test.describe("smoke", () => {
  test("health endpoints responden", async ({ page }) => {
    const health = await page.request.get("/health");
    expect(health.status()).toBe(200);
    expect(await health.json()).toMatchObject({ status: "ok" });

    const ready = await page.request.get("/api/v1/health/ready");
    expect(ready.status()).toBe(200);
    expect(await ready.json()).toMatchObject({ status: "ready" });
  });

  test("widget.js se sirve y apunta a /api/v1", async ({ page }) => {
    const res = await page.request.get("/widget.js");
    expect(res.status()).toBe(200);
    const body = await res.text();
    expect(body).toContain("/api/v1/admin/widget-config/public");
  });

  test("widget-config/public está accesible sin auth", async ({ page }) => {
    const res = await page.request.get("/api/v1/admin/widget-config/public");
    expect(res.status()).toBe(200);
    const cfg = await res.json();
    expect(cfg).toHaveProperty("title");
    expect(cfg).toHaveProperty("color");
  });

  test("/inbox sin auth redirige a /login", async ({ page }) => {
    await page.goto("/inbox");
    await page.waitForURL(/\/login/, { timeout: 10_000 });
    expect(page.url()).toContain("/login");
  });

  test("/login renderiza el form", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByLabel(/email/i)).toBeVisible();
    await expect(page.getByLabel(/contraseña|password/i)).toBeVisible();
  });

  test("/api/auth/* sin v1 devuelve 404 (breaking change aplicado)", async ({ page }) => {
    const res = await page.request.get("/api/auth/me");
    expect(res.status()).toBe(404);
  });

  test("openapi schema disponible en /api/v1/openapi.json", async ({ page }) => {
    const res = await page.request.get("/api/v1/openapi.json");
    expect(res.status()).toBe(200);
    const schema = await res.json();
    expect(schema.info?.title).toBe("MSK Multi-Agente");
    // Debe incluir rutas /api/v1/
    const paths = Object.keys(schema.paths || {});
    expect(paths.some((p) => p.startsWith("/api/v1/inbox"))).toBe(true);
  });

  test("security headers en /inbox", async ({ page }) => {
    const res = await page.request.get("/inbox");
    const headers = res.headers();
    expect(headers["content-security-policy"]).toBeTruthy();
    expect(headers["x-content-type-options"]).toBe("nosniff");
    expect(headers["x-frame-options"]).toBe("SAMEORIGIN");
  });
});
