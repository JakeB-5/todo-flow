const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../src/todo_flow/web');
// dashboard_i18n.test.cjs loads this file; planning summary and track ID badge checks run with the same suite.
require('./dashboard_planning.test.cjs');
require('./dashboard_track_id_badge.test.cjs');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const start = app.indexOf('function initializeTheme()');
const end = app.indexOf("applyLanguage('en');", start);
assert.ok(start >= 0 && end > start, 'theme startup is connected before application startup');
const startup = app.slice(start, end);

function environment(memory = new Map(), failure = '') {
  const control = {value: '', onchange: null};
  const writes = [];
  const document = {
    documentElement: {lang: 'ja', dataset: {}},
    getElementById(id) { assert.equal(id, 'theme'); return control; }
  };
  const context = vm.createContext({document,
    fetch() { assert.fail('Theme changes must not send requests'); },
    location: {reload() { assert.fail('Theme changes must not reload'); }}
  });
  Object.defineProperty(context, 'localStorage', {get() {
    if (failure === 'access') throw Error('SecurityError');
    return {
      getItem(key) {
        if (failure === 'read') throw Error('SecurityError');
        return memory.get(key) ?? null;
      },
      setItem(key, value) {
        if (failure === 'write') throw Error('QuotaExceededError');
        writes.push([key, value]); memory.set(key, value);
      }
    };
  }});
  vm.runInContext(startup, context);
  return {control, document, context, writes, memory,
    change(value) { control.value = value; control.onchange(); }};
}

test('theme defaults, explicit choices and reload preserve language storage', () => {
  const memory = new Map([['todo-flow.language.project', 'ja']]);
  const e = environment(memory);
  assert.equal(e.document.documentElement.dataset.theme, 'light');
  assert.equal(e.control.value, 'light');
  assert.deepEqual(e.writes, []);
  e.change('dark');
  assert.equal(e.document.documentElement.dataset.theme, 'dark');
  assert.deepEqual(e.writes, [['todo-flow.theme', 'dark']]);
  assert.equal(memory.get('todo-flow.language.project'), 'ja');
  assert.equal(e.document.documentElement.lang, 'ja');
  const reloaded = environment(memory);
  assert.equal(reloaded.control.value, 'dark');
  assert.equal(reloaded.document.documentElement.dataset.theme, 'dark');
  reloaded.change('light');
  assert.equal(environment(memory).control.value, 'light');
});

test('unknown and absent stored themes fall back to light without rewriting preferences', () => {
  for (const value of [null, '', 'system', 'DARK', '__proto__']) {
    const e = environment(new Map([['todo-flow.theme', value]]));
    assert.equal(e.control.value, 'light');
    assert.equal(e.document.documentElement.dataset.theme, 'light');
    assert.deepEqual(e.writes, []);
  }
});

test('blocked storage access, reads and writes do not prevent switching', () => {
  for (const failure of ['access', 'read', 'write']) {
    const e = environment(new Map(), failure);
    e.change('dark');
    assert.equal(e.control.value, 'dark');
    assert.equal(e.document.documentElement.dataset.theme, 'dark');
    e.change('light');
    assert.equal(e.document.documentElement.dataset.theme, 'light');
  }
});

test('theme switching touches no navigation, selection, draft, focus or document nodes', () => {
  const e = environment();
  const search = {value: 'authored query', selectionStart: 4};
  const draft = {value: 'Keep <draft> 日本語', selectionStart: 7, selectionEnd: 9};
  const iframe = {src: '/api/tracks/example/document', contentWindow: {sentinel: true}};
  const nodes = {theme: e.control, search, draft, iframe};
  const selected = new Map([['example', 'Authored title']]);
  const drafts = new Map([['decision', draft.value]]);
  const state = {hash: '#activity?track=example&task=one&q=authored', scrollY: 341};
  e.context.selected = selected;
  e.context.drafts = drafts;
  e.context.window = state;
  e.context.location = {hash: state.hash, reload() { assert.fail('reload'); }};
  e.document.activeElement = draft;
  e.document.getElementById = id => nodes[id];
  e.document.querySelectorAll = () => { assert.fail('Theme must not traverse or rerender the document'); };
  const before = JSON.stringify({search, draft, iframe, state, selected: [...selected], drafts: [...drafts]});
  const rootNode = e.document.documentElement;
  const frameWindow = iframe.contentWindow;
  for (const value of ['dark', 'light', 'dark']) e.change(value);
  assert.equal(JSON.stringify({search, draft, iframe, state, selected: [...selected], drafts: [...drafts]}), before);
  assert.equal(e.context.location.hash, state.hash);
  assert.equal(e.document.activeElement, draft);
  assert.equal(e.document.documentElement, rootNode);
  assert.equal(iframe.contentWindow, frameWindow);
  assert.equal(rootNode.lang, 'ja');
});

