import {api} from "./api.js";
import {snapshotCollector} from "./snapshots.js";

// Explicit intents invalidate restores before any asynchronous tab/storage read.
const sidebarVersions=new Map();
const sidebarStates=new Map();
let sidebarQueue=Promise.resolve();
function showSidebar(tabId, mode='open', restoreVersion) {
  if(tabId===undefined)return chrome.tabs.query({active:true,currentWindow:true}).then(([tab])=>{if(!tab?.id)throw Error('Open an application page first.');return showSidebar(tab.id,mode,restoreVersion);});
  const version=restoreVersion ?? (sidebarVersions.get(tabId)||0)+1;
  if(restoreVersion===undefined){sidebarVersions.set(tabId,version);sidebarStates.delete(tabId);if(mode!=='toggle')sidebarStates.set(tabId,mode);}
  const task=sidebarQueue.catch(()=>{}).then(()=>applySidebar(tabId,restoreVersion===undefined?mode:sidebarStates.get(tabId)||mode,version));
  sidebarQueue=task;return task;
}
async function restoreSidebar(tabId, runFallback=false) {
  const version=sidebarVersions.get(tabId)||0;
  const {sidebarTabs={},chromeRun}=await chrome.storage.session.get(['sidebarTabs','chromeRun']);
  const state=sidebarTabs[tabId] || (runFallback&&chromeRun?.active&&chromeRun.tabId===tabId?'open':null);
  if(state)await showSidebar(tabId,state,version);
}
async function applySidebar(tabId, mode, version) {
  const current=()=>version===(sidebarVersions.get(tabId)||0);
  if(!current())return;
  const tab=tabId===undefined?(await chrome.tabs.query({active:true,currentWindow:true}))[0]:await chrome.tabs.get(tabId);
  if(!tab?.id||!/^https?:/.test(tab.url||''))throw Error('Open an HTTP or HTTPS application page to use AutoApply.');
  if(!current())return;
  let [frame]=await chrome.scripting.executeScript({target:{tabId:tab.id},func:(mode,id)=>globalThis.autoApplyContent?.sidebar(mode,id),args:[mode,tab.id]});
  if(!frame?.result){
    if(mode!=='toggle'){const {sidebarTabs={}}=await chrome.storage.session.get('sidebarTabs');sidebarTabs[tab.id]=mode;await chrome.storage.session.set({sidebarTabs});}
    await chrome.scripting.executeScript({target:{tabId:tab.id},files:['content.js']});
    for(let attempt=0;attempt<6&&!frame?.result;attempt++){
      if(attempt)await delay(150);
      if(!current())return;
      [frame]=await chrome.scripting.executeScript({target:{tabId:tab.id},func:(mode,id)=>globalThis.autoApplyContent?.sidebar(mode,id),args:[mode,tab.id]});
    }
  }
  if(!frame?.result)throw Error('AutoApply could not open on this page. Try an application page outside Chrome settings or the web store.');
  if(!current())return;
  sidebarStates.set(tabId,frame.result);
  const {sidebarTabs={}}=await chrome.storage.session.get('sidebarTabs');sidebarTabs[tab.id]=frame.result;
  await chrome.storage.session.set({sidebarTabs});
  await chrome.action.setBadgeText({tabId:tab.id,text:''});
  await chrome.action.setTitle({tabId:tab.id,title:'Open AutoApply'});
}
chrome.action.onClicked.addListener(tab=>showSidebar(tab.id,'open').catch(async error=>{
  console.error(error);await chrome.action.setTitle({tabId:tab.id,title:error.message});await chrome.action.setBadgeText({tabId:tab.id,text:'!'});
}));
chrome.tabs.onUpdated.addListener((tabId,change)=>{if(change.url || change.status==='loading')chrome.runtime.sendMessage({type:'analysis-invalidated',tabId}).catch(()=>{});if(change.status==='complete'){
  restoreSidebar(tabId,true).catch(console.error);
}});
// History API navigation need not mutate the DOM. Recheck inline prompts only.
chrome.tabs.onUpdated.addListener((tabId,change)=>{
  if(change.url && /^https?:/.test(change.url)) chrome.scripting.executeScript({target:{tabId,allFrames:true},
    func:()=>globalThis.autoApplyContent?.syncWritingButtons()}).catch(()=>{});
});
globalThis.autoApplySidebar=showSidebar;
let running = false;
const activeResults = new Map(), writingTasks = new Map();
const manualActionTabs = new Set();
const analysisTasks = new Map();
let analysisBlockers=0;
let autofillTasks=0;
const skillSelections = new Map();
const snapshots=snapshotCollector({api,report,delay:ms=>delay(ms),current:result=>activeResults.get(result.tabId)===result && !cancelledTabs.has(result.tabId) && !pendingSteps.has(result.tabId)});
function skillContext(result) {
  const page=result.page || {};
  return JSON.stringify([result.url?.split(/[?#]/)[0],page.description || '']);
}
async function fillMatchedSkills(result) {
  if(!result.skills_available || cancelledTabs.has(result.tabId)) return;
  const plan=await api('/ext/skills/plan',{page:result.page || {},application_id:result.matched?.application_id});
  if (cancelledTabs.has(result.tabId)) return;
  const context=skillContext(result), saved=skillSelections.get(result.tabId);
  const profileSkills=[...plan.matched,...plan.other];
  const skills=saved?.context===context ? [...new Set([...profileSkills.filter(s=>!saved.excluded.includes(s)),...saved.added])] : profileSkills;
  const frames=await chrome.scripting.executeScript({target:{tabId:result.tabId,allFrames:true},func:()=>globalThis.autoApplyContent?.skillsAvailable()});
  const frame=frames.find(f=>f.result);
  if(!frame || !skills.length || cancelledTabs.has(result.tabId)) return;
  result.skillsBusy=true;await report(result);
  let filled;
  try {
    if (cancelledTabs.has(result.tabId)) return;
    [filled]=await chrome.scripting.executeScript({target:{tabId:result.tabId,frameIds:[frame.frameId]},func:skills=>globalThis.autoApplyContent.autofillSkills(skills),args:[skills]});
  } finally {result.skillsBusy=false;}
  result.skills=filled.result;
  result.pending=result.pending.filter(field=>!/^type to add skills\b/i.test(field.label));
  result.summary.needs_input=result.pending.map(field=>field.label);
  result.sections=(result.sections || []).filter(s=>s.name!=='skills');
  result.sections.push({name:'skills',message:filled.result?.progress || 'Skills filled'});
}
let reporting = Promise.resolve();

function report(result, valid) {
  reporting = reporting.catch(() => {}).then(async () => {
  const {tabResults = {}, applicationAnswers={}} = await chrome.storage.session.get(["tabResults","applicationAnswers"]);
  if(result.tabId && closedTabs.has(result.tabId))return result;
  if(valid && !await valid())throw Error('Application changed. Analyze page again.');
  if(result.url){
    const key=String(result.matched?.application_id || result.url.replace(/\/apply(?:\/.*)?$/i,"").split(/[?#]/)[0]);
    const saved=applicationAnswers[key] || [];
    for(const answer of result.completed || [])if(answer.source==="AI"){const old=saved.findIndex(item=>item.label===answer.label);if(old>=0)saved.splice(old,1);saved.push({...answer,url:result.url});}
    result.aiAnswers=saved;applicationAnswers[key]=saved;await chrome.storage.session.set({applicationAnswers});
  }
  if(valid && !await valid())throw Error('Application changed. Analyze page again.');
  if (result.tabId) tabResults[result.tabId] = result;
  await chrome.storage.session.set({lastResult: result, tabResults});
  if(valid && !await valid())throw Error('Application changed. Analyze page again.');
  await chrome.runtime.sendMessage({type: "autofill-result", result}).catch(() => {});
  return result;
  });
  return reporting;
}

async function scan(tabId) {
  const tab = tabId === undefined ? (await chrome.tabs.query({active: true, currentWindow: true}))[0] : await chrome.tabs.get(tabId);
  if (!tab?.id || !/^https?:\/\//.test(tab.url || "")) throw new Error("Open an HTTP or HTTPS application form first.");
  let frames = [];
  // Navigation can finish before the content script has initialized.
  for (let attempt = 0; attempt < 20; attempt++) {
    const scanned=await chrome.scripting.executeScript({target: {tabId: tab.id, allFrames: true}, world: "ISOLATED",
      func: async () => {try {return await globalThis.autoApplyContent?.scan();}
        catch(error) {return {scan_error:error.message || String(error)};}}});
    const failure=scanned.find(frame=>frame.error || frame.result?.scan_error);
    if(failure) throw new Error(failure.result?.scan_error || failure.error.message || String(failure.error));
    frames = scanned.filter(frame => frame.result);
    if (frames.length) break;
    await delay(100);
  }
  if (!frames.length) throw new Error("Form scanner is unavailable. Reload the application page and try again.");
  const page = {...(frames.find(frame => frame.frameId === 0) || frames[0]).result.page, url: tab.url};
  const contextKey=tab.url.replace(/\/apply(?:\/.*)?(?=[?#]|$)/i, "").split(/[?#]/)[0];
  const {jobContexts={}}=await chrome.storage.session.get("jobContexts");
  const cached=jobContexts[contextKey];
  if(cached?.description && (!page.description || /\/apply(?:\/|$)/i.test(tab.url)))page.description=cached.description;
  page.description=(page.description||"").slice(0,6000);
  jobContexts[contextKey]={description:page.description,url:tab.url};
  await chrome.storage.session.set({jobContexts});
  const fields = frames.flatMap(frame => frame.result.fields.map(field => ({...field, key: `${frame.frameId}:${field.key}`})));
  if (fields.length > 500) throw new Error("This page has more than 500 fields. Open a smaller form section.");
  return {tabId: tab.id, url: tab.url, page, fields, pageIdentities:Object.fromEntries(frames.map(frame=>[frame.frameId,{identity:frame.result.page_identity,documentId:frame.documentId}])), next_action: frames.find(frame => frame.frameId === 0)?.result.next_action, workday_step: frames.find(frame => frame.frameId === 0)?.result.workday_step, skills_available: frames.some(frame=>frame.result.skills_available), sign_in: frames.some(frame => frame.result.sign_in), manual_entry: frames.some(frame => frame.result.manual_entry), timings: {scan_ms: frames.reduce((sum, frame) => sum + (frame.result.scan_ms || 0), 0)}};
}

const context = result => ({page: result.page, url: result.url, application_id: result.matched?.application_id});

async function fill(result, values, files = {}) {
  if (cancelledTabs.has(result.tabId)) return;
  if ((await chrome.tabs.get(result.tabId)).url !== result.url) throw new Error("Application page changed. Run autofill again.");
  for (const frameId of new Set(values.map(value => Number(value.key.split(":")[0])))) {
    if (cancelledTabs.has(result.tabId)) return;
    const prefix = `${frameId}:`;
    const local = values.filter(value => value.key.startsWith(prefix)).map(value => ({...value, key: value.key.slice(prefix.length)}));
    const [frame] = await chrome.scripting.executeScript({target: {tabId: result.tabId, frameIds: [frameId]}, world: "ISOLATED",
      func: (answers, attachments) => globalThis.autoApplyContent.fill(answers, attachments), args: [local, files]});
    const summary = frame.result;
    result.timings.fill_ms = (result.timings.fill_ms || 0) + summary.fill_ms;
    result.summary.filled += summary.filled;
    result.completed ||= [];
    for(const answer of summary.completed || []) {const item={...answer,key:prefix+answer.key};result.completed=result.completed.filter(old=>old.key!==item.key);result.completed.push(item); }
    result.summary.skipped += summary.skipped;
    for (const name of ["pending", "writing"]) {
      result[name] = result[name].filter(field => !field.key.startsWith(prefix) || !local.some(value => prefix + value.key === field.key));
      result[name].push(...summary[name].map(field => ({...field, key: prefix + field.key})));
    }
    result.summary.errors.push(...summary.errors);
  }
  result.summary.needs_input = result.pending.map(field => field.label);
  result.summary.needs_writing = result.writing.map(field => field.label);
}

async function progress(result, text) { result.progress = text; await report(result); }

async function coverLetter(result, answers) {
  if (cancelledTabs.has(result.tabId)) return;
  result.coverBusy = true;
  result.coverProgress = "Writing cover letter...";
  await report(result);
  if (cancelledTabs.has(result.tabId)) return;
  const file = await api("/ext/cover-letter", {...context(result), ...(answers ? {answers} : {})});
  if (cancelledTabs.has(result.tabId) || activeResults.get(result.tabId) !== result) return;
  if (file.questions) {
    result.coverQuestions = file.questions;
    result.coverAnswers = answers || [];
    result.coverProgress = "Cover letter needs your input. Blank answers are allowed.";
  } else {
    result.coverQuestions = [];
    result.coverFile = file;
    const fields = result.fields.filter(field => field.kind === "file" && /cover/i.test(field.label) && !field.answered);
    if (fields.length) await fill(result, fields.map(field => ({key: field.key, status: "FILE", value: "cover_letter"})), {cover_letter: file});
    result.coverProgress = fields.length && !result.pending.some(field => fields.some(upload => upload.key === field.key)) ? "Cover letter attached" : fields.length ? "Cover letter attachment needs review" : "Cover letter ready to download.";
  }
}

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const formWaits = new Map(), cancelledTabs = new Set(), closedTabs = new Set();
function cancelWait(tabId) {
  clearTimeout(formWaits.get(tabId)); formWaits.delete(tabId); cancelledTabs.add(tabId); pendingSteps.delete(tabId);
}
function pollForForm(result) {
  const end = Date.now() + 60000;
  async function tick() {
    if (cancelledTabs.has(result.tabId)) return;
    try {
      const scanned = await scan(result.tabId);
      if (!scanned.sign_in && scanned.fields.length) {
        formWaits.delete(result.tabId); pendingSteps.add(result.tabId); drainSteps(); return;
      }
    } catch {
      // A document transition can temporarily make the scanner unavailable.
    }
    if (cancelledTabs.has(result.tabId)) return;
    if (Date.now() < end) formWaits.set(result.tabId, setTimeout(tick, 1000));
    else { formWaits.delete(result.tabId); result.waiting = false; result.progress = 'No form appeared. Press Autofill to try again.'; await report(result); }
  }
  formWaits.set(result.tabId, setTimeout(tick, 1000));
}
async function advancePreference(value) {
  const profile = await api('/ext/ping');
  const {server = '', autoAdvanceProfiles = {}} = await chrome.storage.local.get(['server','autoAdvanceProfiles']);
  const key = server + ':' + (profile.profile_id || profile.profile_name || profile.name || 'default');
  if (value !== undefined) { autoAdvanceProfiles[key] = Boolean(value); await chrome.storage.local.set({autoAdvanceProfiles}); }
  return {enabled: Boolean(autoAdvanceProfiles[key])};
}
async function clickNext(result, automatic = false) {
  if (automatic && cancelledTabs.has(result.tabId)) return false;
  const scanned = await scan(result.tabId);
  if (automatic && cancelledTabs.has(result.tabId)) return false;
  if (automatic && (result.pending.length || result.writing.length || result.summary.errors.length ||
      scanned.sign_in || scanned.fields.some(field => field.required && !field.answered))) return false;
  const [frame] = await chrome.scripting.executeScript({target:{tabId:result.tabId},
    func: automatic => globalThis.autoApplyContent.nextPage(automatic), args:[automatic]});
  if (frame.result.errors.length) { result.summary.errors.push(...frame.result.errors); result.progress = 'Fix the page validation errors before continuing.'; await report(result); return false; }
  if (!frame.result.clicked) { if (!automatic) { result.progress = frame.result.message || 'No Next page button found.'; await report(result); } return false; }
  const after = await scan(result.tabId);
  // An unchanged form means navigation did not succeed; never repeatedly click it.
  return after.url !== scanned.url || after.workday_step !== scanned.workday_step ||
    after.fields.map(field=>field.key).join() !== scanned.fields.map(field=>field.key).join();
}
async function matchAndFill(result, prepare = false) {
    const sectionAnswers=[];
    if (prepare) {
    const profile = await api('/ext/profile-sections');
    if (cancelledTabs.has(result.tabId)) return;
    const prepared = await chrome.scripting.executeScript({target:{tabId:result.tabId,allFrames:true},world:'ISOLATED',
      func: profile => globalThis.autoApplyContent?.prepareSections(profile), args:[profile]});
    result.sections=[];
    result.fields=prepared.filter(frame=>frame.result).flatMap(frame=>{
      const prefix=frame.frameId+':';
      sectionAnswers.push(...frame.result.answers.map(answer=>({...answer,key:prefix+answer.key})));
      result.sections.push(...frame.result.sections);
      result.summary.errors.push(...(frame.result.errors || []));
      const completed=(frame.result.completed || []).map(answer=>({...answer,key:prefix+answer.key}));
      result.completed ||= [];
      for(const answer of completed) {
        result.completed=result.completed.filter(old=>old.key!==answer.key);
        result.completed.push(answer);
      }
      result.summary.filled += completed.length;
      return frame.result.fields.map(field=>({...field,key:prefix+field.key}));
    });
    }
    // Search menus can expose only a first page. Keep those choices for prompts,
    // but let profile matching resolve answers beyond that page as before.
    const fields = result.fields.filter(field=>!field.section_managed).map(field => field.options_partial ? {...field, options: []} : field);
    let {answers} = await api("/ext/answers", {...context(result), url: result.url, fields});
    if (cancelledTabs.has(result.tabId)) return;
    answers.push(...sectionAnswers);
    // Resolve searchable profile answers first; open menus only for unresolved prompts.
    const unresolved = result.fields.filter(field => field.kind === "combobox" && !field.answered &&
      !answers.some(answer => answer.key === field.key && answer.status === "RESOLVED"));
    const collected = [];
    for (const frameId of new Set(unresolved.map(field => Number(field.key.split(":")[0])))) {
      const prefix = frameId + ":", keys = unresolved.filter(field => field.key.startsWith(prefix)).map(field => field.key.slice(prefix.length));
      const [frame] = await chrome.scripting.executeScript({target: {tabId: result.tabId, frameIds: [frameId]},
        func: keys => globalThis.autoApplyContent.collectOptions(keys), args: [keys]});
      result.timings.options_ms = (result.timings.options_ms || 0) + frame.result.options_ms;
      for (const field of frame.result.fields) {
        const updated = {...field, key: prefix + field.key};
        Object.assign(result.fields.find(item => item.key === updated.key), updated);
        collected.push(updated);
      }
    }
    if (collected.length) {
      const resolved = await api("/ext/answers", {...context(result), url: result.url, fields: collected});
      answers = answers.map(answer => resolved.answers.find(item => item.key === answer.key) || answer);
    }
    const files = {};
    if (answers.some(answer => answer.status === "FILE" && answer.value === "resume" && !result.fields.find(field => field.key === answer.key)?.answered)) files.resume = await api("/ext/resume");
    await fill(result, answers, files);

  // A failed resolved choice still needs its lazy options in the panel.
  for (const field of result.pending.filter(field => field.kind === 'combobox' && !field.options.length && !unresolved.some(item=>item.key===field.key))) {
    await loadOptions(result, field.key);
  }
}
async function loadOptions(result, key) {
  const field = result.fields.find(item=>item.key===key);
  if (field?.kind !== 'combobox') {
    const updated = (await scan(result.tabId)).fields.find(item=>item.key===key);
    if (updated) for (const list of [result.fields,result.pending]) Object.assign(list.find(item=>item.key===key) || {}, updated);
    return;
  }
  const [frameId, local] = key.split(':');
  const [frame] = await chrome.scripting.executeScript({target:{tabId:result.tabId,frameIds:[Number(frameId)]},
    func: keys => globalThis.autoApplyContent.collectOptions(keys), args:[[local]]});
  for (const field of frame.result.fields) {
    for (const list of [result.fields,result.pending]) Object.assign(list.find(item=>item.key===key) || {}, field, {key});
  }
}
async function rescanRevealed(result) {
  for (let pass = 0; pass < 3; pass++) {
    await delay(500);
    if (cancelledTabs.has(result.tabId)) return;
    const scanned = await scan(result.tabId);
    const known = new Set(result.fields.map(field=>field.key));
    const added = scanned.fields.filter(field=>!known.has(field.key) && !field.answered);
    for (const name of ['pending','writing']) result[name] = result[name].filter(field=>scanned.fields.some(item=>item.key===field.key&&!item.answered));
    result.fields = scanned.fields;
    result.pageIdentities = scanned.pageIdentities;
    if (!added.length) continue;
    const all = result.fields;
    result.fields = added;
    await matchAndFill(result);
    result.fields = all;
  }
  result.summary.needs_input = result.pending.map(field=>field.label);
  result.summary.needs_writing = result.writing.map(field=>field.label);
}

export async function autofill(tabId, application, advanceCount = 0) {
  autofillTasks++;
  try{return await autofillCurrent(tabId,application,advanceCount);}
  finally{autofillTasks--;}
}
async function autofillCurrent(tabId, application, advanceCount = 0) {
  if (analysisTasks.size) return {error: "Application is busy analyzing. Wait and retry."};
  if (running) return {error: "Autofill is already running."};
  running = true;
  const ownedEpoch=runEpoch;
  let result = {busy: true, progress: "Scanning application..."};
  try {
    const traceTab=tabId===undefined?(await chrome.tabs.query({active:true,currentWindow:true}))[0]:await chrome.tabs.get(tabId);
    result.tabId=traceTab?.id; result.url=traceTab?.url;
    if(application?.run_id && staleRun(ownedEpoch))return result;
    cancelledTabs.delete(result.tabId);
    activeResults.set(result.tabId,result);
    await report(result);
    await showSidebar(result.tabId);
    await chrome.scripting.executeScript({target:{tabId:traceTab.id,allFrames:true},func:()=>globalThis.autoApplyContent?.beginTrace('autofill')});
    let scanned = await scan(tabId);
    if (cancelledTabs.has(scanned.tabId)) return result;
    if (!scanned.sign_in && !scanned.fields.length && scanned.next_action?.kind!=='submit') scanned = await openApplication(scanned);
    if(cancelledTabs.has(scanned.tabId))return result;
    await chrome.scripting.executeScript({target:{tabId:scanned.tabId,allFrames:true},func:count=>{const api=globalThis.autoApplyContent;api?.beginTrace('autofill');api?.trace('scan','found',null,{total:count});},args:[scanned.fields.length]});
    clearTimeout(formWaits.get(scanned.tabId)); formWaits.delete(scanned.tabId);
    const wasWaiting = activeResults.get(scanned.tabId)?.waiting;
    pendingSteps.delete(scanned.tabId);
    activeResults.delete(scanned.tabId);
    await showSidebar(scanned.tabId);
    if(cancelledTabs.has(scanned.tabId))return result;
    result = {...scanned, completed: [], busy: true, pending: [], writing: [], summary: {filled: 0, skipped: 0, needs_input: [], needs_writing: [], errors: []}};
    activeResults.set(result.tabId, result);
    const {autofillTabs = {}} = await chrome.storage.session.get("autofillTabs");
    const saved = autofillTabs[result.tabId] || {};
    result.matched = typeof application === "object" ? application : application ? {application_id: application} : saved.url?.replace(/\/apply(?:\/.*)?$/i,"").split(/[?#]/)[0]===result.url.replace(/\/apply(?:\/.*)?$/i,"").split(/[?#]/)[0] ? saved.matched : undefined;
    result.matched ||= await api("/ext/match", {url: result.url});
    if(cancelledTabs.has(result.tabId))return result;
    autofillTabs[result.tabId] = {...saved, url: result.url, matched: result.matched};
    await chrome.storage.session.set({autofillTabs});
    if (result.matched.company) result.page.company = result.matched.company;
    if (result.matched.title) result.page.title = result.matched.title;
    if (!result.sign_in && result.next_action?.kind==='submit') {
      result.progress = 'Review the application. Submit when you are ready.';
      return await report({...result,busy:false});
    }
    if (result.sign_in || !result.fields.length) {
      result.state = "needs_input";
      result.detail = result.progress = result.sign_in ? "Sign in to Workday in this tab. Autofill will resume automatically." : 'Waiting for the application form...';
      result.waiting = true;
      result.busy = false;
      pollForForm(result);
      return await report(result);
    }
    if (result.workday_step || wasWaiting) {
      for (let tick = 0; tick < 3; tick++) { await delay(1000); Object.assign(result, await scan(result.tabId)); }
    }
    if (result.matched.company) result.page.company = result.matched.company;
    if (result.matched.title) result.page.title = result.matched.title;
    for (const field of result.fields.filter(field=>field.attached_file)) result.completed.push({...field,value:field.attached_file,source:'already-attached'});
    await progress(result, "Matching profile answers...");
    await matchAndFill(result, true);
    await rescanRevealed(result);
    try {await fillMatchedSkills(result);}catch(error){result.summary.errors.push("Skills: "+error.message);}
    result.progress = result.coverQuestions?.length ? "Answer the cover letter questions below." : "Autofill finished. Review the page.";
  } catch (error) { result.error = error.message; result.traceError={error:error.message,stack:error.stack}; }
  finally {
    if(result.tabId)await chrome.scripting.executeScript({target:{tabId:result.tabId,allFrames:true},func:error=>{const api=globalThis.autoApplyContent;if(error)api?.trace("autofill","error",null,error);return api?.endTrace();},args:[result.traceError || null]}).catch(()=>{});
    running = false; result.busy = false;
  }
  await report(result);
  if (!cancelledTabs.has(result.tabId) && !result.error && result.writing?.length) {
    const task = continueWriting(result).then(()=>advanceAfterWriting(result)).catch(error => {
      result.summary.errors.push(error.message);
      return report(result);
    }).finally(() => { result.writingSettled=true; if(activeResults.get(result.tabId)===result)report(result); snapshots.capture(result); if (writingTasks.get(result.tabId) === task) writingTasks.delete(result.tabId); });
    writingTasks.set(result.tabId, task);
  }
  else snapshots.capture(result);
  if (!cancelledTabs.has(result.tabId) && !result.error && !result.waiting && advanceCount < 10 && (await advancePreference()).enabled && await clickNext(result, true)) {
    if (cancelledTabs.has(result.tabId)) return result;
    pendingSteps.delete(result.tabId);
    return autofill(result.tabId, result.matched, advanceCount + 1);
  }
  return result;
}

async function continueWriting(result) {
  if (cancelledTabs.has(result.tabId)) return;
  for (const field of [...result.writing].filter(field => field.kind !== "file")) {
    if (!result.writing.some(item => item.key === field.key)) continue;
    if (cancelledTabs.has(result.tabId) || activeResults.get(result.tabId) !== result) return;
    result.writingProgress = 'Writing: ' + field.label + '...';
    await report(result);
    try {
      if (cancelledTabs.has(result.tabId)) return;
      const written = await api("/ext/write", {...context(result), question: field.label, max_length: field.max_length,
        instruction: field.max_length === null ? "" : 'Use at most ' + field.max_length + ' characters.'});
      if (cancelledTabs.has(result.tabId) || activeResults.get(result.tabId) !== result) return;
      await fill(result, [{key: field.key, status: "RESOLVED", value: written.answer, source: "AI"}]);
      await rescanRevealed(result);
      result.writingProgress = 'Answer filled: ' + field.label;
      if (written.warning) result.summary.errors.push(field.label + ': ' + written.warning);
    } catch (error) { result.writingProgress = field.label + ': ' + error.message; result.summary.errors.push(result.writingProgress); }
    if (cancelledTabs.has(result.tabId) || activeResults.get(result.tabId) !== result) return;
    await report(result);
  }
  if (cancelledTabs.has(result.tabId) || activeResults.get(result.tabId) !== result) return;
  if (result.writing.some(field => field.kind === "file" && /cover/i.test(field.label))) {
    try { await coverLetter(result); }
    catch (error) { result.coverProgress = 'Cover letter error: ' + error.message; result.summary.errors.push(result.coverProgress); }
    finally { result.coverBusy = false; }
    if (activeResults.get(result.tabId) === result) await report(result);
  }
}

async function advanceAfterWriting(result) {
  if (!running && !cancelledTabs.has(result.tabId) && activeResults.get(result.tabId) === result && (await advancePreference()).enabled && await clickNext(result,true)) {
    if (cancelledTabs.has(result.tabId)) return;
    pendingSteps.delete(result.tabId); await autofill(result.tabId,result.matched);
  }
}
function chatReplacementIntent(value){return typeof value==='string' && /^(?:(?:please|can you|could you|would you)\s+)*(?:replace|overwrite|fill|put|insert|paste|change|update)\b/i.test(value) && /\b(?:field|textarea|text box|cover letter|answer)\b/i.test(value) && !/\b(?:don.t|do not|never|cannot|can.t)\b/i.test(value);}
function authorizedChatField(request,context){
  if(!chatReplacementIntent(request))return null;
  let fields=context?.fields || [];
  if(/\b(?:current|focused|this)\s+(?:field|textarea|text box)\b/i.test(request))fields=fields.filter(f=>f.key===context.current_key);
  else {const named=fields.filter(f=>f.label?.trim() && request.toLowerCase().includes(f.label.trim().toLowerCase()));fields=named.length?named:/cover letter/i.test(request)?fields.filter(f=>/cover/i.test(f.label)&&/letter/i.test(f.label)):[];}
  return fields.length===1?fields[0]:null;
}
function validChatAction(action,context){
  if(!Array.isArray(context?.fields))return false;
  const field=context.fields.find(f=>f.key===action?.key);
  return action && Object.keys(action).sort().join(',')==='key,text,type' && action.type==='replace_text' &&
    field && ['text','textarea','email','tel','url'].includes(field.kind) && typeof action.text==='string' && action.text.trim() &&
    action.text.length<=12000 && (field.max_length==null || action.text.length<=field.max_length);
}
let skillsRunning=false;
async function action(message) {
  if(analysisTasks.size && ['autofill','autofill-skills','chat-replace','fill-answer','cover-letter','next-page','manual-submit','page-context','load-options'].includes(message.type))
    throw Error('Application is busy analyzing. Wait and retry.');
  if(message.type==='skill-selection') {
    const id=message.tabId || (await chrome.tabs.query({active:true,currentWindow:true}))[0]?.id;
    if(message.selection)skillSelections.set(id,message.selection);
    return {ok:true,selection:skillSelections.get(id)};
  }
  if (message.type === 'skills-available') {
    const tab=message.tabId?await chrome.tabs.get(message.tabId):(await chrome.tabs.query({active:true,currentWindow:true}))[0];
    if (!tab?.id || !/^https?:\/\//.test(tab.url || '')) return {skills_available:false};
    // Button discovery must not rescan and replace records during autofill.
    const frames=await chrome.scripting.executeScript({target:{tabId:tab.id,allFrames:true},
      func:()=>({available:globalThis.autoApplyContent?.skillsAvailable(),page:globalThis.autoApplyContent?.skillsPage()})});
    return {tabId:tab.id,url:tab.url,matched:activeResults.get(tab.id)?.matched,skills_available:frames.some(frame=>frame.result?.available),page:frames.find(frame=>frame.frameId===0)?.result?.page};
  }
  if(['autofill-skills','cancel-skills','skills-status'].includes(message.type)) {
    const tab=message.tabId?await chrome.tabs.get(message.tabId):(await chrome.tabs.query({active:true,currentWindow:true}))[0];
    if(!tab?.id) throw Error('Open an application page first.');
    if(message.type==='cancel-skills' || message.type==='skills-status') {
      const frames=await chrome.scripting.executeScript({target:{tabId:tab.id,allFrames:true},func:cancel=>cancel?globalThis.autoApplyContent?.cancelSkills():globalThis.autoApplyContent?.skillsStatus(),args:[message.type==='cancel-skills']});
      return frames.find(f=>f.result?.busy)?.result || frames.find(f=>f.result?.progress)?.result || {};
    }
    if(running || skillsRunning || analysisTasks.size) throw Error('Application is busy. Wait and retry.');
    skillsRunning=true;
    try {
      const profile=message.skills ? {skills:message.skills} : await api('/ext/profile-sections');
      const scanned=await chrome.scripting.executeScript({target:{tabId:tab.id,allFrames:true},func:()=>globalThis.autoApplyContent?.scan()});
      const frame=scanned.find(f=>f.result?.skills_available);
      if(!frame) throw Error('No skills tag field found.');
      const [filled]=await chrome.scripting.executeScript({target:{tabId:tab.id,frameIds:[frame.frameId]},func:skills=>globalThis.autoApplyContent.autofillSkills(skills),args:[profile.skills || []]});
      return filled.result;
    } catch(error) {
      await saveTrace({host:new URL(tab.url).hostname,kind:'skills',entries:[{timestamp:new Date().toISOString(),step:'skills',action:'error',error:error.message,stack:error.stack}]}).catch(()=>{});
      throw error;
    } finally {skillsRunning=false;}
  }
  if(message.type==='autofill' && skillsRunning) throw Error('Wait for skills autofill to finish.');
  if (message.type === "autofill") return autofill(message.tabId);
  if(['chat-context','chat-replace'].includes(message.type)) {
    const tab=message.tabId?await chrome.tabs.get(message.tabId):(await chrome.tabs.query({active:true,currentWindow:true}))[0];
    if(!tab?.id || !/^https?:/.test(tab.url||''))throw Error('Open an application page first.');
    const busy=()=>running || skillsRunning || analysisTasks.size || writingTasks.has(tab.id) || activeResults.get(tab.id)?.coverBusy;
    if(message.type==='chat-context') {
      await showSidebar(tab.id,'open');
      const [frame]=await chrome.scripting.executeScript({target:{tabId:tab.id},func:options=>globalThis.autoApplyContent.chatContext(options),args:[{passive:Boolean(busy()),request:typeof message.request==='string'?message.request.slice(0,12000):''}]});
      return {...frame.result,tabId:tab.id,documentId:frame.documentId};
    }
    if(busy())throw Error('Application is busy. Wait for autofill, skills or writing to finish, then retry.');
    const ctx=message.context,action=message.action;
    if(ctx?.tabId!==tab.id || ctx?.url!==tab.url)throw Error('Application changed. Select the field and ask again.');
    if(!chatReplacementIntent(message.request) || authorizedChatField(message.request,ctx)?.key!==action?.key || !validChatAction(action,ctx))
      throw Error('Replacement was not authorized for this field. Select the field and ask again.');
    const [frame]=await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>globalThis.autoApplyContent?.chatContext({passive:true})});
    if(frame.documentId!==ctx.documentId || frame.result?.document!==ctx.document)throw Error('Application changed. Select the field and ask again.');
    if(busy())throw Error('Application is busy. Wait for autofill, skills or writing to finish, then retry.');
    const [filled]=await chrome.scripting.executeScript({target:{tabId:tab.id,documentIds:[ctx.documentId]},func:async(ctx,action)=>{
      try{return await globalThis.autoApplyContent.chatReplace(ctx,action);}catch(error){return {error:error.message};}
    },args:[ctx,action]});
    return filled?.result || {error:'Application changed. Select the field and retry.'};
  }
  if (message.type === "page-context") return scan(message.tabId);
  if (message.type === 'auto-advance-preference') return advancePreference(message.enabled);
  if (message.type === 'snapshot-preference') return snapshots.preference(message.enabled);
  if (message.type === 'cancel-form-wait') {
    const {lastResult} = await chrome.storage.session.get('lastResult');
    const id = message.tabId || lastResult?.tabId; cancelWait(id);
    const result = activeResults.get(id); if (result) {result.waiting=false;result.progress='Form waiting cancelled.';return report(result);} return {};
  }
  if (message.type === 'page-snapshot') {
    const tab=message.tabId?await chrome.tabs.get(message.tabId):(await chrome.tabs.query({active:true,currentWindow:true}))[0];
    if(!tab?.id || !/^https?:/.test(tab.url || ''))throw Error('Open an application page first.');
    if(analysisTasks.has(tab.id))return analysisTasks.get(tab.id);
    if(running || autofillTasks || skillsRunning || analysisBlockers || writingTasks.has(tab.id) || manualActionTabs.size || batchBusy ||
       activeResults.get(tab.id)?.busy || activeResults.get(tab.id)?.coverBusy || activeResults.get(tab.id)?.waiting ||
       activeResults.get(tab.id)?.writing?.length && !activeResults.get(tab.id)?.writingSettled)
      throw Error('Application is busy. Wait for filling or writing to finish, then retry Analyze page.');
    const task=(async()=>{
      const current=activeResults.get(tab.id),application_id=current?.url===tab.url?current.matched?.application_id:undefined;
      const inspect=(after=false)=>chrome.scripting.executeScript({target:{tabId:tab.id,allFrames:true},func:after=>{
        const api=globalThis.autoApplyContent;
        return api?{...api.inspectPage({repair:after}),...api.pageStructure()}:null;
      },args:[after]});
      const before=await inspect();
      const targets=before.filter(f=>f.result);
      if(targets.some(f=>f.result.diagnostic.busy))throw Error('Application is busy. Wait and retry.');
      const started=[];const token=crypto.randomUUID();let stopped='none',attempts=0,filled=0;
      async function bound(frame,method,args=[]){
        if((await chrome.tabs.get(tab.id)).url!==tab.url || activeResults.get(tab.id)!==current ||
           (current?.url===tab.url?current.matched?.application_id:undefined)!==application_id)
          throw Error('Application changed. Analyze page again.');
        const [value]=await chrome.scripting.executeScript({target:{tabId:tab.id,documentIds:[frame.documentId]},
          func:async(method,args)=>{try{return await globalThis.autoApplyContent[method](...args);}catch(error){return {error:error.message};}},args:[method,[token,...args]]});
        if(value?.documentId!==frame.documentId || value?.result?.error)throw Error(value?.result?.error || 'Application changed. Analyze page again.');
        return value.result;
      }
      // Prepare only the existing controls. Retry known unresolved widgets once,
      // rediscovering live choices and resolving through the usual grounded API.
      try{
        for(const frame of targets){
          const prepared=await bound(frame,'repairStart');started.push(frame);
          for(let pass=0;pass<2;pass++){
            let fields=await bound(frame,'repairOptions');
            if(pass)fields=fields.filter(f=>['combobox','select','checkbox','radio'].includes(f.kind));
            if(!fields.length)break;
            attempts=Math.max(attempts,pass+1);
            const response=await api('/ext/answers',{url:tab.url,application_id,page:prepared.page,fields});
            await bound(frame,'repairFill',[response.answers || []]);
          }
        }
      }catch{stopped='interrupted';}
      const repaired=[];
      for(const frame of started){const result=await bound(frame,'repairFinish');repaired.push({...frame,result});filled+=result.filled;if(result.stopped!=='none')stopped=result.stopped;}
      // Authorized fills can change the snapshot identity. Bind the fresh post
      // capture independently instead of comparing it to the unfilled snapshot.
      const frames=await inspect(true);
      if(!targets.every(old=>frames.some(f=>f.frameId===old.frameId && f.documentId===old.documentId)))throw Error('Application changed. Analyze page again.');
      const available=frames.filter(f=>f.result);
      if(!available.some(f=>f.frameId===0))throw Error('Page inspection unavailable. Reload the application and retry.');
      const nodes=[],fields=[];let truncated=false,size=0,fieldBytes=0;const encoder=new TextEncoder();
      for(const frame of available){
        truncated ||= frame.result.truncated || frame.result.diagnostic.truncated;
        for(const field of frame.result.diagnostic.fields){const row={...field,frame_id:frame.frameId,page_identity:frame.result.identity};fieldBytes+=encoder.encode(JSON.stringify(row)).length;if(fields.length>=50 || fieldBytes>220000){truncated=true;break;}fields.push(row);}
      }
      const beforeFields=[];let beforeBytes=0;
      for(const frame of targets){
        truncated ||= frame.result.truncated || frame.result.diagnostic.truncated;
        for(const field of frame.result.diagnostic.fields){
          const row={...field,frame_id:frame.frameId,page_identity:frame.result.identity};
          beforeBytes+=encoder.encode(JSON.stringify(row)).length;
          if(beforeFields.length>=50 || beforeBytes>220000){truncated=true;break;}beforeFields.push(row);
        }
      }
      const inspections=available.slice(0,20).map(f=>({frame_id:f.frameId,...f.result.inspection}));
      const diagnostic={reason:'page_analysis',fields,truncated,inspections,repair:{filled,attempts,stopped,before:{fields:beforeFields}},busy:false,responsive:available.every(f=>f.result.diagnostic.responsive),inspection_ms:Math.min(30000,available.reduce((sum,f)=>sum+f.result.diagnostic.inspection_ms,0))};
      const budget=800000-encoder.encode(JSON.stringify(diagnostic)).length;
      for(const frame of available)for(const node of frame.result.nodes){size+=encoder.encode(JSON.stringify(node)).length+1;if(nodes.length>=10000 || size>budget){truncated=true;break;}nodes.push(node);}
      async function unchanged(){
        if((await chrome.tabs.get(tab.id)).url!==tab.url || activeResults.get(tab.id)!==current ||
           (current?.url===tab.url?current.matched?.application_id:undefined)!==application_id)return false;
        const latest=await chrome.scripting.executeScript({target:{tabId:tab.id,allFrames:true},func:()=>globalThis.autoApplyContent?.repairContext()});
        return available.every(frame=>latest.some(f=>f.frameId===frame.frameId && f.documentId===frame.documentId && f.result?.identity===frame.result.identity && f.result.revision===frame.result.edit_revision));
      }
      if(!await unchanged())throw Error('Application changed. Analyze page again.');
      const context=new URL(tab.url);context.search='';context.hash='';context.username='';context.password='';
      const saved=await api('/ext/snapshot',{host:context.hostname,context_url:context.href,nodes,truncated,diagnostic});
      if(!await unchanged())throw Error('Application changed. Analyze page again.');
      if(saved.ok && current?.url===tab.url && current.fields &&
         Object.entries(current.pageIdentities || {}).every(([id,page])=>targets.some(frame=>String(frame.frameId)===id && frame.documentId===page.documentId))){
        const live=new Map(repaired.flatMap(frame=>frame.result.fields.map(field=>[`${frame.frameId}:${field.key}`,{...field,key:`${frame.frameId}:${field.key}`}])));
        const completed=[...(current.completed || [])];let added=0;
        for(const frame of repaired)for(const answer of frame.result.completed){
          const item={...answer,key:`${frame.frameId}:${answer.key}`},index=completed.findIndex(old=>old.key===item.key);
          if(index<0){completed.push(item);added++;}else completed[index]=item;
        }
        const pending=current.pending.map(field=>({...field,...live.get(field.key)})).filter(field=>!live.get(field.key)?.answered);
        const updated={...current,snapshotFeedback:undefined,completed,pending,
          fields:current.fields.map(field=>({...field,...live.get(field.key)})),
          pageIdentities:Object.fromEntries(available.map(frame=>[frame.frameId,{identity:frame.result.identity,documentId:frame.documentId}])),
          summary:{...current.summary,filled:current.summary.filled+added,needs_input:pending.map(field=>field.label)}};
        await report(updated,unchanged);
        if(!await unchanged())throw Error('Application changed. Analyze page again.');
        Object.assign(current,updated);
      }
      if(!await unchanged())throw Error('Application changed. Analyze page again.');
      const unavailable=!fields.length && inspections.find(i=>i.message);
      return {...saved,...(unavailable?{summary:unavailable.message,findings:[{label:'Page availability',notes:[unavailable.message]}]}:{}),tabId:tab.id,url:tab.url,application_id:current?.url===tab.url?current.matched?.application_id:undefined,busy:diagnostic.busy};
    })();
    analysisTasks.set(tab.id,task);
    try{return await task;}finally{analysisTasks.delete(tab.id);}
  }
  if(message.type==='retry-submission-tracking') {
    const {tabResults={},autofillTabs={}}=await chrome.storage.session.get(['tabResults','autofillTabs']);
    const current=activeResults.get(message.tabId)||tabResults[message.tabId], authorized=autofillTabs[message.tabId];
    const tab=await chrome.tabs.get(message.tabId);
    if(!current?.siteConfirmed || !authorized || current.url!==tab.url || authorized.applied || confirmingTabs.has(tab.id))
      throw Error('Application changed. Review the employer page.');
    const [frame]=await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>globalThis.autoApplyContent?.confirmationEvidence()});
    if(!frame.result)throw Error('The employer confirmation is no longer visible.');
    confirmingTabs.add(tab.id);
    try {
      if(!authorized.matched?.application_id){authorized.matched=await api('/ext/match',{url:tab.url});current.matched=authorized.matched;}
      if(!authorized.matched?.application_id)throw Error('No matching application to track');
      await api('/ext/applied',{application_id:authorized.matched.application_id,run_id:authorized.matched.run_id,url:tab.url,evidence:frame.result});
      authorized.applied=true;current.applied=true;current.trackingError='';await chrome.storage.session.set({autofillTabs});
    }catch(error){current.trackingError='Tracking failed: '+error.message+'. Retry applied tracking.';}
    finally{confirmingTabs.delete(tab.id);}
    return report(current);
  }
  if (message.type === 'manual-submit') {
    if(running || skillsRunning || batchBusy)throw Error('Wait for autofill and the run to finish.');
    const scanned=await scan(message.tabId);
    const {tabResults={},autofillTabs={},chromeRun={}}=await chrome.storage.session.get(['tabResults','autofillTabs','chromeRun']);
    const current=activeResults.get(scanned.tabId)||tabResults[scanned.tabId];
    const authorized=autofillTabs[scanned.tabId];
    if(chromeRun.active || chromeRun.current || !current || current.busy || current.waiting || current.submitting ||
       current.writing?.length || cancelledTabs.has(scanned.tabId) || scanned.url!==current.url ||
       scanned.next_action?.kind!=='submit' || message.expected?.url!==scanned.url ||
       message.expected?.step!==scanned.next_action.step || current.next_action?.step!==scanned.next_action.step)
      throw Error('Application changed or is busy. Review the employer page and run Autofill again.');
    if(authorized){delete authorized.undone;authorized.submitAt=Date.now();authorized.submitUrl=scanned.url;await chrome.storage.session.set({autofillTabs});}
    current.submitting=true;current.progress='Submitting... Waiting for employer confirmation.';await report(current);
    try {
      if(running || skillsRunning || batchBusy || activeResults.has(scanned.tabId) && activeResults.get(scanned.tabId)!==current || current.waiting)throw Error('Application became busy. Review the employer page.');
      const [frame]=await chrome.scripting.executeScript({target:{tabId:scanned.tabId},
        func:expected=>globalThis.autoApplyContent.manualSubmit(expected),args:[message.expected]});
      if(!frame.result?.clicked || frame.result.errors?.length){
        current.submitting=false;current.progress=frame.result?.errors?.join('\n') || frame.result?.message || 'Submission needs review on the employer page.';
        if(authorized){delete authorized.submitAt;await chrome.storage.session.set({autofillTabs});}
      }
    } catch(error){current.submitting=false;current.progress='Submission could not be activated. Review the employer page.';if(authorized){delete authorized.submitAt;await chrome.storage.session.set({autofillTabs});}await report(current);throw error;}
    setTimeout(()=>{if(current.submitting&&!current.siteConfirmed){current.submitting=false;current.progress='No confirmation yet. Review the employer page before trying again.';report(current);}},10000);
    return report(current);
  }
  if (message.type === 'next-page') {
    if(running || skillsRunning)throw Error('Wait for autofill to finish.');
    const scanned=await scan(message.tabId);
    const {tabResults={}}=await chrome.storage.session.get('tabResults');
    const current=activeResults.get(scanned.tabId)||tabResults[scanned.tabId]||{...scanned,pending:[],writing:[],summary:{filled:0,skipped:0,errors:[]}};
    if(await clickNext(current) && !running)return autofill(current.tabId,current.matched);
    return current;
  }
  if (running || analysisTasks.size) throw new Error("Application is busy. Wait and retry.");
  const {lastResult, tabResults={}} = await chrome.storage.session.get(['lastResult','tabResults']);
  const stored=message.tabId?tabResults[message.tabId]:lastResult;
  const result=activeResults.get(stored?.tabId) || stored;
  if (result?.tabId) activeResults.set(result.tabId, result);
  if(analysisTasks.size)throw Error('Application is busy analyzing. Wait and retry.');
  if (!result?.page) throw new Error("Run autofill on an application first.");
  if (message.type === 'load-options') {
    await loadOptions(result, message.key);
  } else if (message.type === "fill-answer") {
    if(message.url && message.url!==result.url || message.label && !result.fields.some(field=>field.key===message.key && field.label===message.label))throw new Error("Application changed. Open the answer helper again.");
    await fill(result, [{key: message.key, status: "RESOLVED", value: message.value, force: true, source: message.source === "AI" ? "AI" : "you"}]);
    const field = result.fields.find(field => field.key === message.key);
    if (message.remember && field && !["textarea","file"].includes(field.kind)) {
      await api("/ext/remember", {...field, ...context(result), url: result.url, value: message.value});
      result.saved = "Saved";
    }
    await rescanRevealed(result);
    if ((await advancePreference()).enabled && await clickNext(result,true)) return autofill(result.tabId,result.matched);
  } else if (message.type === 'undo-applied') {
    await api('/ext/unapplied',{application_id:result.matched.application_id});result.applied=false;
    result.progress='';result.trackingError='';result.trackingNote='Tracking undone. Site submission is unchanged.';
    const {autofillTabs={}}=await chrome.storage.session.get('autofillTabs');
    if(autofillTabs[result.tabId]){delete autofillTabs[result.tabId].applied;delete autofillTabs[result.tabId].submitAt;autofillTabs[result.tabId].undone=true;await chrome.storage.session.set({autofillTabs});}
  } else if (message.type === "skip-answer") {
    result.pending = result.pending.filter(field => field.key !== message.key);
    result.summary.needs_input = result.pending.map(field => field.label);
  } else if (message.type === "cover-letter") {
    if (result.coverBusy) return result;
    running = true; result.busy = true;
    try { await coverLetter(result, message.answers); }
    catch (error) { result.error = error.message; result.coverProgress = "Cover letter error: " + error.message; }
    finally { running = false; result.busy = false; result.coverBusy = false; }
  }
  return report(result);
}

// Direct entry point for local service-worker integration tests.
globalThis.autoApplyAutofill = autofill;
const pendingSteps = new Set();
let stepDrain;
async function drainSteps() {
  if (running || batchBusy || analysisTasks.size) { stepDrain = setTimeout(drainSteps, 500); return; }
  const tabId = pendingSteps.values().next().value;
  if (tabId === undefined) return;
  pendingSteps.delete(tabId);
  const {autofillTabs = {}} = await chrome.storage.session.get("autofillTabs");
  if (autofillTabs[tabId] && !cancelledTabs.has(tabId)) await autofill(tabId);
  stepDrain = setTimeout(drainSteps, 100);
}
chrome.runtime.onMessage.addListener((message, sender) => {
  if(message.type==='analysis-changed' && sender.id===chrome.runtime.id && sender.tab?.id){
    chrome.runtime.sendMessage({type:'analysis-invalidated',tabId:sender.tab.id}).catch(()=>{});return;
  }
  if (message.type !== "workday-step" || sender.id !== chrome.runtime.id ||
      !sender.tab?.id || sender.frameId !== 0) return;
  if (analysisTasks.has(sender.tab.id) || activeResults.get(sender.tab.id)?.busy || cancelledTabs.has(sender.tab.id)) return;
  const previous=activeResults.get(sender.tab.id);
  if(previous){previous.stalePage=true;previous.snapshotFeedback=undefined;report(previous);}
  pendingSteps.add(sender.tab.id);
  clearTimeout(stepDrain);
  stepDrain = setTimeout(drainSteps, 100);
});
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || !sender.url?.startsWith(chrome.runtime.getURL(""))) return;
  if (message.type === "run-status-refresh") {
    refreshRunStatus().then(respond, error => respond({error: error.message}));
    return true;
  }
  if (!["skill-selection", "skills-available", "autofill-skills", "cancel-skills", "skills-status", "autofill", "page-context", "chat-context", "chat-replace", "fill-answer", "skip-answer", "cover-letter", "undo-applied", "next-page", "manual-submit", "retry-submission-tracking", "load-options", "auto-advance-preference", "cancel-form-wait", "page-snapshot", "snapshot-preference"].includes(message.type)) return;
  if(message.type==='page-snapshot'){
    const bound=Number(new URL(sender.url).searchParams.get('tabId'));
    if(bound && message.tabId!==bound){respond({error:'Application binding changed. Reopen the sidebar.'});return;}
  }
  if(['manual-submit','retry-submission-tracking'].includes(message.type) && (sender.url?.split('?')[0]!==chrome.runtime.getURL('sidepanel.html') ||
     sender.tab?.id && !sender.tab.url?.startsWith(chrome.runtime.getURL('')) && sender.tab.id!==message.tabId ||
     new URL(sender.url).searchParams.has('tabId') && Number(new URL(sender.url).searchParams.get('tabId'))!==message.tabId))return;
  if(message.type==='manual-submit') {
    if(manualActionTabs.has(message.tabId)){respond({error:'Submission is already pending.'});return;}
    manualActionTabs.add(message.tabId);
    action(message).then(respond,error=>respond({error:error.message})).finally(()=>manualActionTabs.delete(message.tabId));
    return true;
  }
  const blocksAnalysis=['autofill','autofill-skills','chat-replace','fill-answer','cover-letter','next-page','page-context','load-options'].includes(message.type);
  if(blocksAnalysis)analysisBlockers++;
  action(message).then(respond, error => respond({error: error.message})).finally(()=>{if(blocksAnalysis)analysisBlockers--;});
  return true;
});
chrome.commands.onCommand.addListener(command => {
  if(command==='autofill')autofill();
  if(command==='toggle-sidebar')showSidebar(undefined,'toggle').catch(console.error);
});

// Alarms wake a suspended MV3 worker; the short timer advances an active run promptly.
let batchBusy = false, runEpoch = 0;
const staleRun = epoch => epoch !== runEpoch;
async function saveRun(run, changePause = false, epoch) {
  if(epoch!==undefined && staleRun(epoch))return;
  if (changePause) await chrome.storage.session.set({chromeRunPaused: Boolean(run.paused)});
  const {chromeRunPaused = false} = await chrome.storage.session.get("chromeRunPaused");
  if(epoch!==undefined && staleRun(epoch))return;
  run.paused = chromeRunPaused;
  await chrome.storage.session.set({chromeRun: run});
  await chrome.runtime.sendMessage({type: "run-status", run}).catch(() => {});
}
async function refreshRunStatus() {
  const epoch=runEpoch;
  let statusError = "";
  try { await api("/ext/run/status"); }
  catch (error) { statusError = error.message; }
  const {chromeRun: run = {index: 0, total: 0}} = await chrome.storage.session.get("chromeRun");
  run.error = statusError;
  await saveRun(run,false,epoch);
  return run;
}
async function waitForTab(tabId, epoch) {
  const end = Date.now() + 30000;
  while (Date.now() < end) {
    if (cancelledTabs.has(tabId) || epoch!==undefined && staleRun(epoch)) throw Error('Application work stopped.');
    if ((await chrome.tabs.get(tabId)).status === "complete") {
      await new Promise(resolve => setTimeout(resolve, 500));
      if ((await chrome.tabs.get(tabId)).status === "complete") return;
    }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error("Application page did not finish loading.");
}
async function openApplication(result) {
  // A job landing page can lead through Workday's entry dialog before sign-in.
  for (let step = 0; step < 2; step++) {
    if(cancelledTabs.has(result.tabId))return result;
    const clicked = await chrome.scripting.executeScript({target: {tabId: result.tabId, allFrames: true},
      func: () => globalThis.autoApplyContent?.enterApplication()});
    if (!clicked.some(frame => frame.result)) break;
    const end = Date.now() + 10000;
    do {
      await waitForTab(result.tabId);
      result = await scan(result.tabId);
      if (result.sign_in || result.fields.length || result.manual_entry) break;
      await new Promise(resolve => setTimeout(resolve, 100));
    } while (Date.now() < end);
    if (result.sign_in || result.fields.length) break;
  }
  return result;
}
// Persist the claim and tab before work begins. A worker restart must never
// refill a reviewed page, or turn a lost session binding into a duplicate tab.
async function stopRunWork(tabId) {
  if(!tabId)return;
  cancelWait(tabId);
  const tab=await chrome.tabs.get(tabId).catch(()=>null);
  // Chrome queues injected scripts behind a loading document. Pausing/closing
  // must not wait for that document (or its employer request) to complete.
  if(tab?.status!=='complete')return;
  await Promise.race([chrome.scripting.executeScript({target:{tabId,allFrames:true},func:()=>globalThis.autoApplyContent?.cancelSkills()}).catch(()=>{}),delay(500)]);
}
async function runStatus(run, epoch) {
  const status=await api('/ext/run/status');
  if(staleRun(epoch))return false;
  const item=status.items?.find(i=>i.application_id===run.current?.application_id && i.run_id===run.current?.run_id);
  if(run.current && status.items && item?.state!=='opened') {
    const stoppedEpoch=++runEpoch;
    await stopRunWork(run.tabId);
    delete run.current;delete run.tabId;delete run.result;delete run.started;
    run.active=status.items.some(i=>['pending','opened'].includes(i.state));
    await saveRun(run,false,stoppedEpoch);
    return false;
  }
  return status;
}
export async function runTick() {
  if (batchBusy || running || skillsRunning || autofillTasks || analysisTasks.size || analysisBlockers || writingTasks.size || manualActionTabs.size) return;
  batchBusy = true;
  let epoch=runEpoch;
  let {chromeRun: run = {paused: false, index: 0, total: 0}} = await chrome.storage.session.get('chromeRun');
  try {
    const {chromeRunPaused = false} = await chrome.storage.session.get('chromeRunPaused');
    if (chromeRunPaused) return;
    const status=await runStatus(run,epoch);
    if(!status || staleRun(epoch))return;
    if (!run.current) {
      // Reconcile a claim made just before a worker/session interruption.
      const opened=status.items?.find(i=>i.state==='opened');
      const item=opened ? {...opened,index:status.items.indexOf(opened)+1,total:status.items.length} : await api('/ext/run/next');
      if(staleRun(epoch))return;
      if(item.wait)return;
      if(item.done){run.active=false;await saveRun(run,false,epoch);return;}
      epoch=++runEpoch;
      run={...run,active:true,current:item,index:item.index||1,total:item.total||run.total,error:''};
      delete run.started;delete run.result;delete run.tabId;delete run.progressSent;
      if(opened) {
        const {autofillTabs={}}=await chrome.storage.session.get('autofillTabs');
        const tabs=await chrome.tabs.query({});
        const tab=tabs.find(t=>autofillTabs[t.id]?.matched?.application_id===item.application_id && autofillTabs[t.id]?.matched?.run_id===item.run_id)
          || tabs.find(t=>(t.pendingUrl||t.url)===item.url);
        if(tab){run.tabId=tab.id;run.started=true;}
        else {run.result={application_id:item.application_id,run_id:item.run_id,state:'skipped',detail:'Application tab no longer available'};}
      }
      await saveRun(run,false,epoch);
    }
    if(staleRun(epoch))return;
    if(run.tabId) {
      try{await chrome.tabs.get(run.tabId);}catch{
        await stopRunWork(run.tabId);
        run.result={application_id:run.current.application_id,run_id:run.current.run_id,state:'skipped',detail:'Application tab closed'};
      }
    }
    if(run.result) {
      await api('/ext/run/result',run.result);
      if(staleRun(epoch))return;
      // Nonterminal progress from older extension versions remains held too.
      if(run.result.state!=='skipped'){delete run.result;run.started=true;await saveRun(run,false,epoch);return;}
      delete run.current;delete run.tabId;delete run.result;delete run.started;
      await saveRun(run,false,epoch);return;
    }
    if (!run.tabId) {
      const tab=await chrome.tabs.create({url:run.current.url,active:true});
      if(staleRun(epoch)){await stopRunWork(tab.id);await chrome.tabs.remove(tab.id).catch(()=>{});return;}
      run.tabId=tab.id;run.phase='loading';await saveRun(run,false,epoch);
    }
    if(!run.started) {
      await waitForTab(run.tabId,epoch);
      if(staleRun(epoch) || !await runStatus(run,epoch))return;
      run.started=true;run.phase='filling';run.error='';await saveRun(run,false,epoch);
      await autofill(run.tabId,run.current);
      await writingTasks.get(run.tabId);
      if(staleRun(epoch) || !await runStatus(run,epoch))return;
    }
    const {tabResults={}}=await chrome.storage.session.get('tabResults');
    let result=activeResults.get(run.tabId)||tabResults[run.tabId];
    if(!activeResults.has(run.tabId) && (!result || result.busy || result.skillsBusy || result.coverBusy)) {
      // The previous worker may have stopped mid-fill. Keep the claim, unlock
      // manual retry, and preserve the page's current values.
      result={...result,tabId:run.tabId,matched:run.current,busy:false,skillsBusy:false,coverBusy:false,
        error:'Autofill interrupted. Review the page or press Autofill to retry.'};
      await report(result);
    }
    // Restart only the read-only form poll, never an already completed fill.
    if(result?.waiting && !formWaits.has(run.tabId) && !cancelledTabs.has(run.tabId))pollForForm(result);
    run.phase=(result?.error || result?.trackingError || result?.summary?.errors?.length || result?.state==='failed')?'failed':result?.waiting?'needs_input':result?.busy?'filling':(result?.state==='needs_input' || result?.pending?.length || result?.writing?.length || result?.coverQuestions?.length)?'needs_input':'filled';
    const detail=(result?.error || result?.trackingError || result?.summary?.errors?.join('; ') || result?.detail || result?.progress || 'Review this application. Close the tab or Skip to continue.').slice(0,2000);
    const progress={application_id:run.current.application_id,run_id:run.current.run_id,state:['filling','loading'].includes(run.phase)?'needs_input':run.phase,detail};
    const signature=JSON.stringify(progress);
    if(signature!==run.progressSent){await api('/ext/run/result',progress);if(staleRun(epoch))return;run.progressSent=signature;}
    run.error='';await saveRun(run,false,epoch);
  } catch(error) {if(!staleRun(epoch)){run.error=error.message;await saveRun(run,false,epoch);}}
  finally {batchBusy=false;}
}
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === "chrome-run") runTick(); });
chrome.alarms.create("chrome-run", {periodInMinutes: 0.5});
setInterval(() => {
  if (batchBusy) chrome.storage.session.get('chromeRun').then(async ({chromeRun:run})=>{
    if(run?.current)await runStatus(run,runEpoch);
  }).catch(()=>{});
  else runTick();
}, 3000);
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && (changes.key || changes.server)) {
    refreshRunStatus().then(() => runTick());
  }
});
chrome.tabs.onActivated.addListener(async ({tabId}) => {
  const {tabResults = {}} = await chrome.storage.session.get("tabResults");
  if (tabResults[tabId]) await chrome.storage.session.set({lastResult: tabResults[tabId]});
});
chrome.tabs.onRemoved.addListener(async tabId => {
  const observedEpoch=runEpoch;
  closedTabs.add(tabId);cancelWait(tabId);activeResults.delete(tabId);
  const {chromeRun:owned}=await chrome.storage.session.get('chromeRun');
  if(!staleRun(observedEpoch) && owned?.tabId===tabId && owned.current) {
    const epoch=++runEpoch;
    await stopRunWork(tabId);
    owned.result={application_id:owned.current.application_id,run_id:owned.current.run_id,state:'skipped',detail:'Application tab closed'};
    await saveRun(owned,false,epoch);
  }
  const {tabResults = {}} = await chrome.storage.session.get("tabResults");
  const {autofillTabs = {}} = await chrome.storage.session.get("autofillTabs");
  cancelWait(tabId);
  activeResults.delete(tabId);
  delete tabResults[tabId]; delete autofillTabs[tabId]; pendingSteps.delete(tabId);
  const {sidebarTabs={}}=await chrome.storage.session.get("sidebarTabs");delete sidebarTabs[tabId];await chrome.storage.session.set({sidebarTabs});
  await chrome.storage.session.set({tabResults, autofillTabs});
});
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || !sender.url?.startsWith(chrome.runtime.getURL("")) || ! ["run-pause","run-cancel","run-skip"].includes(message.type)) return;
  const commandEpoch=++runEpoch;
  (async () => {
    const {chromeRun: run = {index: 0, total: 0}} = await chrome.storage.session.get("chromeRun");
    if(staleRun(commandEpoch))return run;
    if(message.type!=='run-pause') {
      // Advance the epoch before any await; in-flight fills cannot publish or open another tab.
      const current=run.current;
      if(message.type==='run-cancel'){run.paused=true;await saveRun(run,true,commandEpoch);}
      await stopRunWork(run.tabId);
      if(staleRun(commandEpoch))return run;
      await api(message.type==='run-cancel'?'/ext/run/cancel':'/ext/run/skip', message.type==='run-cancel'?{run_id:current?.run_id}:{application_id:current?.application_id,run_id:current?.run_id});
      if(staleRun(commandEpoch))return run;
      delete run.current;delete run.tabId;delete run.result;delete run.started;
      run.active=message.type!=='run-cancel';run.error='';
      if(message.type==='run-cancel'){run.paused=false;await saveRun(run,true,commandEpoch);}else await saveRun(run,false,commandEpoch);
      return run;
    }
    run.paused = Boolean(message.paused);
    if(run.paused)await stopRunWork(run.tabId);
    else if(run.tabId)cancelledTabs.delete(run.tabId);
    await saveRun(run,true,commandEpoch);
    if (!run.paused) runTick();
    return run;
  })().then(respond, error => respond({error: error.message}));
  return true;
});
globalThis.autoApplyRunTick = runTick;
runTick();

