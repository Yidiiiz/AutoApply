import {api} from "./api.js";
import {initializePairing} from "./pairing.js";

const element = id => document.getElementById(id);
const tabId=Number(new URL(location.href).searchParams.get('tabId'))||undefined;
function selectTab(name){
  for(const tab of document.querySelectorAll('.tabs:not(.subtabs) [role="tab"]')){const selected=tab.id==='tab-'+name;tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;element(tab.getAttribute('aria-controls')).hidden=!selected;}
}
for(const name of ['fill','answers','chat'])element('tab-'+name).onclick=()=>selectTab(name);
function selectChatTab(name){for(const tab of document.querySelectorAll('.subtabs [role="tab"]')){const selected=tab.id==='subtab-'+name;tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;element(tab.getAttribute('aria-controls')).hidden=!selected;}}
for(const name of ['chat','helper'])element('subtab-'+name).onclick=()=>selectChatTab(name);
for(const tab of document.querySelectorAll('[role="tab"]'))tab.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const tabs=Array.from(tab.parentElement.querySelectorAll('[role="tab"]'));const index=event.key==='Home'?0:event.key==='End'?tabs.length-1:(tabs.indexOf(tab)+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[index].click();tabs[index].focus();};
element('close').onclick=()=>send({type:'sidebar-control',state:'closed'});
element('minimize').onclick=()=>send({type:'sidebar-control',state:'minimized'});
element('undo-applied').onclick=()=>perform({type:'undo-applied'}).catch(showError);
let skillsBusy=false, skillPlan, skillPlanKey, skillPlanPromise, selectedSkillNames=new Set(), chipStatuses={}, profileRevision="", mainBusy=false;
const skillOverrides=new Map();
let result, source, histories = (await chrome.storage.session.get("answerHistory")).answerHistory || {};
let messages = [], aiBusy = false, chatBusy = false, downloadURL;
const send = message => chrome.runtime.sendMessage({...message,...(tabId?{tabId}:{})});
const question = () => element("question").value.trim();
const applicationKey = () => String(result?.matched?.application_id || result?.url?.replace(/\/apply(?:\/.*)?$/i,"").split(/[?#]/)[0] || tabId || "page");
const historyKey = () => applicationKey()+":"+question();
const history = () => histories[historyKey()] || {versions: [], index: 0};
function openHelper(field){source={...field,tabId:result.tabId,url:result.url};element("question").value=field.label;selectTab("chat");selectChatTab("helper");renderAnswer();element("question").focus();}
function node(tag, text) { const item = document.createElement(tag); item.textContent = text; return item; }
function button(text, action) { const item = node("button", text); item.type = "button"; item.onclick = action; return item; }
async function connection() {
  try { const value = await api("/ext/ping"); if(profileRevision && profileRevision!==value.profile_revision){skillPlanKey=undefined;skillPlan=undefined;}profileRevision=value.profile_revision || ""; element("connection").textContent = ""; element("connection").title = `Connected — ${value.profile_name || value.name}`; element("connection").setAttribute("aria-label", element("connection").title); element("connection").classList.add("connected"); }
  catch (error) { element("connection").textContent = error.message; element("connection").title = "Connection error"; element("connection").setAttribute("aria-label", error.message); element("connection").classList.remove("connected"); }
}
async function perform(message) {
  const response = await send(message);
  if (response?.error) throw new Error(response.error);
  render(response);
  return response;
}
function inputFor(field) {
  let input;
  if (["select", "radio", "combobox", "checkbox"].includes(field.kind)) {
    input = document.createElement("select");
    const options = field.options || [];
    input.multiple = field.multiple || field.kind === "checkbox" && options.length > 1;
    if (!input.multiple) { const blank = node("option", options.length ? "Choose an answer" : "No options available — check the page"); blank.value = ""; input.append(blank); }
    for (const value of options) { const option = node("option", value); option.value = value; input.append(option); }
    input.disabled = !options.length;
  } else {
    input = document.createElement(field.kind === "textarea" ? "textarea" : "input");
    if (input.tagName === "INPUT") input.type = ["email", "tel", "url", "date", "number"].includes(field.kind) ? field.kind : "text";
    if (field.max_length !== null && field.max_length !== undefined) input.maxLength = field.max_length;
  }
  input.setAttribute("aria-label", field.label);
  return input;
}
function managedSkillSearch(field) { return /^type to add skills\s*[.:?]?$/i.test(field.label?.trim() || "") && (field.kind === "combobox" || Boolean(result?.skills_available) && ["text", "search"].includes(field.kind)); }
function prompts() {
  element("needs-input").replaceChildren();
  const pending=(result?.pending || []).filter(field=>!managedSkillSearch(field));
  element("needs-input-heading").hidden=!pending.length;
  for (const field of pending) {
    const row = node("div", ""); row.className = "prompt";
    const actions = node("div", ""); actions.className = "prompt-actions";
    row.append(node("p", field.label));
    if (field.kind === "file") { row.append(node("p", "Check the upload on the page, or attach the file manually.")); }
    else {
      const input = inputFor(field), status = node("p", "");
      row.append(input);
      const save = button("Save", async event => {
        const value = input.multiple ? Array.from(input.selectedOptions, option => option.value) : input.value;
        if (input.value === "") { status.textContent = "Enter an answer or choose Skip."; return; }
        event.target.disabled = true;
        try { await perform({type: "fill-answer", key: field.key, value, remember: true}); }
        catch (error) { status.textContent = error.message; event.target.disabled = false; }
      });
      save.disabled = input.disabled;
      actions.append(save);

      if (['select','radio','checkbox','combobox'].includes(field.kind) && !field.options?.length) {
        actions.append(button('Load options', async event => {
          event.target.disabled = true;
          try { await perform({type:'load-options',key:field.key}); }
          catch (error) { status.textContent=error.message;event.target.disabled=false; }
        }));
      }
      row.append(actions, status);
    }
    actions.append(button("Skip", () => perform({type: "skip-answer", key: field.key}).catch(showError)));
    if (!actions.parentElement) row.append(actions);
    element("needs-input").append(row);
  }

  element("cover-questions").replaceChildren();
  if (result?.coverQuestions?.length) {
    element("cover-questions").append(node("h3", "Cover letter questions (blank allowed)"));
    const inputs = result.coverQuestions.map(label => {
      const input = inputFor({label, kind: "textarea"});
      input.value = result.coverAnswers?.find(item => item.question === label)?.answer || "";
      element("cover-questions").append(node("p", label), input); return {question: label, input};
    });
    element("cover-questions").append(button("Generate with answers", () => perform({type: "cover-letter", answers: inputs.map(item => ({question: item.question, answer: item.input.value}))}).catch(showError)));
  }
}
function completedAnswers(){
  const container=element('completed-answers');container.replaceChildren();
  const generated=Object.entries(histories).filter(([key])=>key.startsWith(applicationKey()+':')).map(([key,h])=>({key:'ai:'+key,label:key.slice(applicationKey().length+1),kind:'textarea',value:h.versions[h.index]?.answer||'',source:'AI',generated:true}));
  for(const field of [...(result?.completed||[]),...(result?.aiAnswers||[]).filter(item=>!result?.completed?.some(f=>f.label===item.label&&f.source==='AI')).map(item=>({...item,generated:true,key:'ai:'+applicationKey()+':'+item.label})),...generated.filter(item=>!result?.completed?.some(f=>f.label===item.label&&f.source==='AI')&&!result?.aiAnswers?.some(f=>f.label===item.label))]){
    if(managedSkillSearch(field)) continue;
    const card=node('div','');card.className='answer-card';
    const label=node('p',field.label);label.className='field-label';
    const value=node('p',Array.isArray(field.value)?field.value.join(', '):String(field.value??''));value.className='answer-value';
    const source=node('small',field.source==='already-attached'?'Already attached':/^profile/i.test(field.source||'')?'Profile':/fuzzy.*memory|fuzzy.*saved/i.test(field.source||'')?'Saved (similar)':/memory|saved|user_provided/i.test(field.source||'')?'Saved':field.source==='you'?'You':field.source==='file'?'Upload':'AI');
    source.className='source-pill';
    const details=node('div','');details.className='answer-details';details.append(label,value);
    card.append(details,source);
    if(field.kind!=='file'){
      const actions=node('div','');actions.className='actions';
      const edit=button('✎',()=>{
        const input=inputFor(field);if(input.multiple){for(const option of input.options)option.selected=(Array.isArray(field.value)?field.value:[field.value]).includes(option.value);}else input.value=String(field.value??'');
        const status=node('p','');
        const save=button('Save',async()=>{save.disabled=true;try{const value=input.multiple?Array.from(input.selectedOptions,o=>o.value):input.value;if(field.generated){const k=field.key.slice(3);const h=histories[k] ||= {versions:[{answer:field.value}],index:0};h.versions[h.index]={...h.versions[h.index],answer:value,words:value.split(/\s+/).filter(Boolean).length,chars:value.length};await chrome.storage.session.set({answerHistory:histories});const stored=result?.aiAnswers?.find(item=>item.label===field.label);if(stored){stored.value=value;const {applicationAnswers={}}=await chrome.storage.session.get("applicationAnswers");applicationAnswers[applicationKey()]=result.aiAnswers;await chrome.storage.session.set({applicationAnswers});}completedAnswers();}else await perform({type:'fill-answer',key:field.key,value,remember:field.kind!=='textarea',source:field.source==='AI'?'AI':'you'});}catch(error){status.textContent=error.message;save.disabled=false;}});
        actions.className='inline-actions';card.classList.add('editing');actions.replaceChildren(save,button('Cancel',completedAnswers));details.replaceChildren(label,input);card.replaceChildren(details,actions,status);input.focus();
      });edit.setAttribute('aria-label','Edit');edit.title='Edit answer';edit.className='edit-answer icon-button';actions.append(edit);card.append(actions);
    }
    container.append(card);
  }
  if(!container.children.length){const empty=node('p','Answers you fill or save will appear here.');empty.className='empty';container.append(empty);}
}
let savedTimer;
function render(value) {
  if (!value || tabId && value.tabId !== tabId) return;
  result = value;
  if(value.skills_available!==undefined) showSkills(value.skills_available);
  element("error").textContent = value.error || "";
  element("company").textContent = value.matched?.company || value.page?.company || "Current application";
  element("job").textContent = value.matched?.title || value.page?.title || value.url || "";
  element("company").title = element("company").textContent; element("job").title = element("job").textContent;
  const busy = Boolean(value.busy || value.skillsBusy || skillsBusy || value.writing?.length && /^Writing:/.test(value.writingProgress || ""));
  const totals = [value.summary?.filled ? `${value.summary.filled} field${value.summary.filled===1 ? "" : "s"} filled` : "", value.skills?.added ? `${value.skills.added} skill${value.skills.added===1 ? "" : "s"} added` : ""].filter(Boolean);
  const pending = value.pending?.length || value.coverQuestions?.length;
  const progress = value.progress || "";
  const detail = value.state === "needs_input" || value.waiting || value.state === "failed" ? value.detail || "" : "";
  // Compact ordinary completion/step narration; keep instructions and wait outcomes.
  const ordinary = !progress || /^Workday step:/i.test(progress) || progress === "Autofill finished. Review the page.";
  element("progress").textContent = !ordinary ? progress : detail || (value.skillsBusy || skillsBusy ? "" : busy ? "Filling…" : value.summary ? (totals.join(" · ") || (pending ? "Review the questions below." : "Review the page.")) : "");
  element("autofill").disabled = Boolean(value.busy || value.skillsBusy || mainBusy || skillsBusy);
  element("skills-activity").hidden=!(value.skillsBusy || skillsBusy);
  if(value.skillsBusy)element("skills-progress").textContent=skillPlan ? skillPlan.matched.length+" matched" : "";
  else if(value.skills)renderSkills(value.skills);
  else if(element("skills-progress").textContent==="Adding skills…")element("skills-progress").textContent="Skills need review";
  element('next-page').disabled = false;
  element('cancel-form-wait').hidden = !value.waiting;
  element("cover-letter").disabled = Boolean(value.busy || value.coverBusy);
  element("cover-progress").textContent = value.coverBusy || value.coverQuestions?.length || /error|needs review/i.test(value.coverProgress || "") ? value.coverProgress || "" : "";
  element("writing-progress").textContent = /^Writing:/.test(value.writingProgress || "") && !value.writing?.length ? "" : /^Answer filled:/.test(value.writingProgress || "") ? "" : value.writingProgress || "";
  element("results").hidden = !value.summary;
  if (value.summary) {
    element("filled").textContent = "";
    element("skipped").textContent = "";
    element("upload-errors").textContent = value.summary.errors.join("\n");
  }
  const sectionText=(value.sections||[]).map(s=>(s.message || s.name[0].toUpperCase()+s.name.slice(1))+(s.message?'':s.total!==undefined?' '+s.added+'/'+s.total:': '+s.added+' added'+(s.skipped?' / '+s.skipped+' not in list':''))).join(' · ');
  element('section-results').textContent='';
  element('answer-section-results').textContent=sectionText;
  prompts(); completedAnswers();
  element("applied-status").hidden=!value.applied;
  element("autofill").textContent=value.busy?"Filling…":"Autofill";
  if(value.saved){element("saved-status").textContent=value.saved;clearTimeout(savedTimer);savedTimer=setTimeout(()=>element("saved-status").textContent="",2000);}
  element("needs-writing").replaceChildren();
  // Expose generated PDF without persisting blob URLs across panel instances.
  if (downloadURL) URL.revokeObjectURL(downloadURL);
  element("download").hidden = !value.coverFile;
  if (value.coverFile) {
    const file = value.coverFile;
    downloadURL = URL.createObjectURL(new Blob([Uint8Array.from(atob(file.base64), char => char.charCodeAt(0))], {type: file.mime}));
    element("download").href = downloadURL; element("download").download = file.filename;
  }
}
const showError = error => { element("error").textContent = error.message; };
async function pageContext() {
  if (!result?.page) {
    const scanned = await send({type: "page-context"});
    if (scanned.error) throw new Error(scanned.error);
    result = {...scanned, matched: await api("/ext/match", {url: scanned.url})};
  }
  const enabled=element("use-job-description").checked;
  const {description,url,...page}=result.page;
  return {page:{...page,...(enabled?{description,url}: {})},url:result.url,application_id:result.matched?.application_id,use_job_description:enabled};
}
function renderAnswer() {
  const current = history(), answer = current.versions[current.index];
  element("helper-result").hidden = !answer?.answer?.trim();
  element("version").textContent = `${answer ? current.index + 1 : 0} / ${current.versions.length}`;
  element("answer").value = answer?.answer || "";
  element("words").textContent = answer ? `${answer.words} words · ${answer.chars} characters` : "";
  element("warning").textContent = answer?.warning || "";
  element("previous").disabled = aiBusy || current.index <= 0;
  element("next").disabled = aiBusy || current.index >= current.versions.length - 1;
  element("copy").disabled = element("refine").disabled = aiBusy || !answer;
  element("generate").disabled = aiBusy;
  element("fill-answer").hidden = !source || source.label !== question();
  element("fill-answer").disabled = aiBusy || !answer || result?.busy;
}
async function write(refine) {
  if (aiBusy || !question() || refine && !history().versions[history().index]?.answer) return;
  const key = question(), current = history(), previous = current.versions[current.index];
  aiBusy = true; renderAnswer(); element("ai-status").textContent = "Writing…";
  try {
    const requestContext=await pageContext(), appKey=applicationKey();
    const answer = await api("/ext/write", {...requestContext, question: key,
      ...(source?.label === key && source.max_length != null ? {max_length: source.max_length} : {}),
      instruction: refine ? element("refinement").value : source?.label === key && source.max_length != null ? `Use at most ${source.max_length} characters.` : "",
      previous: refine ? previous.answer : ""});
    current.versions.push(answer); current.index = current.versions.length - 1; histories[appKey+":"+key] = current;
    await chrome.storage.session.set({answerHistory: histories}); element("ai-status").textContent = ""; completedAnswers();
  } catch (error) { element("ai-status").textContent = error.message; }
  finally { aiBusy = false; renderAnswer(); }
}
function renderMessages() {
  const container = element("messages");
  container.replaceChildren(...messages.map(message => {
    const bubble = node("div", ""); bubble.className = 'bubble ' + message.role;
    const time = node("time", new Date(message.time).toLocaleTimeString([], {hour:'numeric', minute:'2-digit'}));
    time.dateTime = new Date(message.time).toISOString();
    bubble.append(node("p", message.text), time); return bubble;
  }));
  if (chatBusy) { const typing = node("div", "•••"); typing.className = 'bubble assistant typing'; typing.setAttribute('aria-label', 'AI is typing'); container.append(typing); }
  requestAnimationFrame(() => { container.scrollTop = container.scrollHeight; });
}
async function chat() {
  const text = element("chat-input").value.trim();
  if (!text || chatBusy) return;
  chatBusy = true; element("send").disabled = element("clear-chat").disabled = true;
  messages.push({role: "user", text, time:Date.now()}); element("chat-input").value = ""; renderMessages();
  element("chat-status").textContent = "Thinking…";
  try {
    const reply = await api("/ext/chat", {...await pageContext(), messages: messages.slice(-12).map(({role,text})=>({role,text}))});
    messages.push({role: "assistant", text: reply.reply, time:Date.now()}); element("chat-status").textContent = "";
  } catch (error) { messages.pop(); element("chat-input").value = text; renderMessages(); element("chat-status").textContent = error.message; }
  finally { chatBusy = false; renderMessages(); element("send").disabled = element("clear-chat").disabled = false; }
}
let pairingReady;
element("settings").onclick = () => {
  element('assistant-view').hidden = true; element('settings-view').hidden = false;
  element('settings-back').focus(); pairingReady ||= initializePairing(element('settings-view')).then(ready => { if(!ready) pairingReady = undefined; });
};
function closeSettings() { element('settings-view').hidden = true; element('assistant-view').hidden = false; element('settings').focus(); }
element('settings-back').onclick = closeSettings;
element('settings-view').onkeydown = event => { if(event.key === 'Escape') { event.preventDefault(); closeSettings(); } };
element("autofill").onclick = async () => {
  if(mainBusy || skillsBusy) return;
  mainBusy=true;element('autofill').disabled=true;
  try {if(!element('skills-panel').hidden){await connection();await loadSkillPlan();await saveSkillSelection();}await perform({type:'autofill'});}
  catch(error){showError(error);}finally{mainBusy=false;element('autofill').disabled=false;}
};
element("cover-letter").onclick = () => perform({type: "cover-letter"}).catch(showError);
element("generate").onclick = () => write(false);
element("refine").onclick = () => write(true);
element("question").oninput = renderAnswer;
for (const [id, delta] of [["previous", -1], ["next", 1]]) element(id).onclick = async () => {
  history().index += delta; renderAnswer(); completedAnswers(); await chrome.storage.session.set({answerHistory: histories});
};
element("copy").onclick = () => navigator.clipboard.writeText(element("answer").value).then(() => { element("ai-status").textContent = "Copied."; }, showError);
element("fill-answer").onclick = () => perform({type: "fill-answer", key: source.key, value: element("answer").value, source:"AI", url:source.url, label:source.label}).catch(showError);
element("send").onclick = chat;
element("chat-input").onkeydown = event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); chat(); } };
element("clear-chat").onclick = () => { messages = []; renderMessages(); element("chat-status").textContent = ""; };
chrome.runtime.onMessage.addListener(message => { if (message.type === "autofill-result") render(message.result); });
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && tabId && changes.tabResults) render(changes.tabResults.newValue?.[tabId]);
  if (area === "session" && !tabId && changes.lastResult) render(changes.lastResult.newValue);
  if (area === "local" && (changes.server || changes.key)) connection();
});
const initial=await chrome.storage.session.get(["lastResult","tabResults"]);
render(tabId?initial.tabResults?.[tabId]:initial.lastResult); completedAnswers(); renderAnswer(); connection();

