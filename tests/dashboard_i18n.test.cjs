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

// Synthetic task API responses only: no browser, server or model is started.
function inspectorEnvironment() {
  const e=environment();
  function element() {
    const classes=new Set();
    return {innerHTML:'',hidden:true,value:'',classList:{
      add(name){classes.add(name);},
      toggle(name,on){if(on)classes.add(name);else classes.delete(name);},
      contains(name){return classes.has(name);}
    }};
  }
  const elements=new Map(['taskInspector','activityView','language'].map(id=>[id,element()]));
  const cards=['one','two'].map(id=>({...element(),dataset:{task:id}}));
  const requests=[];
  e.document.getElementById=id=>{
    assert.ok(elements.has(id),`Unexpected element: ${id}`);
    return elements.get(id);
  };
  e.document.querySelectorAll=selector=>selector==='[data-task]'?cards:[];
  e.context.fetch=(url,options)=>new Promise((resolve,reject)=>{
    requests.push({url,options,resolve:body=>resolve({ok:true,json:async()=>body}),reject});
  });
  const app=fs.readFileSync(path.join(root,'app.js'),'utf8');
  // Use the production declarations and functions, excluding route startup.
  for(const [start,end] of [
    [0,app.indexOf('function route(')],
    [app.indexOf('async function api('),app.indexOf('async function post(')],
    [app.indexOf('function launchPanel('),app.indexOf('function planning(')]
  ]) {
    assert.ok(start>=0&&end>start,'Production function boundaries must exist');
    e.run(app.slice(start,end));
  }
  e.run("useProjectLanguage({key:'synthetic-inspector',language:'en'})");
  return {...e,elements,cards,requests,
    html:()=>elements.get('taskInspector').innerHTML,
    inspect:id=>e.run('inspectTask('+JSON.stringify(id)+')'),
    panel:launch=>{e.context.fixtureLaunch=launch;return e.run('launchPanel(fixtureLaunch)');}
  };
}
function launchFixture(status='accepted') {
  const failed=status==='unavailable';
  return {attempt:'attempt-one',evidence:'available',summaries:{
    en:{requested:'auto',backend:failed?'No backend selected':'Orca terminal (command worker)',
      reason:'Native session contract remains unverified',
      status:failed?'Selection failed before launch':'Terminal request accepted; worker start not established'},
    ko:{requested:'auto',backend:failed?'선택된 backend 없음':'Orca 터미널(명령 워커)',
      reason:'Native 세션 계약 검증 미완료',
      status:failed?'실행 전 선택 실패':'터미널 요청 수락됨; 워커 시작 근거 아님'}
  }};
}
function taskFixture(id='one',launch=launchFixture()) {
  return {task:{id,kind:'work',purpose:'Authored purpose '+id,owner:'worker-one',
    status:'running',updated:1,generation:2,track:'track/'+id},
    attempt:{id:'attempt-'+id},launch,result:null};
}

test('launch evidence distinguishes missing, unreadable and available in both languages',()=>{
  const e=inspectorEnvironment();
  for(const locale of ['en','ko']) {
    e.run('applyLanguage('+JSON.stringify(locale)+')');
    const missing=locale==='en'?'No launch evidence':'실행 근거 없음';
    const unreadable=locale==='en'?'Unreadable launch evidence':'실행 근거를 읽을 수 없음';
    const recorded=locale==='en'?'Recorded':'기록됨';
    for(const launch of [undefined,null,{evidence:'missing',summaries:launchFixture().summaries}]) {
      const html=e.panel(launch);
      assert.ok(html.includes(missing));
      assert.ok(!html.includes(recorded));
      assert.ok(!html.includes('auto'));
      assert.ok(!html.includes('Orca'));
    }
    const broken=e.panel({...launchFixture(),evidence:'unreadable'});
    assert.ok(broken.includes(unreadable));
    assert.ok(!broken.includes(missing));
    assert.ok(!broken.includes(recorded));
    assert.ok(!broken.includes('Orca'));
    const available=e.panel(launchFixture());
    assert.ok(available.includes(recorded));
    assert.ok(available.includes('auto'));
    assert.ok(!available.includes(missing));
    assert.ok(!available.includes(unreadable));
  }
});

