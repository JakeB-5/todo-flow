const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../src/todo_flow/web');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const notify = fs.readFileSync(path.join(root, 'notify.js'), 'utf8');
const startup = notify.indexOf('// Startup');
assert.ok(startup > 0, 'notification decisions are separated from browser startup');
const plain = value => JSON.parse(JSON.stringify(value));

// Synthetic projections only: no browser, server or model is started.
// Expectations come from the action-required-notifications conditions:
// actionable-notification, notification-dedup and notification-consent.
function environment({memory = new Map(), storage = 'ok', permission = 'default', request = 'granted', notifications = true, project = 'alpha'} = {}) {
  const elements = new Map();
  const document = {documentElement: {lang: 'en'}, hidden: false, activeElement: null, querySelectorAll: () => [],
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, {id, value: '', checked: false, hidden: true, textContent: '', dataset: {}});
      return elements.get(id);
    }};
  const data = {decisions: [], events: [], titles: {}, clock: 100};
  const shown = [], requests = [], prompts = [];
  class FakeNotification {
    constructor(title, options) { this.title = title; this.options = options; this.closed = false; shown.push(this); }
    close() { this.closed = true; }
    static requestPermission() { prompts.push(project); FakeNotification.permission = request; return Promise.resolve(request); }
  }
  FakeNotification.permission = permission;
  const overviewFixture = () => ({project: {key: project, language: 'en'}, counts: {decisions: data.decisions.length},
    revision: Math.max(0, ...data.events.map(event => event.seq)), observedAt: data.clock});
  const context = vm.createContext({document, URL, URLSearchParams, location: {hash: '#todos'}, window: {focus() {}},
    async fetch(url, init) {
      requests.push({url, method: init?.method || 'GET'});
      const target = new URL(url, 'http://dashboard.test');
      const limit = Number(target.searchParams.get('limit') || 25);
      let body;
      if (target.pathname === '/api/overview') body = overviewFixture();
      else if (target.pathname === '/api/decisions') {
        const offset = Number(target.searchParams.get('offset') || 0);
        body = {items: data.decisions.slice(offset, offset + limit), total: data.decisions.length, limit, offset,
          hasMore: offset + limit < data.decisions.length};
      } else if (target.pathname === '/api/events') {
        const before = Number(target.searchParams.get('before') || Infinity);
        const rows = data.events.filter(event => event.seq < before).sort((a, b) => b.seq - a.seq);
        body = {items: rows.slice(0, limit), next: rows.length > limit ? rows[limit - 1].seq : null};
      } else if (target.pathname.startsWith('/api/tracks/')) {
        body = {document: {title: data.titles[decodeURIComponent(target.pathname.slice('/api/tracks/'.length))]}};
      } else throw Error('Unexpected request ' + url);
      return {ok: true, json: async () => body};
    }});
  if (notifications) context.Notification = FakeNotification;
  Object.defineProperty(context, 'localStorage', {get() {
    if (storage === 'access') throw Error('SecurityError');
    return {
      getItem(key) { if (storage === 'read') throw Error('SecurityError'); return memory.get(key) ?? null; },
      setItem(key, value) { if (storage === 'write') throw Error('QuotaExceededError'); memory.set(key, value); }
    };
  }});
  const run = code => vm.runInContext(code, context);
  run(fs.readFileSync(path.join(root, 'i18n.js'), 'utf8'));
  // Production declarations only, excluding route rendering and timers.
  for (const [start, end] of [
    [0, app.indexOf('function route(')],
    [app.indexOf('function notice('), app.indexOf('async function post(')]
  ]) {
    assert.ok(start >= 0 && end > start, 'production function boundaries must exist');
    run(app.slice(start, end));
  }
  run(notify.slice(0, startup));
  const observe = () => { context.fixtureOverview = overviewFixture(); run('overview = fixtureOverview'); };
  return {context, data, shown, requests, prompts, run, memory,
    control: () => document.getElementById('notify'),
    message: () => document.getElementById('message').textContent,
    stored: () => JSON.parse(memory.get('todo-flow.notifications.' + project) || 'null'),
    async poll(fresh = false) { observe(); await run('checkNotifications(' + fresh + ')'); },
    async toggle(on) { observe(); document.getElementById('notify').checked = on; await run("toggleNotifications($('notify'))"); },
    decide(id, track, question, title) { data.clock += 1; data.decisions.push({id, track, question, title, status: 'open', created: data.clock}); },
    event(type, track) { data.clock += 1; data.events.push({seq: data.events.length + 1, type, track, at: data.clock, summary: ''}); }
  };
}