let runPaused = false;
function renderRun(run = {}) {
  runPaused = Boolean(run.paused);
  element('run-controls').hidden=!(run.active || run.paused || run.current);
  element('chrome-run-status').textContent=run.error || (run.total ? (run.index || 0)+'/'+run.total : '');
  const label=runPaused?'Resume run':'Pause run';
  element('run-pause').textContent=runPaused?'▶':'Ⅱ';
  element('run-pause').setAttribute('aria-label',label);element('run-pause').title=label;
  element('run-skip').disabled=!run.current;

}
for(const action of ['cancel','skip'])element('run-'+action).onclick=async()=>{
  try{const value=await send({type:'run-'+action});if(value.error)throw Error(value.error);renderRun(value);}catch(error){showError(error);}
};
element("run-pause").onclick = async () => {
  try { const value=await send({type: "run-pause", paused: !runPaused});if(value.error)throw Error(value.error);renderRun(value); } catch (error) { showError(error); }
};
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && changes.chromeRun) renderRun(changes.chromeRun.newValue);
});
renderRun((await chrome.storage.session.get("chromeRun")).chromeRun);
send({type: "run-status-refresh"}).then(renderRun).catch(showError);

element('next-page').onclick=()=>perform({type:'next-page'}).catch(showError);
element('cancel-form-wait').onclick=()=>perform({type:'cancel-form-wait'}).catch(showError);
element('auto-advance').onchange=()=>send({type:'auto-advance-preference',enabled:element('auto-advance').checked}).then(value=>{if(value.error)throw Error(value.error);}).catch(showError);
send({type:'auto-advance-preference'}).then(value=>{element('auto-advance').checked=Boolean(value.enabled);}).catch(showError);

