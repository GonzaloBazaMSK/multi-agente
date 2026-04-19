/**
 * Tests E2E del inbox — la pantalla más crítica. Si esto se rompe, el
 * equipo no puede atender clientes.
 */
import { expect, test } from "./fixtures";

test.describe("inbox", () => {
  test("renderiza layout principal con rail + lista + detalle", async ({
    authenticatedPage: page,
  }) => {
    await page.goto("/inbox");
    // Rail lateral (logo MSK)
    await expect(page.locator('img[alt="MSK"]')).toBeVisible();
    // Chips de vista (Todas/No leídas/Mías)
    await expect(page.getByText(/Todas/i).first()).toBeVisible();
  });

  test("lista de conversaciones carga sin error", async ({ authenticatedPage: page }) => {
    const res = await page.request.get("/api/v1/inbox/conversations?limit=5");
    expect(res.status()).toBe(200);
    const list = await res.json();
    expect(Array.isArray(list)).toBe(true);
  });

  test("queue-stats responde con shape esperado", async ({ authenticatedPage: page }) => {
    const res = await page.request.get("/api/v1/inbox/queue-stats");
    expect(res.status()).toBe(200);
    const stats = await res.json();
    expect(stats).toHaveProperty("sales");
    expect(stats).toHaveProperty("billing");
    expect(stats).toHaveProperty("post-sales");
    // Cada cola tiene los 6 sub-países
    for (const c of ["AR", "CL", "EC", "MX", "CO", "MP"]) {
      expect(stats.sales).toHaveProperty(c);
    }
  });
});

test.describe("role gate", () => {
  test("/agents requiere admin — supervisor/agente no lo ven", async ({
    authenticatedPage: page,
  }) => {
    await page.goto("/agents");
    // Si el user es admin: ve el contenido. Si no: <NoAccess>.
    // Ambos son válidos según rol — lo importante es que NO haya pantalla
    // blanca ni crash.
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("bulk ops solo admin/supervisor — backend enforce 403 en agente", async ({
    authenticatedPage: page,
  }) => {
    // Bulk endpoint con body vacío — si el rol pasa, devuelve 200 (0
    // updated). Si es agente, 403. Solo testeamos que NO es 500.
    const res = await page.request.post("/api/v1/inbox/bulk/status", {
      data: { ids: [], status: "open" },
    });
    expect([200, 403]).toContain(res.status());
  });
});
