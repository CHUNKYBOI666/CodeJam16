import assert from "node:assert/strict";
import test from "node:test";
import { kitchenLocation } from "../js/kitchen-map.js";

test("keeps the pin at its normalized position across the floor", () => {
  const check = { map_version: "kitchen-v2", map_x: 0.1, map_y: 0.25 };
  assert.deepEqual(kitchenLocation(check), { x: 0.1, y: 0.25 });
  assert.deepEqual(kitchenLocation({ ...check, map_x: 0.9 }), { x: 0.9, y: 0.25 });
  assert.deepEqual(kitchenLocation({ ...check, map_x: 0.5 }), { x: 0.5, y: 0.25 });
});

test("hides missing, previous-layout and invalid locations", () => {
  const check = { map_version: "kitchen-v2", map_x: 0.25, map_y: 0.1 };
  for (const fields of [{}, { ...check, map_version: "kitchen-v1" }, { ...check, map_x: null },
    { ...check, map_y: 2 }]) {
    assert.equal(kitchenLocation(fields), null);
  }
});