test('only new decisions, recovery decisions and cleanup deferrals alert after an opt-in baseline', () => {
  const e = environment();
  const plan = (state, snapshot) => { e.context.input = {state, snapshot}; return plain(e.run('planNotifications(input.state, input.snapshot)')); };
  const old = [
    {id: 'd-old', track: 'alpha', title: 'Alpha', question: 'Which option?', created: 10},
    {id: 'r-old', track: 'alpha', title: 'Alpha', question: 'Execution needs attention: old. Provide a recovery instruction to resume.', created: 11}
  ];
  const history = [{seq: 1, type: 'worker.claimed', track: 'alpha'}, {seq: 2, type: 'cleanup.deferred', track: 'alpha'}, {seq: 3, type: 'work.result', track: 'alpha'}];
  const first = plan({enabled: true, baselined: false}, {revision: 3, observedAt: 20, decisions: old, events: history});
  assert.deepEqual(first.alerts, []);
  assert.equal(first.state.seq, 3);
  assert.equal(first.state.since, 20);
  assert.deepEqual([...first.state.seen].sort(), ['cleanup:alpha', 'decision:d-old', 'decision:r-old']);
  const repeat = plan(first.state, {revision: 3, observedAt: 25, decisions: old, events: history});
  assert.deepEqual(repeat.alerts, []);
  assert.deepEqual(repeat.state, first.state);
  const decisions = [...old,
    {id: 'd-new', track: 'beta', title: 'Beta title', question: 'Choose a policy', created: 30},
    {id: 'r-new', track: 'gamma', title: 'Gamma title', question: 'Execution needs attention: Worker exited 1: token=secret. Provide a recovery instruction to resume.', created: 31}];
  const events = [...history, {seq: 4, type: 'worker.claimed', track: 'beta'}, {seq: 5, type: 'attempt.error', track: 'gamma'},
    {seq: 6, type: 'cleanup.deferred', track: 'alpha'}, {seq: 7, type: 'cleanup.deferred', track: 'delta'}, {seq: 8, type: 'verification.recorded', track: 'beta'}];
  const next = plan(repeat.state, {revision: 8, observedAt: 40, decisions, events});
  assert.deepEqual(next.alerts.map(alert => [alert.category, alert.track, alert.href]), [
    ['cleanup', 'delta', '#track/delta'],
    ['decision', 'beta', '#activity?track=beta'],
    ['recovery', 'gamma', '#activity?track=gamma']
  ]);
  assert.equal(next.state.seq, 8);
  assert.deepEqual(plan(next.state, {revision: 8, observedAt: 50, decisions, events}).alerts, []);
  // A deferral after completion is new; a retry of the same deferral is not.
  const later = [...events, {seq: 9, type: 'cleanup.complete', track: 'alpha'}, {seq: 10, type: 'cleanup.deferred', track: 'alpha'},
    {seq: 11, type: 'cleanup.requested', track: 'delta'}, {seq: 12, type: 'cleanup.deferred', track: 'delta'}];
  assert.deepEqual(plan(next.state, {revision: 12, observedAt: 60, decisions, events: later}).alerts.map(alert => [alert.category, alert.track]), [['cleanup', 'alpha']]);
  // Decisions older than the opt-in are never replayed, even after leaving bounded memory.
  assert.deepEqual(plan({...next.state, seen: []}, {revision: 8, observedAt: 70, decisions: old, events}).alerts, []);
});

