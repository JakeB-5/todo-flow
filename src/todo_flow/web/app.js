const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = (value) => Number(value || 0).toLocaleString(localeTag());
const date = (value, full = false) => value ? new Date(value * 1000).toLocaleString(localeTag(), full ? {} : {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : tr("No record");
let labels, roles, eventLabels;
function refreshLabels() {
labels = {idle:tr("Not started"),active:tr("Requested"),paused:tr("Paused"),'pause-requested':tr("Pause requested"),cancelled:tr("Cancelled"),finished:tr("Request finished"),done:tr("Done"),queued:tr("Queued"),running:tr("Working"),waiting:tr("Awaiting decision"),met:tr("Met"),unmet:tr("Unmet"),'cannot-assess':tr("Cannot assess"),open:tr("Open"),resolved:tr("Resolved"),dismissed:tr("Dismissed"),promoted:tr("Promoted to TODO")};
roles = {assess:tr("Assessment"),work:tr("Implementation / investigation"),verify:tr("Verification"),review:tr("Independent review"),land:tr("Landing"),triage:tr("Post-landing triage"),complete:tr("Completion check"),watch:tr("Watch review")};
eventLabels = {'triage.recorded':tr("Post-landing triage recorded"),'triage.todo-registered':tr("Follow-up TODO registered"),'finding.linked':tr("Finding linked"),'delivery.repair-required':tr("Repair work requested"),'worker.claimed':tr("Worker claimed a task"),'work.requested':tr("Follow-up work requested"),'work.joined':tr("Joined existing work"),'work.result':tr("Work result recorded"),'effect.confirmed':tr("External effect confirmed"),'completion.adopted':tr("Completion confirmed"),'execution.accepted':tr("Execution request accepted"),'decision.answered':tr("Decision recorded"),'attempt.error':tr("Task needs attention"),'verification.recorded':tr("Verification recorded"),'claim.recovered':tr("Work recovered"),'document.registered':tr("Track document registered")};
}
let overview = null, listing = null, currentDetail = null, currentTask = null;
let controller = null, generation = 0, searchTimer = null, eventCursor = null, events = [];
let routeKey = '', detailSignature = '';
let decisionLanguage = '';
const selected = new Map(), drafts = new Map(), evidenceCache = new Map(), evidenceOpen = new Set();
const scrollPositions = new Map();
let archiveLimit = 50;
let activityData = null, activityConnected = true, activityFocus = null;
const savedViews = {todos:'#todos', completed:'#completed', activity:'#activity'};
function priorityLabel(value) {
  const keys={high:'High',medium:'Medium',normal:'Medium',low:'Low',unspecified:'Unspecified'};
  const key=keys[String(value).toLowerCase()];
  return key ? tr(key) : value;
}

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
  if (!response.ok) throw new Error(body.error || tr("Could not load data."));
  return body;
}
async function post(path, data, button) {
  if (!overview) return null;
  if (button) button.disabled = true;
  try {
    const response = await fetch('/api/' + path, {method:'POST', headers:{'Content-Type':'application/json','X-Todo-Flow':overview.token},body:JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || tr("Could not process the request."));
    return result;
  } catch (error) {
    notice(tr("Check the request outcome. ") + error.message + tr(" If disconnected, check the current state before retrying."), true);
    return null;
  } finally {if (button) button.disabled = false;}
}
function summaryCards() {
  if (!overview) return;
  const c = overview.counts;
  $('overview').hidden = route().view === 'completed';
  $('overview').innerHTML = `<span>${tr("Ready to select")} <b>${number(c.ready)}</b></span><span>${tr("Working")} <b>${number(c.running)}</b></span><a href="#activity">${tr("Needs a decision")} <b>${number(c.decisions)}</b></a>`;
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
    todos:['','TODO',tr("Pending and active work"),'TODO'],
    completed:['',tr("Completed archive"),tr("Search past work when you need it."),tr("Completed archive")],
    activity:['',tr("Activity"),tr("Who is working, on what, and what is waiting"),tr("Activity")],
    track:[tr('Track context'),tr("Track details"),tr("Review the goal, current state, remaining work and evidence."),tr("Track details")],
    watch:[tr('Open observations'),tr("Open observations and next actions"),tr("Review why an observation is deferred and what will trigger another check."),'Watch']
  };
  const config = configs[r.view] || configs.todos;
  document.body.dataset.view = r.view;
  $('eyebrow').textContent = config[0]; $('title').textContent = config[1]; $('subtitle').textContent = config[2]; $('breadcrumb').textContent = config[3];
  for (const [id,view] of [['listView','list'],['trackView','track'],['activityView','activity'],['watchView','watch']]) $(id).hidden = view === 'list' ? !['todos','completed'].includes(r.view) : r.view !== view;
  for (const link of document.querySelectorAll('[data-nav]')) {
    const active = link.dataset.nav === r.view || (r.view==='track' && link.dataset.nav===(r.query.get('from')||'todos'));
    link.classList.toggle('active',active); if(active) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current');
  }
  $('headingAction').innerHTML = r.view==='todos' ? `${tr("Create and edit tracks with the ")}<code>todo</code>${tr(" skill")}` : '';
}
function trackHref(id) {
  return '#track/' + encodeURIComponent(id) + '?from=' + (route().view==='completed'?'completed':'todos');
}
function resultLinks(t) {
  if (!overview.project.github) return `<span class="subtle">${tr("Local results")}</span>`;
  const root='https://github.com/'+overview.project.github;
  return `<div class="links">${t.pr?`<a href="${esc(root)}/pull/${t.pr}" target="_blank" rel="noopener" aria-label="${esc(tr('Open PR {id}',{id:t.pr}))}">PR #${t.pr} ↗</a>`:''}${t.issue?`<a href="${esc(root)}/issues/${t.issue}" target="_blank" rel="noopener" aria-label="${esc(tr('Open issue {id}',{id:t.issue}))}">#${t.issue} ↗</a>`:''}${!t.pr&&!t.issue?`<span class="subtle">${tr("No external results linked")}</span>`:''}</div>`;
}
function stateOf(t) {
  const activity=t.activity || [], running=activity.find(w=>w.status==='running'), waiting=activity.find(w=>w.status==='waiting');
  if (t.status==='done') return ['done',tr("Done"),tr("Completion evidence and outputs preserved")];
  if (t.control==='paused'||t.control==='pause-requested') return [t.control,labels[t.control],tr("Work can continue after a resume request")];
  if (running) return running.lease < Date.now()/1000 ? ['unknown',tr("Check worker status"),tr("Check execution before deciding whether to reclaim")] : ['running',roles[running.kind] || tr("Working"),running.purpose];
  if (waiting) return ['waiting',tr("Awaiting decision"),waiting.purpose];
  if (t.selectable) return ['ready',tr("Ready to select"),t.control==='finished'?tr("Endpoint reached \u00b7 review remaining scope"):t.trigger||tr("Select, then run trackrun")];
  return [t.control,labels[t.control] || t.control,activity[0]?.purpose || tr("Waiting for task assignment")];
}
function selectionUI() {
  const ids=new Set((listing?.items||[]).map(x=>x.id));
  const hidden=[...selected.keys()].filter(x=>!ids.has(x)).length;
  $('selectionCount').textContent=selected.size ? tr('{count} tracks selected',{count:number(selected.size)})+(hidden?tr(' · {count} outside this list',{count:number(hidden)}):'') : tr('Select tracks');
  $('selectionHint').textContent=selected.size ? [...selected.values()].slice(0,2).join(' · ')+(selected.size>2?tr(' + {count} more',{count:number(selected.size-2)}):'') : tr('Run trackrun in your session after selecting tracks.');
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
  $('listLabel').textContent=completed?tr("Completed tracks"):tr("Active tracks");
  $('resultCount').textContent=tr(r.query.get('q')?'{count} results':'{count} tracks',{count:number(data.total)});
  $('trackTable').classList.toggle('archive-table',completed);
  $('columns').innerHTML=`<tr><th class="check-col">${completed?'':`<input type="checkbox" id="selectPage" aria-label="${tr("Select all eligible tracks in these results")}">`}</th><th class="title-col">${tr("Track / goal")}</th>${completed?`<th class="priority-col">${tr("Result")}</th><th class="status-col">${tr("Linked outputs")}</th>`:`<th class="priority-col">${tr("Priority")}</th><th class="status-col">${tr("Current state")}</th><th class="reason-col">${tr("Readiness")}</th>`}<th class="date-col">${completed?tr("Completed update"):tr("Last updated")}</th></tr>`;
  $('rows').innerHTML=data.items.map(t=>{
    const [kind,label,reason]=stateOf(t);
    if(!t.selectable)selected.delete(t.id);
    return `<tr><td class="check-cell">${completed?`<span class="checkmark" aria-label="${tr("Done")}">✓</span>`:`<input type="checkbox" data-select="${esc(t.id)}" aria-label="${esc(tr('Select {title}',{title:t.title}))}" ${selected.has(t.id)?'checked':''} ${t.selectable?'':'disabled'}>`}</td><td class="title-cell"><a class="track-title" href="${trackHref(t.id)}">${esc(t.title)}</a><span class="track-id" title="${esc(t.id)}">${esc(t.area==='General'?tr('General'):t.area)}</span><span class="track-goal">${esc(t.goal)}</span></td>${completed?`<td class="priority-cell">${badge('done')}</td><td class="links-cell">${resultLinks(t)}</td>`:`<td class="priority-cell"><span class="priority ${['높음','HIGH','high'].includes(t.priority)?'high':''}">${esc(priorityLabel(t.priority))}</span></td><td class="status-cell">${badge(kind,label)}<div class="status-note">${t.activity?.[0]?.owner?esc(t.activity[0].owner.slice(-8)):'—'}</div></td><td class="reason-col"><div class="reason" title="${esc(reason)}">${esc(reason)}</div></td>`}<td class="date-cell"><time class="subtle" title="${date(t.updated,true)}">${date(t.updated)}</time></td></tr>`;
  }).join('');
  $('empty').hidden=!!data.items.length;
  $('empty').innerHTML=`<div class="empty-symbol">${completed?'✓':'▤'}</div><strong>${r.query.get('q')||r.query.get('control')?tr("No tracks match these filters"):completed?tr("No completed tracks yet"):tr("No active TODOs")}</strong>${completed?tr("Completed tracks are preserved here."):overview.counts.completed?tr("Find past work in the completed archive."):tr("Ask your agent to register a requirement with the todo skill.")}${r.query.size?`<br><button class="text-button" data-reset>${tr("Reset search and filters")}</button>`:''}`;
  $('archiveMore').hidden=!completed;
  $('range').textContent=`${number(data.items.length)} / ${number(data.total)}`;
  $('moreTracks').hidden=!data.hasMore;
  summaryCards();selectionUI();
}
function renderEvents() {
  $('events').innerHTML=events.map(e=>`<div class="event"><time title="${date(e.at,true)}">${new Date(e.at*1000).toLocaleTimeString(localeTag(),{hour:'2-digit',minute:'2-digit',hour12:false})}</time><div>${esc(eventLabels[e.type]||e.type)}<p><a href="#track/${encodeURIComponent(e.track)}">${esc(e.track)}</a>${e.summary?' · '+esc(e.summary):''}</p></div></div>`).join('')||`<p class="subtle">${tr("No activity recorded yet.")}</p>`;
  $('moreEvents').hidden=!eventCursor;
}
function renderDecisions(data) {
  if(document.activeElement?.closest('#decisions')&&decisionLanguage===language)return;
  decisionLanguage=language;
  const expanded=new Set([...document.querySelectorAll('#decisions details[open]')].map(x=>x.dataset.decision));
  $('decisions').innerHTML=data.items.length?`<div class="section-heading"><h2>${tr("Your decisions")}</h2><span class="subtle">${number(data.total)}</span></div>`+data.items.map(d=>`<details class="decision-card" data-decision="${esc(d.id)}" ${expanded.has(d.id)?'open':''}><summary>◇ ${esc(d.title)}</summary><p>${esc(d.question)}</p><textarea data-draft="${esc(d.id)}" id="answer-${esc(d.id)}" aria-label="${esc(d.title)}${tr(" \u2014 decision answer")}" placeholder="${tr("Write your answer and reasoning.")}">${esc(drafts.get(d.id)||'')}</textarea><button class="primary" data-answer="${esc(d.id)}">${tr("Record answer")}</button></details>`).join(''):'';
}
function verificationLabel(t) {
  if(t.verificationCancelled===1||t.verificationComplete===0)return tr("Inconclusive");
  if(t.verificationOutcome==='failed'&&t.verificationOk===0)return tr("Failed");
  if(t.verificationOk===1&&(t.verificationOutcome==null||t.verificationOutcome==='passed'))return tr("Passed");
  if(t.verificationOutcome!=null||t.verificationOk!=null)return tr("Inconclusive");
  return tr("No record");
}
function renderActivity(data) {
  activityData=data;
  const r=route(), track=r.query.get('track');
  $('activityTotal').textContent=tr('{tracks} tracks / {tasks} active tasks',{tracks:number(data.total),tasks:number(data.taskTotal)});
  const html=data.items.map(t=>{
    const w=t.current, uncertain=!activityConnected||t.uncertain>0;
    const state=uncertain?'unknown':t.status;
    const recent=verificationLabel(t);
    return `<section class="activity-track"><a class="work-track" href="#track/${encodeURIComponent(t.id)}">${esc(t.title)}</a>
      <div class="work-meta">${badge(state,uncertain?tr("Execution needs checking"):undefined)} ${['paused','pause-requested'].includes(t.control)?badge(t.control):''}
      <span>${tr('{count} active tasks',{count:number(t.taskCount)})}</span></div>
      <p><b>${tr("Current task")}</b> · ${w?esc(roles[w.kind]||tr("Task")):tr("No active task")}</p>
      <p>${w?esc(taskDescription(w)):tr("Review the open decision.")}</p>
      <p class="subtle">${tr('{running} running · {queued} awaiting assignment · {waiting} waiting',{running:number(t.running),queued:number(t.queued),waiting:number(t.waiting)})}
      · ${tr('{count} open decisions',{count:number(t.decisions)})}</p>
      <p class="subtle">${tr("Recent verification")} · ${recent}${t.verificationAt?' · '+date(t.verificationAt):''}</p>
      <button data-activity-track="${esc(t.id)}" aria-expanded="${track===t.id}" aria-controls="activityTasks">${track===t.id?tr("Close track tasks"):tr("Tasks and evidence")}</button></section>`;
  }).join('')||`<p class="subtle">${tr("No active work right now.")}</p>`;
  if($('work').innerHTML!==html)$('work').innerHTML=html;
  $('activityPages').innerHTML=activityPages(data,'offset');
  $('activityWarning').hidden=activityConnected;
  $('activityView').classList.toggle('has-context',!!track||!!currentTask||!!overview.counts.decisions);
}
function taskDescription(w) {
  if(w.intent==='verification-repair')return tr("Fix the recorded verification failure.");
  if(w.intent==='verification-diagnosis')return tr("Diagnose the check and reverify the same candidate after recovery.");
  const descriptions={
    assess:tr("Assess the track against its conditions."),
    work:tr("Implement or investigate the track."),
    verify:tr("Check the candidate against required verification."),
    review:tr("Independently review the candidate."),
    land:tr("Integrate the verified candidate."),
    triage:tr("Assess findings after landing."),
    complete:tr("Check completion evidence."),
    watch:tr("Reassess the recorded observation.")
  };
  return (descriptions[w.kind]||tr("Task"))+' '+tr("Detailed intent unavailable; open the original instructions.");
}
function activityPages(data,key) {
  return `<button data-activity-page="${Math.max(0,data.offset-data.limit)}" data-page-key="${key}" ${data.offset===0?'disabled':''}>${tr("Previous page")}</button>
    <span>${number(data.total?data.offset+1:0)}–${number(data.offset+data.items.length)} / ${number(data.total)}</span>
    <button data-activity-page="${data.offset+data.limit}" data-page-key="${key}" ${!data.hasMore?'disabled':''}>${tr("Next page")}</button>`;
}
function renderActivityTasks(data,track) {
  const html=`<div class="panel"><h2>${tr("Track tasks")} · ${esc(track)}</h2>`+data.items.map(w=>{
    const uncertain=w.status==='running'&&(!activityConnected||!w.lease||w.lease<=Date.now()/1000);
    return `<button class="activity-task" data-task="${esc(w.id)}" aria-expanded="${currentTask===w.id}" aria-controls="taskInspector">
      ${badge(uncertain?'unknown':w.status,uncertain?tr("Execution needs checking"):undefined)}
      <strong>${esc(roles[w.kind]||tr("Task"))}</strong><span>${esc(taskDescription(w))}</span>
      <small>${esc(w.id)} · ${tr("Last observed")} ${date(w.updated)}</small></button>`;
  }).join('')+`<div class="pagination">${activityPages(data,'tasks_offset')}</div></div>`;
  $('activityTasks').hidden=false;
  if($('activityTasks').innerHTML!==html)$('activityTasks').innerHTML=html;
}
function activityDisconnected() {
  activityConnected=false;
  if(route().view==='activity'&&activityData)renderActivity(activityData);
}
function launchPanel(launch) {
  const evidence=launch?.evidence||'missing';
  const summary=evidence==='available'?(launch?.summaries?.[language]||launch?.summaries?.en):null;
  const state=evidence==='available'?tr("Recorded"):evidence==='unreadable'?tr("Unreadable launch evidence"):tr("No launch evidence");
  const rows=[[tr("Evidence status"),state]];
  if(summary)rows.push([tr("Requested launcher"),summary.requested],[tr("Selected backend"),summary.backend],[tr("Selection reason"),summary.reason],[tr("Launch status"),summary.status]);
  const record=launch?.record;
  if(evidence==='available'&&record?.execution_mode==='orca-native') {
    for(const [label,value] of [["Orca workspace",record.worktree],["Codex session",record.session],["Codex turn",record.turn],["Terminal handle",record.terminal?.handle]]) {
      if(typeof value==='string'&&value)rows.push([tr(label),value]);
    }
  }
  return `<section class="evidence-block"><h3>${esc(tr("Execution evidence"))}</h3><dl>${rows.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl></section>`;
}
async function inspectTask(id, focus=false) {
  currentTask=id;
  $('taskInspector').hidden=false;
  $('activityView').classList.add('has-context');
  for(const card of document.querySelectorAll('[data-task]'))card.classList.toggle('active',card.dataset.task===id);
  try {
    const d=await api('tasks/'+encodeURIComponent(id));if(currentTask!==id)return;
    const w=d.task;
    const html=`<div class="panel"><button data-close-task>${tr("Close task details")}</button><div class="eyebrow">${tr("Selected work")}</div><h2>${esc(roles[w.kind]||w.kind)}</h2><p class="prose">${esc(w.purpose)}</p><dl>${[[tr("Owner"),w.owner||tr("Unassigned")],[tr("Status"),w.status==='running'&&(!w.lease||w.lease<=Date.now()/1000)?tr("Execution needs checking"):labels[w.status]||w.status],[tr("Last observed"),date(w.updated,true)],[tr("Claim generation"),w.generation],[tr("Attempt"),d.attempt?.id||tr("Not yet")],[tr("Track"),w.track]].map(([k,v])=>`<dt>${k}</dt><dd>${esc(v)}</dd>`).join('')}</dl>${w.error?`<section class="evidence-block"><h3>${tr("Task needs attention")}</h3><pre>${esc(w.error)}</pre></section>`:''}${launchPanel(d.launch)}<a class="section-link" href="#track/${encodeURIComponent(w.track)}?from=activity&amp;evidence=verification&amp;return=${encodeURIComponent(savedViews.activity)}">${tr("Verification results")} →</a>${d.result?`<div class="quiet">${esc(d.result.summary)}</div>`:''}<a class="section-link" href="#track/${encodeURIComponent(w.track)}">${tr("View full track context")} →</a></div>`;
    if($('taskInspector').innerHTML!==html)$('taskInspector').innerHTML=html;
    if(focus)$('taskInspector').focus();
  }catch(e){if(currentTask!==id)return;$('taskInspector').innerHTML=`<div class="notice">${esc(e.message)}</div>`;}
}
function planning(doc) {
  const decisions=(doc.decisionRequests||[]).map(d=>`<div class="condition"><div><strong>${esc(d.question)}</strong><small>${tr("Owner")} · ${esc(d.owner)}</small><small>${tr("Scope this decision unlocks")} · ${esc(d.unlocks)}</small><small>${tr("Work that can proceed now")} · ${esc(d.beforeDecision)}</small></div></div>`).join('');
  return (doc.trigger||decisions||doc.links?.length)?`<div class="panel"><h2>${tr("Prerequisites and relationships")}</h2>${doc.trigger?`<p class="prose">${esc(doc.trigger)}</p>`:''}${decisions}${doc.links?.length?`<p class="subtle">${tr("Related records")} · ${esc(doc.links.join(' · '))}</p>`:''}</div>`:'';
}
function triagePanel(t) {
  if(!t.triage)return '';
  const labels={repair:tr("Repair this track"),existing:tr("Link existing track"),'new-track':tr("Register new TODO"),watch:tr("Conditional watch"),resolved:tr("Confirmed resolved"),dismissed:tr("Dismissed with evidence")};
  return `<div class="panel"><h2>${tr("Post-landing triage")}</h2><p>${esc(t.triage.summary)}</p><span class="badge ${t.triage.cleared?'green':'amber'}">${t.triage.cleared?tr("Triage cleared"):tr("Repair needed")}</span>${t.triage.items.map(i=>`<div class="condition"><div><strong>${esc(i.observation)}</strong><small>${esc(labels[i.action])} · ${esc(i.reason)}</small><small>${esc(i.evidence)}</small>${i.target?`<a href="#track/${encodeURIComponent(i.target)}">${esc(i.target)} →</a>`:''}</div></div>`).join('')}<p class="subtle">${tr("Follow-up TODOs await selection. Registration does not start execution.")}</p></div>`;
}
function renderTrack(t,r) {
  currentDetail=t;
  const back=r.query.get('return');
  if(back==='#activity'||back?.startsWith('#activity?'))savedViews.activity=back;
  const signature=JSON.stringify(t);
  if(signature===detailSignature)return;
  detailSignature=signature;
  $('eyebrow').textContent=t.id; $('title').textContent=t.document.title; $('subtitle').textContent=t.document.goal;
  const from=['completed','activity'].includes(r.query.get('from'))?r.query.get('from'):'todos';
  const review=t.review;
  $('trackView').innerHTML=`<a class="section-link" href="${esc(savedViews[from]||'#'+from)}">← ${from==='completed'?tr("Completed archive"):from==='activity'?tr("Activity"):tr("TODO list")}</a>${t.document.documentReview==='pending-human-review'?`<div class="notice">${tr("Triage registered this follow-up document. Review its contents and scope before selection.")}</div>`:''}<section class="document-stage"><div class="section-heading"><h2>${tr("Analysis and plan")}</h2><a href="${esc(t.documentView.url)}" target="_blank" rel="noopener">${tr("Open document")} ↗</a></div><iframe title="${esc(t.document.title)} ${tr("Analysis and plan")}" src="${esc(t.documentView.url)}" sandbox="allow-scripts allow-downloads" referrerpolicy="no-referrer"></iframe></section><details class="runtime-details"><summary>${tr("Conditions \u00b7 execution \u00b7 evidence")}</summary><div class="detail-grid"><div><div class="panel"><h2>${tr("Goal and scope")}</h2><p class="prose">${esc(t.document.scope)}</p></div><div class="panel"><h2>${tr("Problem and evidence")}</h2><p class="prose">${esc(t.document.evidence)}</p></div>${triagePanel(t)}${planning(t.document)}<div class="panel"><h2>${tr("Acceptance conditions")}</h2>${t.document.conditions.map((c,i)=>{const v=review?.conditions?.find(x=>x.id===c.id);return `<div class="condition"><span class="condition-num">${String(i+1).padStart(2,'0')}</span><div><strong>${esc(c.text)}</strong><small>${esc(c.id)} · ${esc(c.method)}</small>${v?.evidence?`<small>${esc(v.evidence)}</small>`:''}</div>${badge(v?.verdict||tr("Not verified"))}</div>`}).join('')}</div>${t.document.design?`<div class="panel"><h2>${tr("Approach and decisions")}</h2><p class="prose">${esc(t.document.design)}</p></div>`:''}<div class="panel"><h2>${tr("Outputs and evidence")}</h2>${resultLinks(t)}${['verification','review','landing'].map(kind=>`<div class="evidence-block"><button class="evidence-button" data-evidence="${kind}" aria-expanded="false">${{verification:tr("Verification results"),review:tr("Independent review"),landing:tr("Landing record")}[kind]} <span>＋</span></button><div id="evidence-${kind}" hidden></div></div>`).join('')}</div></div><aside class="inspector"><div class="panel"><h3>${tr("Current state")}</h3>${badge(t.status==='done'?'done':t.control)}<div class="quiet">${t.status==='done'?tr("Acceptance conditions and delivery results are preserved."):t.control==='finished'?tr("The requested endpoint was reached. This does not necessarily mean the track is complete."):tr("See Activity for assigned work and waiting conditions.")}</div><div class="controls">${['active','pause-requested'].includes(t.control)?`<button data-control="pause" data-track="${t.id}">${tr("Request pause")}</button>`:''}${['paused','pause-requested'].includes(t.control)?`<button data-control="resume" data-track="${t.id}">${tr("Resume")}</button>`:''}${t.status!=='done'&&t.request&&t.control!=='cancelled'?`<button data-control="cancel" data-track="${t.id}">${tr("Cancelled")}</button>`:''}</div><a class="section-link" href="#activity">${tr("View activity")} →</a></div><div class="panel"><h3>${tr("Track information")}</h3><dl class="metadata">${[[tr("Area"),t.document.area||tr("General")],[tr("Priority"),t.document.priority||tr("Unspecified")],[tr("Document revision"),t.revision],[tr("Last updated"),date(t.updated,true)],[tr("Change revision"),t.head||tr("Not yet")],[tr("Workspace"),t.workspace||tr("Not yet")]].map(([k,v])=>`<dt>${k}</dt><dd>${esc(v)}</dd>`).join('')}</dl></div><div class="panel"><h3>${tr("Canonical document")}</h3><p class="subtle">${tr("Create and revise documents with the agent's todo skill. Execution facts are linked from their records.")}</p></div></aside></div></details>`;
  if(r.query.get('evidence')==='verification') {
    document.querySelector('.runtime-details').open=true;
    evidenceOpen.add(t.id+':verification');
  }
  for(const kind of ['verification','review','landing']) if(evidenceOpen.has(t.id+':'+kind))showEvidence(kind,true);
}
async function showEvidence(kind,force=false) {
  if(!currentDetail)return;
  const t=currentDetail, id=t.id+':'+kind, target=$('evidence-'+kind), btn=document.querySelector(`[data-evidence="${kind}"]`);
  const open=force||!evidenceOpen.has(id);if(open)evidenceOpen.add(id);else evidenceOpen.delete(id);
  target.hidden=!open;btn.setAttribute('aria-expanded',String(open));btn.querySelector('span').textContent=open?'−':'＋';
  if(!open)return;
  const key=id+':'+t.updated;
  target.innerHTML=`<p class="subtle">${tr("Loading evidence\u2026")}</p>`;
  try {
    const d=evidenceCache.get(key)||await api(`tracks/${encodeURIComponent(t.id)}/evidence/${kind}`);
    evidenceCache.set(key,d);
    if(currentDetail?.id!==t.id||currentDetail.updated!==t.updated)return;
    $('evidence-'+kind).innerHTML=d.value?`<pre>${esc(JSON.stringify(d.value,null,2))}</pre>`:`<p class="subtle">${tr("No evidence linked yet.")}</p>`;
  }catch(e){if(currentDetail?.id===t.id)$('evidence-'+kind).innerHTML=`<p class="subtle">${tr("Could not load evidence: ")}${esc(e.message)}</p>`;}
}
function renderWatch(data) {
  $('watchView').innerHTML=`<div class="watch-grid">${data.items.map(w=>`<div class="panel"><div class="section-heading">${badge(w.status)}<span class="subtle">${date(w.created)}</span></div><h2>${esc(w.body.observation)}</h2><p>${tr("Why deferred")} · ${esc(w.body.reason)}</p><p>${tr("Recheck trigger")} · ${esc(w.body.trigger)}</p><div class="quiet">${tr("Next action")} · ${esc(w.body.next_action)}</div><a class="section-link" href="#track/${encodeURIComponent(w.track)}">${esc(w.track)} →</a></div>`).join('')||`<div class="panel empty"><strong>${tr("No open watches")}</strong>${tr("Conditional observations appear here with their next actions.")}</div>`}</div>`;
}
async function loadRoute(poll=false) {
  const r=route();if(!['todos','completed','track','activity','watch'].includes(r.view)){go('todos',{},true);return;}
  if(!overview)return;
  const thisRoute=location.hash, changed=routeKey!==thisRoute;
  if(changed && routeKey)scrollPositions.set(routeKey,window.scrollY);
  routeKey=thisRoute;
  if(r.view!=='activity')currentTask=null;
  const version=++generation;if(controller)controller.abort();controller=new AbortController();const signal=controller.signal;
  if(changed){chrome(r);detailSignature='';if(r.view==='completed')archiveLimit=50;if(['todos','completed','activity'].includes(r.view)){savedViews[r.view]=thisRoute;}}
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
      const track=r.query.get('track'), task=r.query.get('task');
      currentTask=task;
      $('taskInspector').hidden=!task;
      if(changed&&task)$('taskInspector').innerHTML='';
      $('activityTasks').hidden=!track;
      const decisionQuery=new URLSearchParams({limit:25,offset:r.query.get('decisions_offset')||0});
      if(track)decisionQuery.set('track',track);
      const [work,decisions,feed,tasks]=await Promise.all([
        api('activity?limit=25&offset='+(r.query.get('offset')||0),signal),
        api('decisions?'+decisionQuery,signal),
        api('events?limit=20',signal),
        track?api('activity/tasks?'+new URLSearchParams({track,limit:10,offset:r.query.get('tasks_offset')||0}),signal):null
      ]);
      if(version!==generation)return;
      activityConnected=true;
      renderActivity(work);renderDecisions(decisions);
      $('decisionPages').innerHTML=activityPages(decisions,'decisions_offset');
      $('decisionPages').hidden=!decisions.total;
      if(tasks)renderActivityTasks(tasks,track);
      if(task)await inspectTask(task,activityFocus==='inspector');
      if(version!==generation)return;
      if(activityFocus&&activityFocus!=='inspector') {
        const button=[...document.querySelectorAll('[data-task],[data-activity-track]')].find(b=>b.dataset.task===activityFocus||b.dataset.activityTrack===activityFocus);
        button?.focus();
      }
      activityFocus=null;
      if(!poll||changed||events.length<=20){events=feed.items;eventCursor=feed.next;renderEvents();}
    } else {
      const data=await collect('watches',{},signal);if(version!==generation)return;renderWatch(data);
    }
    if(changed)window.scrollTo(0,scrollPositions.get(thisRoute)||0);
    $('connection').textContent=tr("Connected");$('connectionDot').classList.remove('stale');
  } catch(e){if(e.name==='AbortError')return;activityDisconnected();$('connection').textContent=tr("Connection lost");$('connectionDot').classList.add('stale');notice(tr("Could not refresh. The last observed state is preserved. ")+e.message,true);}
  finally{if(version===generation){$('tableShell').setAttribute('aria-busy','false');$('tableShell').classList.remove('loading');}}
}
async function refresh(poll=false) {
  try {
    overview=await api('overview');
    const project=overview.project;
    useProjectLanguage(project);
    $('project').textContent=project.display_name||project.github?.split('/')[1]||'Local workspace';
    $('projectMeta').textContent=project.github?.split('/')[0]||tr("Local project");
    $('demoNotice').hidden=!project.demo;
    $('observed').textContent=date(overview.observedAt);
    for(const [id,key]of [['navActive','active'],['navRunning','running'],['navWatch','watch']])$(id).textContent=number(overview.counts[key]);
    if(!routeKey)chrome(route());
    await loadRoute(poll);
  }catch(e){activityDisconnected();$('connection').textContent=tr("Connection lost");$('connectionDot').classList.add('stale');notice(tr("Check the server connection. The last observed screen does not mean work is complete."),true);}
}
function queryChange(changes,replace=false){const r=route(),params=Object.fromEntries(r.query);Object.assign(params,changes);delete params.as_of;go(r.view,params,replace);}
$('search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>queryChange({q:$('search').value,offset:0},true),250);});
$('filter').onchange=()=>queryChange({control:$('filter').value,offset:0});$('sort').onchange=()=>queryChange({sort:$('sort').value,offset:0});
$('moreTracks').onclick=()=>{archiveLimit+=50;loadRoute();};
$('clearSelection').onclick=()=>{selected.clear();document.querySelectorAll('[data-select]').forEach(x=>x.checked=false);selectionUI();};
$('start').onclick=async()=>{
  const command='trackrun '+[...selected.keys()].join(' ');
  try{await navigator.clipboard.writeText(command);notice(tr("Copied. Run in your session: ")+command);}
  catch{notice(tr("Run in your session: ")+command);}
};
$('refresh').onclick=()=>{notice('');const r=route();if(r.view==='completed'){const q=Object.fromEntries(r.query);delete q.as_of;q.offset=0;go('completed',q,true);}else refresh();};
document.addEventListener('change',e=>{if(e.target.dataset.select){const t=listing.items.find(x=>x.id===e.target.dataset.select);if(e.target.checked)selected.set(t.id,t.title);else selected.delete(t.id);selectionUI();}else if(e.target.id==='selectPage'){for(const t of listing.items.filter(x=>x.selectable)){if(e.target.checked)selected.set(t.id,t.title);else selected.delete(t.id);}document.querySelectorAll('[data-select]').forEach(x=>x.checked=selected.has(x.dataset.select));selectionUI();}});
document.addEventListener('input',e=>{if(e.target.dataset.draft)drafts.set(e.target.dataset.draft,e.target.value);});
document.addEventListener('click',async e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.hasAttribute('data-reset'))go(route().view);
  if(b.dataset.task){activityFocus='inspector';queryChange({task:b.dataset.task});}
  if(b.hasAttribute('data-close-task')){activityFocus=currentTask;currentTask=null;queryChange({task:null});}
  if(b.dataset.activityTrack){
    const closing=route().query.get('track')===b.dataset.activityTrack;
    activityFocus=b.dataset.activityTrack;
    currentTask=null;
    queryChange({track:closing?null:b.dataset.activityTrack,task:null,tasks_offset:0,decisions_offset:0});
  }
  if(b.hasAttribute('data-activity-page'))queryChange({[b.dataset.pageKey]:b.dataset.activityPage});
  if(b.dataset.evidence)showEvidence(b.dataset.evidence);
  if(b.dataset.control){const result=await post('control',{track:b.dataset.track,action:b.dataset.control},b);if(result){notice(tr("Control request recorded. Current state: ")+(labels[result.control]||result.control));await refresh();}}
  if(b.dataset.answer){const answer=drafts.get(b.dataset.answer)||'';if(!answer.trim()){notice(tr("Enter an answer."));return;}const result=await post('answer',{decision:b.dataset.answer,answer},b);if(result){drafts.delete(b.dataset.answer);b.blur();notice(tr("Answer recorded. A running driver can continue the follow-up work."));await refresh();}}
});
$('moreEvents').onclick=async()=>{if(!eventCursor)return;try{const data=await api('events?limit=20&before='+eventCursor);events.push(...data.items);eventCursor=data.next;renderEvents();}catch(e){notice(e.message,true);}};
document.addEventListener('keydown',e=>{if(e.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&['todos','completed'].includes(route().view)){e.preventDefault();$('search').focus();}});
document.querySelector('.skip').onclick=e=>{e.preventDefault();$('content').setAttribute('tabindex','-1');$('content').focus();$('content').scrollIntoView();};
window.addEventListener('hashchange',()=>{clearTimeout(searchTimer);loadRoute();});
$('language').onchange=async()=>{
  const y=window.scrollY;
  const runtimeOpen=document.querySelector('.runtime-details')?.open;
  saveDisplayLanguage($('language').value);
  detailSignature='';
  chrome(route());
  renderEvents();
  await refresh();
  if(runtimeOpen&&document.querySelector('.runtime-details'))document.querySelector('.runtime-details').open=true;
  window.scrollTo(0,y);
};
applyLanguage('en');
refresh();setInterval(()=>{if(!document.hidden)refresh(true);},5000);
