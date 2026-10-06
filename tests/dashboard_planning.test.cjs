const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../src/todo_flow/web');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');

// planning-summary-visible / planning-guidance-clear / planning-missing-and-bounded.
// Synthetic list rows only: no server, browser or model is started. Expectations come
// from the authored document fields, not from worker selection or provider evidence.
function environment() {
  const elements = new Map();
  const requests = [];
  let open = [];
  const document = {
    documentElement: {lang: 'en', dataset: {}},
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, {id, innerHTML: '', hidden: false, textContent: '', value: '', dataset: {},
        setAttribute() {}, classList: {add() {}, remove() {}, toggle() {}}});
      return elements.get(id);
    },
    querySelectorAll: selector => selector === '#rows details[data-plan][open]' ? open : []
  };
  const context = vm.createContext({document, URL, URLSearchParams, location: {hash: '#todos'},
    fetch(url) { requests.push(url); throw Error('List rendering must not fetch per-row details'); }});
  vm.runInContext(fs.readFileSync(path.join(root, 'i18n.js'), 'utf8'), context);
  for (const [start, end] of [
    ['', 'async function api('],
    ['function summaryCards(', 'async function collect('],
    ['function trackHref(', 'function renderEvents(']
  ]) {
    const from = start ? app.indexOf(start) : 0, to = app.indexOf(end);
    assert.ok(from >= 0 && to > from, 'Production function boundaries must exist');
    vm.runInContext(app.slice(from, to), context);
  }
  const run = code => vm.runInContext(code, context);
  run("overview={project:{github:null},counts:{ready:1,running:0,decisions:0,completed:0}};applyLanguage('en')");
  return {run, requests,
    setOpen(ids) { open = ids.map(plan => ({dataset: {plan}})); },
    render(items, language = 'en') {
      context.fixture = {items, total: items.length, limit: 100, offset: 0, hasMore: false};
      run('applyLanguage(' + JSON.stringify(language) + ');renderList(fixture,route())');
      return document.getElementById('rows').innerHTML;
    }};
}
function track(id, planning) {
  return {id, title: 'Authored ' + id, goal: 'Authored goal', area: 'General', priority: 'P1', status: 'open',
    control: 'idle', selectable: true, updated: 1, activity: [], delivery: {phase: 'pending'}, planning};
}
function authored(value) { return {status: 'authored', value}; }
const planned = {
  effort: {estimate: '중간 — 목록·표시·안내', basis: 'Reuses <existing> metadata'},
  roles: {
    work: {provider: 'codex', model: authored('gpt-6.1-sol'), effort: authored('medium'), basis: 'Known path'},
    review: {provider: 'claude', model: authored('claude-opus-4-6'), effort: authored('high'), basis: 'Fresh independent review'}
  }
};
function planOf(html, id) {
  const start = html.indexOf('<details class="plan-summary" data-plan="' + id + '"');
  assert.ok(start >= 0, 'plan summary for ' + id);
  const row = html.slice(start, html.indexOf('</details>', start) + 10);
  const summary = row.slice(row.indexOf('<summary>') + 9, row.indexOf('</summary>'));
  return {row, summary};
}
const guidance = [
  'This is the authored recommendation, not the worker that ran. trackrun applies the execution mode, explicit role options and project settings first, so the actual choice can differ.',
  'Work size estimates change scope, verification burden and uncertainty. It is not a duration or price.',
  'Reasoning effort is a per-role model setting, separate from work size. Recommendations weigh complexity, risk, needed roles and user constraints.',
  'Actual runs are recorded in Activity',
  'Selection guide'
];

test('rows show the authored work size and work recommendation; other roles stay in the row expansion', () => {
  const e = environment();
  const html = e.render([track('planned', planned)]);
  const {row, summary} = planOf(html, 'planned');
  assert.ok(summary.includes('Work size: 중간 — 목록·표시·안내'));
  assert.ok(summary.includes('Implementation recommendation: codex · gpt-6.1-sol · Reasoning effort medium'));
  // The work recommendation is not every role's model, and reasoning effort is not work size.
  assert.ok(!summary.includes('claude-opus-4-6'));
  assert.ok(!summary.includes('Work size: medium'));
  assert.ok(row.includes('<b>Independent review</b> · claude · claude-opus-4-6 · Reasoning effort high · Basis: Fresh independent review'));
  assert.ok(row.includes('<b>Implementation / investigation</b> · codex · gpt-6.1-sol · Reasoning effort medium · Basis: Known path'));
  assert.ok(row.includes('Reuses &lt;existing&gt; metadata'));
  assert.ok(!row.includes('<existing>'));
  // The existing ID badge remains beside the title.
  assert.ok(html.includes('<span class="track-id" title="planned">General</span>'));
  assert.deepEqual(e.requests, []);
});

