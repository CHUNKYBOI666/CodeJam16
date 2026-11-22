import { renderKitchenMap } from "./kitchen-map.js";

export const outcomes = ["Confirmed person down", "False alarm", "Couldn't verify"];

export function ownerLabel(check, viewerId) {
  return check.owner_id === viewerId ? "You are on it" : `${check.owner_name} is on it`;
}

export function ago(instant, now) {
  const seconds = Math.max(0, Math.floor((now - Date.parse(instant)) / 1000));
  if (seconds < 5) return "Just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

function element(tag, label, className) {
  const node = document.createElement(tag);
  if (label) node.textContent = label;
  if (className) node.className = className;
  return node;
}

function button(label, action, id, disabled, outcome) {
  const node = element("button", label, "button");
  node.type = "button";
  node.dataset.action = action;
  node.dataset.id = id;
  if (outcome) node.dataset.outcome = outcome;
  node.disabled = disabled;
  return node;
}

export function renderChecks(container, rows, { viewerId, editor, online, pending }) {
  const layout = document.createDocumentFragment();
  for (const check of rows) {
    const waiting = check.status === "needs_responder";
    const closed = check.status === "resolved";
    const own = check.owner_id === viewerId;
    const row = element("article", null, `check ${waiting ? "waiting" : closed ? "closed" : "assigned"}`);
    const text = element("div");
    const category = check.source === "mock" ? "Mock check" : check.source === "replay" ? "Rehearsal" : "";
    const heading = element("h3", "Possible person down");
    if (category) heading.append(element("span", category, "source"));
    const timestamp = element("span", null, "age");
    timestamp.dataset.at = closed ? check.resolved_at : check.detected_at;
    const map = renderKitchenMap(check);
    text.append(heading, map ?? element("p", check.place, "place"), timestamp);
    if (closed) {
      text.append(element("p", `${check.outcome} · ${check.owner_name}`, "owner"));
    } else if (!waiting) {
      text.append(element("p", ownerLabel(check, viewerId), "owner"));
      if (check.status === "arrived") text.append(element("p", "Arrived", "muted"));
    }
    row.append(text);
    const disabled = !online || pending;
    if (waiting) row.append(button("Accept", "accept", check.id, disabled));
    if (own && check.status === "accepted") row.append(button("I have arrived", "arrive", check.id, disabled));
    if (own && check.status === "arrived" && editor !== check.id) {
      row.append(button("Resolve", "edit", check.id, disabled));
    }
    if (own && check.status === "arrived" && editor === check.id) {
      const controls = element("div", null, "outcomes");
      controls.append(element("p", "Choose a resolution outcome"));
      for (const outcome of outcomes) {
        controls.append(button(outcome, "resolve", check.id, disabled, outcome));
      }
      controls.append(button("Cancel", "cancel", check.id, false));
      row.append(controls);
    }
    layout.append(row);
  }
  if (!rows.length) layout.append(element("p", "No open checks.", "empty"));
  container.replaceChildren(layout);
}

export function renderPeople(container, names) {
  container.replaceChildren(...names.map((name) => element("li", name)));
}
