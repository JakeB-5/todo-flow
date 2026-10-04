const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../src/todo_flow/web');
require('./dashboard_theme.test.cjs');
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
test('all static accessible messages and literal UI messages have translations',()=>{
  const e=environment();
  const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
  const app=fs.readFileSync(path.join(root,'app.js'),'utf8');
  const messages=[...html.matchAll(/data-i18n(?:-aria-label|-title|-placeholder)?="([^"]+)"/g)].map(x=>x[1].replaceAll('&#x27;',"'").replaceAll('&amp;','&'));
  for(const match of app.matchAll(/tr\(("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/g)) messages.push(vm.runInNewContext(match[1]));
  const placeholders=text=>[...text.matchAll(/\{(\w+)\}/g)].map(match=>match[1]).sort();
  for(const locale of ['ko','ja','zh-CN']) {
    const catalog=e.run('catalogs['+JSON.stringify(locale)+']');
    assert.deepEqual(Object.keys(catalog).sort(),Object.keys(e.run('koreanMessages')).sort());
    for(const message of messages) assert.ok(Object.hasOwn(catalog,message),locale+': '+message);
    for(const [message,translation] of Object.entries(catalog)) {
      assert.equal(typeof translation,'string',locale+': '+message);
      assert.ok(translation.length,locale+': '+message);
      assert.deepEqual(placeholders(translation),placeholders(message),locale+': '+message);
    }
  }
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

test('all four locales update accessible attributes and preserve English fallback',()=>{
  const e=environment();
  const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
  const selector=html.match(/<select id="language"[\s\S]*?<\/select>/)[0];
  assert.deepEqual([...selector.matchAll(/<option value="([^"]+)"/g)].map(x=>x[1]),['en','ko','ja','zh-CN']);
  const title={dataset:{i18n:'Current state'},textContent:''};
  const attributes={};
  const control={
    getAttribute(name){return {'data-i18n-aria-label':'Dashboard language','data-i18n-title':'Refresh','data-i18n-placeholder':'Write your answer and reasoning.'}[name];},
    setAttribute(name,value){attributes[name]=value;}
  };
  const languageSelect={value:''};
  e.document.getElementById=()=>languageSelect;
  e.document.querySelectorAll=query=>query==='[data-i18n]'?[title]:[control];
  for(const [locale,state,label,refresh,placeholder,selection,tag] of [
    ['en','Current state','Dashboard language','Refresh','Write your answer and reasoning.','2 tracks selected','en-US'],
    ['ko','현재 상황','대시보드 언어','새로고침','판단 근거와 답변을 남겨주세요.','2개 트랙 선택','ko-KR'],
    ['ja','現在の状態','ダッシュボードの言語','更新','回答と判断理由を入力してください。','トラック 2 件を選択','ja-JP'],
    ['zh-CN','当前状态','仪表板语言','刷新','请输入答复及理由。','已选择 2 个跟踪项','zh-CN']
  ]) {
    e.run('applyLanguage('+JSON.stringify(locale)+')');
    assert.equal(e.document.documentElement.lang,locale);
    assert.equal(languageSelect.value,locale);
    assert.equal(title.textContent,state);
    assert.equal(attributes['aria-label'],label);
    assert.equal(attributes.title,refresh);
    assert.equal(attributes.placeholder,placeholder);
    assert.equal(e.run("tr('{count} tracks selected',{count:2})"),selection);
    assert.ok(e.run("tr('Select {title}',{title:'Authored <title>'})").includes('Authored <title>'));
    assert.equal(e.run("tr('Unknown message {id}',{id:'original'})"),'Unknown message original');
    assert.equal(e.run("tr('constructor')"),'constructor');
    assert.equal(e.run('localeTag()'),tag);
  }
  for(const invalid of ['unsupported','constructor','__proto__']) {
    e.run('applyLanguage('+JSON.stringify(invalid)+')');
    assert.equal(e.document.documentElement.lang,'en');
    assert.equal(title.textContent,'Current state');
  }
});

test('each locale has isolated project preferences, reload and disabled-storage behavior',()=>{
  for(const locale of ['en','ko','ja','zh-CN']) {
    const e=environment();
    const project={key:'project-a',language:locale};
    const before=JSON.stringify(project);
    e.context.project=project;
    e.run('useProjectLanguage(project)');
    assert.equal(e.document.documentElement.lang,locale);
    const override=locale==='ja'?'zh-CN':'ja';
    e.run('saveDisplayLanguage('+JSON.stringify(override)+')');
    e.run('useProjectLanguage({key:"project-b",language:'+JSON.stringify(locale)+'})');
    assert.equal(e.document.documentElement.lang,locale);
    e.run('useProjectLanguage(project)');
    assert.equal(e.document.documentElement.lang,override);
    e.run("languageProject='';useProjectLanguage(project)");
    assert.equal(e.document.documentElement.lang,override);
    assert.equal(JSON.stringify(project),before);
    e.memory.set('todo-flow.language.project-a','constructor');
    e.run("languageProject='';useProjectLanguage(project)");
    assert.equal(e.document.documentElement.lang,locale);
    const blocked=environment({storageFails:true});
    blocked.run('useProjectLanguage('+JSON.stringify(project)+')');
    assert.equal(blocked.document.documentElement.lang,locale);
    blocked.run('saveDisplayLanguage('+JSON.stringify(override)+')');
    assert.equal(blocked.document.documentElement.lang,override);
  }
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
function workerFixture(id='one') {
  const field=(value,status,source)=>({value,status,source});
  return {attempt:'attempt-'+id,
    provider:field('codex','selected','worker-selection.json:selected.provider'),
    model:{selected:field('chosen-'+id,'selected','worker-selection.json:selected.model'),
      confirmed:field(id==='one'?'native-'+('x'.repeat(450))+'<tag>':null,
        id==='one'?'confirmed':'unconfirmed','native-session.json:provider_confirmed.model')},
    effort:{selected:field(id==='one'?'high':'low','selected','worker-selection.json:selected.effort'),
      confirmed:field(null,'unconfirmed','native-session.json:provider_confirmed.effort')}};
}

test('worker summary uses field-level confirmation and preserves activity context across languages',async()=>{
  const e=activityEnvironment();
  const one=workerFixture('one'), two=workerFixture('two');
  e.data.items[0].current.worker=one;
  e.data.items[1].current.worker=two;
  const originalFetch=e.context.fetch;
  e.context.fetch=async url=>{
    const response=await originalFetch(url);
    const body=await response.json();
    if(url==='/api/tasks/one')body.worker=one;
    return {ok:true,json:async()=>body};
  };
  const hash=e.context.location.hash;
  const original=JSON.stringify([one,two]);
  e.run("selected.set('alpha','Authored title');drafts.set('d1','Keep draft')");
  for(const locale of ['en','ko']) {
    e.run('saveDisplayLanguage('+JSON.stringify(locale)+')');
    await e.load();
    const work=e.html('work'), detail=e.html('taskInspector');
    assert.ok(work.includes('native-'+('x'.repeat(450))+'&lt;tag&gt;'));
    assert.ok(work.includes('chosen-two'));
    assert.ok(!work.includes('chosen-one'));
    assert.ok(!work.includes('native-session.json'));
    assert.ok(!work.includes('<tag>'));
    assert.ok(work.includes('high · '+e.run("tr('Selected value')")));
    assert.ok(work.includes('low · '+e.run("tr('Selected value')")));
    assert.ok(work.includes(e.run("tr('Provider-confirmed value')")));
    assert.ok(work.includes(e.run("tr('Reasoning effort')")));
    assert.ok(detail.includes('chosen-one'));
    assert.ok(!detail.includes('chosen-two'));
    assert.ok(detail.includes('native-session.json:provider_confirmed.model'));
    assert.ok(detail.includes('worker-selection.json:selected.effort'));
    assert.ok(detail.includes(e.run("tr('Not confirmed')")));
    assert.ok(detail.includes(e.run("tr('Source')")));
    assert.ok(!detail.includes('<tag>'));
    assert.equal(e.context.location.hash,hash);
    assert.equal(e.run('currentTask'),'one');
    assert.equal(e.run("selected.get('alpha')"),'Authored title');
    assert.equal(e.run("drafts.get('d1')"),'Keep draft');
  }
  assert.equal(JSON.stringify([one,two]),original);
});

test('worker details distinguish delegation, missing records and unreadable evidence',async()=>{
  const e=inspectorEnvironment();
  for(const locale of ['en','ko']) {
    e.run('applyLanguage('+JSON.stringify(locale)+')');
    const first=taskFixture('one');
    first.worker=workerFixture('one');
    let pending=e.inspect('one');
    e.requests.at(-1).resolve(first);await pending;
    assert.ok(e.html().includes('chosen-one'));
    const second=taskFixture('two');
    second.worker=workerFixture('two');
    second.worker.model.selected={value:null,status:'delegated',source:'worker-selection.json:selected.model'};
    second.worker.effort.selected={value:null,status:'missing',source:null};
    second.worker.effort.confirmed={value:null,status:'unreadable',source:null};
    pending=e.inspect('two');
    e.requests.at(-1).resolve(second);await pending;
    assert.ok(!e.html().includes('chosen-one'));
    assert.ok(e.html().includes(e.run("tr('Provider default')")));
    assert.ok(e.html().includes(e.run("tr('No record')")));
    assert.ok(e.html().includes(e.run("tr('Unreadable worker evidence')")));
    assert.ok(e.html().includes('attempt-two'));
    assert.equal(e.run('currentTask'),'two');
    pending=e.inspect('legacy');
    e.requests.at(-1).resolve(taskFixture('legacy'));await pending;
    assert.ok(e.html().includes(e.run("tr('Worker model and reasoning effort')")));
    assert.ok(e.html().includes(e.run("tr('No record')")));
    assert.ok(!e.html().includes(e.run("tr('Provider default')")));
  }
});

function taskFixture(id='one',launch=launchFixture()) {
  return {task:{id,kind:'work',purpose:'Authored purpose '+id,owner:'worker-one',
    status:'running',lease:Date.now()/1000+60,updated:1,generation:2,track:'track/'+id},
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

test('native session associations retain identifiers and escape authored-looking text',()=>{
  const e=environment();
  const app=fs.readFileSync(path.join(root,'app.js'),'utf8');
  e.run(app.slice(0,app.indexOf('const date =')));
  e.run(app.slice(app.indexOf('function launchPanel('),app.indexOf('async function inspectTask(')));
  e.context.launch={evidence:'available',record:{execution_mode:'orca-native',worktree:'repo::/candidate',session:'thread-<script>',turn:'turn-one',terminal:{handle:'term-one'}}};
  let html=e.run('launchPanel(launch)');
  assert.ok(html.includes('Codex session'));
  assert.ok(html.includes('thread-&lt;script&gt;'));
  assert.ok(!html.includes('thread-<script>'));
  e.run("applyLanguage('ko')");html=e.run('launchPanel(launch)');
  assert.ok(html.includes('Codex 세션'));
  assert.ok(html.includes('repo::/candidate'));
});


function activityEnvironment() {
  const e=environment();
  const elements=new Map();
  e.document.getElementById=id=>{
    if(!elements.has(id))elements.set(id,{innerHTML:'',hidden:false,textContent:'',dataset:{},
      setAttribute(){},focus(){e.document.activeElement=this;},
      classList:{add(){},remove(){},toggle(){}}});
    return elements.get(id);
  };
  e.context.URL=URL;
  e.context.URLSearchParams=URLSearchParams;
  e.context.AbortController=AbortController;
  e.context.location={hash:'#activity?track=alpha&task=one&offset=25&tasks_offset=10'};
  e.context.window={scrollY:0,scrollTo(){}};
  const requests=[];
  const handlers=new Map();
  e.document.addEventListener=(name,handler)=>handlers.set(name,handler);
  const purpose='검증 실패 😀\n<authored>' .repeat(800);
  const current={id:'one',track:'alpha',kind:'work',status:'running',lease:Date.now()/1000+60,
    updated:1,intent:'verification-repair'};
  const data={items:[
    {id:'alpha',title:'Alpha',taskCount:2,running:1,queued:1,waiting:0,uncertain:0,
      decisions:0,status:'running',delivery:{phase:'landed',reason:'triage-pending'},verificationOk:0,verificationOutcome:'failed',current},
    {id:'beta',title:'Beta',taskCount:1,running:0,queued:0,waiting:1,uncertain:0,
      decisions:1,status:'waiting',verificationOk:null,
      current:{id:'three',kind:'work',status:'waiting',intent:null}}
  ],total:2,taskTotal:3,limit:25,offset:0,hasMore:false};
  let disconnected=false;
  e.context.fetch=async url=>{
    requests.push(url);
    if(disconnected)throw Error('offline');
    let body;
    if(url.startsWith('/api/activity/tasks?'))body={items:[current],total:11,offset:10,limit:10,hasMore:false};
    else if(url.startsWith('/api/activity?'))body=data;
    else if(url.startsWith('/api/decisions?'))body={items:[],total:0,limit:25,offset:0,hasMore:false};
    else if(url.startsWith('/api/events?'))body={items:[],next:null};
    else if(url==='/api/tasks/one')body={...taskFixture(),task:{...current,purpose}};
    else throw Error('Unexpected request '+url);
    return {ok:true,json:async()=>body};
  };
  const app=fs.readFileSync(path.join(root,'app.js'),'utf8');
  for(const [start,end] of [
    [0,app.indexOf('function go(')],
    [app.indexOf('function go('),app.indexOf('function badge(')],
    [app.indexOf('function badge('),app.indexOf('async function post(')],
    [app.indexOf('function deliveryState('),app.indexOf('function stateOf(')],
    [app.indexOf('function renderDecisions('),app.indexOf('function planning(')],
    [app.indexOf('async function loadRoute('),app.indexOf('async function refresh(')],
    [app.indexOf('function queryChange('),app.indexOf("$('search').addEventListener")],
    [app.indexOf("document.addEventListener('click'"),app.indexOf("$('moreEvents').onclick")]
  ])e.run(app.slice(start,end));
  e.run("function chrome(){} function renderEvents(){}; overview={counts:{decisions:1}}; useProjectLanguage({key:'activity-fixture',language:'en'})");
  return {...e,elements,requests,data,purpose,handlers,disconnect(){disconnected=true;},
    html:id=>elements.get(id)?.innerHTML||'',load:()=>e.run('loadRoute()')};
}

test('four-language switches retain navigation, selections, drafts and authored text',async()=>{
  const e=activityEnvironment();
  const app=fs.readFileSync(path.join(root,'app.js'),'utf8');
  e.run(app.slice(app.indexOf('function selectionUI('),app.indexOf('function renderList(')));
  e.run("selected.set('alpha','Authored title');listing={items:[{id:'alpha',selectable:true}]};drafts.set('d1','Keep <draft> unchanged');");
  const original=JSON.stringify(e.data);
  const hash=e.context.location.hash;
  const decision={items:[{id:'d1',title:'Authored title',question:'Authored question'}],total:1};
  e.context.decisionFixture=decision;
  e.document.querySelectorAll=selector=>selector==='#decisions details[open]'?[{dataset:{decision:'d1'}}]:[];
  for(const [locale,tag] of [['en','en-US'],['ko','ko-KR'],['ja','ja-JP'],['zh-CN','zh-CN']]) {
    // Switching via the language control leaves the decision input first.
    // Do not carry the synthetic typing focus into the next route load.
    e.document.activeElement=null;
    e.run('saveDisplayLanguage('+JSON.stringify(locale)+')');
    await e.load();
    e.run('selectionUI();renderDecisions(decisionFixture)');
    assert.equal(e.context.location.hash,hash);
    assert.equal(e.run('currentTask'),'one');
    assert.equal(e.run("selected.get('alpha')"),'Authored title');
    assert.equal(e.elements.get('selectPage').checked,true);
    assert.equal(e.elements.get('start').disabled,false);
    assert.equal(e.elements.get('selectionHint').textContent,'Authored title');
    assert.ok(e.html('decisions').includes('Keep &lt;draft&gt; unchanged'));
    assert.ok(e.html('decisions').includes('data-decision="d1" open'));
    assert.ok(e.html('decisions').includes('Authored question'));
    assert.ok(e.html('decisions').includes(e.run("tr('Write your answer and reasoning.')")));
    assert.ok(e.html('taskInspector').includes('検証')||e.html('taskInspector').includes('검증 실패 😀'));
    assert.ok(e.html('taskInspector').includes('&lt;authored&gt;'));
    assert.equal(e.run('number(1234567.5)'),Number(1234567.5).toLocaleString(tag));
    assert.equal(e.run('date(1700000000,true)'),new Date(1700000000000).toLocaleString(tag,{}));
    assert.equal(JSON.stringify(e.data),original);
    const focusedInput={closest:()=>true};
    e.document.activeElement=focusedInput;
    e.elements.get('decisions').innerHTML='Typing now';
    e.run('renderDecisions(decisionFixture)');
    assert.equal(e.html('decisions'),'Typing now');
    assert.equal(e.document.activeElement,focusedInput);
    assert.equal(e.run("drafts.get('d1')"),'Keep <draft> unchanged');
  }
});

test('Japanese and Chinese launch summaries render safely with legacy English fallback',async()=>{
  for(const [locale,status,missing,broken] of [
    ['ja','ターミナル要求は受理されました。ワーカー開始の証拠ではありません','起動の根拠なし','起動の根拠を読み取れません'],
    ['zh-CN','终端请求已接受，但尚无工作进程启动的证据','无启动依据','无法读取启动依据']
  ]) {
    const e=inspectorEnvironment();
    e.run('saveDisplayLanguage('+JSON.stringify(locale)+')');
    const launch=launchFixture();
    launch.summaries[locale]={requested:'auto',backend:'<native>',reason:'Authored & reason',status};
    const response=taskFixture('one',launch);
    const before=JSON.stringify(response);
    const pending=e.inspect('one');
    e.requests.at(-1).resolve(response);
    await pending;
    assert.ok(e.html().includes(status));
    assert.ok(e.html().includes('&lt;native&gt;'));
    assert.ok(e.html().includes('Authored &amp; reason'));
    assert.ok(e.html().includes('Authored purpose one'));
    assert.ok(!e.html().includes('<native>'));
    assert.equal(JSON.stringify(response),before);
    assert.ok(e.panel(null).includes(missing));
    assert.ok(e.panel({evidence:'unreadable'}).includes(broken));
    assert.ok(e.panel(launchFixture()).includes(launchFixture().summaries.en.status));
  }
});

test('activity uses bounded routes and restores track, task and page context from the URL',async()=>{
  for(const locale of ['en','ko']) {
    const e=activityEnvironment();
    e.run('saveDisplayLanguage('+JSON.stringify(locale)+')');
    await e.load();
    assert.equal(e.requests.length,5);
    assert.ok(e.requests.includes('/api/activity?limit=25&offset=25'));
    assert.ok(e.requests.includes('/api/activity/tasks?track=alpha&limit=10&offset=10'));
    assert.ok(e.requests.includes('/api/decisions?limit=25&offset=0&track=alpha'));
    assert.equal(e.run('currentTask'),'one');
    assert.equal(e.run("route().query.get('track')"),'alpha');
    assert.ok(e.html('activityTasks').includes('aria-expanded="true"'));
    assert.ok(e.html('taskInspector').includes('검증 실패 😀'));
    assert.ok(e.html('taskInspector').includes('&lt;authored&gt;'));
    assert.ok(!e.html('work').includes('검증 실패 😀'));
    assert.ok(!e.html('activityTasks').includes('검증 실패 😀'));
    assert.ok(e.html('taskInspector').includes('data-close-task'));
    assert.ok(e.html('taskInspector').includes('evidence=verification'));
    assert.ok(e.html('taskInspector').includes('return=%23activity'));
    assert.ok(e.html('work').includes(locale==='en'?'Recent verification':'최근 검증 결과'));
    assert.ok(e.html('work').includes(locale==='en'?'Fix the recorded verification failure.':'기록된 검증 실패를 수정합니다.'));
    assert.ok(e.html('work').includes(locale==='en'?'Detailed intent unavailable':'구체적인 목적 정보가 없습니다'));
    const hash=e.context.location.hash;
    e.run("saveDisplayLanguage('ko')");
    await e.load();
    assert.equal(e.context.location.hash,hash);
    assert.equal(e.run('currentTask'),'one');
    assert.ok(e.html('taskInspector').includes('검증 실패 😀'));
  }
});

test('activity distinguishes legacy nonpass from explicit requirement failure in both languages',async()=>{
  for(const [locale,inconclusive,failed] of [
    ['en','Inconclusive','Failed'],['ko','판정 불가','실패']
  ]) {
    const e=activityEnvironment();
    e.run('saveDisplayLanguage('+JSON.stringify(locale)+')');
    const track=e.data.items[0];
    delete track.verificationOutcome;
    await e.load();
    const prefix=locale==='en'?'Recent verification':'최근 검증 결과';
    assert.ok(e.html('work').includes(prefix+' · '+inconclusive));
    assert.ok(!e.html('work').includes(prefix+' · '+failed));
    track.verificationOutcome='failed';
    await e.load();
    assert.ok(e.html('work').includes(prefix+' · '+failed));
    assert.ok(!e.html('work').includes(prefix+' · '+inconclusive));
  }
});

test('activity separates recorded failure, implementation, waiting and uncertain execution',async()=>{
  const e=activityEnvironment();
  await e.load();
  assert.ok(e.html('work').includes('Implementation / investigation'));
  assert.ok(e.html('work').includes('Failed'));
  assert.ok(e.html('work').includes('awaiting assignment'));
  assert.ok(e.html('work').includes('Awaiting decision'));
  assert.ok(!e.html('work').includes('Check the candidate against required verification.'));
  e.data.items[0].uncertain=1;
  e.data.items[0].current.lease=1;
  await e.load();
  assert.ok(e.html('work').includes('Execution needs checking'));
  assert.ok(e.html('activityTasks').includes('Execution needs checking'));
  assert.ok(e.html('taskInspector').includes('Execution needs checking'));
  e.disconnect();
  await e.load();
  assert.equal(e.elements.get('activityWarning').hidden,false);
  assert.ok(e.html('work').includes('Execution needs checking'));
  assert.ok(e.html('taskInspector').includes('검증 실패 😀'));
  assert.equal(e.run('currentTask'),'one');
});

test('activity task pages expose bounded navigation without losing instructions',()=>{
  const e=activityEnvironment();
  e.context.page={items:[{id:'old',kind:'work',status:'done',intent:null}],total:31,limit:10,offset:10,hasMore:true};
  e.run("renderActivityTasks(page,'alpha')");
  assert.ok(e.html('activityTasks').includes('data-activity-page="0"'));
  assert.ok(e.html('activityTasks').includes('data-activity-page="20"'));
  assert.ok(e.html('activityTasks').includes('data-page-key="tasks_offset"'));
  assert.ok(e.html('activityTasks').includes('Detailed intent unavailable'));
});

test('task details receive keyboard focus and the close button preserves track context',async()=>{
  const e=activityEnvironment();
  e.run("activityFocus='inspector'");
  await e.load();
  assert.equal(e.document.activeElement,e.elements.get('taskInspector'));
  const button={dataset:{},hasAttribute:name=>name==='data-close-task'};
  await e.handlers.get('click')({target:{closest:()=>button}});
  assert.equal(e.run("route().query.get('track')"),'alpha');
  assert.equal(e.run("route().query.get('task')"),null);
  assert.equal(e.run("route().query.get('tasks_offset')"),'10');
  // The real browser dispatches hashchange after the native button activation.
  e.document.activeElement=null;
  await e.load();
  assert.equal(e.elements.get('taskInspector').hidden,true);
  assert.equal(e.run('currentTask'),null);
});


// delivery-phases / cleanup-completion-proof: these expectations come from the
// track conditions, not status=done. Projection receipt validation is exercised
// by DeliveryProjectionTests; these fixtures exercise its public UI boundary.
function deliveryEnvironment() {
  const e=activityEnvironment();
  const app=fs.readFileSync(path.join(root,'app.js'),'utf8');
  for(const [start,end] of [
    [app.indexOf('function summaryCards('),app.indexOf('function chrome(')],
    [app.indexOf('function trackHref('),app.indexOf('function deliveryState(')],
    [app.indexOf('function stateOf('),app.indexOf('function renderDecisions(')],
    [app.indexOf('function planning('),app.indexOf('async function showEvidence(')],
    [app.indexOf('async function refresh('),app.indexOf('function queryChange(')],
    [app.indexOf("$('language').onchange="),app.indexOf('// Appearance is local UI state.')]
  ])e.run(app.slice(start,end));
  e.document.querySelector=()=>null;
  const decode=value=>value.replaceAll('&quot;','"').replaceAll('&#39;',"'").replaceAll('&lt;','<').replaceAll('&gt;','>').replaceAll('&amp;','&');
  // A string DOM adapter for the production label-only refresh. This does not
  // emulate layout, native tab order or screen-reader announcements.
  function deliveryNodes(owner) {
    return [...owner.innerHTML.matchAll(/<span class="delivery-status"([^>]*)>([\s\S]*?)<\/small><\/span>/g)].map(match=>{
      const attrs=Object.fromEntries([...match[1].matchAll(/([\w-]+)="([^"]*)"/g)].map(x=>[x[1],decode(x[2])]));
      const dataset=Object.fromEntries(['phase','reason','detail','fresh'].map(key=>[key,attrs['data-'+key]]));
      let content=match[2]+'</small>';
      return {dataset,setAttribute(key,value){attrs[key]=value;},
        get innerHTML(){return content;},
        set innerHTML(value){
          content=value;
          for(const [key,value] of Object.entries(dataset))attrs['data-'+key]=value;
          e.context.attributes=attrs;
          const opening=e.run('Object.entries(attributes).map(([k,v])=>k+\'=\"\'+esc(v)+\'\"\').join(" ")');
          owner.innerHTML=owner.innerHTML.replace(match[0],'<span class="delivery-status" '+opening+'>'+value+'</span>');
        }};
    });
  }
  e.document.querySelectorAll=selector=>selector==='.delivery-status'
    ? [...e.elements.values()].flatMap(deliveryNodes):[];
  const get=e.document.getElementById;
  e.document.getElementById=id=>{
    const element=get(id);
    element.querySelectorAll=selector=>selector==='.delivery-status'?deliveryNodes(element):[];
    return element;
  };
  e.context.history={replaceState(_state,_title,hash){e.context.location.hash=hash;}};
  const overview={project:{key:'delivery-fixture',language:'en'},counts:{ready:1,running:1,decisions:0,completed:1},observedAt:1};
  const track={id:'delivery',title:'Authored title',goal:'Authored goal',area:'General',priority:'P2',
    status:'done',control:'finished',request:'current',selectable:false,updated:1,activity:[],
    delivery:{phase:'cleanup-deferred',reason:'cleanup-deferred',detail:'User <changes> & retained shell'},
    document:{title:'Authored title',goal:'Authored goal',scope:'Authored scope',evidence:'Authored evidence',conditions:[]},
    documentView:{url:'/document/delivery'},revision:1};
  const page={items:[track],total:1,offset:0,limit:50,hasMore:false,asOf:1};
  let offline=false;
  const activityFetch=e.context.fetch;
  e.context.fetch=async(url,options)=>{
    assert.ok(!options?.method||options.method==='GET','display changes must not write records');
    if(offline)throw Error('offline');
    if(url==='/api/overview')return {ok:true,json:async()=>overview};
    if(url==='/api/tracks/delivery')return {ok:true,json:async()=>track};
    if(url.startsWith('/api/tracks?')){
      const query=new URL(url,'http://fixture').searchParams;
      assert.ok(Number(query.get('limit'))<=100);
      return {ok:true,json:async()=>page};
    }
    return activityFetch(url,options);
  };
  e.context.fixture=track;
  e.context.overviewFixture=overview;
  e.run('overview=overviewFixture;useProjectLanguage(overview.project)');
  return {...e,track,page,setOffline(value){offline=value;}};
}

test('delivery phases and reasons appear in lists, archive, Activity and detail in four languages',async()=>{
  const e=deliveryEnvironment();
  const cases=[
    ['pending',null,null,['Awaiting landing','반영 대기','反映待ち','等待合入']],
    ['landed','triage-pending',null,['Landed','반영됨','反映済み','已合入']],
    ['triage','triage-pending',null,['Triage','트리아지','トリアージ','分诊']],
    ['cleanup','cleanup-pending',null,['Cleaning up','정리 중','後処理中','正在清理']],
    ['cleanup-deferred','cleanup-deferred','User <changes> & retained shell',['Cleanup deferred','정리 보류','後処理保留','清理暂缓']],
    ['complete',null,null,['Run complete','실행 완료','実行完了','执行完成']],
    ['check-needed','cleanup-stale',null,['Check needed','확인 필요','確認が必要','需要确认']]
  ];
  const locales=['en','ko','ja','zh-CN'];
  for(const [phase,reason,detail,labels] of cases) {
    e.track.delivery={phase,reason,detail};
    e.data.items[0].delivery=e.track.delivery;
    const before=JSON.stringify([e.track,e.data]);
    for(const [index,locale] of locales.entries()) {
      e.run('saveDisplayLanguage('+JSON.stringify(locale)+');detailSignature=""');
      for(const [hash,target] of [['#todos','rows'],['#completed','rows'],['#activity','work'],['#track/delivery?from=completed','trackView']]) {
        e.context.location.hash=hash;
        await e.load();
        const html=e.html(target);
        assert.ok(html.includes(labels[index]),locale+' '+hash+' '+phase);
        if(phase!=='complete')assert.ok(!html.includes(cases[5][3][index]));
        if(phase==='landed')assert.ok(html.includes(['Awaiting triage.','트리아지 대기 중입니다.','トリアージ待ちです。','正在等待分诊。'][index]));
        if(detail)assert.ok(html.includes('User &lt;changes&gt; &amp; retained shell'));
        if(phase==='check-needed')assert.ok(html.includes(['Cleanup evidence is for an earlier request or candidate.','정리 근거가 이전 요청 또는 후보에 해당합니다.','後処理の根拠は以前の要求または候補のものです。','清理依据属于之前的请求或候选。'][index]));
      }
    }
    assert.equal(JSON.stringify([e.track,e.data]),before);
  }
  // Older servers/absent evidence must not turn a done record into run completion.
  delete e.track.delivery;
  e.context.location.hash='#completed';
  await e.load();
  assert.ok(e.html('rows').includes('需要确认'));
  assert.ok(!e.html('rows').includes('执行完成'));
});

test('disconnect and offline language switches invalidate completion until that view is fetched again',async()=>{
  const checks=[['#todos','rows'],['#completed','rows'],['#activity','work'],['#track/delivery?from=completed','trackView']];
  for(const [hash,target] of checks) {
    const e=deliveryEnvironment();
    e.track.delivery={phase:'complete',reason:null,detail:null};
    e.data.items[0].delivery=e.track.delivery;
    // An open record in the active list retains its independent task state.
    if(hash==='#todos')e.track.status='open';
    e.context.location.hash=hash;
    e.run("selected.set('alpha','Authored title');drafts.set('d1','Keep <draft> unchanged')");
    await e.load();
    assert.ok(e.html(target).includes('Run complete'));
    const before=JSON.stringify([e.track,e.data]);
    const navigation=e.context.location.hash;
    const focused={dataset:{},tagName:'SELECT'};
    e.document.activeElement=focused;
    e.setOffline(true);
    await e.run('refresh()');
    for(const [locale,unknown,complete] of [
      ['en','Check needed','Run complete'],['ko','확인 필요','실행 완료'],
      ['ja','確認が必要','実行完了'],['zh-CN','需要确认','执行完成']
    ]) {
      e.document.getElementById('language').value=locale;
      await e.document.getElementById('language').onchange();
      assert.ok(e.html(target).includes(unknown),hash+' '+locale);
      assert.ok(!e.html(target).includes(complete),hash+' '+locale);
      assert.equal(e.context.location.hash,navigation);
      assert.equal(e.document.activeElement,focused);
      assert.equal(e.run("selected.get('alpha')"),'Authored title');
      assert.equal(e.run("drafts.get('d1')"),'Keep <draft> unchanged');
    }
    e.setOffline(false);
    await e.run('refresh()');
    assert.ok(e.html(target).includes('执行完成'),hash+' recovered');
    assert.ok(!e.html(target).includes('需要确认'),hash+' recovered');
    assert.equal(JSON.stringify([e.track,e.data]),before);
  }
});

test('fresh detail recovery does not recreate the document or restore stale archive completion',async()=>{
  const e=deliveryEnvironment();
  e.track.delivery={phase:'complete',reason:null,detail:null};
  e.context.location.hash='#completed';
  await e.load();
  e.context.location.hash='#track/delivery?from=completed';
  await e.load();
  e.setOffline(true);
  await e.run('refresh()');
  assert.ok(!e.html('rows').includes('Run complete'));
  // A label-only recovery preserves existing document/expanded UI markup.
  const view=e.elements.get('trackView');
  view.innerHTML+='<!-- retained document context -->';
  e.setOffline(false);
  await e.run('refresh()');
  assert.ok(e.html('trackView').includes('Run complete'));
  assert.ok(e.html('trackView').includes('<!-- retained document context -->'));
  assert.ok(!e.html('rows').includes('Run complete'));
});


test('delivery Activity page navigation retains selection, drafts and keyboard task context',async()=>{
  const e=activityEnvironment();
  e.data.offset=25;e.data.total=100;e.data.hasMore=true;
  e.run("selected.set('alpha','Authored title');drafts.set('d1','Keep <draft> unchanged');activityFocus='inspector'");
  await e.load();
  assert.equal(e.document.activeElement,e.elements.get('taskInspector'));
  assert.ok(e.html('work').includes('Landed'));
  const before=JSON.stringify(e.data);
  const button={dataset:{activityPage:'50',pageKey:'offset'},hasAttribute:name=>name==='data-activity-page'};
  await e.handlers.get('click')({target:{closest:()=>button}});
  await e.load();
  assert.ok(e.requests.includes('/api/activity?limit=25&offset=50'));
  assert.equal(e.run("route().query.get('track')"),'alpha');
  assert.equal(e.run("route().query.get('task')"),'one');
  assert.equal(e.run("route().query.get('tasks_offset')"),'10');
  assert.equal(e.run("selected.get('alpha')"),'Authored title');
  assert.equal(e.run("drafts.get('d1')"),'Keep <draft> unchanged');
  assert.equal(JSON.stringify(e.data),before);
});
