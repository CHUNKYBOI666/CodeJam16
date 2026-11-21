// Detection → screen time, measured on the server's clock so phone clock drift does not matter.
(function (root) {
  function detectionToScreenSeconds(detectedAt, serverTime, requestStartedMs, renderedMs) {
    const atServer = (Date.parse(serverTime) - Date.parse(detectedAt)) / 1000;
    return Math.max(0, atServer + (renderedMs - requestStartedMs) / 2000);
  }

  // Checks already present when the page first loads were not watched arriving: record null.
  function measureNewChecks(firstSeen, snapshot, requestStartedMs, renderedMs, isInitial) {
    (snapshot?.checks || []).forEach((check) => {
      if (check.source === 'rehearsal' || firstSeen.has(check.id)) return;
      firstSeen.set(check.id, isInitial ? null
        : detectionToScreenSeconds(check.detected_at, snapshot.server_time, requestStartedMs, renderedMs));
    });
  }

  const api = { detectionToScreenSeconds, measureNewChecks };
  root.CodejamLatency = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
