import assert from "node:assert/strict";
import test from "node:test";
import { cameraLabel, createStore } from "../js/state.js";

const waiting = { id: "1", status: "needs_responder" };
const accepted = { ...waiting, status: "accepted" };

test("an in-flight snapshot cannot resurrect a claim after a Realtime change", () => {
  const store = createStore();
  store.applySnapshot(store.beginSnapshot(), [waiting]);
  const pending = store.beginSnapshot();
  store.applyChange(accepted);
  assert.equal(store.applySnapshot(pending, [waiting]), false);
  assert.deepEqual(store.view().inProgress, [accepted]);
  assert.equal(store.view().alertCheck, null);
});

test("older responses are ignored and a recovery snapshot can raise an alert", () => {
  const store = createStore();
  const older = store.beginSnapshot();
  store.applySnapshot(store.beginSnapshot(), [waiting]);
  assert.equal(store.applySnapshot(older, []), false);
  assert.equal(store.view().alertCheck, waiting);
});

test("dismissal preserves the waiting row and a new episode can still alert", () => {
  const store = createStore();
  store.applyChange(waiting);
  store.dismiss(waiting.id);
  store.applySnapshot(store.beginSnapshot(), [waiting]);
  assert.deepEqual(store.view().waiting, [waiting]);
  assert.equal(store.view().alertCheck, null);
  const next = { ...waiting, id: "2" };
  store.applyChange(next);
  assert.equal(store.view().alertCheck, next);
});

test("feed expiry and replay labeling", () => {
  const feed = { status: "replay", fps: 12, updated_at: new Date(0).toISOString() };
  assert.equal(cameraLabel(feed, 40_000), "Rehearsal clip · 12 fps");
  assert.equal(cameraLabel(feed, 40_001), "Camera offline");
});

test("a delayed Realtime insert cannot reopen a check already claimed in a snapshot", () => {
  const store = createStore();
  store.applySnapshot(store.beginSnapshot(), [{ ...accepted, revision: 1 }]);
  store.applyChange({ ...waiting, revision: 0 });
  assert.equal(store.view().alertCheck, null);
});

test("a delayed rename cannot overwrite a newer owner label", () => {
  const store = createStore();
  store.applyChange({ ...accepted, owner_name: "Newest", revision: 4 });
  store.applyChange({ ...accepted, owner_name: "Older", revision: 3 });
  assert.equal(store.view().inProgress[0].owner_name, "Newest");
});
