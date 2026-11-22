import "@fontsource/dm-sans/latin-400.css";
import "@fontsource/dm-sans/latin-500.css";
import "@fontsource/dm-sans/latin-700.css";
import { createApi } from "./api.js";
import { createTakeover, unlockAudio } from "./alert.js";
import { ago, renderChecks, renderPeople } from "./render.js";
import { renderQr } from "./qr.js";
import { cameraLabel, createStore } from "./state.js";

const get = (id) => document.getElementById(id);
const appName = document.title;
const join = get("join");
const form = get("join-form");
const nameInput = get("name");
const joinButton = get("join-button");
const joinFeedback = get("join-feedback");
const actionFeedback = get("action-feedback");
const resetButton = get("reset-checks");
const resetDialog = get("reset-dialog");
const resetForm = get("reset-form");
const resetCancel = get("reset-cancel");
const resetConfirm = get("reset-confirm");
const resetError = get("reset-error");
const roomUrl = new URL(location.href);
const joinCode = roomUrl.searchParams.get("room");
if (roomUrl.searchParams.has("room")) {
  roomUrl.searchParams.delete("room");
  history.replaceState(null, "", roomUrl);
}

const store = createStore();
let api;
let viewer;
let feed;
let members = new Map();
let present = [];
let online = false;
let pending = false;
let renaming = false;
let editor = null;
let stopSubscription;
let resetSync;
let activeInvite;
let clockOffsetMs;
let lastCheckView;
let lastLatencyMs;
const shownChecks = new Set();

function now() {
  return Date.now() + (clockOffsetMs ?? 0);
}

const takeover = createTakeover({
  dialog: get("takeover"),
  place: get("takeover-place"),
  age: get("takeover-age"),
  source: get("takeover-source"),
  map: get("takeover-map"),
  accept: get("takeover-accept"),
  dismiss: get("takeover-dismiss"),
}, (id) => void sendAction(id, "accept"), (id) => {
  store.dismiss(id);
  try { sessionStorage.setItem(`welfare-dismissed:${id}`, "yes"); } catch { /* Optional storage. */ }
  render();
});

function alertCandidate(view) {
  let candidate = view.alertCheck;
  while (candidate) {
    let dismissed = false;
    try { dismissed = sessionStorage.getItem(`welfare-dismissed:${candidate.id}`) === "yes"; }
    catch { /* The in-memory dismissal still works. */ }
    if (!dismissed) return candidate;
    store.dismiss(candidate.id);
    candidate = store.view().alertCheck;
  }
  return null;
}

function render() {
  join.hidden = Boolean(viewer) && !renaming;
  get("room").hidden = !viewer || renaming;
  get("identity").hidden = !viewer || renaming;
  if (!viewer) {
    takeover(null);
    document.title = appName;
    return;
  }
  get("viewer").textContent = viewer.name;
  get("connection").hidden = online;
  resetButton.disabled = !online || pending;
  resetConfirm.disabled = !online || pending;
  resetCancel.disabled = pending;
  const view = store.view();
  const open = [...view.waiting, ...view.inProgress];
  if (editor && !open.some((check) => check.id === editor && check.status === "arrived")) editor = null;
  const waiting = view.banner === "waiting";
  get("banner").dataset.tone = view.banner;
  get("banner-text").textContent = waiting ? "Someone may need help" : "All quiet";
  get("check-count").textContent = open.length ? `${open.length} open` : "";
  const signature = JSON.stringify([open, view.closed, viewer.id, editor, online, pending]);
  if (signature !== lastCheckView) {
    const focused = document.activeElement?.dataset;
    const options = { viewerId: viewer.id, editor, online, pending };
    renderChecks(get("checks"), open, options);
    renderChecks(get("closed-list"), view.closed, options);
    get("closed").hidden = !view.closed.length;
    get("closed-count").textContent = view.closed.length;
    if (focused?.id && !pending) {
      const matching = [...get("checks").querySelectorAll("button")].find((button) =>
        button.dataset.id === focused.id && button.dataset.action === focused.action &&
        button.dataset.outcome === focused.outcome);
      matching?.focus();
    }
    lastCheckView = signature;
  }
  for (const age of get("room").querySelectorAll(".age")) age.textContent = ago(age.dataset.at, now());
  get("camera").textContent = cameraLabel(feed, now());
  const latency = get("latency");
  latency.hidden = lastLatencyMs === undefined;
  if (!latency.hidden) latency.textContent = `Created to screen · ${(lastLatencyMs / 1000).toFixed(2)} s`;
  const names = present.map((id) => members.get(id)?.name).filter(Boolean).sort((a, b) => a.localeCompare(b));
  get("here-count").textContent = names.length;
  renderPeople(get("people"), names);
  const alert = renaming || editor || resetDialog.open ? null : alertCandidate(view);
  takeover(alert, alert ? ago(alert.detected_at, now()) : "", online, pending);
  if (!renaming && !document.hidden) {
    for (const check of open) {
      if (shownChecks.has(check.id) || clockOffsetMs === undefined) continue;
      shownChecks.add(check.id);
      const elapsedMs = now() - Date.parse(check.created_at);
      if (elapsedMs >= -100 && elapsedMs < 30_000) lastLatencyMs = Math.max(0, elapsedMs);
    }
  }
}

