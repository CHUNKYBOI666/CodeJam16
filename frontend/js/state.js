const WAITING = "needs_responder";

export function createStore() {
  let checks = new Map();
  const dismissed = new Set();
  let version = 0;

  return {
    beginSnapshot() {
      return ++version;
    },
    isCurrent(token) {
      return token === version;
    },
    applySnapshot(token, rows) {
      if (token !== version) return false;
      checks = new Map(rows.map((row) => [row.id, row]));
      return true;
    },
    applyChange(row) {
      const current = checks.get(row.id);
      if (current?.revision !== undefined && row.revision <= current.revision) return;
      version += 1;
      checks.set(row.id, row);
    },
    invalidate() {
      version += 1;
    },
    dismiss(id) {
      dismissed.add(id);
    },
    view() {
      const sorted = [...checks.values()].sort((a, b) =>
        Date.parse(b.created_at) - Date.parse(a.created_at));
      const waiting = sorted.filter((row) => row.status === WAITING);
      return {
        banner: waiting.length ? "waiting" : "quiet",
        waiting,
        inProgress: sorted.filter((row) => ["accepted", "arrived"].includes(row.status)),
        closed: sorted.filter((row) => row.status === "resolved")
          .sort((a, b) => Date.parse(b.resolved_at) - Date.parse(a.resolved_at)),
        alertCheck: waiting.find((row) => !dismissed.has(row.id)) ?? null,
      };
    },
  };
}

export function cameraLabel(feed, nowMs) {
  if (!feed || nowMs - Date.parse(feed.updated_at) > 40_000) return "Camera offline";
  if (feed.status === "detector_unavailable") return "Detector unavailable";
  if (feed.status === "offline") return "Camera offline";
  const label = feed.status === "replay" ? "Rehearsal clip" : "Live";
  return `${label} · ${Math.round(feed.fps)} fps`;
}
