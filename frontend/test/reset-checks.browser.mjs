import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

test("a joined phone can cancel or confirm a room-wide reset", async () => {
  const browser = await chromium.launch({ channel: process.env.CODEJAM_CHROME_CHANNEL || undefined });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 667 }, isMobile: true });
    const main = await readFile(new URL("../js/main.js", import.meta.url), "utf8");
    const fixtures = new Map([
      ["js/main.js", main.replace(/^import "@fontsource[^\n]+\n/gm, "")],
      ["js/qr.js", "export function renderQr() {}"],
      ["js/api.js", `
        export function createApi() {
          const person = { id: "joined", name: "Demo responder" };
          const time = new Date().toISOString();
          let checks = [{
            id: "demo-check", place: "Kitchen", status: "needs_responder", source: "live",
            created_at: time, detected_at: time, revision: 0,
          }];
          window.resetCalls = 0;
          return {
            watchSession() {},
            async session() { return { user: person }; },
            async member() { return person; },
            async invite() { return null; },
            async serverTime() { return time; },
            async snapshot() { return { checks, people: [person] }; },
            async subscribe() { return () => {}; },
            async resetChecks() { window.resetCalls++; checks = []; return 1; },
          };
        }
      `],
    ]);
    const assets = new Set(["index.html", "styles.css", "js/alert.js", "js/render.js", "js/state.js", "js/kitchen-map.js"]);
    await page.route("https://codejam.test/**", async route => {
      const path = new URL(route.request().url()).pathname.slice(1) || "index.html";
      if (fixtures.has(path)) {
        await route.fulfill({ contentType: "text/javascript", body: fixtures.get(path) });
      } else if (assets.has(path)) {
        await route.fulfill({ path: fileURLToPath(new URL("../" + path, import.meta.url)) });
      } else {
        await route.abort();
      }
    });

    await page.goto("https://codejam.test/");
    await page.getByRole("button", { name: "Not now" }).click();
    const reset = page.getByRole("button", { name: "Reset checks" });
    await reset.click();
    const dialog = page.getByRole("dialog", { name: "Reset all checks?" });
    await dialog.getByRole("button", { name: "Cancel" }).click();
    assert.equal(await page.evaluate(() => window.resetCalls), 0);

    await reset.click();
    await dialog.getByRole("button", { name: "Delete all checks" }).click();
    await page.getByText("Checks cleared.", { exact: true }).waitFor();
    await page.locator("#checks").getByText("No open checks.", { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.resetCalls), 1);
    assert.equal(await dialog.isVisible(), false);
  } finally {
    await browser.close();
  }
});