function feedback(message = "") {
  actionFeedback.textContent = message;
  actionFeedback.hidden = !message;
}

async function syncClock() {
  const started = Date.now();
  const server = Date.parse(await api.serverTime());
  clockOffsetMs = server - (started + Date.now()) / 2;
}

async function sync() {
  if (!viewer) return;
  const token = store.beginSnapshot();
  try {
    const result = await api.snapshot();
    if (!store.applySnapshot(token, result.checks)) return;
    const member = result.people.find((person) => person.id === viewer?.id);
    if (!member) {
      resetSession();
      return;
    }
    viewer = member;
    feed = result.feed;
    members = new Map(result.people.map((person) => [person.id, person]));
    online = navigator.onLine;
    render();
  } catch {
    if (!store.isCurrent(token)) return;
    online = false;
    render();
  }
}

async function updateInvite() {
  try {
    const code = await api.invite();
    if (!code || !viewer) return;
    activeInvite = code;
    const url = new URL("/", import.meta.env.VITE_SITE_URL || location.origin);
    url.searchParams.set("room", code);
    const link = get("invite");
    link.href = url;
    link.textContent = url;
    link.hidden = false;
    await renderQr(get("qr"), url.href);
    get("qr").hidden = false;
  } catch {
    // Sharing can be retried at the next successful connection.
  }
}

async function enter(person) {
  viewer = person;
  online = false;
  members.set(person.id, person);
  render();
  void updateInvite();
  await Promise.allSettled([syncClock(), sync()]);
  const id = person.id;
  const stop = await api.subscribe(id, {
    check(row) {
      if (viewer?.id !== id || !row.id) return;
      store.applyChange(row);
      render();
      if (!online) void sync();
    },
    checksDeleted() {
      if (viewer?.id !== id) return;
      clearTimeout(resetSync);
      resetSync = setTimeout(() => void sync(), 100);
    },
    feed(row) {
      if (viewer?.id !== id || !row.updated_at) return;
      store.invalidate();
      if (!feed || Date.parse(row.updated_at) >= Date.parse(feed.updated_at)) feed = row;
      render();
    },
    members() { if (viewer?.id === id) void sync(); },
    presence(ids) {
      if (viewer?.id !== id) return;
      present = ids;
      render();
    },
    status(status) {
      if (viewer?.id !== id) return;
      if (status === "SUBSCRIBED") {
        void sync();
        void updateInvite();
      } else {
        online = false;
        store.invalidate();
        render();
      }
    },
  });
  if (viewer?.id === id) stopSubscription = stop;
  else void stop();
}

function resetSession() {
  void stopSubscription?.();
  stopSubscription = undefined;
  clearTimeout(resetSync);
  if (resetDialog.open) resetDialog.close();
  viewer = null;
  feed = null;
  editor = null;
  renaming = false;
  online = false;
  activeInvite = undefined;
  members.clear();
  present = [];
  lastLatencyMs = undefined;
  get("invite").hidden = true;
  get("qr").hidden = true;
  store.applySnapshot(store.beginSnapshot(), []);
  get("join-title").textContent = `Join ${appName}`;
  get("cancel-rename").hidden = true;
  joinButton.textContent = "Join";
  joinButton.disabled = !joinCode;
  joinFeedback.textContent = joinCode ? "Join again to open Checks." : "Open a current room QR code to join.";
  render();
}