test('notifications are off by default and never prompt or poll without a user toggle', async () => {
  const e = environment();
  e.decide('d1', 'alpha', 'Which option?', 'Alpha');
  e.event('cleanup.deferred', 'alpha');
  await e.poll(false);
  await e.poll(true);
  assert.deepEqual(e.requests, []);
  assert.deepEqual(e.prompts, []);
  assert.equal(e.shown.length, 0);
  assert.equal(e.control().checked, false);
  assert.equal(e.stored(), null);
});

test('denied permission, missing API and blocked storage keep the dashboard-only path', async () => {
  const denied = 'Notification permission was not granted. The dashboard still shows every item.';
  const unsaved = 'Notification settings cannot be saved in this browser. The dashboard still shows every item.';
  for (const [options, message] of [
    [{request: 'denied'}, denied],
    [{request: 'default'}, denied],
    [{notifications: false}, 'Browser notifications are unavailable here. The dashboard still shows every item.'],
    [{storage: 'write', permission: 'granted'}, unsaved],
    [{storage: 'access', permission: 'granted'}, unsaved]
  ]) {
    const e = environment(options);
    await e.toggle(true);
    assert.equal(e.control().checked, false);
    assert.equal(e.message(), message);
    assert.equal(e.prompts.length, options.request ? 1 : 0);
    e.decide('d1', 'alpha', 'Which option?', 'Alpha');
    e.event('cleanup.deferred', 'alpha');
    await e.poll(false);
    await e.poll(true);
    assert.equal(e.shown.length, 0);
    assert.deepEqual(e.requests, []);
    assert.equal(e.stored()?.enabled ?? false, false);
  }
});

test('first opt-in records history as seen; new actions notify once across polling, reconnects and reloads', async () => {
  const memory = new Map();
  const e = environment({memory, permission: 'default', request: 'granted'});
  e.decide('d-old', 'alpha', 'Which option?', 'Alpha');
  e.decide('r-old', 'alpha', 'Execution needs attention: old failure. Provide a recovery instruction to resume.', 'Alpha');
  e.event('cleanup.deferred', 'alpha');
  e.event('worker.claimed', 'alpha');
  await e.toggle(true);
  assert.equal(e.prompts.length, 1);
  assert.equal(e.control().checked, true);
  assert.equal(e.shown.length, 0);
  assert.equal(e.stored().baselined, true);
  for (let i = 0; i < 3; i++) await e.poll(i % 2 === 1);
  assert.equal(e.shown.length, 0);
  // Ordinary progress and a retry of the same deferral are not new actions.
  e.event('worker.claimed', 'beta');
  e.event('work.result', 'beta');
  e.event('verification.recorded', 'beta');
  e.event('cleanup.deferred', 'alpha');
  await e.poll();
  assert.equal(e.shown.length, 0);
  e.decide('d-new', 'beta', 'Choose a policy <with private detail>', 'Beta <title>');
  e.event('attempt.error', 'gamma');
  e.decide('r-new', 'gamma', 'Execution needs attention: Worker exited 1: token=SECRET log. Provide a recovery instruction to resume.', 'Gamma title');
  e.event('cleanup.deferred', 'delta');
  e.data.titles.delta = 'Delta title';
  await e.poll();
  assert.deepEqual(e.shown.map(shown => shown.options.body), [
    'Cleanup deferred · Delta title',
    'Decision requested · Beta <title>',
    'Execution needs recovery · Gamma title'
  ]);
  for (const shown of e.shown) {
    assert.equal(shown.title, 'TODO Flow');
    for (const hidden of ['SECRET', 'private detail', 'Worker exited', 'Which option', 'Choose a policy']) assert.ok(!shown.options.body.includes(hidden));
    assert.ok(shown.options.tag.startsWith('todo-flow:alpha:'));
  }
  // Repeated polling, hidden-tab polling and reconnects do not repeat alerts.
  await e.poll();
  await e.poll(true);
  await e.poll();
  assert.equal(e.shown.length, 3);
  // Clicking opens the related screen; nothing is answered or controlled.
  e.shown[1].onclick();
  assert.equal(e.context.location.hash, '#activity?track=beta');
  assert.ok(e.shown[1].closed);
  e.shown[0].onclick();
  assert.equal(e.context.location.hash, '#track/delta');
  assert.ok(e.requests.every(request => request.method === 'GET'));
  assert.ok(!e.requests.some(request => request.url.startsWith('/api/answer') || request.url.startsWith('/api/control')));
  // A reloaded page with the same browser storage does not replay earlier alerts.
  const reloaded = environment({memory, permission: 'granted'});
  reloaded.data.decisions = e.data.decisions;
  reloaded.data.events = e.data.events;
  reloaded.data.clock = e.data.clock;
  await reloaded.poll();
  assert.equal(reloaded.shown.length, 0);
  assert.equal(reloaded.control().checked, true);
  assert.deepEqual(reloaded.prompts, []);
  reloaded.decide('d-later', 'beta', 'Another choice', 'Beta <title>');
  await reloaded.poll();
  await reloaded.poll();
  assert.equal(reloaded.shown.length, 1);
  assert.equal(reloaded.shown[0].options.body, 'Decision requested · Beta <title>');
});

