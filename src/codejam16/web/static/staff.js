(function () {
  const POLL_MS = 1200;
  const OUTCOMES = ['Confirmed person down', 'False alarm', "Couldn't verify"];
  const app = document.getElementById('app');
  const el = (id) => document.getElementById(id);

  let snapshot = null;
  let csrfToken = '';
  let requestCount = 0;
  let appliedRequest = 0;
  let busyId = null;
  let lastLatency = null;
  let initialSnapshotSeen = false;
  const firstSeen = new Map();
  const actionErrors = new Map();
  const drawn = {};

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // Redraw a region only when its markup changed, so a focused button is not replaced under a finger.
  function paint(id, markup) {
    if (drawn[id] === markup) return;
    drawn[id] = markup;
    el(id).innerHTML = markup;
  }

  function isMine(check) {
    return check.owner_id === snapshot.viewer.id;
  }

  function stateOf(check) {
    if (check.status === 'needs_responder') {
      return { own: false, word: 'Needs a responder', late: check.overdue };
    }
    return { own: true, word: isMine(check) ? 'You are on it' : `${check.owner_name} is on it`, late: false };
  }

  function actionsFor(check) {
    if (check.status === 'needs_responder') {
      return `<button class="btn" data-action="accept" data-id="${esc(check.id)}">Accept</button>`;
    }
    if (!isMine(check)) {
      return `<p class="notice"><b>${esc(check.owner_name)}</b> has this check. Nothing for you to do.</p>`;
    }
    if (check.status === 'accepted') {
      return `<button class="btn own" data-action="arrive" data-id="${esc(check.id)}">I have arrived</button>`;
    }
    const buttons = OUTCOMES.map((outcome) =>
      `<button class="btn quiet" data-action="resolve" data-id="${esc(check.id)}" data-outcome="${esc(outcome)}">${esc(outcome)}</button>`).join('');
    return `<p class="notice">How did it end?</p><div class="outcomes">${buttons}</div>`;
  }

  function renderChecks() {
    const checks = snapshot.checks;
    if (!checks.length) {
      paint('checks', '<div class="empty"><b>Waiting for the camera</b>A check appears here when someone goes down at the East bar.</div>');
    } else {
      paint('checks', checks.map((check) => {
        const state = stateOf(check);
        const failure = actionErrors.get(check.id);
        return `<article class="check" data-check="${esc(check.id)}">
          <div class="state${state.own ? ' own' : ''}"><i></i>${esc(state.word)}${state.late ? '<span class="late">Overdue</span>' : ''}</div>
          <h2 class="where">${esc(check.zone_name)}</h2>
          <p class="fine">Possible person down. ${esc(check.landmark)}.</p>
          ${failure ? `<p class="error" role="alert">${esc(failure)}</p>` : ''}
          <div class="actions">${busyId === check.id ? '<p class="notice">Saving…</p>' : actionsFor(check)}</div>
        </article>`;
      }).join(''));
    }
    const recent = snapshot.recent.slice(0, 3);
    paint('recent', recent.length
      ? '<span>Recently closed</span>' + recent.map((c) => `<span>${esc(c.zone_name)}: ${esc(c.outcome)}, closed by ${esc(c.owner_name)}</span>`).join('')
      : '');
  }

  function zoneState(zoneId) {
    const here = snapshot.checks.filter((c) => c.zone_id === zoneId);
    if (!here.length) return { cls: 'idle', words: 'No active check', count: 0, check: null };
    const waiting = here.find((c) => c.status === 'needs_responder');
    if (waiting) return { cls: 'wait', words: 'Needs a responder', count: here.length, check: waiting };
    return { cls: 'own', words: `${here[0].owner_name} is on it`, count: here.length, check: here[0] };
  }

  function renderMap() {
    const zones = snapshot.zones.map((zone) => ({ zone, state: zoneState(zone.id) }));
    paint('pins', zones.map(({ zone, state }) => `
      <g class="pin ${state.cls}" data-zone="${esc(zone.id)}" tabindex="0" role="button" aria-label="${esc(zone.name)}: ${esc(state.words)}">
        <circle class="halo" cx="${zone.map_x}" cy="${zone.map_y}" r="28"/>
        <circle class="dot" cx="${zone.map_x}" cy="${zone.map_y}" r="17"/>
        ${state.count ? `<text class="count" x="${zone.map_x}" y="${zone.map_y + 6}">${state.count}</text>` : ''}
        <text class="name" x="${zone.map_x}" y="${zone.map_y + 56}">${esc(zone.name)}</text>
      </g>`).join(''));
    paint('zone-lines', zones.map(({ zone, state }) =>
      `<div class="zone-line"><b>${esc(zone.name)}</b><span class="state${state.cls === 'own' ? ' own' : ''}"><i${state.cls === 'idle' ? ' class="idle"' : ''}></i>${esc(state.words)}</span></div>`).join(''));
  }

  function renderFeed() {
    const feed = snapshot.feed || {};
    const parts = [feed.label || 'Signal unavailable', feed.detail || '', feed.fps ? `${feed.fps} fps` : ''];
    if (lastLatency !== null) parts.push(`Detection to screen ${lastLatency.toFixed(1)} s`);
    el('feed').textContent = parts.filter(Boolean).join(' · ');
  }

  function render() {
    renderChecks();
    renderMap();
    renderFeed();
  }

  function recordLatency(startedMs) {
    const before = new Set(firstSeen.keys());
    window.CodejamLatency.measureNewChecks(firstSeen, snapshot, startedMs, performance.now(), !initialSnapshotSeen);
    initialSnapshotSeen = true;
    firstSeen.forEach((seconds, id) => {
      if (!before.has(id) && seconds !== null) {
        lastLatency = seconds;
        console.info(`Detection → screen ${id}: ${seconds.toFixed(2)} s`);
      }
    });
  }

  function apply(next, request, startedMs) {
    if (request < appliedRequest) return;
    appliedRequest = request;
    snapshot = next;
    csrfToken = next.csrf_token || csrfToken;
    const live = new Set(next.checks.map((c) => c.id));
    actionErrors.forEach((_message, id) => { if (!live.has(id)) actionErrors.delete(id); });
    if (startedMs !== undefined) recordLatency(startedMs);
    el('sync').textContent = '';
    render();
  }

  async function refresh() {
    const request = ++requestCount;
    const startedMs = performance.now();
    try {
      const response = await fetch('/api/state', { credentials: 'same-origin', cache: 'no-store' });
      if (response.redirected || response.status === 401 || response.status === 302) { location.href = '/join'; return; }
      if (!response.ok) throw new Error(String(response.status));
      apply(await response.json(), request, startedMs);
    } catch (_error) {
      el('sync').textContent = 'Reconnecting';
    }
  }

  async function runAction(id, action, outcome) {
    busyId = id;
    actionErrors.delete(id);
    if (snapshot) render();
    const request = ++requestCount;
    try {
      const response = await fetch(`/api/checks/${encodeURIComponent(id)}/actions`, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
        body: JSON.stringify({ action, outcome }),
      });
      const body = await response.json();
      busyId = null;
      if (response.ok) {
        apply(body.state, request);
      } else {
        actionErrors.set(id, body.error || 'That did not work. Try again.');
        render();
        refresh();
      }
    } catch (_error) {
      busyId = null;
      actionErrors.set(id, 'No connection. Try again.');
      el('sync').textContent = 'Reconnecting';
      render();
    }
  }

  function setTab(tab, replace) {
    app.dataset.tab = tab;
    document.querySelectorAll('.tabs button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.tab === tab)));
    el('checks-pane').classList.toggle('on', tab === 'checks');
    el('map-pane').classList.toggle('on', tab === 'map');
    if (replace) history.replaceState(null, '', tab === 'map' ? '/map' : '/');
  }

  function openZone(zoneId) {
    const { check } = zoneState(zoneId);
    if (!check) return;
    setTab('checks', true);
    const node = document.querySelector(`[data-check="${CSS.escape(check.id)}"]`);
    if (!node) return;
    node.scrollIntoView({ block: 'start' });
    node.classList.remove('focus');
    void node.offsetWidth;
    node.classList.add('focus');
  }

  document.querySelector('.tabs').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-tab]');
    if (button) setTab(button.dataset.tab, true);
  });
  el('checks').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-action]');
    if (button && !busyId) runAction(button.dataset.id, button.dataset.action, button.dataset.outcome);
  });
  const pins = el('pins');
  pins.addEventListener('click', (event) => {
    const pin = event.target.closest('.pin');
    if (pin) openZone(pin.dataset.zone);
  });
  pins.addEventListener('keydown', (event) => {
    const pin = event.target.closest('.pin');
    if (pin && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); openZone(pin.dataset.zone); }
  });

  setTab(app.dataset.initialTab === 'map' ? 'map' : 'checks', false);
  refresh();
  setInterval(refresh, POLL_MS);
})();
