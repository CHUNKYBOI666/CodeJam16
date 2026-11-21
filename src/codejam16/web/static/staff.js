(() => {
  const root = document.getElementById('app');
  if (!root) return;

  const role = root.dataset.role;
  const initialView = root.dataset.view;
  const queueView = document.getElementById('queue-view');
  const mapView = document.getElementById('map-view');
  const checkList = document.getElementById('check-list');
  const detailPanel = document.getElementById('detail-panel');
  const toastRegion = document.getElementById('toast-region');
  let snapshot = null;
  let selectedId = new URLSearchParams(location.search).get('check');
  let previousChecks;
  const alerts = document.createElement('div');
  alerts.className = 'phone-alerts';
  alerts.setAttribute('aria-live', 'polite');
  queueView.prepend(alerts);

  function updateAlerts(items) {
    if (role !== 'responder') return;
    const waiting = items.filter(item => item.status === 'needs_responder');
    for (const link of [...alerts.children]) {
      if (!waiting.some(item => item.id === link.dataset.id)) link.remove();
    }
    for (const item of waiting) {
      if (!previousChecks || previousChecks.has(item.id)) continue;
      const link = document.createElement('a');
      link.href = '/?check=' + encodeURIComponent(item.id);
      link.dataset.id = item.id;
      link.textContent = 'New welfare check · ' + item.zone_name + ' — ' + item.landmark + ' →';
      alerts.prepend(link);
    }
    previousChecks = new Set(items.map(item => item.id));
  }
  let filter = 'all';
  let requestSequence = 0;
  let csrfToken = '';
  let pollingTimer = null;
  let clockTimer = null;

  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);
  const date = (value) => {
    if (!value) return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  };
  const elapsed = (value) => {
    const then = new Date(value).getTime();
    if (!Number.isFinite(then)) return 'just now';
    const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
    if (seconds < 45) return 'Just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
    const hours = Math.floor(seconds / 3600);
    return `${hours} hr${hours === 1 ? '' : 's'} ago`;
  };
  const initials = (name) => String(name || '?').split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase();
  const statusLabel = (status) => ({ needs_responder: 'Needs responder', accepted: 'Being resolved', arrived: 'Being resolved', resolved: 'Closed' })[status] || status;
  const zones = () => snapshot?.zones || [];
  const zoneName = (id) => zones().find((zone) => zone.id === id)?.name || id;
  const firstSeen = new Map();
  let initialSnapshotSeen = false;
  const owner = (check) => check.owner_name || 'Waiting for someone to help';
  const checks = () => snapshot?.checks || [];
  const user = () => snapshot?.viewer || {};

  function showToast(message, isError = false) {
    const toast = document.createElement('div');
    toast.className = `toast${isError ? ' error' : ''}`;
    toast.textContent = message;
    toastRegion.appendChild(toast);
    window.setTimeout(() => toast.remove(), 3600);
  }

  function setConnection(stale) {
    const indicator = document.getElementById('sync-indicator');
    indicator.classList.toggle('stale', stale);
    indicator.querySelector('b').textContent = stale ? 'Reconnecting' : 'In sync';
    const syncText = document.getElementById('last-sync');
    syncText.textContent = stale ? 'Waiting for connection…' : 'Updated just now';
    const led = document.querySelector('.connection-led');
    led.classList.toggle('offline', snapshot?.feed?.status === 'offline');
  }

  function feedText(feed) {
    const fps = feed?.fps ? ` · ${feed.fps} fps` : '';
    return [feed?.label || 'Signal unavailable', `${feed?.detail || 'Check venue connection'}${fps}`];
  }

  function updateFeed(feed) {
    const [label, detail] = feedText(feed);
    document.getElementById('feed-label').textContent = label;
    document.getElementById('feed-detail').textContent = detail;
    document.getElementById('map-feed-detail').textContent = detail;
    document.getElementById('map-feed-status').textContent = label.toUpperCase();
    const bar = document.getElementById('feed-progress-bar');
    const flowing = feed?.status === 'live' || feed?.status === 'replay';
    bar.style.width = flowing ? '100%' : feed?.status === 'rehearsal' || feed?.status === 'finished' ? '55%' : '10%';
    const down = feed?.status === 'offline' || feed?.status === 'detector_unavailable';
    bar.style.background = down ? '#d58a73' : '#88aa91';
    document.querySelector('.connection-led').classList.toggle('offline', down);
  }

  function renderStats() {
    const all = checks();
    const needs = all.filter((check) => check.status === 'needs_responder').length;
    const progress = all.length - needs;
    const resolved = snapshot?.recent?.length || 0;
    document.getElementById('stat-needs').textContent = needs;
    document.getElementById('stat-progress').textContent = progress;
    document.getElementById('stat-resolved').textContent = resolved;
    document.getElementById('active-count').textContent = all.length;
    document.getElementById('nav-open-count')?.replaceChildren(document.createTextNode(all.length));
    document.getElementById('nav-queue-count').textContent = needs;
    document.getElementById('filter-all-count').textContent = all.length;
    document.getElementById('filter-needs-count').textContent = needs;
    document.getElementById('filter-mine-count').textContent = all.filter((c) => c.owner_id === user().id).length;
    document.getElementById('map-open-count').textContent = all.length;
    document.getElementById('map-check-label').textContent = all.length === 1 ? 'check' : 'checks';
    document.getElementById('map-needs-count').textContent = needs;
    document.getElementById('map-progress-count').textContent = progress;
    document.getElementById('map-resolved-count').textContent = resolved;
    document.getElementById('map-active-count').textContent = all.length;
    renderZones(all);
  }

  function zoneState(list) {
    if (list.some((check) => check.status === 'needs_responder')) return 'active';
    return list.length ? 'resolving' : '';
  }

  function renderZones(all) {
    const svg = 'http://www.w3.org/2000/svg';
    const host = document.getElementById('map-pins');
    host.replaceChildren();
    document.getElementById('zone-nav-list').innerHTML = zones().map((zone) => {
      const state = zoneState(all.filter((check) => check.zone_id === zone.id));
      return `<div class="zone-nav"><span class="zone-dot ${state ? `zone-dot-${state}` : 'zone-dot-slate'}"></span><span>${esc(zone.name)}</span></div>`;
    }).join('');
    zones().forEach((zone) => {
      const list = all.filter((check) => check.zone_id === zone.id);
      const group = document.createElementNS(svg, 'g');
      group.setAttribute('class', `map-pin-group${zoneState(list) === 'resolving' ? ' resolving' : ''}${list.some((check) => check.overdue) ? ' overdue' : ''}`);
      group.dataset.zone = zone.id;
      group.setAttribute('tabindex', '0');
      group.setAttribute('role', 'button');
      group.setAttribute('aria-label', `${zone.name} checks`);
      [['pin-pulse', 29], ['pin-shadow', 18], ['pin-dot', 13]].forEach(([name, radius]) => {
        const circle = document.createElementNS(svg, 'circle');
        circle.setAttribute('class', name);
        circle.setAttribute('cx', zone.map_x);
        circle.setAttribute('cy', zone.map_y);
        circle.setAttribute('r', radius);
        group.appendChild(circle);
      });
      const count = document.createElementNS(svg, 'text');
      count.setAttribute('class', 'pin-count');
      count.setAttribute('x', zone.map_x);
      count.setAttribute('y', zone.map_y + 4);
      count.textContent = list.length;
      group.appendChild(count);
      host.appendChild(group);
    });
  }

  function orderedChecks() {
    return [...checks()].sort((a, b) => {
      if ((a.status === 'needs_responder') !== (b.status === 'needs_responder')) return a.status === 'needs_responder' ? -1 : 1;
      return new Date(a.detected_at) - new Date(b.detected_at);
    });
  }

  function visibleChecks() {
    return orderedChecks().filter((check) => {
      if (filter === 'needs_responder') return check.status === 'needs_responder';
      if (filter === 'mine') return check.owner_id === user().id;
      return true;
    });
  }

  function markerIcon() {
    return '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 21s7-6.15 7-12a7 7 0 1 0-14 0c0 5.85 7 12 7 12Z" stroke="currentColor" stroke-width="1.7"/><circle cx="12" cy="9" r="2.4" stroke="currentColor" stroke-width="1.7"/></svg>';
  }

  function renderQueue() {
    const list = visibleChecks();
    if (!list.length) {
      checkList.innerHTML = `<div class="empty-state"><div class="empty-pin">✓</div><strong>${filter === 'mine' ? 'Nothing assigned to you yet' : 'You’re all caught up'}</strong><p>${filter === 'needs_responder' ? 'No checks are waiting for a responder.' : 'New welfare checks will appear here as soon as they’re raised.'}</p></div>`;
      return;
    }
    checkList.innerHTML = list.map((check) => `
      <article class="check-row${selectedId === check.id ? ' selected' : ''}" data-id="${esc(check.id)}" tabindex="0" role="button" aria-label="Open ${esc(check.zone_name)} check">
        <div class="signal-icon">${markerIcon()}</div>
        <div class="check-copy"><div class="check-title-line"><span class="status-dot ${esc(check.status)}"></span><span class="check-title">Possible person down</span>${check.source === 'rehearsal' ? '<span class="sample-chip">SAMPLE</span>' : ''}${check.overdue ? '<span class="overdue-chip">Overdue</span>' : ''}</div>
          <div class="check-subline"><span>${esc(check.zone_name)}</span><span class="sep">·</span><span>${esc(check.landmark)}</span></div></div>
        <div class="check-trailing"><span class="time-ago">${esc(elapsed(check.detected_at))}</span><span class="row-owner">${esc(check.owner_name || statusLabel(check.status))}</span></div><span class="row-arrow">›</span>
      </article>`).join('');
    checkList.querySelectorAll('.check-row').forEach((row) => {
      row.addEventListener('click', () => selectCheck(row.dataset.id));
      row.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectCheck(row.dataset.id); } });
    });
  }

  function checkHistory(check) {
    return (snapshot?.history || []).filter((event) => event.incident_id === check.id).reverse();
  }

  function renderTimeline(check) {
    const entries = checkHistory(check);
    if (!entries.length) return '<div class="timeline-item"><span class="timeline-marker"></span><div class="timeline-copy"><strong>Possible person down detected</strong><small>Camera 01 · Automated observation</small></div><span class="timeline-time">' + esc(date(check.detected_at)) + '</span></div>';
    return entries.map((event) => {
      const label = ({ created: 'Possible person down detected', accepted: 'Check accepted', arrived: 'Responder arrived', resolved: event.note || 'Check resolved' })[event.event] || event.event;
      const descriptor = event.actor_id === 'camera' ? 'Camera 01 · Automated observation' : event.actor_name;
      return `<div class="timeline-item"><span class="timeline-marker"></span><div class="timeline-copy"><strong>${esc(label)}</strong><small>${esc(descriptor)}</small></div><span class="timeline-time">${esc(date(event.created_at))}</span></div>`;
    }).join('');
  }

  function actionsFor(check) {
    const me = user();
    if (me.role !== 'responder') return '<div class="not-owner-note">Supervisor view is read only. Responders update the shared check from their queue.</div>';
    if (check.status === 'needs_responder') return `<button class="button button-primary" data-action="accept">Accept this check <span>→</span></button><div class="action-hint">Accepting assigns this check to you and clears the waiting flag.</div>`;
    if (check.owner_id !== me.id) return `<div class="not-owner-note">${esc(check.owner_name || 'A teammate')} owns this check. Only the assigned responder can update it.</div>`;
    if (check.status === 'accepted') return '<button class="button button-primary" data-action="arrive">I’ve arrived <span>→</span></button><div class="action-hint">Your team will see when you reach the location.</div>';
    if (check.status === 'arrived') return `<div class="detail-section-title">RESOLVE THIS CHECK</div><button class="button button-secondary" data-outcome="Confirmed person down">Confirmed person down</button><button class="button button-quiet" data-outcome="False alarm">False alarm</button><button class="button button-quiet" data-outcome="Couldn't verify">Couldn’t verify</button><div class="action-hint">Your outcome is saved to the shared history.</div>`;
    return '';
  }

  function renderDetail() {
    const check = checks().find((item) => item.id === selectedId);
    if (!check) {
      detailPanel.classList.remove('mobile-open');
      detailPanel.innerHTML = '<div class="empty-detail"><div class="empty-pin">⌖</div><h3>Select a check</h3><p>Choose a welfare check to see its location and next step.</p></div>';
      return;
    }
    const status = statusLabel(check.status);
    detailPanel.classList.toggle('mobile-open', window.matchMedia('(max-width: 640px)').matches);
    detailPanel.innerHTML = `
      <div class="detail-content"><div class="detail-topline"><span>CHECK · ${esc(check.id.slice(0, 8).toUpperCase())}${check.source === 'rehearsal' ? ' · REHEARSAL SAMPLE' : ''}</span><button class="detail-close" aria-label="Close details">×</button></div>
        <div class="detail-title"><div class="signal-icon">${markerIcon()}</div><div><h2>Possible person down</h2><p>${esc(status)}${check.overdue ? ' · Overdue' : ''}</p></div></div>
        <div class="detail-alert"><span class="alert-mark">i</span><span>The camera noticed someone may be down. Please check in and decide what support is needed.</span></div>
        <div class="detail-section"><div class="detail-section-title">LOCATION</div><div class="location-line"><div class="location-icon">${markerIcon()}</div><div class="location-copy"><strong>${esc(check.zone_name)}</strong><span>${esc(check.landmark)}</span></div></div></div>
        <div class="detail-section"><div class="detail-facts"><div><span class="fact-label">DETECTED</span><span class="fact-value">${esc(date(check.detected_at))} <span style="color:#9aa49d;font-weight:400">· ${esc(elapsed(check.detected_at))}</span></span></div><div><span class="fact-label">STATUS</span><span class="fact-value">${esc(status)}</span></div>${firstSeen.get(check.id) != null ? `<div><span class="fact-label">DETECTION → SCREEN</span><span class="fact-value">${firstSeen.get(check.id).toFixed(1)} s</span></div>` : ''}</div></div>
        <div class="detail-section"><div class="detail-section-title">ASSIGNED TO</div><div class="owner-row">${check.owner_name ? `<span class="avatar">${esc(initials(check.owner_name))}</span><span>${esc(check.owner_name)}</span>` : '<span class="fact-value">Waiting for someone to help</span>'}</div></div>
        <div class="detail-section"><div class="detail-section-title">CHECK HISTORY</div><div class="timeline">${renderTimeline(check)}</div></div>
        <div class="detail-actions">${actionsFor(check)}</div>
      </div>`;
    detailPanel.querySelector('.detail-close')?.addEventListener('click', () => { selectedId = null; history.replaceState(null, '', location.pathname); renderDetail(); renderQueue(); });
    detailPanel.querySelector('[data-action]')?.addEventListener('click', (event) => runAction(check.id, event.currentTarget.dataset.action));
    detailPanel.querySelectorAll('[data-outcome]').forEach((button) => button.addEventListener('click', () => runAction(check.id, 'resolve', button.dataset.outcome)));
  }

  function renderMapChecks() {
    const list = orderedChecks();
    const host = document.getElementById('map-check-list');
    if (!list.length) {
      host.innerHTML = '<div class="empty-state" style="min-height:125px"><strong>All clear</strong><p>No active checks across the venue.</p></div>';
    } else {
      host.innerHTML = list.map((check) => `<div class="map-check-row" data-zone="${esc(check.zone_id)}"><span class="map-row-indicator ${esc(check.status)}"></span><div class="map-row-copy"><strong>Possible person down · ${esc(check.zone_name)}${check.source === 'rehearsal' ? ' · Sample' : ''}</strong><span>${esc(check.owner_name || 'Waiting for a responder')}${check.overdue ? ' · Overdue' : ''}</span></div><span class="map-row-time">${esc(elapsed(check.detected_at))}</span></div>`).join('');
      host.querySelectorAll('.map-check-row').forEach((row) => row.addEventListener('click', () => openQueueForZone(row.dataset.zone)));
    }
    document.querySelectorAll('.map-pin-group').forEach((pin) => {
      const open = () => openQueueForZone(pin.dataset.zone);
      pin.hidden = !list.some(check => check.zone_id === pin.dataset.zone);
      pin.style.display = pin.hidden ? 'none' : '';
      pin.onclick = open;
      pin.onkeydown = (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } };
    });
  }

  function openQueueForZone(zone) {
    const target = checks().find((check) => check.zone_id === zone);
    if (!target) { showToast(`No active checks in ${zoneName(zone)}.`); return; }
    queueView.classList.remove('hidden');
    mapView.classList.add('hidden');
    document.getElementById('breadcrumb-current').textContent = 'Welfare checks';
    document.querySelector('[data-nav="queue"]')?.classList.add('active');
    document.querySelector('[data-nav="map"]')?.classList.remove('active');
    root.dataset.view = 'queue';
    selectedId = target.id;
    renderQueue();
    renderDetail();
    if (window.matchMedia('(max-width: 640px)').matches) window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function renderRecent() {
    const recent = snapshot?.recent || [];
    const markup = recent.length ? recent.map((check) => `<article class="recent-card"><span class="resolved-check">✓</span><div class="recent-card-copy"><strong>Possible person down · ${esc(check.zone_name)}${check.source === 'rehearsal' ? ' · Sample' : ''}</strong><span>Resolved by ${esc(check.owner_name || 'response team')} · ${esc(date(check.resolved_at))}</span></div><span class="outcome-tag">${esc(check.outcome)}</span></article>`).join('') : '<div class="recent-empty">Resolved checks will appear here.</div>';
    document.getElementById('recent-list').innerHTML = markup;
    document.getElementById('map-recent-list').innerHTML = markup;
  }

  function recordFirstSeen(requestStartedMs) {
    const before = new Set(firstSeen.keys());
    window.CodejamLatency.measureNewChecks(firstSeen, snapshot, requestStartedMs, performance.now(), !initialSnapshotSeen);
    initialSnapshotSeen = true;
    firstSeen.forEach((seconds, id) => {
      if (!before.has(id) && seconds !== null) console.info(`Detection → screen ${id}: ${seconds.toFixed(2)} s`);
    });
  }

  function render() {
    if (!snapshot) return;
    renderStats();
    renderQueue();
    renderDetail();
    renderMapChecks();
    renderRecent();
    updateFeed(snapshot.feed);
    const now = new Date();
    document.getElementById('current-time').textContent = now.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' }) + ' · ' + now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  }

  async function refresh() {
    const sequence = ++requestSequence;
    const requestStartedMs = performance.now();
    try {
      const response = await fetch('/api/state', { credentials: 'same-origin', cache: 'no-store' });
      if (response.status === 401 || response.redirected) { window.location.assign('/join'); return; }
      if (!response.ok) throw new Error('Could not refresh the shared check list');
      const next = await response.json();
      if (sequence < requestSequence) return;
      updateAlerts(next.checks);
      snapshot = next;
      csrfToken = next.csrf_token || '';
      recordFirstSeen(requestStartedMs);
      render();
      setConnection(false);
    } catch (error) {
      setConnection(true);
    }
  }

  async function runAction(id, action, outcome) {
    const buttons = detailPanel.querySelectorAll('button[data-action], button[data-outcome]');
    buttons.forEach((button) => { button.disabled = true; });
    try {
      const response = await fetch(`/api/checks/${encodeURIComponent(id)}/actions`, {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
        body: JSON.stringify({ action, ...(outcome ? { outcome } : {}) })
      });
      const body = await response.json();
      if (!response.ok) {
        await refresh();
        showToast(body.error || 'That update could not be saved. The current check is shown.', true);
        return;
      }
      snapshot = body.state;
      render();
      showToast(action === 'accept' ? 'You’re assigned. Your team is up to date.' : action === 'arrive' ? 'Arrival shared with your team.' : 'Outcome saved. The check is resolved.');
    } catch (_error) {
      await refresh();
      showToast('Connection interrupted. The latest check state has been loaded.', true);
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  }

  function selectCheck(id) {
    selectedId = id;
    renderQueue();
    renderDetail();
  }

  function setView(view) {
    const showMap = view === 'map' && role === 'supervisor';
    queueView.classList.toggle('hidden', showMap);
    mapView.classList.toggle('hidden', !showMap);
    document.getElementById('breadcrumb-current').textContent = showMap ? 'Floor view' : 'Welfare checks';
    document.querySelector('[data-nav="queue"]')?.classList.toggle('active', !showMap);
    document.querySelector('[data-nav="map"]')?.classList.toggle('active', showMap);
    root.dataset.view = showMap ? 'map' : 'queue';
  }

  document.querySelectorAll('.filter-tab').forEach((button) => button.addEventListener('click', () => {
    filter = button.dataset.filter;
    document.querySelectorAll('.filter-tab').forEach((item) => item.classList.toggle('active', item === button));
    renderQueue();
  }));
  document.querySelectorAll('[data-nav]').forEach((link) => link.addEventListener('click', (event) => {
    const view = link.dataset.nav;
    if (view === 'queue' || view === 'map') {
      event.preventDefault();
      setView(view);
      if (view === 'map' && snapshot) renderMapChecks();
      else renderQueue();
    }
  }));

  setView(initialView);
  refresh();
  pollingTimer = window.setInterval(refresh, 1200);
  clockTimer = window.setInterval(() => { if (snapshot) render(); }, 30000);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') refresh(); });
  window.addEventListener('online', refresh);
  window.addEventListener('pagehide', () => { clearInterval(pollingTimer); clearInterval(clockTimer); });
})();
