const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../src/todo_flow/web');
function environment({storageFails=false}={}) {
  const memory = new Map();
  const document = {documentElement:{lang:'en'},querySelectorAll:()=>[],getElementById:()=>({value:''})};
  const context = vm.createContext({document,refreshLabels(){},localStorage:{
    getItem(key){if(storageFails)throw Error('Blocked');return memory.get(key);},
    setItem(key,value){if(storageFails)throw Error('Blocked');memory.set(key,value);}
  }});
  vm.runInContext(fs.readFileSync(path.join(root,'i18n.js'),'utf8'),context);
  return {context,document,memory,run:code=>vm.runInContext(code,context)};
}
test('project defaults, browser override, isolation and reload',()=>{
  const e=environment();
  e.run("useProjectLanguage({key:'a',language:'ko'})");
  assert.equal(e.document.documentElement.lang,'ko');
  assert.equal(e.run("tr('Current state')"),'현재 상황');
  e.run("saveDisplayLanguage('en');useProjectLanguage({key:'a',language:'ko'})");
  assert.equal(e.run("tr('Current state')"),'Current state');
  e.run("useProjectLanguage({key:'b',language:'ko'})");
  assert.equal(e.document.documentElement.lang,'ko');
  e.run("useProjectLanguage({key:'a',language:'ko'})");
  assert.equal(e.document.documentElement.lang,'en');
});
test('storage failure still allows project default and switching',()=>{
  const e=environment({storageFails:true});
  e.run("useProjectLanguage({key:'a',language:'ko'});saveDisplayLanguage('en')");
  assert.equal(e.document.documentElement.lang,'en');
});
test('named interpolation supports natural word order and unknown messages',()=>{
  const e=environment();
  assert.equal(e.run("tr('{count} tracks selected',{count:2})"),'2 tracks selected');
  e.run("applyLanguage('ko')");
  assert.equal(e.run("tr('{count} tracks selected',{count:2})"),'2개 트랙 선택');
  assert.equal(e.run("tr('Select {title}',{title:'User-authored title'})"),'User-authored title 선택');
  assert.equal(e.run("tr('Unrecognized server diagnostic')"),'Unrecognized server diagnostic');
  assert.equal(e.run("tr('constructor')"),'constructor');
  assert.equal(e.run("localeTag()"),'ko-KR');
});
test('invalid preference falls back to project language; invalid project language to English',()=>{
  const e=environment();
  e.memory.set('todo-flow.language.a','unsupported');
  e.run("useProjectLanguage({key:'a',language:'ko'})");
  assert.equal(e.document.documentElement.lang,'ko');
  e.run("useProjectLanguage({key:'b',language:'unsupported'})");
  assert.equal(e.document.documentElement.lang,'en');
});
test('all static accessible messages and literal UI messages have Korean translations',()=>{
  const e=environment();
  const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
  const app=fs.readFileSync(path.join(root,'app.js'),'utf8');
  const messages=[...html.matchAll(/data-i18n(?:-aria-label|-title|-placeholder)?="([^"]+)"/g)].map(x=>x[1].replaceAll('&#x27;',"'").replaceAll('&amp;','&'));
  for(const match of app.matchAll(/tr\(("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/g)) messages.push(vm.runInNewContext(match[1]));
  for(const message of messages) assert.ok(e.run('Object.hasOwn(koreanMessages,'+JSON.stringify(message)+')'),message);
});
test('focused decision drafts survive a language change and ordinary polling does not replace them',()=>{
  const e=environment();
  const decisions={innerHTML:''};
  e.document.getElementById=id=>id==='decisions'?decisions:{value:''};
  e.document.querySelectorAll=selector=>selector==='#decisions details[open]'?[{dataset:{decision:'d1'}}]:[];
  const app=fs.readFileSync(path.join(root,'app.js'),'utf8');
  e.run(app.slice(0,app.indexOf('const date =')));
  e.run("let decisionLanguage='';const drafts=new Map([['d1','Keep <draft> unchanged']]);");
  e.run(app.slice(app.indexOf('function renderDecisions('),app.indexOf('function renderActivity(')));
  const data={items:[{id:'d1',title:'Authored title',question:'Choose a policy'}],total:1};
  e.context.data=data;
  e.run('renderDecisions(data)');
  assert.ok(decisions.innerHTML.includes('Your decisions'));
  e.document.activeElement={closest:()=>true};
  decisions.innerHTML='User is typing';
  e.run('renderDecisions(data)');
  assert.equal(decisions.innerHTML,'User is typing');
  e.run("applyLanguage('ko');renderDecisions(data)");
  assert.ok(decisions.innerHTML.includes('사용자 판단이 필요한 일'));
  assert.ok(decisions.innerHTML.includes('Keep &lt;draft&gt; unchanged'));
  assert.ok(decisions.innerHTML.includes('data-decision="d1" open'));
  assert.ok(decisions.innerHTML.includes('Authored title'));
});
