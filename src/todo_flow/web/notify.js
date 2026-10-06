// Action-required notifications are a browser-local, per-project display preference.
// They only read existing projections: decisions are never answered and execution is never changed.
// Pure decisions (stored preference, consent, categories, deduplication) precede the startup marker.
// Cleanup deferral keys remembered per project; decision keys are never trimmed by count.
const notificationMemory = 500;
// Each poll requests at most this many 100-item pages of decisions and of events.
const notificationPages = 5;
const notificationOff = new Set();
let notificationBusy = false, notificationPending = false;

function notificationStorageKey(project) {
  return 'todo-flow.notifications.' + project.key;
}
// An alerted decision newer than `since` stays remembered until a complete cycle advances `since` past it,
// so decision keys are only forgotten all at once (`forgetDecisions`). Cleanup keys keep bounded memory.
function rememberedKeys(keys, forgetDecisions) {
  const cleanup = new Set(keys.filter(key => !key.startsWith('decision:')).slice(-notificationMemory));
  return keys.filter(key => key.startsWith('decision:') ? !forgetDecisions : cleanup.has(key));
}
function readNotificationState(storage, project) {
  try {
    const value = JSON.parse(storage.getItem(notificationStorageKey(project)) || 'null');
    if (!value || value.enabled !== true) return {enabled: false};
    const seen = Array.isArray(value.seen) ? value.seen.filter(item => typeof item === 'string') : [];
    return {enabled: true, baselined: value.baselined === true, seq: Number(value.seq) || 0,
      since: Number(value.since) || 0, offset: Math.max(0, Math.floor(Number(value.offset) || 0)),
      cursor: typeof value.cursor === 'string' ? value.cursor : null, top: Number(value.top) || 0,
      seen: rememberedKeys(seen, false)};
  } catch {
    // Blocked or unreadable storage cannot hold consent, so notifications stay off.
    return {enabled: false};
  }
}
function writeNotificationState(storage, project, state) {
  try {
    storage.setItem(notificationStorageKey(project), JSON.stringify(state));
    return true;
  } catch {
    return false;
  }
}
// Consent requires this project's stored opt-in and the browser's current permission.
function notificationsAllowed(state, notifier) {
  return state?.enabled === true && !!notifier && notifier.permission === 'granted';
}
// engine.fail records recovery requests as decisions with this fixed English prefix.
function decisionCategory(decision) {
  return String(decision?.question || '').startsWith('Execution needs attention') ? 'recovery' : 'decision';
}
// Returns the next stored state and only the new action-required alerts.
// The first run after opt-in is a baseline: existing history is recorded as seen, never replayed.
// Only open decisions, recovery decisions and cleanup deferrals alert; progress events never do.
// A cleanup deferral alerts once per track until cleanup completes or a new execution is accepted.
function planNotifications(state, snapshot) {
  const baseline = state.baselined !== true;
  const seen = new Set(baseline ? [] : state.seen || []);
  let since = baseline ? Number(snapshot.observedAt) || 0 : Number(state.since) || 0;
  let seq = baseline ? 0 : Number(state.seq) || 0;
  const alerts = [];
  // `through` is the highest sequence whose whole range was read; later events wait for the next poll.
  const through = Number.isFinite(snapshot.through) ? snapshot.through : Infinity;
  const events = (snapshot.events || []).filter(event => Number.isFinite(event?.seq) && event.seq <= through)
    .sort((a, b) => a.seq - b.seq);
  for (const event of events) {
    if (event.seq <= seq) continue;
    seq = event.seq;
    const key = 'cleanup:' + event.track;
    if (event.type === 'cleanup.complete' || event.type === 'execution.accepted') seen.delete(key);
    else if (event.type === 'cleanup.deferred' && !seen.has(key)) {
      seen.add(key);
      if (!baseline) alerts.push({key, category: 'cleanup', track: event.track, title: event.track,
        href: '#track/' + encodeURIComponent(event.track)});
    }
  }
  // `top` is the newest decision read in the current oldest-first cycle; a fresh cycle starts from zero.
  let top = (baseline || snapshot.fresh) ? 0 : Number(state.top) || 0;
  for (const decision of snapshot.decisions || []) {
    const key = 'decision:' + decision.id;
    top = Math.max(top, Number(decision.created) || 0);
    const known = seen.has(key);
    seen.add(key);
    if (known || baseline || !(Number(decision.created) > since)) continue;
    alerts.push({key, category: decisionCategory(decision), track: decision.track,
      title: decision.title || decision.track, href: '#activity?track=' + encodeURIComponent(decision.track)});
  }
  if (baseline) seq = Math.max(seq, Number(snapshot.revision) || 0);
  // Never move past an unread range: the sequence only advances to what was actually read.
  else if (Number.isFinite(snapshot.through)) seq = Math.max(seq, snapshot.through);
  // A cycle that read every open decision contiguously from the oldest has handled all decisions up to `top`.
  // Advancing `since` protects every decision read in the cycle, so only then are decision keys forgotten;
  // unread earlier keys belong to closed decisions. An unfinished cycle keeps every decision key.
  if (snapshot.complete) { since = Math.max(since, top); top = 0; }
  const offset = Math.max(0, Math.floor(Number(snapshot.offset) || 0));
  const cursor = offset > 0 && typeof snapshot.cursor === 'string' ? snapshot.cursor : null;
  return {state: {enabled: true, baselined: true, seq, since, offset, cursor, top, seen: rememberedKeys([...seen], snapshot.complete === true)}, alerts};
}
// Only the category and track title: never decision questions, logs, prompts or credentials.
function notificationContent(alert, project) {
  const labels = {decision: tr("Decision requested"), recovery: tr("Execution needs recovery"), cleanup: tr("Cleanup deferred")};
  return {title: 'TODO Flow', body: labels[alert.category] + ' · ' + alert.title,
    tag: 'todo-flow:' + project.key + ':' + alert.key, href: alert.href};
}
function browserStorage() {
  try { return localStorage; } catch { return null; }
}
function notificationApi() {
  return typeof Notification === 'function' ? Notification : null;
}
function notificationState(project) {
  return notificationOff.has(project.key) ? {enabled: false} : readNotificationState(browserStorage(), project);
}
function syncNotificationToggle(project) {
  if (!notificationPending) $('notify').checked = notificationsAllowed(notificationState(project), notificationApi());
}
// Baseline only: newest-first event pages, bounded, until the last processed sequence is reached.
// Older history is never replayed because the baseline sequence jumps to the current revision.
async function notificationEvents(after) {
  const items = [];
  let before = null;
  for (let page = 0; page < notificationPages; page++) {
    const query = new URLSearchParams({limit: 100});
    if (before) query.set('before', before);
    const data = await api('events?' + query);
    items.push(...data.items);
    if (!data.next || data.items.some(event => event.seq <= after)) break;
    before = data.next;
  }
  return items;
}
// Oldest-first windows after the last processed sequence, bounded per poll; the rest waits for the next poll.
// Sequences are unique integers, so `before: end + 1` with limit 100 returns every event in (through, end].
async function notificationEventsAfter(after, revision) {
  const items = [];
  let through = after;
  for (let page = 0; page < notificationPages && through < revision; page++) {
    const end = Math.min(through + 100, revision);
    const data = await api('events?' + new URLSearchParams({limit: 100, before: end + 1}));
    const start = through;
    items.push(...data.items.filter(event => event.seq > start && event.seq <= end));
    through = end;
  }
  return {items, through};
}
// Open decisions are oldest-first: read bounded offset pages and resume from the stored offset next poll.
// Each later page overlaps the last processed decision (`cursor`). If it moved, an earlier decision closed or
// appeared and offsets shifted, so the cycle restarts from the oldest instead of skipping decisions.
// A fresh cycle that reaches the end has read every open decision contiguously (`complete`).
async function notificationDecisions(offset, cursor) {
  let items = [], fresh = !(offset > 0 && typeof cursor === 'string');
  if (fresh) { offset = 0; cursor = null; }
  for (let page = 0; page < notificationPages; page++) {
    const start = cursor ? offset - 1 : offset;
    const data = await api('decisions?' + new URLSearchParams({limit: 100, offset: start}));
    let rows = data.items || [];
    if (cursor) {
      // The server also moves an offset past the end to the last page; both cases restart the cycle.
      if (Number(data.offset) !== start || rows[0]?.id !== cursor) {
        items = []; offset = 0; cursor = null; fresh = true;
        continue;
      }
      rows = rows.slice(1);
    }
    items.push(...rows);
    if (!data.hasMore || !rows.length) return {items, fresh, complete: true, offset: 0, cursor: null};
    offset += rows.length;
    cursor = rows[rows.length - 1].id;
  }
  return {items, fresh, complete: false, offset, cursor};
}
async function showNotification(notifier, alert, project) {
  if (alert.category === 'cleanup') {
    try {
      const title = (await api('tracks/' + encodeURIComponent(alert.track))).document?.title;
      if (title) alert = {...alert, title};
    } catch { /* The track ID remains a safe fallback label. */ }
  }
  const content = notificationContent(alert, project);
  try {
    const shown = new notifier(content.title, {body: content.body, tag: content.tag});
    shown.onclick = () => {
      try { window.focus(); } catch { /* Focus can be refused by the browser. */ }
      location.hash = content.href;
      shown.close();
    };
  } catch { /* A failed display leaves the dashboard list as the record. */ }
}
async function checkNotifications(fresh = false) {
  if (notificationBusy || !overview?.project) return;
  const notifier = notificationApi();
  syncNotificationToggle(overview.project);
  // Disabled projects make no extra requests, including from hidden tabs.
  if (!notificationsAllowed(notificationState(overview.project), notifier)) return;
  notificationBusy = true;
  try {
    const current = fresh ? await api('overview') : overview;
    const project = current.project;
    const state = notificationState(project);
    if (!notificationsAllowed(state, notifier)) return;
    const decisions = await notificationDecisions(state.baselined ? state.offset || 0 : 0, state.baselined ? state.cursor : null);
    const revision = Number(current.revision) || 0;
    let events = [], through;
    if (!state.baselined) events = await notificationEvents(0);
    else if (revision > state.seq) {
      const read = await notificationEventsAfter(state.seq, revision);
      events = read.items;
      through = read.through;
    }
    const plan = planNotifications(state, {revision: current.revision, observedAt: current.observedAt,
      decisions: decisions.items, offset: decisions.offset, cursor: decisions.cursor, fresh: decisions.fresh,
      complete: decisions.complete, events, through});
    // A toggle or another tab may have changed the preference while these reads were pending.
    if (JSON.stringify(notificationState(project)) !== JSON.stringify(state)) return;
    // Remember before showing: an alert that cannot be remembered could repeat after reload.
    if (!writeNotificationState(browserStorage(), project, plan.state)) return;
    for (const alert of plan.alerts) await showNotification(notifier, alert, project);
  } catch {
    // Notifications are optional; the dashboard lists remain the source of truth.
  } finally {
    notificationBusy = false;
  }
}
async function toggleNotifications(control) {
  const project = overview?.project, wanted = control.checked;
  control.checked = false;
  if (!project) return;
  if (!wanted) {
    notificationOff.add(project.key);
    writeNotificationState(browserStorage(), project, {enabled: false});
    notice(tr("Notifications are off for this project."));
    return;
  }
  const notifier = notificationApi();
  if (!notifier) {
    notice(tr("Browser notifications are unavailable here. The dashboard still shows every item."));
    return;
  }
  notificationPending = true;
  try {
    let permission = notifier.permission;
    // The browser prompt is requested only from this explicit user toggle.
    if (permission !== 'granted') {
      try { permission = (await notifier.requestPermission()) || notifier.permission; } catch { permission = 'denied'; }
    }
    if (permission !== 'granted') {
      notice(tr("Notification permission was not granted. The dashboard still shows every item."));
      return;
    }
    if (!writeNotificationState(browserStorage(), project, {enabled: true, baselined: false})) {
      notice(tr("Notification settings cannot be saved in this browser. The dashboard still shows every item."));
      return;
    }
    notificationOff.delete(project.key);
    control.checked = true;
    notice(tr("Notifications are on for this project. Earlier items will not be repeated."));
  } finally {
    notificationPending = false;
  }
  await checkNotifications();
}
// Startup
$('notify').onchange = () => toggleNotifications($('notify'));
setInterval(() => { checkNotifications(document.hidden); }, 5000);