test('theme labels and accessible name follow all supported languages without resetting theme', () => {
  const e = environment();
  const messages = ['Dashboard theme', 'Light theme', 'Dark theme'];
  const options = messages.slice(1).map(message => ({dataset: {i18n: message}, textContent: ''}));
  const attributes = {'data-i18n-aria-label': messages[0]};
  e.control.getAttribute = name => attributes[name];
  e.control.setAttribute = (name, value) => { attributes[name] = value; };
  const languageControl = {value: ''};
  e.document.getElementById = id => id === 'language' ? languageControl : e.control;
  e.document.querySelectorAll = selector => selector === '[data-i18n]' ? options :
    selector === '[data-i18n-aria-label]' ? [e.control] : [];
  e.context.refreshLabels = () => {};
  vm.runInContext(fs.readFileSync(path.join(root, 'i18n.js'), 'utf8'), e.context);
  e.change('dark');
  const expected = {
    en: ['Dashboard theme', 'Light theme', 'Dark theme'],
    ko: ['대시보드 테마', '밝은 테마', '어두운 테마'],
    ja: ['ダッシュボードのテーマ', 'ライトテーマ', 'ダークテーマ'],
    'zh-CN': ['仪表板主题', '浅色主题', '深色主题']
  };
  for (const [language, labels] of Object.entries(expected)) {
    vm.runInContext(`applyLanguage(${JSON.stringify(language)})`, e.context);
    assert.deepEqual([attributes['aria-label'], ...options.map(option => option.textContent)], labels);
    assert.equal(e.document.documentElement.dataset.theme, 'dark');
    assert.equal(e.control.value, 'dark');
    e.change('light'); e.change('dark');
    assert.equal(e.document.documentElement.lang, language);
    assert.equal(languageControl.value, language);
  }
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const selector = html.match(/<select id="theme"[\s\S]*?<\/select>/)[0];
  assert.deepEqual([...selector.matchAll(/<option value="([^"]+)"/g)].map(match => match[1]), ['light', 'dark']);
  assert.ok(selector.includes('data-i18n-aria-label="Dashboard theme"'));
  assert.ok(!selector.includes('tabindex="-1"'));
});

function luminance(hex) {
  const channels = hex.slice(1).match(/../g).map(value => parseInt(value, 16) / 255)
    .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}
function contrast(a, b) {
  const values = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

test('both palettes provide text and essential boundary contrast', () => {
  const css = fs.readFileSync(path.join(root, 'style.css'), 'utf8');
  for (const selector of [':root', ':root[data-theme=dark]']) {
    const begin = css.indexOf(selector + '{') + selector.length + 1;
    const block = css.slice(begin, css.indexOf('}', begin));
    const tokens = Object.fromEntries([...block.matchAll(/--([\w-]+):(#[0-9a-f]{6})(?=;|$)/g)].map(match => [match[1], match[2]]));
    const check = (foreground, background, minimum) => {
      assert.ok(tokens[foreground] && tokens[background], `${selector}: missing token`);
      const ratio = contrast(tokens[foreground], tokens[background]);
      assert.ok(ratio >= minimum, `${selector}: ${foreground}/${background} = ${ratio}`);
    };
    for (const surface of ['bg', 'paper', 'sidebar', 'soft', 'hover']) {
      check('ink', surface, 4.5);
      check('muted', surface, 4.5);
      check('green', surface, 4.5);
      check('line', surface, 3);
    }
    for (const [foreground, background] of [['amber', 'warning-bg'], ['blue', 'blue-bg'], ['red', 'red-bg'], ['button-ink', 'green'], ['button-ink', 'button-hover']]) check(foreground, background, 4.5);
    check('green', 'warning-bg', 3);
    check('line', 'warning-bg', 3);
  }
});