test('accepted and prelaunch failure retain their meaning independently of task status',async()=>{
  const e=inspectorEnvironment();
  for(const locale of ['en','ko']) {
    e.run('applyLanguage('+JSON.stringify(locale)+')');
    for(const status of ['accepted','unavailable']) {
      const launch=launchFixture(status);
      const response=taskFixture('one',launch);
      // A task's scheduler status must not replace the launch receipt status.
      response.task.status='running';
      const pending=e.inspect('one');
      e.requests.at(-1).resolve(response);
      await pending;
      const html=e.html();
      for(const value of Object.values(launch.summaries[locale]))assert.ok(html.includes(value),value);
      assert.ok(html.includes(locale==='en'?'Working':'작업 중'));
      assert.ok(!html.includes('<dd>Done</dd>'));
      assert.ok(!html.includes('<dd>완료</dd>'));
      assert.ok(!html.includes('<dd>Started</dd>'));
      if(status==='unavailable')assert.ok(!html.includes('Orca terminal (command worker)'));
    }
  }
});

test('inspector selection, navigation and authored content survive language switching',async()=>{
  const e=inspectorEnvironment();
  const response=taskFixture();
  const before=JSON.stringify(response);
  const first=e.inspect('one');
  assert.equal(e.requests[0].url,'/api/tasks/one');
  assert.equal(e.elements.get('taskInspector').hidden,false);
  assert.ok(e.elements.get('activityView').classList.contains('has-context'));
  e.requests[0].resolve(response);
  await first;
  assert.ok(e.html().includes('Execution evidence'));
  assert.ok(e.html().includes(response.launch.summaries.en.status));
  e.run("saveDisplayLanguage('ko')");
  const second=e.inspect('one');
  e.requests[1].resolve(response);
  await second;
  assert.ok(e.html().includes('실행 근거'));
  assert.ok(e.html().includes(response.launch.summaries.ko.status));
  assert.ok(!e.html().includes(response.launch.summaries.en.status));
  assert.ok(e.html().includes('Authored purpose one'));
  assert.ok(e.html().includes('href="#track/track%2Fone"'));
  assert.equal(e.run('currentTask'),'one');
  assert.ok(e.cards[0].classList.contains('active'));
  assert.ok(!e.cards[1].classList.contains('active'));
  assert.equal(JSON.stringify(response),before);
  // Language is selected when the response is rendered, not when requested.
  const third=e.inspect('one');
  e.run("saveDisplayLanguage('en')");
  e.requests[2].resolve(response);
  await third;
  assert.ok(e.html().includes(response.launch.summaries.en.status));
  assert.ok(!e.html().includes(response.launch.summaries.ko.status));
});

test('launch summaries, task fields, results and errors are HTML escaped',async()=>{
  const e=inspectorEnvironment();
  const unsafe='<img src=x onerror="alert(1)">&\'';
  const safe='&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&amp;&#39;';
  const launch=launchFixture();
  for(const summary of Object.values(launch.summaries)) {
    for(const field of ['requested','backend','reason','status'])summary[field]=unsafe;
  }
  for(const locale of ['en','ko']) {
    e.run('applyLanguage('+JSON.stringify(locale)+')');
    const panel=e.panel(launch);
    assert.equal(panel.split(safe).length-1,4);
    assert.ok(!panel.includes(unsafe));
  }
  const response=taskFixture('one',launch);
  response.task.purpose=unsafe;
  response.task.owner=unsafe;
  response.task.track=unsafe;
  response.attempt.id=unsafe;
  response.result={summary:unsafe};
  const pending=e.inspect('one');
  e.requests[0].resolve(response);
  await pending;
  assert.equal(e.html().split(safe).length-1,9);
  assert.ok(!e.html().includes('<img'));
  assert.ok(e.html().includes('href="#track/'+encodeURIComponent(unsafe)+'"'));
  const failed=e.inspect('two');
  e.requests[1].reject(Error(unsafe));
  await failed;
  assert.equal(e.html(),'<div class="notice">'+safe+'</div>');
});

test('late success and failure from a previously selected task cannot overwrite selection',async()=>{
  for(const outcome of ['success','failure']) {
    const e=inspectorEnvironment();
    const old=e.inspect('one');
    const current=e.inspect('two');
    assert.deepEqual(e.requests.map(r=>r.url),['/api/tasks/one','/api/tasks/two']);
    e.requests[1].resolve(taskFixture('two'));
    await current;
    const selectedHTML=e.html();
    if(outcome==='success')e.requests[0].resolve(taskFixture('one'));
    else e.requests[0].reject(Error('OLD REQUEST ERROR'));
    await old;
    assert.equal(e.html(),selectedHTML);
    assert.ok(e.html().includes('Authored purpose two'));
    assert.equal(e.run('currentTask'),'two');
    assert.ok(!e.cards[0].classList.contains('active'));
    assert.ok(e.cards[1].classList.contains('active'));
  }
});
