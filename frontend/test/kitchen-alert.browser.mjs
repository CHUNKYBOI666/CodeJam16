import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

test("mapped alerts show a dot without location text and unmapped alerts keep the place", async () => {
  const browser = await chromium.launch({ channel: process.env.CODEJAM_CHROME_CHANNEL || undefined });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 667 }, isMobile: true, hasTouch: true });
    const assets = new Set(["index.html", "styles.css", "js/alert.js", "js/render.js", "js/kitchen-map.js"]);
    await page.route("https://codejam.test/**", async route => {
      const path = new URL(route.request().url()).pathname.slice(1) || "index.html";
      if (path === "js/main.js") {
        await route.fulfill({ contentType: "text/javascript", body: "" });
      } else if (assets.has(path)) {
        await route.fulfill({ path: fileURLToPath(new URL("../" + path, import.meta.url)) });
      } else {
        await route.abort();
      }
    });
    await page.goto("https://codejam.test/");
    const result = await page.evaluate(async () => {
      const { createTakeover } = await import("/js/alert.js");
      const { renderChecks } = await import("/js/render.js");
      const get = key => document.getElementById("takeover-" + key);
      const show = createTakeover({
        dialog: document.getElementById("takeover"), place: get("place"), age: get("age"),
        source: get("source"), map: get("map"), accept: get("accept"), dismiss: get("dismiss"),
      }, () => {}, () => {});
      const plain = { id: "plain", place: "Kitchen", source: "live", status: "needs_responder" };
      const mapped = { ...plain, id: "mapped", map_version: "kitchen-v2", map_x: 0.24, map_y: 0.68 };
      show(mapped, "Just now", true, false);
      const pin = get("map").querySelector(".kitchen-map-pin");
      const takeover = {
        placeHidden: get("place").hidden,
        mapText: get("map").innerText.trim(),
        position: [Number.parseFloat(pin.style.left), Number.parseFloat(pin.style.top)],
        acceptVisible: get("accept").getBoundingClientRect().bottom <= innerHeight,
      };

      show(plain, "Just now", true, false);
      const fallback = !get("place").hidden && get("place").textContent === "Kitchen"
        && !get("map").querySelector(".kitchen-map-pin");
      const rows = document.createElement("section");
      renderChecks(rows, [mapped, plain], { viewerId: "responder", editor: null, online: true, pending: false });
      return { takeover, fallback, rows: [...rows.children].map(row => ({
        pin: !!row.querySelector(".kitchen-map-pin"),
        place: row.querySelector(".place")?.textContent ?? null,
      })) };
    });
    assert.deepEqual(result.takeover, { placeHidden: true, mapText: "", position: [24, 68], acceptVisible: true });
    assert.equal(result.fallback, true);
    assert.deepEqual(result.rows, [{ pin: true, place: null }, { pin: false, place: "Kitchen" }]);
  } finally {
    await browser.close();
  }
});
