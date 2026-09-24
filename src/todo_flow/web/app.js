const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = (value) => Number(value || 0).toLocaleString('ko-KR');
const date = (value, full = false) => value ? new Date(value * 1000).toLocaleString('ko-KR', full ? {} : {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '기록 없음';
const labels = {idle:'미실행',active:'실행 요청됨',paused:'일시정지','pause-requested':'정지 요청됨',cancelled:'실행 취소',finished:'요청 종료',done:'완료',queued:'배정 대기',running:'작업 중',waiting:'결정 대기',met:'충족',unmet:'미충족','cannot-assess':'판정 불가',open:'열림',resolved:'해결',dismissed:'종결',promoted:'TODO 승격'};
const roles = {assess:'상황 판단',work:'구현·조사',verify:'검증',review:'독립 리뷰',land:'랜딩',triage:'랜딩 후 트리아지',complete:'완료 확인',watch:'Watch 검토'};
const eventLabels = {'triage.recorded':'랜딩 후 트리아지가 기록됐습니다','triage.todo-registered':'후속 TODO가 등록됐습니다','finding.linked':'발견 사항이 연결됐습니다','delivery.repair-required':'반영 후 재작업이 필요합니다','worker.claimed':'워커가 작업을 맡았습니다','work.requested':'후속 작업이 등록됐습니다','work.joined':'기존 작업에 합류했습니다','work.result':'작업 결과가 기록됐습니다','effect.confirmed':'외부 반영이 확인됐습니다','completion.adopted':'완료가 확인됐습니다','execution.accepted':'실행 요청이 접수됐습니다','decision.answered':'결정이 기록됐습니다','attempt.error':'작업을 확인해야 합니다','verification.recorded':'검증 결과가 기록됐습니다','claim.recovered':'작업을 이어받았습니다','document.registered':'트랙 문서가 등록됐습니다'};
let overview = null, listing = null, currentDetail = null, currentTask = null;
let controller = null, generation = 0, searchTimer = null, eventCursor = null, events = [];
let routeKey = '', detailSignature = '';
const selected = new Map(), drafts = new Map(), evidenceCache = new Map(), evidenceOpen = new Set();
const scrollPositions = new Map();
let archiveLimit = 50;
const savedViews = {todos:'#todos', completed:'#completed'};

function route() {
  const url = new URL((location.hash.slice(1) || 'todos'), 'http://view/');
  const path = url.pathname.slice(1).split('/');
  return {view:path[0], id:decodeURIComponent(path[1] || ''), query:url.searchParams};
}
function go(view, params = {}, replace = false) {
  const query = new URLSearchParams(Object.entries(params).filter(([,v]) => v !== '' && v != null));
  const hash = '#' + view + (query.size ? '?' + query : '');
  if (replace) {history.replaceState(null, '', hash); loadRoute();}
  else if (location.hash === hash) loadRoute();
  else location.hash = hash;
}
function badge(value, text) {
  const color = ['done','met','ready','resolved'].includes(value) ? 'green' : ['running','active'].includes(value) ? 'blue' : ['waiting','paused','pause-requested','unknown'].includes(value) ? 'amber' : ['unmet','cancelled'].includes(value) ? 'red' : '';
  return `<span class="badge ${color}">${['done','met','ready'].includes(value)?'✓ ':value==='running'?'● ':['waiting','unknown'].includes(value)?'◇ ':''}${esc(text || labels[value] || value)}</span>`;
}
function notice(message, error = false) {
  $('message').hidden = !message;
  $('message').textContent = message;
  $('message').dataset.error = String(error);
}
async function api(path, signal) {
  const response = await fetch('/api/' + path, {signal});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || '조회할 수 없습니다.');
  return body;
}
async function post(path, data, button) {
  if (!overview) return null;
  if (button) button.disabled = true;
  try {
    const response = await fetch('/api/' + path, {method:'POST', headers:{'Content-Type':'application/json','X-Todo-Flow':overview.token},body:JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '요청을 처리하지 못했습니다.');
    return result;
  } catch (error) {
    notice('요청 결과를 확인해주세요. ' + error.message + ' 연결이 끊긴 경우 현황을 먼저 확인한 뒤 다시 요청하세요.', true);
    return null;
  } finally {if (button) button.disabled = false;}
}
function summaryCards() {
  if (!overview) return;
  const c = overview.counts;
  $('overview').hidden = route().view === 'completed';
  $('overview').innerHTML = `<span>선택 가능 <b>${number(c.ready)}</b></span><span>작업 중 <b>${number(c.running)}</b></span><a href="#activity">판단 대기 <b>${number(c.decisions)}</b></a>`;
  $('decisionHint').hidden = true;
}
// Active work is a continuous list. Batches bound each response, not what the user can see.
async function collect(path, params, signal, maximum = Infinity) {
  const items = new Map();
  let offset = 0, result;
  do {
    const query = new URLSearchParams(params);
    query.set('limit', String(Math.min(100, maximum - offset)));
    query.set('offset', String(offset));
    result = await api(path + '?' + query, signal);
    if (result.asOf && !params.as_of) params.as_of = result.asOf;
    for (const item of result.items) items.set(item.id, item);
    const next = result.offset + result.items.length;
    if (next <= offset) break;
    offset = next;
  } while (result.hasMore && offset < maximum);
  return {...result, items:[...items.values()], offset:0, hasMore:offset < result.total};
}
function chrome(r) {
  const configs = {
    todos:['','TODO','대기·진행 중인 작업','TODO'],
    completed:['','완료 기록','필요한 기록을 검색하세요.','완료 기록'],
    activity:['','실행 현황','담당·현재 작업·대기 이유','실행 현황'],
    track:['TRACK CONTEXT','트랙 상세','목표, 현재 상황, 남은 일과 연결된 근거를 확인합니다.','트랙 상세'],
    watch:['OPEN OBSERVATIONS','미해결 관찰과 다음 조치','기록이 해결을 대신하지 않습니다. 이유와 재확인 조건을 함께 살펴보세요.','Watch']
  };
  const config = configs[r.view] || configs.todos;
  document.body.dataset.view = r.view;
  $('eyebrow').textContent = config[0]; $('title').textContent = config[1]; $('subtitle').textContent = config[2]; $('breadcrumb').textContent = config[3];
  for (const [id,view] of [['listView','list'],['trackView','track'],['activityView','activity'],['watchView','watch']]) $(id).hidden = view === 'list' ? !['todos','completed'].includes(r.view) : r.view !== view;
  for (const link of document.querySelectorAll('[data-nav]')) {
    const active = link.dataset.nav === r.view || (r.view==='track' && link.dataset.nav===(r.query.get('from')||'todos'));
    link.classList.toggle('active',active); if(active) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current');
  }
  $('headingAction').innerHTML = r.view==='todos' ? '트랙 등록·수정은 <code>todo</code> 스킬에서' : '';
}
function trackHref(id) {
  return '#track/' + encodeURIComponent(id) + '?from=' + (route().view==='completed'?'completed':'todos');
}
function resultLinks(t) {
  if (!overview.project.github) return '<span class="subtle">로컬 결과</span>';
  const root='https://github.com/'+overview.project.github;
  return `<div class="links">${t.pr?`<a href="${esc(root)}/pull/${t.pr}" target="_blank" rel="noopener" aria-label="PR ${t.pr} 열기">PR #${t.pr} ↗</a>`:''}${t.issue?`<a href="${esc(root)}/issues/${t.issue}" target="_blank" rel="noopener" aria-label="이슈 ${t.issue} 열기">#${t.issue} ↗</a>`:''}${!t.pr&&!t.issue?'<span class="subtle">연결된 외부 결과 없음</span>':''}</div>`;
}
function stateOf(t) {
  const activity=t.activity || [], running=activity.find(w=>w.status==='running'), waiting=activity.find(w=>w.status==='waiting');
  if (t.status==='done') return ['done','완료','완료 근거와 산출물 보존'];
  if (t.control==='paused'||t.control==='pause-requested') return [t.control,labels[t.control],'재개 요청 후 이어갈 수 있습니다'];
  if (running) return running.lease < Date.now()/1000 ? ['unknown','관측 확인 필요','실행을 확인한 뒤 인수 여부를 판단합니다'] : ['running',roles[running.kind] || '작업 중',running.purpose];
  if (waiting) return ['waiting','결정 대기',waiting.purpose];
  if (t.selectable) return ['ready','선택 가능',t.control==='finished'?'요청 종료점 달성 · 남은 범위 확인':t.trigger||'선정 후 trackrun으로 실행'];
  return [t.control,labels[t.control] || t.control,activity[0]?.purpose || '작업 배정을 기다립니다'];
}
function selectionUI() {
  const ids=new Set((listing?.items||[]).map(x=>x.id));
  const hidden=[...selected.keys()].filter(x=>!ids.has(x)).length;
  $('selectionCount').textContent=selected.size ? `${number(selected.size)}개 트랙 선택${hidden?` · 현재 목록 밖 ${number(hidden)}개`:''}` : '트랙을 선택하세요';
  $('selectionHint').textContent=selected.size ? [...selected.values()].slice(0,2).join(' · ')+(selected.size>2?` 외 ${selected.size-2}개`:'') : '선정 후 세션에서 trackrun으로 실행합니다.';
  $('selection').hidden=route().view!=='todos'||!selected.size;
  $('start').disabled=!selected.size;
  $('clearSelection').disabled=!selected.size;
  const visible=(listing?.items||[]).filter(t=>t.selectable), count=visible.filter(t=>selected.has(t.id)).length;
  const all=$('selectPage'); if(all){all.checked=!!visible.length&&count===visible.length;all.indeterminate=count>0&&count<visible.length;all.disabled=!visible.length;}
}
function renderList(data, r) {
  listing=data;
  const completed=r.view==='completed';
  $('filter').hidden=completed; $('selection').hidden=completed;
  $('listLabel').textContent=completed?'완료 트랙':'활성 트랙';
  $('resultCount').textContent=`${number(data.total)}개${r.query.get('q')?' 검색 결과':''}`;
  $('trackTable').classList.toggle('archive-table',completed);
  $('columns').innerHTML=`<tr><th class="check-col">${completed?'':'<input type="checkbox" id="selectPage" aria-label="검색 결과의 선택 가능한 트랙 전체 선택">'}</th><th class="title-col">트랙 / 목표</th>${completed?'<th class="priority-col">결과</th><th class="status-col">연결된 산출물</th>':'<th class="priority-col">우선순위</th><th class="status-col">현재 상황</th><th class="reason-col">선택 판단</th>'}<th class="date-col">${completed?'완료 갱신':'최근 갱신'}</th></tr>`;
  $('rows').innerHTML=data.items.map(t=>{
    const [kind,label,reason]=stateOf(t);
    if(!t.selectable)selected.delete(t.id);
    return `<tr><td class="check-cell">${completed?'<span class="checkmark" aria-label="완료">✓</span>':`<input type="checkbox" data-select="${esc(t.id)}" aria-label="${esc(t.title)} 선택" ${selected.has(t.id)?'checked':''} ${t.selectable?'':'disabled'}>`}</td><td class="title-cell"><a class="track-title" href="${trackHref(t.id)}">${esc(t.title)}</a><span class="track-id" title="${esc(t.id)}">${esc(t.area)}</span><span class="track-goal">${esc(t.goal)}</span></td>${completed?`<td class="priority-cell">${badge('done')}</td><td class="links-cell">${resultLinks(t)}</td>`:`<td class="priority-cell"><span class="priority ${['높음','HIGH','high'].includes(t.priority)?'high':''}">${esc(t.priority)}</span></td><td class="status-cell">${badge(kind,label)}<div class="status-note">${t.activity?.[0]?.owner?esc(t.activity[0].owner.slice(-8)):'—'}</div></td><td class="reason-col"><div class="reason" title="${esc(reason)}">${esc(reason)}</div></td>`}<td class="date-cell"><time class="subtle" title="${date(t.updated,true)}">${date(t.updated)}</time></td></tr>`;
  }).join('');
  $('empty').hidden=!!data.items.length;
  $('empty').innerHTML=`<div class="empty-symbol">${completed?'✓':'▤'}</div><strong>${r.query.get('q')||r.query.get('control')?'조건에 맞는 트랙이 없습니다':completed?'아직 완료한 트랙이 없습니다':'현재 활성 TODO가 없습니다'}</strong>${completed?'완료한 트랙은 여기에 보존됩니다.':overview.counts.completed?'지난 작업은 완료 내역에서 확인할 수 있습니다.':'세션에서 todo 스킬로 요구를 전달하면 에이전트가 트랙을 등록합니다.'}${r.query.size?'<br><button class="text-button" data-reset>검색·필터 초기화</button>':''}`;
  $('archiveMore').hidden=!completed;
  $('range').textContent=`${number(data.items.length)} / ${number(data.total)}개`;
  $('moreTracks').hidden=!data.hasMore;
  summaryCards();selectionUI();
}
function renderEvents() {
  $('events').innerHTML=events.map(e=>`<div class="event"><time title="${date(e.at,true)}">${new Date(e.at*1000).toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit',hour12:false})}</time><div>${esc(eventLabels[e.type]||e.type)}<p><a href="#track/${encodeURIComponent(e.track)}">${esc(e.track)}</a>${e.summary?' · '+esc(e.summary):''}</p></div></div>`).join('')||'<p class="subtle">아직 기록된 활동이 없습니다.</p>';
  $('moreEvents').hidden=!eventCursor;
}
function renderDecisions(data) {
  if(document.activeElement?.closest('#decisions'))return;
  const expanded=new Set([...document.querySelectorAll('#decisions details[open]')].map(x=>x.dataset.decision));
  $('decisions').innerHTML=data.items.length?`<div class="section-heading"><h2>사용자 판단이 필요한 일</h2><span class="subtle">${number(data.total)}개</span></div>`+data.items.map(d=>`<details class="decision-card" data-decision="${esc(d.id)}" ${expanded.has(d.id)?'open':''}><summary>◇ ${esc(d.title)}</summary><p>${esc(d.question)}</p><textarea data-draft="${esc(d.id)}" id="answer-${esc(d.id)}" aria-label="${esc(d.title)} 결정 답변" placeholder="판단 근거와 답변을 남겨주세요.">${esc(drafts.get(d.id)||'')}</textarea><button class="primary" data-answer="${esc(d.id)}">답변 기록</button></details>`).join(''):'';
}
function renderActivity(data) {
  $('activityTotal').textContent=`${number(data.total)}개 작업`;
  $('work').innerHTML=data.items.map(w=>`<div class="work-row"><a class="work-track" href="#track/${encodeURIComponent(w.track)}">${esc(w.title)}</a><button class="work-card ${currentTask===w.id?'active':''}" data-task="${esc(w.id)}"><span class="work-meta">${badge(w.status==='running'&&w.lease<Date.now()/1000?'unknown':w.status,w.status==='running'&&w.lease<Date.now()/1000?'관측 확인 필요':undefined)}<span class="subtle">${esc(roles[w.kind]||w.kind)}</span></span><strong>${esc(w.purpose)}</strong><small>${w.owner?'워커 '+esc(w.owner.slice(-8)):'담당 미배정'} · ${date(w.updated)}</small></button></div>`).join('')||'<p class="subtle">현재 활동 중인 작업이 없습니다.</p>';
  $('activityView').classList.toggle('has-context',!!currentTask||!!overview.counts.decisions);
}
async function inspectTask(id) {
  currentTask=id;
  $('taskInspector').hidden=false;
  $('activityView').classList.add('has-context');
  for(const card of document.querySelectorAll('[data-task]'))card.classList.toggle('active',card.dataset.task===id);
  try {
    const d=await api('tasks/'+encodeURIComponent(id));if(currentTask!==id)return;
    const w=d.task;
    $('taskInspector').innerHTML=`<div class="panel"><div class="eyebrow">SELECTED WORK</div><h2>${esc(roles[w.kind]||w.kind)}</h2><p class="prose">${esc(w.purpose)}</p><dl>${[['담당',w.owner||'미배정'],['처리 상태',labels[w.status]||w.status],['마지막 관측',date(w.updated,true)],['작업권 세대',w.generation],['실행 시도',d.attempt?.id||'아직 없음'],['트랙',w.track]].map(([k,v])=>`<dt>${k}</dt><dd>${esc(v)}</dd>`).join('')}</dl>${d.result?`<div class="quiet">${esc(d.result.summary)}</div>`:''}<a class="section-link" href="#track/${encodeURIComponent(w.track)}">트랙 전체 맥락 보기 →</a></div>`;
  }catch(e){$('taskInspector').innerHTML=`<div class="notice">${esc(e.message)}</div>`;}
}
function planning(doc) {
  const decisions=(doc.decisionRequests||[]).map(d=>`<div class="condition"><div><strong>${esc(d.question)}</strong><small>담당 · ${esc(d.owner)}</small><small>결정이 여는 범위 · ${esc(d.unlocks)}</small><small>먼저 가능한 범위 · ${esc(d.beforeDecision)}</small></div></div>`).join('');
  return (doc.trigger||decisions||doc.links?.length)?`<div class="panel"><h2>착수 조건과 관계</h2>${doc.trigger?`<p class="prose">${esc(doc.trigger)}</p>`:''}${decisions}${doc.links?.length?`<p class="subtle">관련 기록 · ${esc(doc.links.join(' · '))}</p>`:''}</div>`:'';
}
function triagePanel(t) {
  if(!t.triage)return '';
  const labels={repair:'현재 트랙 재작업',existing:'기존 트랙 연결','new-track':'새 TODO 등록',watch:'조건부 Watch',resolved:'해결 확인',dismissed:'근거를 남겨 종결'};
  return `<div class="panel"><h2>랜딩 후 트리아지</h2><p>${esc(t.triage.summary)}</p><span class="badge ${t.triage.cleared?'green':'amber'}">${t.triage.cleared?'처분 완료':'재작업 필요'}</span>${t.triage.items.map(i=>`<div class="condition"><div><strong>${esc(i.observation)}</strong><small>${esc(labels[i.action])} · ${esc(i.reason)}</small><small>${esc(i.evidence)}</small>${i.target?`<a href="#track/${encodeURIComponent(i.target)}">${esc(i.target)} →</a>`:''}</div></div>`).join('')}<p class="subtle">후속 TODO는 선정 대기입니다. 등록이 실행을 뜻하지 않습니다.</p></div>`;
}
function renderTrack(t,r) {
  currentDetail=t;
  const signature=JSON.stringify(t);
  if(signature===detailSignature)return;
  detailSignature=signature;
  $('eyebrow').textContent=t.id; $('title').textContent=t.document.title; $('subtitle').textContent=t.document.goal;
  const from=r.query.get('from')==='completed'?'completed':'todos';
  const review=t.review;
  $('trackView').innerHTML=`<a class="section-link" href="${esc(savedViews[from]||'#'+from)}">← ${from==='completed'?'완료 내역':'TODO 목록'}으로</a>${t.document.documentReview==='pending-human-review'?'<div class="notice">트리아지가 자동 등록한 후속 문서입니다. 내용과 구현 범위를 검토한 뒤 선정하세요.</div>':''}<section class="document-stage"><div class="section-heading"><h2>분석·계획 문서</h2><a href="${esc(t.documentView.url)}" target="_blank" rel="noopener">문서 크게 열기 ↗</a></div><iframe title="${esc(t.document.title)} 분석·계획 문서" src="${esc(t.documentView.url)}" sandbox="allow-scripts allow-downloads" referrerpolicy="no-referrer"></iframe></section><details class="runtime-details"><summary>완료 조건 · 실행 상태 · 검증 근거</summary><div class="detail-grid"><div><div class="panel"><h2>목표와 범위</h2><p class="prose">${esc(t.document.scope)}</p></div><div class="panel"><h2>문제와 근거</h2><p class="prose">${esc(t.document.evidence)}</p></div>${triagePanel(t)}${planning(t.document)}<div class="panel"><h2>완료 조건</h2>${t.document.conditions.map((c,i)=>{const v=review?.conditions?.find(x=>x.id===c.id);return `<div class="condition"><span class="condition-num">${String(i+1).padStart(2,'0')}</span><div><strong>${esc(c.text)}</strong><small>${esc(c.id)} · ${esc(c.method)}</small>${v?.evidence?`<small>${esc(v.evidence)}</small>`:''}</div>${badge(v?.verdict||'미검증')}</div>`}).join('')}</div>${t.document.design?`<div class="panel"><h2>접근과 결정</h2><p class="prose">${esc(t.document.design)}</p></div>`:''}<div class="panel"><h2>산출물과 검증 근거</h2>${resultLinks(t)}${['verification','review','landing'].map(kind=>`<div class="evidence-block"><button class="evidence-button" data-evidence="${kind}" aria-expanded="false">${{verification:'검증 결과',review:'독립 리뷰',landing:'반영 기록'}[kind]} <span>＋</span></button><div id="evidence-${kind}" hidden></div></div>`).join('')}</div></div><aside class="inspector"><div class="panel"><h3>현재 상황</h3>${badge(t.status==='done'?'done':t.control)}<div class="quiet">${t.status==='done'?'완료 조건과 반영 결과를 보존합니다.':t.control==='finished'?'요청한 종료점에 도달했습니다. 트랙 자체의 완료 여부와 구분됩니다.':'실행 현황에서 담당 작업과 대기 조건을 확인할 수 있습니다.'}</div><div class="controls">${['active','pause-requested'].includes(t.control)?`<button data-control="pause" data-track="${t.id}">일시정지 요청</button>`:''}${['paused','pause-requested'].includes(t.control)?`<button data-control="resume" data-track="${t.id}">재개</button>`:''}${t.status!=='done'&&t.request&&t.control!=='cancelled'?`<button data-control="cancel" data-track="${t.id}">실행 취소</button>`:''}</div><a class="section-link" href="#activity">실행 현황 보기 →</a></div><div class="panel"><h3>트랙 정보</h3><dl class="metadata">${[['영역',t.document.area||'일반'],['우선순위',t.document.priority||'미지정'],['문서 revision',t.revision],['최근 갱신',date(t.updated,true)],['변경 버전',t.head||'아직 없음'],['작업 공간',t.workspace||'아직 없음']].map(([k,v])=>`<dt>${k}</dt><dd>${esc(v)}</dd>`).join('')}</dl></div><div class="panel"><h3>문서의 정본</h3><p class="subtle">등록과 수정은 에이전트의 todo 스킬에서 진행합니다. 실행 사실은 해당 기록에서 연결됩니다.</p></div></aside></div></details>`;
  for(const kind of ['verification','review','landing']) if(evidenceOpen.has(t.id+':'+kind))showEvidence(kind,true);
}
async function showEvidence(kind,force=false) {
  if(!currentDetail)return;
  const t=currentDetail, id=t.id+':'+kind, target=$('evidence-'+kind), btn=document.querySelector(`[data-evidence="${kind}"]`);
  const open=force||!evidenceOpen.has(id);if(open)evidenceOpen.add(id);else evidenceOpen.delete(id);
  target.hidden=!open;btn.setAttribute('aria-expanded',String(open));btn.querySelector('span').textContent=open?'−':'＋';
  if(!open)return;
  const key=id+':'+t.updated;
  target.innerHTML='<p class="subtle">근거를 불러오는 중…</p>';
  try {
    const d=evidenceCache.get(key)||await api(`tracks/${encodeURIComponent(t.id)}/evidence/${kind}`);
    evidenceCache.set(key,d);
    if(currentDetail?.id!==t.id||currentDetail.updated!==t.updated)return;
    $('evidence-'+kind).innerHTML=d.value?`<pre>${esc(JSON.stringify(d.value,null,2))}</pre>`:'<p class="subtle">아직 연결된 근거가 없습니다.</p>';
  }catch(e){if(currentDetail?.id===t.id)$('evidence-'+kind).innerHTML=`<p class="subtle">근거 조회 실패: ${esc(e.message)}</p>`;}
}
function renderWatch(data) {
  $('watchView').innerHTML=`<div class="watch-grid">${data.items.map(w=>`<div class="panel"><div class="section-heading">${badge(w.status)}<span class="subtle">${date(w.created)}</span></div><h2>${esc(w.body.observation)}</h2><p>지금 미룬 이유 · ${esc(w.body.reason)}</p><p>재확인 조건 · ${esc(w.body.trigger)}</p><div class="quiet">다음 조치 · ${esc(w.body.next_action)}</div><a class="section-link" href="#track/${encodeURIComponent(w.track)}">${esc(w.track)} →</a></div>`).join('')||'<div class="panel empty"><strong>열린 Watch가 없습니다</strong>조건부 관찰이 등록되면 다음 조치와 함께 표시됩니다.</div>'}</div>`;
}
async function loadRoute(poll=false) {
  const r=route();if(!['todos','completed','track','activity','watch'].includes(r.view)){go('todos',{},true);return;}
  if(!overview)return;
  const thisRoute=location.hash, changed=routeKey!==thisRoute;
  if(changed && routeKey)scrollPositions.set(routeKey,window.scrollY);
  routeKey=thisRoute;
  const version=++generation;if(controller)controller.abort();controller=new AbortController();const signal=controller.signal;
  if(changed){chrome(r);detailSignature='';if(r.view==='completed')archiveLimit=50;if(['todos','completed'].includes(r.view)){savedViews[r.view]=thisRoute;}}
  if(['todos','completed'].includes(r.view)){
    if(document.activeElement!==$('search'))$('search').value=r.query.get('q')||'';
    $('filter').value=r.query.get('control')||'all';$('sort').value=r.query.get('sort')||'updated';
    $('tableShell').setAttribute('aria-busy','true');if(changed)$('tableShell').classList.add('loading');
  }
  try {
    if(['todos','completed'].includes(r.view)){
      const params=Object.fromEntries([...r.query].filter(([k])=>['q','control','sort','as_of'].includes(k)));
      params.view=r.view==='completed'?'completed':'active';
      const data=await collect('tracks',params,signal,r.view==='completed'?archiveLimit:Infinity);if(version!==generation)return;
      if(r.view==='completed'&&!r.query.has('as_of')){r.query.set('as_of',data.asOf);history.replaceState(null,'','#completed?'+r.query);routeKey=location.hash;savedViews.completed=location.hash;}
      renderList(data,r);
    } else if(r.view==='track'){
      const t=await api('tracks/'+encodeURIComponent(r.id),signal);if(version!==generation)return;renderTrack(t,r);
    } else if(r.view==='activity'){
      const [work,decisions,feed]=await Promise.all([collect('activity',{},signal),collect('decisions',{},signal),api('events?limit=20',signal)]);
      if(version!==generation)return;
      renderActivity(work);renderDecisions(decisions);if(currentTask)inspectTask(currentTask);if(!poll||changed||events.length<=20){events=feed.items;eventCursor=feed.next;renderEvents();}
    } else {
      const data=await collect('watches',{},signal);if(version!==generation)return;renderWatch(data);
    }
    if(changed)window.scrollTo(0,scrollPositions.get(thisRoute)||0);
    $('connection').textContent='연결됨';$('connectionDot').classList.remove('stale');
  } catch(e){if(e.name==='AbortError')return;$('connection').textContent='관측 중단';$('connectionDot').classList.add('stale');notice('조회하지 못했습니다. 마지막으로 확인한 내용은 유지됩니다. '+e.message,true);}
  finally{if(version===generation){$('tableShell').setAttribute('aria-busy','false');$('tableShell').classList.remove('loading');}}
}
async function refresh(poll=false) {
  try {
    overview=await api('overview');
    const project=overview.project;
    $('project').textContent=project.display_name||project.github?.split('/')[1]||'Local workspace';
    $('projectMeta').textContent=project.github?.split('/')[0]||'로컬 프로젝트';
    $('demoNotice').hidden=!project.demo;
    $('observed').textContent=date(overview.observedAt);
    for(const [id,key]of [['navActive','active'],['navRunning','running'],['navWatch','watch']])$(id).textContent=number(overview.counts[key]);
    if(!routeKey)chrome(route());
    await loadRoute(poll);
  }catch(e){$('connection').textContent='관측 중단';$('connectionDot').classList.add('stale');notice('서버 연결을 확인해주세요. 마지막 관측 화면이 완료를 의미하지는 않습니다.',true);}
}
function queryChange(changes,replace=false){const r=route(),params=Object.fromEntries(r.query);Object.assign(params,changes);delete params.as_of;go(r.view,params,replace);}
$('search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>queryChange({q:$('search').value,offset:0},true),250);});
$('filter').onchange=()=>queryChange({control:$('filter').value,offset:0});$('sort').onchange=()=>queryChange({sort:$('sort').value,offset:0});
$('moreTracks').onclick=()=>{archiveLimit+=50;loadRoute();};
$('clearSelection').onclick=()=>{selected.clear();document.querySelectorAll('[data-select]').forEach(x=>x.checked=false);selectionUI();};
$('start').onclick=async()=>{
  const command='trackrun '+[...selected.keys()].join(' ');
  try{await navigator.clipboard.writeText(command);notice('복사했습니다. 세션에서 실행하세요: '+command);}
  catch{notice('세션에서 실행하세요: '+command);}
};
$('refresh').onclick=()=>{notice('');const r=route();if(r.view==='completed'){const q=Object.fromEntries(r.query);delete q.as_of;q.offset=0;go('completed',q,true);}else refresh();};
document.addEventListener('change',e=>{if(e.target.dataset.select){const t=listing.items.find(x=>x.id===e.target.dataset.select);if(e.target.checked)selected.set(t.id,t.title);else selected.delete(t.id);selectionUI();}else if(e.target.id==='selectPage'){for(const t of listing.items.filter(x=>x.selectable)){if(e.target.checked)selected.set(t.id,t.title);else selected.delete(t.id);}document.querySelectorAll('[data-select]').forEach(x=>x.checked=selected.has(x.dataset.select));selectionUI();}});
document.addEventListener('input',e=>{if(e.target.dataset.draft)drafts.set(e.target.dataset.draft,e.target.value);});
document.addEventListener('click',async e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.hasAttribute('data-reset'))go(route().view);
  if(b.dataset.task)inspectTask(b.dataset.task);
  if(b.dataset.evidence)showEvidence(b.dataset.evidence);
  if(b.dataset.control){const result=await post('control',{track:b.dataset.track,action:b.dataset.control},b);if(result){notice('제어 요청을 기록했습니다. 현재 상태: '+(labels[result.control]||result.control));await refresh();}}
  if(b.dataset.answer){const answer=drafts.get(b.dataset.answer)||'';if(!answer.trim()){notice('답변을 입력해주세요.');return;}const result=await post('answer',{decision:b.dataset.answer,answer},b);if(result){drafts.delete(b.dataset.answer);b.blur();notice('답변이 기록됐습니다. 실행 장치가 후속 작업을 이어갑니다.');await refresh();}}
});
$('moreEvents').onclick=async()=>{if(!eventCursor)return;try{const data=await api('events?limit=20&before='+eventCursor);events.push(...data.items);eventCursor=data.next;renderEvents();}catch(e){notice(e.message,true);}};
document.addEventListener('keydown',e=>{if(e.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&['todos','completed'].includes(route().view)){e.preventDefault();$('search').focus();}});
document.querySelector('.skip').onclick=e=>{e.preventDefault();$('content').setAttribute('tabindex','-1');$('content').focus();$('content').scrollIntoView();};
window.addEventListener('hashchange',()=>{clearTimeout(searchTimer);loadRoute();});
refresh();setInterval(()=>{if(!document.hidden)refresh(true);},5000);
