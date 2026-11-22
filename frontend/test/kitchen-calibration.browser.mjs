import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

test("exports the moved A–D and clears them when the camera frame changes", async () => {
  const browser = await chromium.launch({ channel: process.env.CODEJAM_CHROME_CHANNEL || undefined });
  try {
    const page = await browser.newPage({ viewport: { width: 1100, height: 760 } });
    const frame = await page.evaluate(() => {
      const canvas = document.createElement("canvas");
      canvas.width = 1920;
      canvas.height = 1080;
      const context = canvas.getContext("2d");
      context.fillStyle = "#b78f69";
      context.fillRect(0, 0, canvas.width, canvas.height);
      return canvas.toDataURL("image/png").split(",")[1];
    });
    await page.goto(new URL("../../tools/kitchen-calibration.html", import.meta.url).href);
    const camera = page.locator(".camera");
    const photo = page.locator("#camera-file");
    const saved = page.locator("#setup-file");
    const exported = page.getByRole("button", { name: "Export calibration" });
    const uploadFrame = name => photo.setInputFiles({ name, mimeType: "image/png", buffer: Buffer.from(frame, "base64") });

    await uploadFrame("first.png");
    await page.waitForFunction(() => !document.querySelector("#setup-file").disabled);
    assert.equal(await exported.isEnabled(), false);
    for (const point of [[200, 75], [996, 75], [1010, 600], [220, 600]]) {
      const position = await camera.evaluate((element, [x, y]) => {
        const screen = new DOMPoint(x, y).matrixTransform(element.getScreenCTM());
        return { x: screen.x, y: screen.y };
      }, point);
      await page.mouse.click(position.x, position.y);
    }
    const before = (await page.locator("#outline").getAttribute("points")).split(" ")
      .map(pair => pair.split(",").map(Number));
    await page.locator('[data-corner="A"]').focus();
    await page.keyboard.press("ArrowRight");
    const pendingDownload = page.waitForEvent("download");
    await exported.click();
    const download = await pendingDownload;
    const path = await download.path();
    const calibration = JSON.parse(await readFile(path, "utf8"));
    assert.equal(download.suggestedFilename(), "kitchen-minimap-setup.json");
    assert.deepEqual(calibration.dimensions, [1920, 1080]);
    assert.equal(calibration.camera_id, "kitchen-iphone");
    assert.equal(calibration.source, 0);
    assert.equal(calibration.map_version, "kitchen-v2");
    const [a, ...unchanged] = before;
    const expected = [[Math.round(a[0] + 4), Math.round(a[1])], ...unchanged];
    assert.deepEqual(calibration.points.image, expected.map(([x, y]) => [x / 1200, y / 675]));
    const [mapX, mapY] = await page.locator(".kitchen-map-pin").evaluate(pin => [
      Number.parseFloat(pin.style.left) / 100,
      Number.parseFloat(pin.style.top) / 100,
    ]);
    const python = process.env.CODEJAM_TEST_PYTHON || fileURLToPath(new URL("../../.venv/bin/python", import.meta.url));
    const projection = JSON.parse(execFileSync(python, ["-c", `
import json
import sys
from types import SimpleNamespace
from location import load_location_calibration

size = (1920, 1080)
calibration = load_location_calibration(sys.argv[1], "kitchen-iphone", 0, size)
center = (960, 540)
reading = SimpleNamespace(shoulder_mid=center, hip_mid=center)
print(json.dumps(calibration.map_fields(reading, size)))
`, path], {
      cwd: fileURLToPath(new URL("../../vision/", import.meta.url)),
      encoding: "utf8",
    }));
    assert.ok(Math.abs(mapX - projection.map_x) < 1e-5);
    assert.ok(Math.abs(mapY - projection.map_y) < 1e-5);

    await uploadFrame("recaptured.png");
    await page.waitForFunction(() => !document.querySelector("#setup-file").disabled);
    assert.equal(await exported.isEnabled(), false);
    assert.equal(await page.locator("#corners").getAttribute("visibility"), "hidden");
    await saved.setInputFiles({
      name: "saved.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(calibration)),
    });
    await page.waitForFunction(() => !document.querySelector("#export-corners").disabled);
  } finally {
    await browser.close();
  }
});
