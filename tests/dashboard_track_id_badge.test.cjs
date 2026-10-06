const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../src/todo_flow/web');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'style.css'), 'utf8');

function section(from, to) {
  const start = app.indexOf(from);
  const end = app.indexOf(to, start);
  assert.ok(start >= 0 && end > start, `app.js section ${from}`);
  return app.slice(start, end);
}

// Run the real list renderer against a minimal DOM and inspect the produced row markup.
function renderList(items, view = 'todos') {
  const elements = new Map();
  const getElementById = id => {
    if (!elements.has(id)) elements.set(id, {innerHTML: '', textContent: '', hidden: false, value: '',
      disabled: false, checked: false, indeterminate: false, classList: {add() {}, remove() {}, toggle() {}}});
    return elements.get(id);
  };
  const document = {documentElement: {}, querySelectorAll: () => [], getElementById};
  const context = vm.createContext({document, URL, URLSearchParams, location: {hash: '#' + view},
    localStorage: {getItem: () => null, setItem() {}}, refreshLabels() {}});
  vm.runInContext(fs.readFileSync(path.join(root, 'i18n.js'), 'utf8'), context);
  vm.runInContext(app.slice(0, app.indexOf('function go(')), context);
  vm.runInContext(section('function badge(', 'function notice('), context);
  vm.runInContext(section('function trackHref(', 'function renderEvents('), context);
  vm.runInContext("function summaryCards() {} applyLanguage('en'); overview = {project: {}, counts: {completed: 0}};", context);
  context.fixture = {items, total: items.length, hasMore: false};
  vm.runInContext('renderList(fixture, route())', context);
  const header = elements.get('columns').innerHTML;
  const rows = [...elements.get('rows').innerHTML.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map(match => match[1]);
  return {header, rows};
}

const track = (id, title, overrides = {}) => ({id, title, area: 'workflow', goal: `${title} goal`, priority: 'P1',
  control: 'idle', status: 'open', selectable: true, updated: 1700000000, activity: [], delivery: {}, ...overrides});
const badgeOf = row => row.match(/<span class="track-id-badge">([^<]*)<\/span>/);

test('list rows show each canonical track ID as a badge above the title without adding a column', () => {
  const ids = ['worker-quality-time-comparison', 'release-artifact-provenance'];
  for (const [view, columns] of [['todos', 6], ['completed', 5]]) {
    const extra = view === 'completed' ? {status: 'done', selectable: false} : {};
    const {header, rows} = renderList(ids.map((id, i) => track(id, `Title ${i}`, extra)), view);
    assert.equal((header.match(/<th[ >]/g) || []).length, columns, `${view}: header columns`);
    assert.equal(rows.length, ids.length);
    rows.forEach((row, i) => {
      assert.equal((row.match(/<td[ >]/g) || []).length, columns, `${view}: row cells match the header`);
      const cell = row.match(/<td class="title-cell">([\s\S]*?)<\/td>/)[1];
      const badge = badgeOf(cell);
      assert.ok(badge, `${view}: ${ids[i]} badge`);
      assert.equal(badge[1], ids[i]);
      assert.ok(cell.indexOf(badge[0]) < cell.indexOf('class="track-title"'), 'badge precedes the title link');
      assert.ok(cell.includes(`href="#track/${ids[i]}?from=${view}">Title ${i}</a>`), 'title link is preserved');
      assert.ok(cell.includes(`<span class="track-id" title="${ids[i]}">workflow</span>`), 'area label is preserved');
      assert.ok(cell.includes(`<span class="track-goal">Title ${i} goal</span>`), 'goal is preserved');
      if (view === 'todos') assert.ok(row.includes(`data-select="${ids[i]}"`), 'selection checkbox is preserved');
    });
  }
});

test('track ID badges escape markup and keep long IDs whole', () => {
  const hostile = '<img src=x onerror=alert(1)>"&';
  const long = 'very-long-track-identifier-'.repeat(6) + 'end';
  const {rows} = renderList([track(hostile, 'Hostile'), track(long, 'Long')]);
  assert.equal(badgeOf(rows[0])[1], '&lt;img src=x onerror=alert(1)&gt;&quot;&amp;');
  assert.ok(!rows[0].includes('<img'));
  assert.equal(badgeOf(rows[1])[1], long);
});

test('badge styles stay visible on narrow screens, wrap long IDs and use theme colors', () => {
  const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .map(match => ({selectors: match[1].split(',').map(selector => selector.trim()), body: match[2]}));
  const targetsBadge = selector => /\.track-id-badge(?![\w-])/.test(selector);
  const badgeRules = rules.filter(rule => rule.selectors.some(targetsBadge));
  assert.ok(badgeRules.length, 'badge has a style rule');
  for (const rule of badgeRules) assert.ok(!/display:\s*none|visibility:\s*hidden/.test(rule.body), rule.body);
  const body = badgeRules.map(rule => rule.body).join(';');
  assert.match(body, /overflow-wrap:\s*anywhere/);
  assert.match(body, /color:\s*var\(--muted\)/);
  assert.match(body, /monospace/);
  const hidden = rules.filter(rule => /display:\s*none/.test(rule.body)).flatMap(rule => rule.selectors);
  assert.ok(hidden.includes('.track-id'), 'the existing narrow-screen area label rule is unchanged');
  assert.ok(!hidden.some(targetsBadge), 'no hiding rule applies to the badge');
});