test('missing plans, provider defaults, partial roles and long names are shown as recorded', () => {
  const e = environment();
  const longModel = 'model-' + 'x'.repeat(200) + '<tag>';
  const html = e.render([
    track('legacy', undefined),
    track('empty', {effort: null, roles: null}),
    track('delegated', {effort: {estimate: 'small', basis: null}, roles: {work: {provider: 'codex', model: {status: 'default'}, effort: {status: 'default'}, basis: 'Accept defaults'}}}),
    track('partial', {effort: null, roles: {review: {provider: 'claude', model: {status: 'missing'}, effort: {status: 'missing'}, basis: null}}}),
    track('long', {effort: null, roles: {work: {provider: 'codex', model: authored(longModel), effort: {status: 'missing'}, basis: null}}})
  ]);
  for (const id of ['legacy', 'empty']) {
    const {summary} = planOf(html, id);
    assert.ok(summary.includes('Work size: Not recorded'), id);
    assert.ok(summary.includes('Implementation recommendation: No worker plan recorded'), id);
    assert.ok(!summary.includes('Provider default'), id);
  }
  const delegated = planOf(html, 'delegated').summary;
  assert.ok(delegated.includes('Work size: small'));
  assert.ok(delegated.includes('codex · Provider default · Reasoning effort Provider default'));
  const partial = planOf(html, 'partial');
  assert.ok(partial.summary.includes('Implementation recommendation: Not recorded'));
  assert.ok(partial.row.includes('claude · Not recorded · Reasoning effort Not recorded'));
  assert.ok(!partial.row.includes('Provider default'));
  const long = planOf(html, 'long').summary;
  assert.ok(long.includes('model-' + 'x'.repeat(200) + '&lt;tag&gt;'));
  assert.ok(long.includes('Reasoning effort Not recorded'));
  assert.ok(!html.includes('<tag>'));
  assert.deepEqual(e.requests, []);
});

test('guidance explains recommendations, work size and Activity in all four languages', () => {
  const e = environment();
  for (const language of ['en', 'ko', 'ja', 'zh-CN']) {
    const {row, summary} = planOf(e.render([track('planned', planned)], language), 'planned');
    for (const message of guidance) {
      const text = e.run('tr(' + JSON.stringify(message) + ')');
      assert.ok(row.includes(text), language + ': ' + message);
      if (language !== 'en') assert.notEqual(text, message, language + ': ' + message);
    }
    assert.ok(summary.includes(e.run('tr("Work size")')));
    assert.ok(summary.includes(e.run('tr("Implementation recommendation")')));
    assert.ok(row.includes('href="#activity"'));
    assert.ok(row.includes('skills/todo/worker-routing.md'));
    // Authored content is never translated.
    assert.ok(summary.includes('중간 — 목록·표시·안내'));
    assert.ok(row.includes('Known path'));
    assert.ok(row.includes('gpt-6.1-sol'));
  }
  e.run("applyLanguage('ko')");
  assert.equal(e.run('tr("Work size")'), '작업 규모');
  assert.equal(e.run('tr("Not recorded")'), '미작성');
  assert.equal(e.run('tr("Provider default")'), '공급자 기본값에 위임');
});

test('expanded plans and selections survive polling rerenders and language changes', () => {
  const e = environment();
  const items = () => [track('planned', planned), track('legacy', undefined)];
  let html = e.render(items());
  assert.ok(!html.includes('data-plan="planned" open'));
  e.run("selected.set('planned','Authored planned')");
  e.setOpen(['planned']);
  html = e.render(items(), 'ko');
  assert.ok(html.includes('data-plan="planned" open'));
  assert.ok(!html.includes('data-plan="legacy" open'));
  const at = html.indexOf('data-select="planned"');
  assert.ok(at >= 0);
  assert.ok(html.slice(at, html.indexOf('>', at)).includes('checked'));
  assert.equal(e.run("selected.get('planned')"), 'Authored planned');
  assert.deepEqual(e.requests, []);
});