test('preferences are per project, revocable and re-enabling starts a new baseline', async () => {
  const memory = new Map();
  const alpha = environment({memory, permission: 'granted'});
  await alpha.toggle(true);
  assert.deepEqual(alpha.prompts, []);
  const beta = environment({memory, permission: 'granted', project: 'beta'});
  beta.decide('b1', 'one', 'Which option?', 'One');
  await beta.poll();
  await beta.poll(true);
  assert.equal(beta.control().checked, false);
  assert.deepEqual(beta.requests, []);
  assert.equal(beta.shown.length, 0);
  alpha.decide('a1', 'one', 'Which option?', 'One');
  await alpha.poll();
  assert.equal(alpha.shown.length, 1);
  await alpha.toggle(false);
  assert.equal(alpha.control().checked, false);
  assert.equal(alpha.stored().enabled, false);
  assert.equal(alpha.message(), 'Notifications are off for this project.');
  alpha.decide('a2', 'one', 'Second', 'One');
  alpha.event('cleanup.deferred', 'one');
  const before = alpha.requests.length;
  await alpha.poll();
  await alpha.poll(true);
  assert.equal(alpha.requests.length, before);
  assert.equal(alpha.shown.length, 1);
  // Re-enabling records actions that happened while off as already seen.
  await alpha.toggle(true);
  await alpha.poll();
  assert.equal(alpha.shown.length, 1);
  alpha.decide('a3', 'one', 'Third', 'One');
  await alpha.poll();
  assert.equal(alpha.shown.length, 2);
  // Browser-level revocation is respected and the toggle reflects it.
  alpha.context.Notification.permission = 'denied';
  alpha.decide('a4', 'one', 'Fourth', 'One');
  await alpha.poll();
  assert.equal(alpha.shown.length, 2);
  assert.equal(alpha.control().checked, false);
});

test('notification messages are translated and the opt-in toggle loads after the dashboard script', () => {
  const e = environment();
  const messages = [...notify.matchAll(/tr\(("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/g)].map(match => vm.runInNewContext(match[1]));
  assert.ok(messages.length >= 8);
  for (const locale of ['ko', 'ja', 'zh-CN']) {
    const catalog = e.run('catalogs[' + JSON.stringify(locale) + ']');
    for (const message of messages) assert.ok(Object.hasOwn(catalog, message), locale + ': ' + message);
  }
  e.context.alert = {key: 'decision:d1', category: 'recovery', track: 'alpha', title: 'Alpha', href: '#activity?track=alpha'};
  for (const [locale, body] of [['en', 'Execution needs recovery · Alpha'], ['ko', '실행 복구 필요 · Alpha'], ['ja', '実行の復旧が必要 · Alpha'], ['zh-CN', '执行需要恢复 · Alpha']]) {
    e.run('applyLanguage(' + JSON.stringify(locale) + ')');
    assert.equal(e.run("notificationContent(alert, {key: 'p'})").body, body);
  }
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  assert.ok(html.indexOf('<script src="/app.js">') < html.indexOf('<script src="/notify.js">'));
  const toggle = html.match(/<input type="checkbox" id="notify"[^>]*>/)[0];
  assert.ok(!toggle.includes('checked'));
});