async function sendAction(id, action, outcome) {
  if (!id || !online || pending) return;
  pending = true;
  feedback();
  render();
  try {
    await api.action(id, action, outcome);
    if (action === "resolve") editor = null;
  } catch (error) {
    feedback(error.code === "P0001" ? error.message : "Couldn’t confirm the update. Checking the current status…");
  } finally {
    await sync();
    pending = false;
    render();
  }
}

resetButton.addEventListener("click", () => {
  if (!viewer || !online || pending) return;
  resetError.hidden = true;
  resetDialog.showModal();
  render();
});
resetCancel.addEventListener("click", () => resetDialog.close());
resetDialog.addEventListener("close", render);
resetDialog.addEventListener("cancel", event => {
  if (pending) event.preventDefault();
});
resetForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (!viewer || !online || pending) return;
  pending = true;
  resetError.hidden = true;
  feedback();
  render();
  try {
    const count = await api.resetChecks();
    editor = null;
    await sync();
    resetDialog.close();
    feedback(count ? "Checks cleared." : "There were no checks to clear.");
  } catch {
    resetError.textContent = "Couldn’t confirm the reset. Check your connection and try again.";
    resetError.hidden = false;
  } finally {
    pending = false;
    render();
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  unlockAudio();
  joinButton.disabled = true;
  joinFeedback.textContent = "";
  try {
    const code = renaming ? await api.invite() : joinCode;
    if (!code) throw new Error("Open a current room QR code to join.");
    if (!renaming && !(await api.session())) await api.signIn();
    const person = await api.join(nameInput.value, code);
    if (renaming) {
      viewer = person;
      renaming = false;
      await sync();
    } else await enter(person);
  } catch (error) {
    joinFeedback.textContent = error.message === "Wrong room code"
      ? "That link has expired. Ask for a new QR code."
      : error.message === "Type your name to join" ? error.message : "Couldn’t connect. Try again.";
  } finally {
    joinButton.disabled = false;
    render();
  }
});

get("rename").addEventListener("click", () => {
  renaming = true;
  joinFeedback.textContent = "";
  get("join-title").textContent = "Change name";
  joinButton.textContent = "Save name";
  joinButton.disabled = false;
  get("cancel-rename").hidden = false;
  nameInput.value = viewer.name;
  render();
  nameInput.focus();
});
get("cancel-rename").addEventListener("click", () => {
  renaming = false;
  render();
  get("rename").focus();
});
get("checks").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const { action, id, outcome } = button.dataset;
  if (action === "edit" || action === "cancel") {
    editor = action === "edit" ? id : null;
    feedback();
    render();
    get("checks").querySelector(`[data-action="${editor ? "resolve" : "edit"}"][data-id="${id}"]`)?.focus();
  } else void sendAction(id, action, outcome);
});

document.addEventListener("pointerup", unlockAudio);
document.addEventListener("keydown", unlockAudio);
window.addEventListener("online", () => { void sync(); });
window.addEventListener("offline", () => { online = false; store.invalidate(); render(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) void sync(); });
setInterval(() => { void sync(); if (viewer && !activeInvite) void updateInvite(); }, 5000);
setInterval(() => { if (viewer) void syncClock().catch(() => {}); }, 60_000);
setInterval(() => {
  if (!viewer) return;
  render();
  const flashing = store.view().waiting.length && Math.floor(Date.now() / 1000) % 2;
  document.title = flashing ? `Needs a responder · ${appName}` : appName;
}, 1000);

async function start() {
  try {
    api = createApi();
    api.watchSession(resetSession);
    const session = await api.session();
    const existing = session ? await api.member(session.user.id) : null;
    if (existing) await enter(existing);
    else resetSession();
  } catch {
    joinFeedback.textContent = "Unable to connect to the room. Reload to try again.";
  }
}

void start();