const confirmingTabs=new Set();
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
  if(sender.id!==chrome.runtime.id||!sender.tab?.id)return;
  const tabId=sender.tab.id;
  if(message.type==='sidebar-ready' && sender.frameId===0)restoreSidebar(tabId).catch(console.error);
  if(message.type==='sidebar-state' && sender.frameId===0){showSidebar(tabId,message.state).catch(console.error);return;}
  if(message.type==='sidebar-control'&&sender.url?.startsWith(chrome.runtime.getURL(''))){showSidebar(tabId,message.state).then(()=>respond({ok:true}));return true;}
  if(!['ask-ai','sidebar-ready','sidebar-state','user-answer','submit-intent','submission-confirmation'].includes(message.type)||sender.url?.startsWith(chrome.runtime.getURL('')))return;
  (async()=>{
    const {autofillTabs={},tabResults={}}=await chrome.storage.session.get(['autofillTabs','tabResults']);
    if(message.type==='ask-ai') {
      const scanned=await scan(tabId), key=sender.frameId+':'+message.key;
      const field=scanned.fields.find(field=>field.key===key); if(!field)return;
      const result=activeResults.get(tabId)||tabResults[tabId]||{...scanned,pending:[],writing:[],completed:[],summary:{filled:0,skipped:0,errors:[]}};
      result.fields=scanned.fields;
      activeResults.set(tabId,result);await report(result);
      await showSidebar(tabId,'open');
      await chrome.storage.session.set({helperRequest:{tabId,field,result}}); return;
    }
    if(message.type==='sidebar-ready'){
      const previous=activeResults.get(tabId)||tabResults[tabId];
      if(previous && !previous.busy && (previous.url!==sender.tab.url || previous.pageIdentities?.[0]?.documentId!==sender.documentId)) {
        previous.stalePage=true;previous.snapshotFeedback=undefined;await report(previous);
      }
      return;
    }
    const authorized=autofillTabs[tabId],result=activeResults.get(tabId) || tabResults[tabId];
    if(message.type==='user-answer'){
      if(!result||result.url!==sender.tab.url||running)return;
      const key=sender.frameId+':'+message.key,field=result.fields.find(field=>field.key===key);
      if(!field||['file','password','hidden'].includes(field.kind))return;
      const answer={...field,value:message.value,source:'you'};
      result.completed=(result.completed||[]).filter(item=>item.key!==key);result.completed.push(answer);
      result.pending=result.pending.filter(item=>item.key!==key);
      result.writing=result.writing.filter(item=>item.key!==key);
      result.summary.needs_input=result.pending.map(item=>item.label);
      result.summary.needs_writing=result.writing.map(item=>item.label);
      if(field.kind!=='textarea'&&message.value!==''&&message.value!==null){await api('/ext/remember',{...field,...context(result),url:result.url,value:message.value});result.saved='Saved';}
      await report(result);return;
    }
    if(!authorized||sender.frameId!==0||cancelledTabs.has(tabId))return;
    if(message.type==='submit-intent'){if(message.url!==sender.tab.url || result?.url!==sender.tab.url)return;delete authorized.undone;authorized.submitAt=Date.now();authorized.submitUrl=sender.tab.url;await chrome.storage.session.set({autofillTabs});return;}
    if(authorized.applied||authorized.undone||result?.siteConfirmed||confirmingTabs.has(tabId))return;
    const url=new URL(sender.tab.url),strong=(/^(?:.+\.)?(?:greenhouse\.io|lever\.co)$/.test(url.hostname)&&/\/(confirmation|thanks)(?:[/?#]|$)/i.test(url.pathname))||/^(?:.+\.)?ashbyhq\.com$/.test(url.hostname)&&/\/application\/.+\/submitted(?:[/?#]|$)/i.test(url.pathname);
    if(!strong&&(!authorized.submitAt||Date.now()-authorized.submitAt>120000))return;
    if(!message.evidence||message.url!==sender.tab.url || !result || result.matched?.application_id!==authorized.matched?.application_id || new URL(authorized.url).origin!==url.origin)return;
    const [proof]=await chrome.scripting.executeScript({target:{tabId},func:()=>globalThis.autoApplyContent?.confirmationEvidence()});
    if(!strong && (!proof.result || proof.result!==message.evidence))return;
    if(confirmingTabs.has(tabId)||result.siteConfirmed)return;
    confirmingTabs.add(tabId);
    try{
      result.siteConfirmed=true;result.submitting=false;result.progress='';await report(result);
      if(!authorized.matched?.application_id){result.trackingError='No matching application to track.';await report(result);return;}
      await api('/ext/applied',{application_id:authorized.matched.application_id,run_id:authorized.matched.run_id,url:message.url,evidence:message.evidence});
      authorized.applied=true;await chrome.storage.session.set({autofillTabs});
      if(result){result.applied=true;result.trackingError='';result.progress='';await report(result);}
    }catch(error){result.trackingError='Tracking failed: '+error.message+'. Retry applied tracking.';await report(result);throw error;}finally{confirmingTabs.delete(tabId);}
  })().then(respond,error=>respond({error:error.message}));return true;
});

// Content scripts can request only bounded skill catalog operations.
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
  if(sender.id!==chrome.runtime.id || !['skill-aliases','skill-choose'].includes(message.type)) return;
  const request=message.type==='skill-aliases' ? api('/ext/skills/aliases') :
    api('/ext/skills/choose',{skill:message.skill,options:message.options});
  request.then(respond,()=>respond({choice:null,aliases:{}}));
  return true;
});

async function saveTrace(trace) {
  const saved=await api('/ext/trace',trace);
  await chrome.storage.session.set({lastTrace:{filename:saved.filename,saved:new Date().toISOString()}});
  return saved;
}

// Content traces contain only extension actions and structural option metadata.
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
  if(sender.id!==chrome.runtime.id || message.type!=='save-trace')return;
  saveTrace(message.trace).then(respond).catch(error=>respond({error:error.message}));return true;
});