function helperRequest(request) { if (request && (!tabId || request.tabId===tabId)) {result=request.result;openHelper(request.field);} }
chrome.storage.onChanged.addListener((changes,area)=>{if(area==='session'&&changes.helperRequest)helperRequest(changes.helperRequest.newValue);});
helperRequest((await chrome.storage.session.get('helperRequest')).helperRequest);
element('send-structure').onclick=async()=>{try{const response=await send({type:'page-snapshot'});if(response.error)throw Error(response.error);element('snapshot-status').textContent='Page structure sent.';}catch(error){element('snapshot-status').textContent=error.message;}};


function showSkills(available) {
  for(const id of ['skills-panel','autofill-skills']) element(id).hidden=!available;
  if(available) loadSkillPlan();
}
function stableSkillContext() {
  const page=result?.page || {};
  return JSON.stringify([result?.url?.split(/[?#]/)[0],page.description || '']);
}
async function saveSkillSelection() {
  if(!skillPlan)return;
  const selection={context:stableSkillContext(),excluded:skillPlan.matched.filter(s=>!selectedSkillNames.has(s)),added:[...selectedSkillNames].filter(s=>!skillPlan.matched.includes(s))};
  skillOverrides.set(selection.context,selection);
  await send({type:'skill-selection',selection});
}
async function loadSkillPlan() {
  if(!result?.page){const value=await send({type:'skills-available'});if(!value.page)return;result={...result,...value};}
  const context=stableSkillContext(), key=context+profileRevision;
  if(skillPlanKey===key) return skillPlanPromise;
  skillPlanKey=key;skillPlan=undefined;
  element('skills-progress').textContent='Loading…';
  skillPlanPromise=(async()=>{
    try {
      const plan=await api('/ext/skills/plan',{page:result.page,...(result?.matched?.application_id?{application_id:result.matched.application_id}:{})});
      if(skillPlanKey!==key)return;
      const stored=await send({type:'skill-selection'});
      if(skillPlanKey!==key)return;
      skillPlan=plan;const saved=skillOverrides.get(context) || (stored?.selection?.context===context?stored.selection:undefined);
      selectedSkillNames=new Set([...plan.matched.filter(s=>!saved?.excluded.includes(s)),...(saved?.added || [])]);
      chipStatuses={};renderSkillPlan();element('skills-progress').textContent=plan.matched.length+' matched';
    }catch(error){if(skillPlanKey===key){skillPlanKey=undefined;element('skills-progress').textContent=error.message;}}
  })();
  return skillPlanPromise;
}
function changedSkills(){renderSkillPlan();saveSkillSelection().catch(showError);}
function renderSkillPlan() {
  if(!skillPlan) return;
  const matched=element('matched-skills');matched.replaceChildren();
  for(const skill of selectedSkillNames) {
    const chip=node('span','');chip.className='skill-chip';chip.dataset.status=chipStatuses[skill] || 'pending';
    const status=node('small',chipStatuses[skill] || 'pending');status.className='skill-status';
    const remove=button('×',()=>{selectedSkillNames.delete(skill);changedSkills();});
    remove.setAttribute('aria-label','Remove '+skill);remove.disabled=skillsBusy;
    chip.append(node('span',skill),status,remove);matched.append(chip);
  }
  if(!matched.children.length) matched.append(node('p','No skills selected. Include skills below.'));
  const missing=element('missing-skills');missing.replaceChildren();
  for(const skill of skillPlan.missing) {
    const include=button((selectedSkillNames.has(skill)?'✓ ':'+ ')+skill,()=>{selectedSkillNames.add(skill);changedSkills();});
    include.setAttribute('aria-label','Include '+skill);include.disabled=skillsBusy || selectedSkillNames.has(skill);missing.append(include);
  }
  if(!missing.children.length) missing.append(node('p','No missing skills identified.'));
  const other=element('other-skill-options');other.replaceChildren();
  function check(name,checked,action) {
    const label=node('label',''),input=document.createElement('input');input.type='checkbox';input.checked=checked;input.disabled=skillsBusy;input.onchange=()=>action(input.checked);label.append(input,node('span',name));other.append(label);
  }
  check('Add all',skillPlan.other.length>0 && skillPlan.other.every(skill=>selectedSkillNames.has(skill)),checked=>{
    for(const skill of skillPlan.other) {if(checked) selectedSkillNames.add(skill);else selectedSkillNames.delete(skill);}changedSkills();
  });
  const manual=button('Type to add skills',()=>{element('manual-skills').hidden=false;element('manual-skill-input').focus();});other.append(manual);
  for(const skill of skillPlan.other) check(skill,selectedSkillNames.has(skill),checked=>{if(checked) selectedSkillNames.add(skill);else selectedSkillNames.delete(skill);changedSkills();});
  if(!skillPlan.other.length) other.append(node('p','All profile skills are in the main list.'));
}
function renderSkills(value) {
  element('skills-progress').textContent=value.busy ? (skillPlan ? skillPlan.matched.length+" matched" : "") : result?.skills && skillPlan ? skillPlan.matched.length+' matched' : value.progress || '';
  element('skills-activity').hidden=!value.busy && !result?.skillsBusy && !skillsBusy;
  chipStatuses={...chipStatuses,...value.items};renderSkillPlan();
}
async function runSkills() {
  if(skillsBusy || mainBusy) return;
  skillsBusy=true;
  await connection();
  await loadSkillPlan();
  if(!skillPlan){skillsBusy=false;return;}
  renderSkillPlan();
  for(const id of ['autofill-skills','autofill','next-page']) element(id).disabled=true;
  element('skills-activity').hidden=false;
  const timer=setInterval(()=>send({type:'skills-status'}).then(renderSkills).catch(showError),250);
  try {const response=await send({type:'autofill-skills',...(skillPlan?{skills:[...selectedSkillNames]}:{})});if(response.error)throw Error(response.error);renderSkills(response);}
  catch(error){showError(error);}
  finally {clearInterval(timer);skillsBusy=false;renderSkillPlan();element('skills-activity').hidden=true;for(const id of ['autofill-skills','autofill','next-page'])element(id).disabled=false;}
}
for(const id of ['autofill-skills']) element(id).onclick=runSkills;
element('cancel-skills').onclick=()=>send({type:'cancel-skills'}).catch(showError);
element('manual-skill-add').onclick=()=>{const input=element('manual-skill-input');if(input.value.trim()){selectedSkillNames.add(input.value.trim());input.value='';changedSkills();}};
send({type:'skills-available'}).then(value=>{if(!result?.page)result={...result,...value};showSkills(Boolean(value.skills_available));}).catch(()=>{});
