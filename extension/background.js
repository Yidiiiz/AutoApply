import {api} from "./api.js";
import {snapshotCollector} from "./snapshots.js";
import {dashboardStartHandler} from './dashboard-launcher.js';

const startDashboard = dashboardStartHandler();
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if(message?.type !== 'start-dashboard')return;
  startDashboard(message, sender).then(respond, () => respond({status:'error', error:'start_failed'}));
  return true;
});

// Explicit intents invalidate restores before any asynchronous tab/storage read.
const sidebarVersions=new Map();
const sidebarStates=new Map();
let sidebarQueue=Promise.resolve();
function showSidebar(tabId, mode='open', restoreVersion) {
  if(tabId===undefined)return chrome.tabs.query({active:true,currentWindow:true}).then(([tab])=>{if(!tab?.id)throw Error('Open an application page first.');return showSidebar(tab.id,mode,restoreVersion);});
  const version=restoreVersion ?? (sidebarVersions.get(tabId)||0)+1;
  if(restoreVersion===undefined){sidebarVersions.set(tabId,version);sidebarStates.delete(tabId);if(mode!=='toggle')sidebarStates.set(tabId,mode);if(mode==='closed')automaticPending.delete(tabId);}
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
    await chrome.scripting.executeScript({target:{tabId:tab.id},files:['native-roots.js','credentials.js','content.js']});
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
chrome.tabs.onUpdated.addListener((tabId,change)=>{if(change.url || change.status==='loading'){clearAutoAccount(tabId);chrome.scripting.executeScript({target:{tabId,allFrames:true},func:()=>globalThis.autoApplyCredentials?.invalidate()}).catch(()=>{});chrome.runtime.sendMessage({type:'analysis-invalidated',tabId}).catch(()=>{});}if(change.status==='complete'){
  restoreSidebar(tabId,true).catch(console.error);
  scheduleAutoAccount(tabId);
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
const snapshots=snapshotCollector({api,report,recover:result=>queueAutomaticAnalysis(result.tabId),delay:ms=>delay(ms),current:result=>activeResults.get(result.tabId)===result && !cancelledTabs.has(result.tabId) && !pendingSteps.has(result.tabId)});
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
  const stopped=()=>result.tabId && (closedTabs.has(result.tabId)||cancelledTabs.has(result.tabId));
  reporting = reporting.catch(() => {}).then(async () => {
  if(stopped())return result;
  const {tabResults = {}, applicationAnswers={}} = await chrome.storage.session.get(["tabResults","applicationAnswers"]);
  if(stopped())return result;
  if(stopped())return result;
  if(valid && !await valid())throw Error('Application changed. Analyze page again.');
  if(result.url){
    const key=String(result.matched?.application_id || result.url.replace(/\/apply(?:\/.*)?$/i,"").split(/[?#]/)[0]);
    const saved=applicationAnswers[key] || [];
    for(const answer of result.completed || [])if(answer.source==="AI"){const old=saved.findIndex(item=>item.label===answer.label);if(old>=0)saved.splice(old,1);saved.push({...answer,url:result.url});}
    result.aiAnswers=saved;applicationAnswers[key]=saved;await chrome.storage.session.set({applicationAnswers});
  }
  if(stopped())return result;
  if(valid && !await valid())throw Error('Application changed. Analyze page again.');
  if (result.tabId) tabResults[result.tabId] = result;
  await chrome.storage.session.set({lastResult: result, tabResults});
  if(stopped())return result;
  if(valid && !await valid())throw Error('Application changed. Analyze page again.');
  await chrome.runtime.sendMessage({type: "autofill-result", result}).catch(() => {});
  return result;
  });
  reporting.finally(drainAutomaticAnalysis).catch(()=>{});
  return reporting;
}

// Read-only probes can remain pending in an opaque/navigating verification
// iframe (including about:blank#otp). Discard late results; never race a fill,
// activation or reservation against this timeout.
async function readFrames(options) {
  let timer;
  try {
    return await Promise.race([
      chrome.scripting.executeScript({...options,world:'ISOLATED'}),
      new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Page inspection timed out. Wait for the page to finish loading, then retry.')),1500);})
    ]);
  } finally {clearTimeout(timer);}
}
async function accountFrames(tabId) {
  const probe={func:()=>globalThis.autoApplyCredentials?.describe()};
  const top=await readFrames({...probe,target:{tabId,frameIds:[0]}});
  // A known private account root owns its diagnosis, including visible OTP or
  // CAPTCHA iframe markers. No unrelated frame needs to execute for this path.
  if(top.some(frame=>frame.result?.active))return top;
  return readFrames({...probe,target:{tabId,allFrames:true}});
}
const compactApplicationState=tabId=>readFrames({target:{tabId,allFrames:true},func:()=>{
  const api=globalThis.autoApplyContent;if(!api)return null;
  return {diagnostic:{fields:[],busy:false,truncated:false},state:api.runtimeState(),readiness:api.inputReadiness()};
}});
function postingKey(value) {
  try {
    const url=new URL(value),path=url.pathname.replace(/\/apply(?:\/.*)?$/i,'');
    if(url.hostname.endsWith('.myworkdayjobs.com')) {
      const match=/\/job\/[^/]+\/[^/]+_([^/]+)$/.exec(path);
      return match?url.origin+':'+match[1]:null;
    }
    return url.origin+path;
  } catch {return null;}
}
async function publicJobContext(page,url) {
  const key=postingKey(url);if(!key)return {...page,description:''};
  const {jobContexts={}}=await chrome.storage.session.get('jobContexts');
  const bound=['JobPosting','workday_public'].includes(page?.context_source) && postingKey(page.posting_url)===key;
  if(bound) {
    const previous=jobContexts[key] || {};
    jobContexts[key]={posting_url:page.posting_url,context_source:page.context_source,
      title:String(page.title||'').slice(0,300),company:String(page.company||'').slice(0,300),
      description:String(page.description||previous.description||'').slice(0,6000),
      job_locations:(page.job_locations?.length?page.job_locations:previous.job_locations||[]).slice(0,20).map(v=>({city:String(v.city||'').slice(0,100),region:String(v.region||'').slice(0,100),country:String(v.country||'').slice(0,100)}))};
    // Bounded public metadata only, keyed by posting identity across locale/apply routes.
    const keys=Object.keys(jobContexts);for(const old of keys.slice(0,Math.max(0,keys.length-40)))delete jobContexts[old];
    await chrome.storage.session.set({jobContexts});
  }
  return {...page,...(jobContexts[key]||{}),url};
}
async function scan(tabId) {
  const tab = tabId === undefined ? (await chrome.tabs.query({active: true, currentWindow: true}))[0] : await chrome.tabs.get(tabId);
  if (!tab?.id || !/^https?:\/\//.test(tab.url || "")) throw new Error("Open an HTTP or HTTPS application form first.");
  const accounts=await accountFrames(tab.id);
  const accountFrameIds=accounts.filter(frame=>frame.result?.active).map(frame=>frame.frameId);
  const target=accountFrameIds.length?{tabId:tab.id,frameIds:accountFrameIds}:{tabId:tab.id,allFrames:true};
  let frames = [];
  // Navigation can finish before the content script has initialized.
  for (let attempt = 0; attempt < 20; attempt++) {
    const scanned=await readFrames({target,
      func: async () => {try {return await globalThis.autoApplyContent?.scan();}
        catch(error) {return {scan_error:error.message || String(error)};}}});
    const failure=scanned.find(frame=>frame.error || frame.result?.scan_error);
    if(failure) throw new Error(failure.result?.scan_error || failure.error.message || String(failure.error));
    frames = scanned.filter(frame => frame.result);
    if (frames.length) break;
    await delay(100);
  }
  if (!frames.length) throw new Error("Form scanner is unavailable. Reload the application page and try again.");
  const page = await publicJobContext((frames.find(frame => frame.frameId === 0) || frames[0]).result.page,tab.url);
  const fields = frames.flatMap(frame => frame.result.fields.map(field => ({...field, key: `${frame.frameId}:${field.key}`})));
  if (fields.length > 500) throw new Error("This page has more than 500 fields. Open a smaller form section.");
  return {tabId: tab.id, url: tab.url, page, fields, pageIdentities:Object.fromEntries(frames.map(frame=>[frame.frameId,{identity:frame.result.page_identity,documentId:frame.documentId}])), next_action: frames.find(frame => frame.frameId === 0)?.result.next_action, workday_step: frames.find(frame => frame.frameId === 0)?.result.workday_step, accountMode:accounts.find(f=>f.result?.active)?.result.mode, accountReady:accounts.find(f=>f.result?.active)?.result.actionReady, skills_available: frames.some(frame=>frame.result.skills_available), sign_in: frames.some(frame => frame.result.sign_in), manual_entry: frames.some(frame => frame.result.manual_entry), timings: {scan_ms: frames.reduce((sum, frame) => sum + (frame.result.scan_ms || 0), 0)}};
}

const context = result => ({page: result.page, url: result.url, application_id: result.matched?.application_id});
async function applicationMatch(url,saved={},application){
  const jobUrl=value=>value?.replace(/\/apply(?:\/.*)?$/i,'').split(/[?#]/)[0];
  const matched=typeof application==='object'?application:application?{application_id:application}:
    jobUrl(saved.url)===jobUrl(url)?saved.matched:undefined;
  return matched?.application_id!=null?matched:await api('/ext/match',{url});
}

async function fill(result, values, files = {}, repairBindings) {
  if (cancelledTabs.has(result.tabId)) return;
  if ((await chrome.tabs.get(result.tabId)).url !== result.url) throw new Error("Application page changed. Run autofill again.");
  for (const frameId of new Set(values.map(value => Number(value.key.split(":")[0])))) {
    if (cancelledTabs.has(result.tabId)) return;
    const prefix = `${frameId}:`;
    const local = values.filter(value => value.key.startsWith(prefix)).map(value => ({...value, key: value.key.slice(prefix.length)}));
    const binding=repairBindings?.get(frameId),documentId=binding?.documentId || result.pageIdentities?.[frameId]?.documentId;
    const [frame] = await chrome.scripting.executeScript({target: {tabId: result.tabId,...(documentId?{documentIds:[documentId]}:{frameIds:[frameId]})}, world: "ISOLATED",
      func: async (answers, attachments,token) => {
        if(!token)return globalThis.autoApplyContent.fill(answers, attachments);
        await globalThis.autoApplyContent.repairFill(token,answers,attachments);
        const done=globalThis.autoApplyContent.repairFinish(token),keys=new Set(answers.map(a=>a.key));
        const pending=done.fields.filter(f=>keys.has(f.key) && !f.answered);
        return {filled:done.filled,completed:done.completed,pending,writing:[],skipped:0,errors:[],fill_ms:0};
      }, args: [local, files,binding?.token ?? null]});
    if(!frame || documentId && frame.documentId!==documentId)throw Error('Application changed. Run autofill again.');
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
const formWaitTokens = new Map();
async function waitForApplicationInputs(tabId, automatic=false) {
  const tab=await chrome.tabs.get(tabId),epoch=runEpoch,version=sidebarVersions.get(tabId);
  // Reserve the same minimal record used by passive footer initialization before
  // probing hydration. That listener may update this owner, but cannot replace it.
  let owner=activeResults.get(tabId);
  if(!owner){
    owner={tabId,url:tab.url,page:{},fields:[],pending:[],writing:[],completed:[],
      summary:{filled:0,skipped:0,needs_input:[],needs_writing:[],errors:[]}};
    activeResults.set(tabId,owner);
  }
  const probe=async()=>(await compactApplicationState(tabId)).map(frame=>({...frame,result:frame.result?.readiness}));
  const before=(await probe()).filter(f=>f.result);
  const ready=frames=>frames.some(f=>f.result?.private || f.result?.controls || f.result?.navigation?.kind && f.result.navigation.kind!=='none');
  if(ready(before) || !before.some(f=>f.result.initializing))return {waited:false,owner};
  const validate=async frames=>{
    const {chromeRunPaused=false}=await chrome.storage.session.get('chromeRunPaused');
    if(staleRun(epoch) || chromeRunPaused || closedTabs.has(tabId) || cancelledTabs.has(tabId) ||
       activeResults.get(tabId)!==owner || (await chrome.tabs.get(tabId)).url!==tab.url ||
       sidebarStates.get(tabId)==='closed' && sidebarVersions.get(tabId)!==version ||
       automatic && !(await snapshots.preference()).enabled || !before.every(old=>frames.some(f=>
         f.frameId===old.frameId && f.documentId===old.documentId && f.result?.url===old.result.url && f.result.revision===old.result.revision)))
      throw Error('Application or answer changed while waiting. Retry when the inputs are ready.');
  };
  const status={...owner,tabId,url:tab.url,busy:true,waiting:true,progress:'Waiting for application inputs...'};
  let frames=before;
  try {
    await validate(frames);await report(status);
    const end=Date.now()+8000;
    // Each read rediscovers native/open roots. No root cache crosses this await.
    do {
      await delay(200);
      await validate(frames);
      frames=(await probe()).filter(f=>f.result);
      await validate(frames);
      if(ready(frames) || frames.some(f=>f.result.status==='access_blocked'))break;
    } while(Date.now()<end);
    const empty=!ready(frames),message=frames.find(f=>f.result.message)?.result.message || 'No application inputs are exposed yet. Retry when the form is ready.';
    return {waited:true,empty,message,owner,check:async()=>validate((await probe()).filter(f=>f.result))};
  } finally {
    if(activeResults.get(tabId)===owner && !closedTabs.has(tabId) && !cancelledTabs.has(tabId))
      await report({...status,busy:false,waiting:false,progress:'Check the application inputs, then retry if needed.'});
  }
}
function cancelWait(tabId) {
  chrome.scripting.executeScript({target:{tabId,allFrames:true},func:()=>globalThis.autoApplyCredentials?.invalidate()}).catch(()=>{});
  clearTimeout(formWaits.get(tabId)); formWaits.delete(tabId); cancelledTabs.add(tabId); pendingSteps.delete(tabId);
  formWaitTokens.delete(tabId);
}
function pollForForm(result) {
  const epoch=runEpoch, token=Symbol();
  formWaitTokens.set(result.tabId,token);
  const current=()=>formWaitTokens.get(result.tabId)===token && !staleRun(epoch) && activeResults.get(result.tabId)===result && !cancelledTabs.has(result.tabId);
  clearTimeout(formWaits.get(result.tabId));
  const end = Date.now() + 60000;
  async function tick() {
    if (!current()) return;
    try {
      const scanned = await scan(result.tabId);
      if(!current())return;
      if(scanned.sign_in){const detail=await fillAccount(result,true,Boolean(result.accountPreview));if(!current())return;if(detail && detail!==result.progress){result.progress=result.detail=detail;await report(result);}}
      if (!scanned.sign_in && scanned.fields.length) {
        formWaits.delete(result.tabId); pendingSteps.add(result.tabId); drainSteps(); return;
      }
    } catch {
      // A document transition can temporarily make the scanner unavailable.
    }
    if (!current()) return;
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
    const epoch=runEpoch;
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
    const fields = result.fields.filter(field=>!field.section_managed || !field.answered && (field.date_context || field.section_entry || /degree/i.test(field.label))).map(field => field.options_partial ? {...field, options: []} : field);
    const response = await api("/ext/answers", {...context(result), url: result.url, fields, allow_ai:false});
    result.answerRevision=[response.profile_revision,response.facts_revision];
    let {answers}=response;
    if (cancelledTabs.has(result.tabId)) return;
    answers.push(...sectionAnswers.filter(answer=>!fields.some(field=>field.key===answer.key)));
    const deferred=new Set(fields.filter(f=>f.kind==='combobox' && !f.options_partial && !f.options.length && !f.options_observed).map(f=>f.key));
    const files = {};
    if (answers.some(answer => answer.status === "FILE" && answer.value === "resume" && !result.fields.find(field => field.key === answer.key)?.answered)) files.resume = await api("/ext/resume");
    await fill(result, answers.filter(a=>!deferred.has(a.key)), files);
    if(cancelledTabs.has(result.tabId) || staleRun(epoch))return;
    // Native known answers settle first; one existing grounded batch handles the
    // remaining eligible set using actual discovered offers and minimal facts.
    const eligible=new Set(answers.filter(a=>deferred.has(a.key) || a.reason_code==='reasoning_pending' ||
      !['RESOLVED','FILE'].includes(a.status) && !['optional_skip','policy_requires_user','unsupported_field','unusable_label','needs_writing'].includes(a.reason_code) &&
      ['combobox','select','radio','checkbox'].includes(fields.find(f=>f.key===a.key)?.kind)).map(a=>a.key));
    const remaining=[],bindings=new Map();
    for(const frameId of new Set([...eligible].map(key=>Number(key.split(':')[0])))) {
      const documentId=result.pageIdentities?.[frameId]?.documentId,token=crypto.randomUUID();
      if(!documentId)throw Error('Application changed. Run autofill again.');
      const keys=[...eligible].filter(k=>k.startsWith(frameId+':')).map(k=>k.slice(k.indexOf(':')+1));
      const [frame]=await chrome.scripting.executeScript({target:{tabId:result.tabId,documentIds:[documentId]},
        func:async(token,keys)=>{await globalThis.autoApplyContent.repairStart(token);return globalThis.autoApplyContent.repairOptions(token,keys);},args:[token,keys]});
      if(!frame || frame.documentId!==documentId)throw Error('Application changed. Run autofill again.');
      bindings.set(frameId,{documentId,token});
      for(const field of frame.result) {
        const updated={...field,key:frameId+':'+field.key};
        if(!eligible.has(updated.key))continue;
        Object.assign(result.fields.find(f=>f.key===updated.key)||{},updated);
        if(updated.kind!=='combobox' || updated.options.length)remaining.push(updated);
      }
    }
    if(remaining.length) {
      await progress(result,'Reasoning about remaining answers...');
      const resolved=await api('/ext/answers',{...context(result),fields:remaining});
      if(cancelledTabs.has(result.tabId) || staleRun(epoch) || activeResults.get(result.tabId)!==result)return;
      await fill(result,resolved.answers,files,bindings);
    }
    for(const key of deferred)if(!remaining.some(f=>f.key===key)) {
      const field=result.fields.find(f=>f.key===key);
      if(field && !field.answered && !result.pending.some(f=>f.key===key))result.pending.push({...field,reason_code:'no_offered_options'});
    }

  // A failed resolved choice still needs its lazy options in the panel.
  for (const field of result.pending.filter(field => field.kind === 'combobox' && !field.options.length && !field.options_observed && !eligible.has(field.key))) {
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
  finally{autofillTasks--;drainAutomaticAnalysis();}
}
const credentialDocuments=new Map();
const accountDiagnostics=new Map();
const accountContexts=new WeakMap();
let accountQueue=Promise.resolve();
const autoAccountTimers=new Map(),autoAccountTasks=new Set(),autoAccountRetries=new Map();
const supportedAccountUrl=url=>{try{const u=new URL(url);return u.protocol==='https:' && (!u.port || u.port==='443') && !u.username && !u.password && /(?:^|\.)(?:myworkdayjobs\.com|greenhouse\.io|lever\.co|smartrecruiters\.com|pinpointhq\.com|applytojob\.com|icims\.com|taleo\.net|successfactors\.com)$/.test(u.hostname);}catch{return false;}};
function clearAutoAccount(tabId){
  clearTimeout(autoAccountTimers.get(tabId));
  autoAccountTimers.delete(tabId);autoAccountRetries.delete(tabId);
}
function scheduleAutoAccount(tabId,retry){
  // Retain a changed-state signal across transient work, with one timer per tab.
  // Fresh signals renew the budget; navigation and closure retire the old one.
  const state=retry || {remaining:12,end:Date.now()+6000};
  if(retry && (autoAccountRetries.get(tabId)!==state || !state.remaining || Date.now()>=state.end)){
    if(autoAccountRetries.get(tabId)===state)clearAutoAccount(tabId);
    return;
  }
  clearTimeout(autoAccountTimers.get(tabId));
  autoAccountRetries.set(tabId,state);
  autoAccountTimers.set(tabId,setTimeout(async()=>{
    autoAccountTimers.delete(tabId);state.remaining--;
    let busy=false;
    try{busy=await autoFillAccount(tabId,state);}catch{}
    if(autoAccountRetries.get(tabId)!==state)return;
    if(busy)scheduleAutoAccount(tabId,state);else clearAutoAccount(tabId);
  },300));
}
async function autoFillAccount(tabId,state){
  const current=()=>autoAccountRetries.get(tabId)===state && !closedTabs.has(tabId) && !cancelledTabs.has(tabId);
  const idle=()=>!running && !autofillTasks && !skillsRunning && !analysisTasks.size && !analysisBlockers && !batchBusy && !manualActionTabs.size && !writingTasks.has(tabId) &&
    !closedTabs.has(tabId) && !cancelledTabs.has(tabId) && !activeResults.get(tabId)?.busy && !activeResults.get(tabId)?.coverBusy && !activeResults.get(tabId)?.skillsBusy &&
    (!activeResults.get(tabId)?.writing?.length || activeResults.get(tabId)?.writingSettled);
  if(!current())return;
  if(autoAccountTasks.has(tabId) || !idle())return true;
  autoAccountTasks.add(tabId);
  try{
    const {autoAccountFill=true}=await chrome.storage.local.get('autoAccountFill');
    const {chromeRunPaused=false,chromeRun,autofillTabs={}}=await chrome.storage.session.get(['chromeRunPaused','chromeRun','autofillTabs']);
    if(!current() || chromeRunPaused || chromeRun?.active)return;
    if(!idle())return true;
    const tab=await chrome.tabs.get(tabId);
    if(!supportedAccountUrl(tab.url))return;
    const frames=await accountFrames(tabId),supported=frames.filter(f=>f.result?.supported && f.result.autoEligible && sameAccountOrigin(f.result.url,tab.url));
    if(!current())return;
    const observed=frames.find(f=>f.result?.active && sameAccountOrigin(f.result.url,tab.url));
    if(observed){
      const previous=activeResults.get(tabId);
      const result=previous?.url===tab.url?previous:{tabId,url:tab.url,page:{},fields:[],pending:[],writing:[],completed:[],summary:{filled:0,skipped:0,needs_input:[],needs_writing:[],errors:[]}};
      Object.assign(result,{sign_in:true,accountPreview:true,accountMode:observed.result.mode,accountReady:observed.result.actionReady});
      if(supported.length!==1 || !autoAccountFill){
        result.progress=result.detail=supported.length!==1?accountFailure(result,'describe',{...observed.result.diagnosis,reason:observed.result.supported && !observed.result.autoEligible?'credentials_preserved':observed.result.diagnosis.reason},observed.documentId,true):'Review the account page, then sign in or fill account details.';
        activeResults.set(tabId,result);await report(result);return;
      }
    }
    if(supported.length!==1 || !autoAccountFill)return;
    if(!idle())return true;
    const frame=supported[0],document=frame.documentId+':'+frame.result.identity+':'+frame.result.mode;
    const previous=activeResults.get(tabId);
    const auth=await accountContext(tab.url,autofillTabs[tabId] || {},'auto_fill');
    if(!current() || (await chrome.tabs.get(tabId)).url!==tab.url)return;
    if(!idle())return true;
    const result=previous?.accountDocument===document && previous.url===tab.url?previous:
      {tabId,url:tab.url,page:{},fields:[],pending:[],writing:[],completed:[],summary:{filled:0,skipped:0,needs_input:[],needs_writing:[],errors:[]}};
    Object.assign(result,auth,{sign_in:true,accountPreview:true,waiting:true,busy:false,state:'needs_input'});
    activeResults.set(tabId,result);
    const detail=await fillAccount(result,false,true);
    if(!current() || activeResults.get(tabId)!==result || (await chrome.tabs.get(tabId)).url!==tab.url)return;
    if(!idle())return true;
    result.progress=result.detail=detail;
    await report(result);
  }finally{autoAccountTasks.delete(tabId);}
}
const sameAccountOrigin=(a,b)=>{
  try{return new URL(a).protocol==='https:' && new URL(b).protocol==='https:' && new URL(a).origin===new URL(b).origin;}catch{return false;}
};
async function accountContext(url,saved,intent,application) {
  // Null-ID matches are absence of attribution, not a usable stored job.
  let matched=application?.application_id!=null?application:application!=null && typeof application!=='object'?{application_id:application}:await api('/ext/match',{url});
  let source_url=url;
  const prior=saved.account_action?.source_url || saved.url;
  // A different unqueued job on the same employer must stay unattributed.
  // Reuse only a shared account route, its parent landing route, or the same job.
  const reusable=sameAccountOrigin(url,prior) && (()=>{
    const path=new URL(url).pathname,previous=new URL(prior).pathname;
    return /\/(?:login|signin|sign-in|signup|sign-up|auth|account|createaccount|create-account|userhome)(?:\/|$)/i.test(path) ||
      previous.startsWith(path.replace(/\/$/,'')+'/') ||
      path.replace(/\/apply(?:\/.*)?$/i,'')===previous.replace(/\/apply(?:\/.*)?$/i,'');
  })();
  if(matched?.application_id==null && reusable)source_url=prior;
  if(matched?.application_id!=null && matched.application_id===saved.matched?.application_id && sameAccountOrigin(url,saved.url))matched={...saved.matched,...matched};
  if(matched?.application_id==null && reusable && saved.matched?.application_id!=null){
    matched=saved.matched;source_url=prior;
  }
  const source=new URL(source_url);
  return {matched:matched || {application_id:null},account_action:{intent,source_url:source.origin+source.pathname}};
}
chrome.tabs.onRemoved.addListener(tabId=>{credentialDocuments.delete(tabId);accountDiagnostics.delete(tabId);clearAutoAccount(tabId);});
const accountReasons={
  scan_incomplete:'Account scan is incomplete.',no_password:'Account password control is unavailable.',
  controls_not_editable:'Account controls are not editable.',scope_ambiguous:'Account form scope is ambiguous.',
  native_owner_mismatch:'Account controls have a different native form owner.',
  signup_mode_unavailable:'Account signup mode is unavailable.',account_surface_ambiguous:'Account surface contains competing forms or boundaries.',
  insecure_origin:'Account actions require HTTPS.',cross_origin:'Account form targets a different origin.',
  reset_password:'Password recovery form requires manual completion.',current_password:'Password change form requires manual completion.',
  otp:'Account verification code requires manual completion.',captcha:'Account CAPTCHA requires manual completion.',
  no_matching_email:'Account requires exactly one editable email control.',confirmation_unrecognized:'Password confirmation control is unrecognized.',
  action_ambiguous:'Account has multiple primary actions.',action_unavailable:'Account primary action is unavailable.',
  context_changed:'Account form changed; retry Analyze or Autofill.',credentials_preserved:'Existing different account credentials were preserved.',
  consent_required:'Account consent control is unavailable or requires attention.',consent_unsettled:'Account consent did not remain checked.',
  form_invalid:'Account form validation failed.',credentials_unconfigured:'Set your account password in dashboard Settings, or sign in manually.',
  strict_required:'Set strict password in Settings (12+ characters, uppercase, lowercase, number and special character).',
  frames_ambiguous:'Multiple account frames are supported.',unavailable:'Account action unavailable. Sign in manually, then continue.'
};
function accountFailure(result,phase,diagnosis={},binding='',polling=false){
  const reason=Object.hasOwn(accountReasons,diagnosis.reason)?diagnosis.reason:'unavailable';
  const observed={phase};
  for(const key of ['password_count','email_count','action_count','frame_count','external_action_count','native_owner_mismatch_count','account_form_count','known_surface_count'])
    if(Number.isInteger(diagnosis[key]))observed[key]=Math.max(0,Math.min(1000,diagnosis[key]));
  const diagnostic=JSON.stringify([binding,reason,observed]);
  if(polling && accountDiagnostics.get(result.tabId)===diagnostic)return accountReasons[reason];
  accountDiagnostics.set(result.tabId,diagnostic);
  // Detached, best effort: trace transport cannot delay or fail account actions.
  try{void saveTrace({host:new URL(result.url).hostname,kind:'autofill',entries:[{step:'account',action:'rejected',label:'Account step',reason,observed}]}).catch(()=>{});}catch{}
  return accountReasons[reason];
}
async function fillAccount(result, polling=false, preview=false) {
  // A bounded serial reservation spans credential lookup, settlement and the
  // durable claim. Analyze calls this directly, never reenters Autofill.
  const epoch=runEpoch, owner=activeResults.get(result.tabId), accountUrl=result.url, pollToken=formWaitTokens.get(result.tabId);
  const operation=async()=>{
    let phase='describe',diagnosticBinding='';
    const failure=(reason,diagnosis={})=>accountFailure(result,phase,{...diagnosis,reason:reason || diagnosis.reason},diagnosticBinding,polling);
    const valid=async()=>{
      const {chromeRunPaused=false,chromeRun}=await chrome.storage.session.get(['chromeRunPaused','chromeRun']);
      const enabled=!preview || (await chrome.storage.local.get('autoAccountFill')).autoAccountFill!==false;
      return enabled && !chromeRunPaused && !staleRun(epoch) && !closedTabs.has(result.tabId) && !cancelledTabs.has(result.tabId) &&
        (!preview || !chromeRun?.active && !running && !autofillTasks && !skillsRunning && !analysisTasks.size && !analysisBlockers && !manualActionTabs.size && !batchBusy) &&
        (!polling || !analysisTasks.has(result.tabId) && formWaitTokens.get(result.tabId)===pollToken) && activeResults.get(result.tabId)===owner && (await chrome.tabs.get(result.tabId)).url===accountUrl;
    };
    if(!accountUrl?.startsWith('https://'))return failure('insecure_origin');
    try{
      if(!await valid())return failure('context_changed');
      const frames=await accountFrames(result.tabId);
      diagnosticBinding=JSON.stringify(frames.map(f=>[f.documentId,f.result?.revision]));
      const supported=frames.filter(f=>f.result?.supported && new URL(f.result.url).origin===new URL(accountUrl).origin);
      if(supported.length!==1){
        const rejected=frames.find(f=>f.frameId===0 && f.result?.active) || frames.find(f=>f.result?.active);
        const foreign=frames.some(f=>f.result?.supported && new URL(f.result.url).origin!==new URL(accountUrl).origin);
        return failure(supported.length>1?'frames_ambiguous':foreign?'cross_origin':rejected?.result?.diagnosis?.reason || 'unavailable',{
          ...rejected?.result?.diagnosis,frame_count:frames.length});
      }
      const frame=supported[0],document=frame.documentId+':'+frame.result.identity+':'+frame.result.mode;
      if(preview && !frame.result.autoEligible)return failure('context_changed');
      result.accountDocument=document;result.accountMode=frame.result.mode;result.accountReady=frame.result.actionReady;
      result.summary.filled=frame.result.filled || 0;
      const binding=document+':'+frame.result.identity+':'+frame.result.revision;
      if(polling && accountContexts.get(result)!==binding)
        return failure('context_changed');
      accountContexts.set(result,binding);
      const key=result.tabId+':'+document;
      const {accountActionClaims={}}=await chrome.storage.session.get('accountActionClaims');
      if(accountActionClaims[key])return 'Account action already attempted. Complete verification or review the account page; Autofill resumes after authentication.';
      if(polling && credentialDocuments.get(result.tabId)===document)return null;
      phase='credentials';
      const value=await api('/ext/credentials',{url:accountUrl,frame_url:frame.result.url,
        ...(result.matched?.application_id!=null?{application_id:result.matched.application_id,run_id:result.matched.run_id}:{}),
        account_action:result.account_action,mode:frame.result.mode,password_policy:frame.result.passwordPolicy});
      if(!value.configured)return failure(value.strict_required?'strict_required':'credentials_unconfigured');
      if(!await valid())return failure('context_changed');
      phase='prepare';
      const [prepared]=await chrome.scripting.executeScript({target:{tabId:result.tabId,documentIds:[frame.documentId]},world:'ISOLATED',func:(value,expected,preview)=>globalThis.autoApplyCredentials?.prepare(value,expected,preview),args:[value,frame.result,preview]});
      result.summary.filled=prepared?.result?.filled || 0;
      result.accountReady=prepared?.result?.status==='ready';
      if(prepared?.result?.status==='filled'){
        if(!await valid())return failure('context_changed');
        failure(null,prepared.result.diagnosis);
        return `Account details filled. ${frame.result.mode==='signup'?'Create account':'Sign in'} button is not available yet.`;
      }
      if(prepared?.result?.status!=='ready')return failure(null,prepared?.result?.diagnosis);
      if(!await valid())return failure('context_changed');
      if(preview)return `Account details filled. Review the page, then click ${frame.result.mode==='signup'?'Create account':'Sign in'}.`;
      // Persist before activation. A timeout/restart is never permission to click
      // again. This claim contains document identities only, never credentials.
      const claim=crypto.randomUUID();accountActionClaims[key]=claim;
      const releaseClaim=async()=>{const {accountActionClaims:latest={}}=await chrome.storage.session.get('accountActionClaims');if(latest[key]===claim){delete latest[key];await chrome.storage.session.set({accountActionClaims:latest});}};
      await chrome.storage.session.set({accountActionClaims});
      if(!await valid()){await releaseClaim();return failure('context_changed');}
      phase='activate';
      const [activated]=await chrome.scripting.executeScript({target:{tabId:result.tabId,documentIds:[frame.documentId]},world:'ISOLATED',func:id=>globalThis.autoApplyCredentials?.activate(id),args:[prepared.result.ticket]});
      if(activated?.result?.status==='unavailable'){await releaseClaim();return failure('context_changed');}
      if(activated?.result?.status==='activated')credentialDocuments.set(result.tabId,document);
      if(activated?.result?.status==='activated' && frame.result.mode==='signup' && ['standard','strict'].includes(value.variant)){
        // Remember only after the guarded native signup action was clicked.
        await api('/ext/credentials',{url:accountUrl,frame_url:frame.result.url,
          ...(result.matched?.application_id!=null?{application_id:result.matched.application_id,run_id:result.matched.run_id}:{}),
          account_action:result.account_action,mode:'signup',password_policy:frame.result.passwordPolicy,remember_variant:value.variant}).catch(()=>{});
      }
      return activated?.result?.status==='activated'?'Account action activated. Waiting for authentication; review any verification request.':failure('context_changed');
    }catch{return failure('unavailable');}
  };
  const task=accountQueue.catch(()=>{}).then(operation);accountQueue=task;return task;
}
async function autofillCurrent(tabId, application, advanceCount = 0) {
  if (analysisTasks.size) return {error: "Application is busy analyzing. Wait and retry."};
  if (running) return {error: "Autofill is already running."};
  running = true;
  const ownedEpoch=runEpoch;
  let traceTarget;
  let result = {busy: true, progress: "Scanning application..."};
  try {
    const traceTab=tabId===undefined?(await chrome.tabs.query({active:true,currentWindow:true}))[0]:await chrome.tabs.get(tabId);
    result.tabId=traceTab?.id; result.url=traceTab?.url;
    if(application?.run_id && staleRun(ownedEpoch))return result;
    cancelledTabs.delete(result.tabId);
    activeResults.set(result.tabId,result);
    await report(result);
    await showSidebar(result.tabId);
    const readiness=await waitForApplicationInputs(result.tabId);
    let scanned = await scan(tabId);
    await readiness.check?.();
    if (cancelledTabs.has(scanned.tabId)) return result;
    if (!scanned.sign_in && !scanned.fields.length && scanned.next_action?.kind!=='submit') scanned = await openApplication(scanned);
    if(cancelledTabs.has(scanned.tabId))return result;
    traceTarget={tabId:scanned.tabId,frameIds:Object.keys(scanned.pageIdentities).map(Number)};
    await chrome.scripting.executeScript({target:traceTarget,func:count=>{const api=globalThis.autoApplyContent;api?.beginTrace('autofill');api?.trace('scan','found',null,{total:count});},args:[scanned.fields.length]});
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
    if(result.sign_in)Object.assign(result,await accountContext(result.url,saved,'autofill',application));
    else {
      result.matched=await applicationMatch(result.url,saved,application);
    }
    if(cancelledTabs.has(result.tabId))return result;
    autofillTabs[result.tabId] = {...saved, url: result.url, matched: result.matched,account_action:result.account_action};
    await chrome.storage.session.set({autofillTabs});
    if (result.matched.company) result.page.company = result.matched.company;
    if (result.matched.title) result.page.title = result.matched.title;
    if (!result.sign_in && !result.fields.length && result.next_action?.kind==='submit') {
      result.progress = 'Review the application. Submit when you are ready.';
      return await report({...result,busy:false});
    }
    if (result.sign_in || !result.fields.length) {
      result.state = "needs_input";
      result.detail = result.progress = result.sign_in ? await fillAccount(result) : readiness.empty?readiness.message:'Waiting for the application form...';
      result.waiting = result.sign_in || !readiness.empty;
      result.busy = false;
      if(result.waiting)pollForForm(result);
      const reported=await report(result);
      if(!result.sign_in)snapshots.capture(result);
      return reported;
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
    if(traceTarget)await chrome.scripting.executeScript({target:traceTarget,func:error=>{const api=globalThis.autoApplyContent;if(error)api?.trace("autofill","error",null,error);return api?.endTrace();},args:[result.traceError || null]}).catch(()=>{});
    running = false; result.busy = false;
  }
  await report(result);
  if (!cancelledTabs.has(result.tabId) && !result.error && result.writing?.length) {
    const task = continueWriting(result).then(()=>advanceAfterWriting(result)).catch(error => {
      result.summary.errors.push(error.message);
      return report(result);
    }).finally(() => { result.writingSettled=true; if(activeResults.get(result.tabId)===result)report(result); snapshots.capture(result); if (writingTasks.get(result.tabId) === task) writingTasks.delete(result.tabId);drainAutomaticAnalysis(); });
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
function chatReplacementIntent(value){return typeof value==='string' && !/\b(?:don.t|do not|never|cannot|can.t|draft\s+only|only\s+(?:a\s+)?draft|without\s+(?:changing|updating|replacing|filling|applying))\b/i.test(value) && /\b(?:field|textarea|text box|cover letter|answer)\b/i.test(value) && /^(?:(?:please|can you|could you|would you)\s+)*(?:(?:replace|overwrite|fill|put|insert|paste|change|update|generate|write|revise|rewrite|include|add|mention|make)\b|for\s+(?:the |my )?cover letter[,:]?\s+(?:include|add|mention|write|revise)\b|(?:the|my)\s+(?:answer|cover letter)\s+(?:should|needs to)\s+(?:be|say|include|mention)\b)/i.test(value.trim());}
function authorizedChatField(request,context){
  if(!chatReplacementIntent(request))return null;
  let fields=context?.fields || [];
  if(/\b(?:current|focused|this)\s+(?:field|textarea|text box|answer)\b/i.test(request))fields=fields.filter(f=>f.key===context.current_key);
  else {const named=fields.filter(f=>f.label?.trim() && request.toLowerCase().includes(f.label.trim().toLowerCase()));fields=named.length?named:/\b(?:my|the) answer\b/i.test(request)?fields.filter(f=>f.key===context.current_key):/cover letter/i.test(request)?fields.filter(f=>/cover/i.test(f.label)&&/letter/i.test(f.label)):[];}
  return fields.length===1?fields[0]:null;
}
function validCoverArtifact(file){return file && Object.keys(file).sort().join(',')==='base64,filename,mime' && file.mime==='application/pdf' && typeof file.filename==='string' && /^Cover Letter\.pdf$/.test(file.filename) && typeof file.base64==='string' && file.base64.length<=8000000 && /^JVBERi0[A-Za-z0-9+/=]+$/.test(file.base64);}
function validChatAction(action,context){
  if(!Array.isArray(context?.fields))return false;
  const field=context.fields.find(f=>f.key===action?.key);
  return action && field && (field.kind==='file' ? Object.keys(action).sort().join(',')==='file,key,text,type' && action.type==='replace_cover_letter' && field.purpose==='cover_letter' && /cover.*letter/i.test(field.label) && !/resume|\bcv\b/i.test(field.label) && validCoverArtifact(action.file) : Object.keys(action).sort().join(',')==='key,text,type' && action.type==='replace_text' && ['text','textarea','email','tel','url'].includes(field.kind)) && typeof action.text==='string' && action.text.trim() &&
    action.text.length<=12000 && (field.max_length==null || action.text.length<=field.max_length);
}
let skillsRunning=false;
async function action(message) {
  if(message.type==='answer-context-changed'){
    if(!Array.isArray(message.revision) || message.revision.length!==2 || typeof message.revision[0]!=='string' || !Number.isInteger(message.revision[1]))return {};
    const targets=message.tabId?[message.tabId]:[...activeResults.keys()];
    for(const tabId of targets){
      const result=activeResults.get(tabId);
      if(!result?.answerRevision || JSON.stringify(result.answerRevision)===JSON.stringify(message.revision))continue;
      result.answerRevision=message.revision;
      automaticSeen.delete(tabId);clearAutomaticRetry(tabId);queueAutomaticAnalysis(tabId);
    }
    await saveAutomaticRecovery();return {ok:true};
  }
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
    if(busy() || cancelledTabs.has(tab.id))throw Error('Application is busy or stopped. Wait or reopen the application, then retry.');
    const ctx=message.context,action=message.action;
    if(ctx?.tabId!==tab.id || ctx?.url!==tab.url)throw Error('Application changed. Select the field and ask again.');
    if(!chatReplacementIntent(message.request) || authorizedChatField(message.request,ctx)?.key!==action?.key || !validChatAction(action,ctx))
      throw Error('Replacement was not authorized for this field. Select the field and ask again.');
    const [frame]=await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>globalThis.autoApplyContent?.chatContext({passive:true})});
    if(frame.documentId!==ctx.documentId || frame.result?.document!==ctx.document)throw Error('Application changed. Select the field and ask again.');
    if(busy() || cancelledTabs.has(tab.id))throw Error('Application is busy or stopped. Wait or reopen the application, then retry.');
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
    if(running || autofillTasks || skillsRunning || analysisBlockers || writingTasks.has(tab.id) || manualActionTabs.size ||
       activeResults.get(tab.id)?.busy || activeResults.get(tab.id)?.coverBusy || activeResults.get(tab.id)?.skillsBusy ||
       activeResults.get(tab.id)?.writing?.length && !activeResults.get(tab.id)?.writingSettled)
      throw Error('Application is busy. Wait for filling or writing to finish, then retry Analyze page.');
    // Stop the idle wait without cancellation or credential invalidation. The
    // token also retires a tick already awaiting a scan; accountQueue drains
    // an in-flight lookup before this explicit action can prepare anything.
    clearTimeout(formWaits.get(tab.id));formWaits.delete(tab.id);formWaitTokens.delete(tab.id);pendingSteps.delete(tab.id);
    const task=(async()=>{
      // Reserve Analyze while a short run-status poll drains. A status request
      // is not form work and should not randomly force a manual retry.
      for(let wait=0;batchBusy && wait<20;wait++)await delay(50);
      if(batchBusy || running || autofillTasks || skillsRunning || analysisBlockers)
        throw Error('Application is busy. Wait for filling or writing to finish, then retry Analyze page.');
      const epoch=runEpoch;
      let current=activeResults.get(tab.id),application_id=current?.url===tab.url?current.matched?.application_id:undefined,
        run_id=current?.url===tab.url?current.matched?.run_id:undefined;
      const fresh=!current || current.url!==tab.url;
      const transport=error=>/Dashboard is busy|Dashboard is not reachable|Dashboard request failed \(503\)/i.test(error.message || '');
      async function answerRequest(path,payload,frame){
        for(let retry=0;;retry++){
          for(const target of Array.isArray(frame)?frame:[frame])await bound(target,'repairCheck');
          try{return await api(path,payload);}catch(error){
            if(!transport(error) || retry===2)throw error;
            await delay(200*(retry+1));
          }
        }
      }
      const inspect=(after=false)=>readFrames({target:{tabId:tab.id,allFrames:true},func:(after,automatic)=>{
        const api=globalThis.autoApplyContent;if(!api)return null;
        const inspected=api.inspectPage({repair:after});
        const structural=inspected.diagnostic.fields.some(f=>!f.answered && f.repair_status!=='optional_skip' &&
          (f.repair_status==='verification_failed' || f.reasons.some(r=>['unsupported_widget','omitted_by_scanner','menu_not_exposed'].includes(r))));
        return {...inspected,...(!automatic || structural?api.pageStructure():{nodes:[],truncated:false}),structural,
          ...(automatic && after?{automaticState:api.runtimeState()}: {})};
      },args:[after,Boolean(message.automatic)]});
      const readiness=await waitForApplicationInputs(tab.id,Boolean(message.automatic));
      if(!current)current=readiness.owner;
      const auth=await accountFrames(tab.id);
      await readiness.check?.();
      if(auth.some(frame=>frame.result?.active)){
        if(message.automatic){scheduleAutoAccount(tab.id);return {ok:true,summary:'Account details require private preview.',findings:[]};}
        if(staleRun(epoch) || cancelledTabs.has(tab.id) || (await chrome.tabs.get(tab.id)).url!==tab.url)throw Error('Application changed. Analyze page again.');
        const {autofillTabs={}}=await chrome.storage.session.get('autofillTabs');
        const saved=current?.matched?.application_id!=null?current:autofillTabs[tab.id] || {};
        const {matched,account_action}=await accountContext(tab.url,saved,'analyze');
        const account=current?.url===tab.url?current:{tabId:tab.id,url:tab.url,matched,page:{},fields:[],pending:[],writing:[],completed:[],summary:{filled:0,skipped:0,needs_input:[],needs_writing:[],errors:[]}};
        account.matched=matched;account.account_action=account_action;account.accountPreview=false;
        if(staleRun(epoch) || cancelledTabs.has(tab.id) || (await chrome.tabs.get(tab.id)).url!==tab.url)throw Error('Application changed. Analyze page again.');
        const detail=await fillAccount(account);
        if(staleRun(epoch) || cancelledTabs.has(tab.id) || (await chrome.tabs.get(tab.id)).url!==tab.url)throw Error('Application work stopped.');
        account.sign_in=true;account.waiting=true;account.busy=false;account.state='needs_input';account.progress=account.detail=detail;
        if(staleRun(epoch) || cancelledTabs.has(tab.id) || (await chrome.tabs.get(tab.id)).url!==tab.url)throw Error('Application work stopped.');
        autofillTabs[tab.id]={...autofillTabs[tab.id],url:tab.url,matched,account_action};
        await chrome.storage.session.set({autofillTabs});
        activeResults.set(tab.id,account);await report(account);pollForForm(account);
        return {ok:true,tabId:tab.id,url:tab.url,summary:detail,findings:[{label:'Account step',notes:[detail]}],busy:false};
      }
      // Bind compact control metadata first. No DOM serialization precedes fills.
      const before=await compactApplicationState(tab.id);
      await readiness.check?.();
      if(current)current.waiting=false;
      const targets=before.filter(f=>f.result);
      if(targets.some(f=>f.result.diagnostic.busy))throw Error('Application is busy. Wait and retry.');
      if(fresh){
        current={tabId:tab.id,url:tab.url,page:{},fields:[],pending:[],writing:[],completed:[],
          pageIdentities:Object.fromEntries(targets.map(f=>[f.frameId,{documentId:f.documentId}])),
          summary:{filled:0,skipped:0,needs_input:[],needs_writing:[],errors:[]}};
        activeResults.set(tab.id,current);
      }
      const started=[];const token=crypto.randomUUID();let stopped='none',attempts=0,filled=0;
      async function bound(frame,method,args=[]){
        if(staleRun(epoch) || cancelledTabs.has(tab.id) || closedTabs.has(tab.id) || (await chrome.tabs.get(tab.id)).url!==tab.url || activeResults.get(tab.id)!==current ||
           (current?.url===tab.url?current.matched?.application_id:undefined)!==application_id || current.matched?.run_id!==run_id)
          throw Error('Application changed. Analyze page again.');
        if(message.automatic && !(await snapshots.preference()).enabled)throw Error('Automatic analysis disabled.');
        const [value]=await chrome.scripting.executeScript({target:{tabId:tab.id,documentIds:[frame.documentId]},
          func:async(method,args)=>{try{return await globalThis.autoApplyContent[method](...args);}catch(error){return {error:error.message};}},args:[method,[token,...args]]});
        if(value?.documentId!==frame.documentId || value?.result?.error)throw Error(value?.result?.error || 'Application changed. Analyze page again.');
        return value.result;
      }
      // One tab-wide fast batch, followed by at most one grounded AI batch.
      // Keys are frame-bound here and stripped only at the native write boundary.
      try{
        const candidates=[];let page;
        for(const frame of targets){
          const prepared=await bound(frame,'repairStart');started.push(frame);
          if(frame.frameId===0 || !page)page=prepared.page;
          frame.result.diagnostic.fields=prepared.before;
          candidates.push(...prepared.fields.filter(f=>!f.answered).map(f=>({...f,key:`${frame.frameId}:${f.key}`})));
        }
        page=await publicJobContext(page,tab.url);
        // Establish the same queue attribution as Autofill, after binding the
        // document and before any answer or explicit submission can use it.
        const {autofillTabs={}}=await chrome.storage.session.get('autofillTabs');
        const matched=await applicationMatch(tab.url,autofillTabs[tab.id],current.matched);
        for(const frame of started)await bound(frame,'repairCheck');
        application_id=matched?.application_id;run_id=matched?.run_id;current.matched=matched;
        autofillTabs[tab.id]={...autofillTabs[tab.id],url:tab.url,matched};
        await chrome.storage.session.set({autofillTabs});
        const apply=async response=>{
          const files={};
          if(response.answers?.some(answer=>answer.status==='FILE' && answer.value==='resume'))
            files.resume=await answerRequest('/ext/resume',undefined,started);
          for(const frame of started){
            const prefix=frame.frameId+':';
            const local=(response.answers || []).filter(a=>a.key.startsWith(prefix)).map(a=>({...a,key:a.key.slice(prefix.length)}));
            if(local.length)await bound(frame,'repairFill',[local,files]);
          }
        };
        if(candidates.length){
          const fast=await answerRequest('/ext/answers',{url:tab.url,application_id,page,fields:candidates,allow_ai:false},started);
          attempts=1;current.answerRevision=[fast.profile_revision,fast.facts_revision];
          // A saved answer is not an offered option. Defer closed choices until
          // their bound menu has been observed, while native answers fill now.
          const unobserved=new Set(candidates.filter(f=>f.kind==='combobox' && !f.options?.length && !f.options_observed).map(f=>f.key));
          const deferred=new Set((fast.answers || []).filter(a=>a.status==='RESOLVED' && unobserved.has(a.key)).map(a=>a.key));
          await apply({...fast,answers:(fast.answers || []).filter(a=>!deferred.has(a.key))});
          const eligible=new Set((fast.answers || []).filter(a=>deferred.has(a.key) || a.reason_code==='reasoning_pending' ||
            !['RESOLVED','FILE'].includes(a.status) && !['optional_skip','policy_requires_user','unsupported_field','unusable_label','needs_writing'].includes(a.reason_code) &&
            ['combobox','select','radio','checkbox'].includes(candidates.find(f=>f.key===a.key)?.kind)).map(a=>a.key));
          const remaining=[];
          if(eligible.size)for(const frame of started){
            // Only unresolved, eligible menus may be opened, once per revision.
            const keys=[...eligible].filter(k=>k.startsWith(frame.frameId+':')).map(k=>k.slice(k.indexOf(':')+1));
            if(!keys.length)continue;
            const fields=await bound(frame,'repairOptions',[keys]);
            remaining.push(...fields.map(f=>({...f,key:`${frame.frameId}:${f.key}`})).filter(f=>eligible.has(f.key) && (f.kind!=='combobox' || f.options.length)));
          }
          if(remaining.length){
            current.progress='Reasoning about remaining answers...';await report(current);
            const response=await answerRequest('/ext/answers',{url:tab.url,application_id,page,fields:remaining},started);
            attempts=2;await apply(response);
          }
        }
      }catch(error){
        stopped=transport(error)?'transport_error':/Pairing|Not paired/i.test(error.message)?'interrupted':
          /answer changed/i.test(error.message)?'user_edit':'repair_error';
      }
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
        if(staleRun(epoch) || cancelledTabs.has(tab.id) || closedTabs.has(tab.id) || (await chrome.tabs.get(tab.id)).url!==tab.url || activeResults.get(tab.id)!==current ||
           (current?.url===tab.url?current.matched?.application_id:undefined)!==application_id || current.matched?.run_id!==run_id)return false;
        const latest=await readFrames({target:{tabId:tab.id,allFrames:true},func:()=>globalThis.autoApplyContent?.repairContext()});
        return available.every(frame=>latest.some(f=>f.frameId===frame.frameId && f.documentId===frame.documentId && f.result?.identity===frame.result.identity && f.result.revision===frame.result.edit_revision));
      }
      if(!await unchanged())throw Error('Application changed. Analyze page again.');
      const context=new URL(tab.url);context.search='';context.hash='';context.username='';context.password='';
      const attention=fields.filter(f=>!f.answered && f.repair_status!=='verified' && f.repair_status!=='optional_skip');
      const retryable=stopped==='transport_error' || attention.some(f=>['provider_unavailable','stale_data','no_offered_options'].includes(f.reason_code)) || inspections.some(i=>i.status==='loading');
      const answerStatus=f=>f.reason_code==='policy_requires_user'?'Your answer is required by your saved preference.':
        f.reason_code==='no_offered_options'?'Waiting for offered choices.':/^provider_|^stale_data$/.test(f.reason_code || '')?'Answer recovery unavailable.':
        f.repair_status==='user_edited'?'Your edit is protected.':f.reason_code==='needs_fact'?'Needs an actual saved fact.':'Review this control.';
      const liveSummary=`Filled ${filled}; ${attention.length} unresolved`;
      const reviewOnly=!fields.length && repaired.find(f=>f.frameId===0)?.result.next_action?.kind==='submit';
      const saved=reviewOnly || message.automatic && !available.some(f=>f.result.structural)?
        {ok:true,summary:liveSummary,findings:attention.map(f=>({label:f.label,notes:[answerStatus(f)]}))}:
        await api('/ext/snapshot',{host:context.hostname,context_url:context.href,nodes,truncated,diagnostic,automatic:Boolean(message.automatic)});
      if(!await unchanged())throw Error('Application changed. Analyze page again.');
      if(saved.ok && current?.url===tab.url){
        const live=new Map(repaired.flatMap(frame=>frame.result.fields.map(field=>[`${frame.frameId}:${field.key}`,{...field,key:`${frame.frameId}:${field.key}`}])));
        const bindings=new Map(repaired.flatMap(frame=>frame.result.fields.map(field=>[
          frame.documentId+':'+field.control_identity,live.get(`${frame.frameId}:${field.key}`)])));
        const retained=(field,answered=true)=>{
          const frameId=Number(field.key.split(':')[0]),documentId=current.pageIdentities?.[frameId]?.documentId;
          const now=field.control_identity && bindings.get(documentId+':'+field.control_identity);
          return now && (!answered || now.answered)?{...field,...now}:null;
        };
        const completed=(current.completed || []).map(field=>retained(field)).filter(Boolean);
        for(const frame of repaired)for(const answer of frame.result.completed){
          const item={...answer,key:`${frame.frameId}:${answer.key}`},index=completed.findIndex(old=>old.key===item.key);
          if(index<0)completed.push(item);else completed[index]=item;
        }
        const pending=[...live.values()].filter(field=>!field.answered && field.repair_status!=='optional_skip' && field.reason_code!=='optional_skip');
        const writing=(current.writing || []).map(field=>retained(field,false)).filter(Boolean);
        const top=repaired.find(frame=>frame.frameId===0)?.result;
        const updated={...current,snapshotFeedback:undefined,completed,pending,writing,
          fields:[...live.values()],page:{...(top?.page || {}),url:tab.url,...(current.matched?.company?{company:current.matched.company}:{}),...(current.matched?.title?{title:current.matched.title}:{})},
          next_action:top?.next_action,sign_in:false,accountMode:undefined,waiting:false,busy:false,stalePage:false,
          pageIdentities:Object.fromEntries(available.map(frame=>[frame.frameId,{identity:frame.result.identity,documentId:frame.documentId}])),
          progress:!fields.length && !filled && inspections.find(i=>i.message)?.message ||
            (saved.repair?.id?`Worker queued: ${saved.repair.message || saved.repair.id}`:pending.length?answerStatus(pending[0]):`Filled ${filled} answers.`),
          summary:{...current.summary,filled:completed.length,skipped:[...live.values()].filter(f=>f.repair_status==='optional_skip').length,
            needs_input:pending.map(field=>field.label),needs_writing:writing.map(field=>field.label)}};
        await report(updated,unchanged);
        if(!await unchanged())throw Error('Application changed. Analyze page again.');
        Object.assign(current,updated);
      }
      if(!await unchanged())throw Error('Application changed. Analyze page again.');
      // A durable capture plus queue acknowledgement can park ONLY our current
      // owned run. Keep the unsubmitted tab and repair context intact. Persist
      // the idempotent result before sending it so a worker restart can reconcile.
      await runReady;
      const {chromeRun:run,chromeRunPaused=false}=await chrome.storage.session.get(['chromeRun','chromeRunPaused']);
      if(saved.ok && saved.analysis_id && saved.repair?.id &&
         ['queued','dispatching','running','awaiting_review'].includes(saved.repair.status) &&
         !['paused','disabled','quota','blocked'].includes(saved.repair.reason) &&
         ['none','transport_error','repair_error'].includes(stopped) &&
         !chromeRunPaused && run?.active && run.tabId===tab.id && run.current?.application_id===application_id &&
         run.current?.run_id===current?.matched?.run_id && !current?.sign_in &&
         !inspections.some(i=>['access_blocked','loading','account_private','embedded_content_unavailable'].includes(i.status)) &&
         await unchanged()){
        const status=await runStatus(run,epoch);
        if(status && await unchanged()){
          run.result={application_id,run_id:run.current.run_id,state:'skipped',
            detail:`Parked unsubmitted for repair ${saved.repair.id}; capture ${saved.analysis_id}. ${saved.repair.message || ''}`.slice(0,2000)};
          // Retain a document/edit binding for a retry after a lost HTTP ack.
          run.parkBinding={url:tab.url,pages:available.map(frame=>({frameId:frame.frameId,documentId:frame.documentId,
            identity:frame.result.identity,revision:frame.result.edit_revision}))};
          await saveRun(run,false,epoch);
          if(!await unchanged()){
            delete run.result;delete run.parkBinding;await saveRun(run,false,epoch);
            throw Error('Application changed. Analyze page again.');
          }
          await api('/ext/run/result',run.result);
          if(staleRun(epoch))throw Error('Application work stopped.');
          if(!await unchanged()){
            delete run.result;delete run.parkBinding;run.paused=true;
            run.error='Page changed while parking. Review the preserved application before resuming.';
            await saveRun(run,true,epoch);throw Error(run.error);
          }
          delete run.parkBinding;
          delete run.current;delete run.tabId;delete run.result;delete run.started;delete run.progressSent;
          run.error='';await saveRun(run,false,epoch);
        }
      }
      const unavailable=!fields.length && !filled && inspections.find(i=>i.message);
      let automaticSignature,recoverySignature;
      if(message.automatic && saved.ok && stopped==='none'){
        // Cache the inspected post-fill state only while its original repair
        // capability still binds this document, edit revision and step. A late
        // replacement step must retain its queued signal instead of being seen.
        for(const frame of started)await bound(frame,'repairCheck');
        if(await unchanged())recoverySignature=automaticAnalysisSignature(available.filter(f=>f.result.automaticState?.application)
          .map(f=>({...f,result:f.result.automaticState})));
        if(!retryable)automaticSignature=recoverySignature;
      }
      return {...saved,...(unavailable?{summary:unavailable.message,findings:[{label:'Page availability',notes:[unavailable.message]}]}:{}),tabId:tab.id,url:tab.url,application_id:current?.url===tab.url?current.matched?.application_id:undefined,busy:diagnostic.busy,
        automaticRetry:retryable,
        ...(recoverySignature?{recoverySignature}:{}),
        ...(automaticSignature?{automaticSignature}: {})};
    })();
    analysisTasks.set(tab.id,task);
    try{return await task;}finally{
      analysisTasks.delete(tab.id);drainAutomaticAnalysis();
      const waiting=activeResults.get(tab.id);
      if(waiting?.waiting && !formWaits.has(tab.id) && !cancelledTabs.has(tab.id))pollForForm(waiting);
    }
  }
  if(message.type==='retry-submission-tracking') {
    const {tabResults={},autofillTabs={}}=await chrome.storage.session.get(['tabResults','autofillTabs']);
    const current=activeResults.get(message.tabId)||tabResults[message.tabId], authorized=autofillTabs[message.tabId];
    const tab=await chrome.tabs.get(message.tabId);
    if(!current?.siteConfirmed || !authorized || current.url!==tab.url || authorized.applied || authorized.undone || submissionRevocations.has(tab.id) || confirmingTabs.has(tab.id))
      throw Error('Application changed. Review the employer page.');
    const [frame]=await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>globalThis.autoApplyContent?.confirmationEvidence()});
    if(!frame.result)throw Error('The employer confirmation is no longer visible.');
    confirmingTabs.add(tab.id);
    try {
      if(!authorized.matched?.application_id){authorized.matched=await api('/ext/match',{url:tab.url});current.matched=authorized.matched;}
      if(!authorized.matched?.application_id)throw Error('No matching application to track');
      const latest=await chrome.storage.session.get(['pendingSubmissions','autofillTabs']);
      const binding=latest.autofillTabs?.[tab.id];
      if(binding && !binding.matched?.application_id && !binding.undone && binding.url===authorized.url)binding.matched=authorized.matched;
      if(!binding || binding.undone || submissionRevocations.has(tab.id) || binding.matched?.application_id!==authorized.matched.application_id ||
         binding.matched?.run_id!==authorized.matched.run_id || binding.url!==authorized.url)throw Error('Submission binding changed.');
      const pendingSubmissions=latest.pendingSubmissions || {};
      if(!pendingSubmissions[tab.id]){
        const pending={id:crypto.randomUUID(),bindingUrl:binding.url,documentId:frame.documentId,payload:{application_id:binding.matched.application_id,
          run_id:binding.matched.run_id,url:tab.url,evidence:frame.result}};
        binding.pendingSubmissionId=pending.id;pendingSubmissions[tab.id]=pending;
        await chrome.storage.session.set({autofillTabs:latest.autofillTabs,pendingSubmissions});
      }
      await commitSubmission(tab.id,pendingSubmissions[tab.id]);
    }catch(error){current.trackingError='Tracking failed: '+error.message+'. Retry applied tracking.';}
    finally{confirmingTabs.delete(tab.id);}
    return report(current);
  }
  if (message.type === 'manual-submit') {
    for(let wait=0;batchBusy && wait<20;wait++)await delay(50);
    if(running || autofillTasks || skillsRunning || batchBusy || analysisTasks.size)throw Error('Wait for autofill to finish.');
    const epoch=runEpoch,scanned=await scan(message.tabId);
    const {tabResults={},autofillTabs={},chromeRun={},chromeRunPaused=false}=await chrome.storage.session.get(['tabResults','autofillTabs','chromeRun','chromeRunPaused']);
    const current=activeResults.get(scanned.tabId)||tabResults[scanned.tabId];
    const authorized=autofillTabs[scanned.tabId];
    const ownsRun=chromeRun.tabId===scanned.tabId && chromeRun.current?.application_id===current?.matched?.application_id &&
      chromeRun.current?.run_id===current?.matched?.run_id && current?.matched?.application_id!=null &&
      authorized?.matched?.application_id===current.matched.application_id && authorized?.matched?.run_id===current.matched.run_id;
    if(staleRun(epoch) || chromeRunPaused || (chromeRun.active || chromeRun.current) && !ownsRun || !current || current.busy || current.coverBusy || current.skillsBusy || current.waiting || current.submitting ||
       current.writing?.length || cancelledTabs.has(scanned.tabId) || scanned.url!==current.url ||
       scanned.next_action?.kind!=='submit' || message.expected?.url!==scanned.url ||
       message.expected?.step!==scanned.next_action.step || current.next_action?.step!==scanned.next_action.step)
      throw Error('Application changed or is busy. Review the employer page and run Autofill again.');
    const [baseline]=await readFrames({target:{tabId:scanned.tabId,documentIds:[scanned.pageIdentities[0].documentId]},func:()=>globalThis.autoApplyContent?.confirmationEvidence()});
    if(!baseline || baseline.documentId!==scanned.pageIdentities[0].documentId || staleRun(epoch))throw Error('Application changed. Review the employer page.');
    if(authorized){submissionRevocations.delete(scanned.tabId);delete authorized.undone;authorized.submitBefore=baseline.result;authorized.submitAt=Date.now();authorized.submitUrl=scanned.url;authorized.submitDocumentId=scanned.pageIdentities?.[0]?.documentId;
      authorized.submitApplicationId=authorized.matched?.application_id;authorized.submitRunId=authorized.matched?.run_id;await chrome.storage.session.set({autofillTabs});}
    current.submitting=true;current.progress='Submitting... Waiting for employer confirmation.';await report(current);
    try {
      const {chromeRunPaused:paused=false}=await chrome.storage.session.get('chromeRunPaused');
      if(staleRun(epoch) || paused || cancelledTabs.has(scanned.tabId) || closedTabs.has(scanned.tabId) || running || skillsRunning || analysisTasks.size || writingTasks.has(scanned.tabId) || activeResults.has(scanned.tabId) && activeResults.get(scanned.tabId)!==current || current.waiting)throw Error('Application became busy. Review the employer page.');
      const [frame]=await chrome.scripting.executeScript({target:{tabId:scanned.tabId,documentIds:[scanned.pageIdentities[0].documentId]},
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
    if(scanned.sign_in){
      const {autofillTabs={}}=await chrome.storage.session.get('autofillTabs');
      Object.assign(current,{...scanned},await accountContext(scanned.url,autofillTabs[scanned.tabId] || current,'autofill'));
      current.accountPreview=false;
      activeResults.set(scanned.tabId,current);
      autofillTabs[scanned.tabId]={...autofillTabs[scanned.tabId],url:scanned.url,matched:current.matched,account_action:current.account_action};
      await chrome.storage.session.set({autofillTabs});
      current.detail=current.progress=await fillAccount(current);
      current.waiting=true;current.busy=false;
      pollForForm(current);
      return report(current);
    }
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
    await retireSubmissionProof(result.tabId);
    // An HTTP write already accepted before Undo must settle first, so the
    // explicit reversal remains the last server operation.
    await submissionTasks.get(result.tabId)?.catch(()=>{});
    await submissionWrites.get(result.tabId)?.catch(()=>{});
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
  if(message.type==='public-job-context') {
    if(sender.frameId!==0 || !sender.tab?.url || postingKey(sender.url)!==postingKey(sender.tab.url))return;
    publicJobContext(message.page,sender.tab.url).then(()=>respond({ok:true}),()=>respond({ok:false}));
    return true;
  }
  if (sender.id !== chrome.runtime.id || !sender.url?.startsWith(chrome.runtime.getURL(""))) return;
  if(message.type==='sidebar-layout'){
    const bound=Number(new URL(sender.url).searchParams.get('tabId'));
    if(bound && bound!==message.tabId)return;
    (async()=>{
      const id=bound || message.tabId || sender.tab?.id;
      if(!id)return {preferred:(await chrome.storage.local.get('sidebarLayout')).sidebarLayout||'overlay',effective:'overlay'};
      const [frame]=await chrome.scripting.executeScript({target:{tabId:id},func:mode=>globalThis.autoApplyContent?.sidebarLayout(mode),args:[message.mode||null]});
      return frame.result;
    })().then(respond,error=>respond({error:error.message}));return true;
  }
  if (message.type === "run-status-refresh") {
    refreshRunStatus().then(respond, error => respond({error: error.message}));
    return true;
  }
  if (!["skill-selection", "skills-available", "autofill-skills", "cancel-skills", "skills-status", "autofill", "page-context", "chat-context", "chat-replace", "fill-answer", "skip-answer", "cover-letter", "undo-applied", "next-page", "manual-submit", "retry-submission-tracking", "load-options", "auto-advance-preference", "cancel-form-wait", "page-snapshot", "snapshot-preference", "answer-context-changed"].includes(message.type)) return;
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
  action(message).then(respond, error => respond({error: error.message})).finally(()=>{if(blocksAnalysis)analysisBlockers--;drainAutomaticAnalysis();});
  return true;
});
chrome.commands.onCommand.addListener(command => {
  if(command==='autofill')autofill();
  if(command==='toggle-sidebar')showSidebar(undefined,'toggle').catch(console.error);
});

// Alarms wake a suspended MV3 worker; the short timer advances an active run promptly.
let batchBusy = false, runEpoch = 0, ownedRun;
let runSaving=Promise.resolve(), cancelSending;
// Load ownership before starting a tick; onRemoved can synchronously invalidate
// a known owner, including while a document or backend request is loading.
const runReady=chrome.storage.session.get('chromeRun').then(({chromeRun})=>{ownedRun ||= chromeRun;});
const staleRun = epoch => epoch !== runEpoch;
function saveRun(run, changePause = false, epoch) {
  if(epoch!==undefined && staleRun(epoch))return Promise.resolve();
  ownedRun=structuredClone(run);
  const snapshot=structuredClone(run);
  runSaving=runSaving.catch(()=>{}).then(async()=>{
    if(epoch!==undefined && staleRun(epoch))return;
    if(changePause)await chrome.storage.session.set({chromeRunPaused:Boolean(snapshot.paused)});
    const {chromeRunPaused=false}=await chrome.storage.session.get('chromeRunPaused');
    if(epoch!==undefined && staleRun(epoch))return;
    snapshot.paused=chromeRunPaused;
    await chrome.storage.session.set({chromeRun:snapshot});
    if(epoch!==undefined && staleRun(epoch))return;
    await chrome.runtime.sendMessage({type:'run-status',run:snapshot}).catch(()=>{});
  });
  return runSaving;
}
async function flushRunCancel() {
  if(cancelSending)return cancelSending;
  cancelSending=(async()=>{
    const {pendingRunCancel}=await chrome.storage.session.get('pendingRunCancel');
    if(!pendingRunCancel)return;
    try {await api('/ext/run/cancel',pendingRunCancel);}
    catch(error){
      // A newer backend run must never be cancelled by an old retry.
      const status=await api('/ext/run/status');
      if(status.items?.some(item=>item.run_id===pendingRunCancel.run_id && ['pending','opened'].includes(item.state)))throw error;
    }
    const {pendingRunCancel:latest}=await chrome.storage.session.get('pendingRunCancel');
    if(latest?.run_id===pendingRunCancel.run_id)await chrome.storage.session.remove('pendingRunCancel');
  })().finally(()=>{cancelSending=undefined;});
  return cancelSending;
}
function cancelOwnedRun(run) {
  const epoch=++runEpoch; // before ANY await, including storage and tab cleanup
  const retired=retireSubmissionProof(run.tabId);
  cancelWait(run.tabId);activeResults.delete(run.tabId);
  const stopping=stopRunWork(run.tabId);
  const stopped={...run,active:false,paused:false,error:''};
  for(const key of ['current','tabId','result','started','progressSent','phase'])delete stopped[key];
  ownedRun=stopped;
  // One durable tombstone survives worker restarts and failed HTTP responses.
  const persisted=chrome.storage.session.set({chromeRun:stopped,chromeRunPaused:false,pendingRunCancel:{run_id:run.current.run_id}});
  const saved=saveRun(stopped,true,epoch);
  return persisted.then(()=>Promise.all([saved,flushRunCancel(),stopping,retired]));
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
  await Promise.race([chrome.scripting.executeScript({target:{tabId,allFrames:true},func:()=>{globalThis.autoApplyContent?.cancelSkills();globalThis.autoApplyContent?.cancelRepair();}}).catch(()=>{}),delay(500)]);
}
async function runStatus(run, epoch) {
  const status=await api('/ext/run/status');
  if(staleRun(epoch))return false;
  const item=status.items?.find(i=>i.application_id===run.current?.application_id && i.run_id===run.current?.run_id);
  if(run.current && status.items && item?.state!=='opened') {
    const stoppedEpoch=++runEpoch;
    await stopRunWork(run.tabId);
    delete run.current;delete run.tabId;delete run.result;delete run.started;delete run.parkBinding;
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
  await runReady;
  let {chromeRun: run = {paused: false, index: 0, total: 0}} = await chrome.storage.session.get('chromeRun');
  try {
    await flushRunCancel();
    if(staleRun(epoch))return;
    const {chromeRunPaused = false} = await chrome.storage.session.get('chromeRunPaused');
    if (chromeRunPaused) return;
    const {pendingSubmissions={}}=await chrome.storage.session.get('pendingSubmissions');
    // Verified receipts outlive tabs and owned runs. Retry each persisted proof
    // once on the existing tick, without accessing a closed document again.
    for(const [id,pending] of Object.entries(pendingSubmissions)){
      const tabId=Number(id);
      if(submissionTasks.has(tabId) || submissionWrites.has(tabId))continue;
      await commitSubmission(tabId,pending).catch(()=>{});
      if(staleRun(epoch))return;
    }
    if(submissionTasks.has(run.tabId) || submissionWrites.has(run.tabId))return;
    if(run.parkBinding){
      const tab=await chrome.tabs.get(run.tabId);
      const pages=await chrome.scripting.executeScript({target:{tabId:run.tabId,allFrames:true},func:()=>globalThis.autoApplyContent?.repairContext()});
      if(tab.url!==run.parkBinding.url || !run.parkBinding.pages.every(old=>pages.some(page=>
        page.frameId===old.frameId && page.documentId===old.documentId && page.result?.identity===old.identity && page.result.revision===old.revision))){
        delete run.result;delete run.parkBinding;run.paused=true;run.error='Page changed. Review the preserved application before resuming.';
        await saveRun(run,true,epoch);return;
      }
    }
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
      delete run.started;delete run.result;delete run.tabId;delete run.progressSent;delete run.parkBinding;
      if(opened) {
        const {autofillTabs={}}=await chrome.storage.session.get('autofillTabs');
        const tabs=await chrome.tabs.query({});
        const tab=tabs.find(t=>autofillTabs[t.id]?.matched?.application_id===item.application_id && autofillTabs[t.id]?.matched?.run_id===item.run_id)
          || tabs.find(t=>(t.pendingUrl||t.url)===item.url);
        if(tab){run.tabId=tab.id;run.started=true;}
        else {await cancelOwnedRun(run);return;}
      }
      await saveRun(run,false,epoch);
    }
    if(staleRun(epoch))return;
    if(run.tabId) {
      try{await chrome.tabs.get(run.tabId);}catch{
        await cancelOwnedRun(run);return;
      }
    }
    if(run.result) {
      await api('/ext/run/result',run.result);
      if(staleRun(epoch))return;
      // Nonterminal progress from older extension versions remains held too.
      if(run.result.state!=='skipped'){delete run.result;run.started=true;await saveRun(run,false,epoch);return;}
      delete run.current;delete run.tabId;delete run.result;delete run.started;delete run.parkBinding;
      await saveRun(run,false,epoch);return;
    }
    if (!run.tabId) {
      const tab=await chrome.tabs.create({url:run.current.url,active:true});
      if(staleRun(epoch)){await stopRunWork(tab.id);await chrome.tabs.remove(tab.id).catch(()=>{});return;}
      run.tabId=tab.id;run.phase='loading';ownedRun=run;
      if(closedTabs.has(tab.id)){await cancelOwnedRun(run);return;}
      await saveRun(run,false,epoch);
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
    const detail=(result?.error || result?.trackingError || result?.summary?.errors?.join('; ') || result?.detail || result?.progress || 'Review this application. Skip to continue; closing the tab ends the run.').slice(0,2000);
    const progress={application_id:run.current.application_id,run_id:run.current.run_id,state:['filling','loading'].includes(run.phase)?'needs_input':run.phase,detail};
    const signature=JSON.stringify(progress);
    if(signature!==run.progressSent){await api('/ext/run/result',progress);if(staleRun(epoch))return;run.progressSent=signature;}
    run.error='';await saveRun(run,false,epoch);
  } catch(error) {if(!staleRun(epoch)){run.error=error.message;await saveRun(run,false,epoch);}}
  finally {batchBusy=false;drainAutomaticAnalysis();}
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
  closedTabs.add(tabId);
  // Retire polls immediately; an already received proof message still gets its
  // bounded native verification before cancellation can invalidate its owner.
  clearTimeout(formWaits.get(tabId));formWaits.delete(tabId);formWaitTokens.delete(tabId);pendingSteps.delete(tabId);
  await submissionTasks.get(tabId)?.catch(()=>{});
  cancelWait(tabId);
  await runReady;
  const {pendingSubmissions={},autofillTabs:authorizedTabs={}}=await chrome.storage.session.get(['pendingSubmissions','autofillTabs']);
  const pending=pendingSubmissions[tabId],authorized=authorizedTabs[tabId];
  const preserveProof=validSubmissionProof(tabId,authorized,pending);
  const owner=ownedRun?.tabId===tabId && ownedRun.current?ownedRun:null;
  const matches=payload=>owner && payload?.application_id===owner.current.application_id && payload?.run_id===owner.current.run_id;
  if(owner){
    if(preserveProof && matches(pending.payload)){/* Retain ownership until acknowledgement. */}
    else if(authorized?.applied && matches(authorized.matched))await runStatus(owner,runEpoch).catch(console.error);
    else await cancelOwnedRun(owner).catch(console.error);
  }
  const {tabResults = {}} = await chrome.storage.session.get("tabResults");
  const {autofillTabs = {}} = await chrome.storage.session.get("autofillTabs");
  cancelWait(tabId);
  activeResults.delete(tabId);
  delete tabResults[tabId]; if(!preserveProof)delete autofillTabs[tabId]; pendingSteps.delete(tabId);
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
      if(message.type==='run-cancel' && run.current){await cancelOwnedRun(run);return ownedRun;}
      await retireSubmissionProof(run.tabId);
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
    if(run.paused){
      // Pausing revokes unverified intent across suspension/restart. Already
      // verified pending receipts remain durable and wait for resume.
      const {autofillTabs={}}=await chrome.storage.session.get('autofillTabs');
      for(const binding of Object.values(autofillTabs))delete binding.submitAt;
      await chrome.storage.session.set({autofillTabs});
      await stopRunWork(run.tabId);
    }
    else if(run.tabId)cancelledTabs.delete(run.tabId);
    await saveRun(run,true,commandEpoch);
    if (!run.paused) runTick();
    return run;
  })().then(respond, error => respond({error: error.message}));
  return true;
});
globalThis.autoApplyRunTick = runTick;
runTick();

// Automatic recovery shares page-snapshot's guarded live repair. Signals carry
// no field values; the worker rebinds document and shape before doing work.
const automaticPending=new Map(),automaticSeen=new Map(),automaticRunning=new Set();
const automaticRetryTimers=new Map();
const automaticReady=chrome.storage.session.get('automaticRecovery').then(({automaticRecovery=[]})=>{
  for(const [tabId,state] of automaticRecovery){automaticSeen.set(tabId,state);if(!state.settled && state.retryAt)scheduleAutomaticRetry(tabId,state.retryAt);}
});
const saveAutomaticRecovery=()=>chrome.storage.session.set({automaticRecovery:[...automaticSeen].slice(-64)});
function clearAutomaticRetry(tabId){clearTimeout(automaticRetryTimers.get(tabId));automaticRetryTimers.delete(tabId);}
function scheduleAutomaticRetry(tabId,at){
  clearAutomaticRetry(tabId);
  automaticRetryTimers.set(tabId,setTimeout(()=>{automaticRetryTimers.delete(tabId);queueAutomaticAnalysis(tabId);},Math.max(100,at-Date.now())));
}
const automaticAnalysisSignature=frames=>JSON.stringify([...frames].sort((a,b)=>a.frameId-b.frameId).map(f=>[f.documentId,f.result.shape]));
let automaticDrain,automaticDraining=false;
function queueAutomaticAnalysis(tabId){
  if(!tabId || closedTabs.has(tabId) || cancelledTabs.has(tabId))return;
  automaticPending.set(tabId,true);drainAutomaticAnalysis();
}
function drainAutomaticAnalysis(){
  clearTimeout(automaticDrain);
  automaticDrain=setTimeout(()=>void runAutomaticAnalysis(),100);
}
async function runAutomaticAnalysis(){
  if(running || autofillTasks || skillsRunning || analysisBlockers || batchBusy || manualActionTabs.size || analysisTasks.size)return;
  if(automaticDraining)return;automaticDraining=true;
  try{await automaticReady;for(const tabId of automaticPending.keys()){
    const old=activeResults.get(tabId);
    if(pendingSteps.has(tabId) || writingTasks.has(tabId) || old?.busy || old?.coverBusy || old?.skillsBusy)continue;
    automaticPending.delete(tabId);
    try{
      const {autoSendStructure=true}=await chrome.storage.local.get('autoSendStructure');
      const {chromeRunPaused=false}=await chrome.storage.session.get('chromeRunPaused');
      if(!autoSendStructure || chromeRunPaused || sidebarStates.get(tabId)==='closed' || closedTabs.has(tabId) || cancelledTabs.has(tabId))continue;
      const tab=await chrome.tabs.get(tabId);
      const frames=await readFrames({target:{tabId,allFrames:true},func:()=>globalThis.autoApplyContent?.runtimeState()});
      if(frames.some(f=>f.result?.account)){scheduleAutoAccount(tabId);continue;}
      const valid=frames.filter(f=>f.result?.application);
      if(!valid.length)continue;
      const signature=automaticAnalysisSignature(valid);
      const cached=automaticSeen.get(tabId),revision=JSON.stringify(old?.answerRevision);
      const same=cached?.signature===signature && cached.revision===revision;
      if(same && cached.settled)continue;
      if(same && cached.retryAt>Date.now()){scheduleAutomaticRetry(tabId,cached.retryAt);continue;}
      const attempts=(same?cached.attempts || 0:0)+1;
      automaticRunning.add(tabId);
      try{
        if(old){old.recoveryPending=true;old.progress='Matching unanswered questions...';await report(old);}
        const analyzed=await action({type:'page-snapshot',tabId,automatic:true});
        const retry=analyzed.automaticRetry && attempts<3;
        const state={signature:analyzed.recoverySignature || analyzed.automaticSignature || signature,
          revision:JSON.stringify(activeResults.get(tabId)?.answerRevision),attempts,
          settled:!retry,retryAt:retry?Date.now()+16000:0};
        automaticSeen.set(tabId,state);await saveAutomaticRecovery();
        const current=activeResults.get(tabId);
        if(current){current.recoveryPending=retry;if(retry)current.progress='Retrying answer recovery shortly...';await report(current);}
        if(retry)scheduleAutomaticRetry(tabId,state.retryAt);
      }catch(error){
        const retry=attempts<3 && !/changed|stopped|disabled|closed|paired/i.test(error.message || '');
        const state={signature,revision,attempts,settled:!retry,retryAt:retry?Date.now()+16000:0};
        automaticSeen.set(tabId,state);await saveAutomaticRecovery();
        const current=activeResults.get(tabId);
        if(current){current.recoveryPending=retry;current.progress=retry?'Retrying answer recovery shortly...':'Answer recovery stopped. Review the page.';await report(current);}
        if(retry)scheduleAutomaticRetry(tabId,state.retryAt);
      }finally{automaticRunning.delete(tabId);}
    }catch{/* A new shape, user action or profile refresh can rearm recovery. */}
  }}finally{automaticDraining=false;}
}
chrome.tabs.onRemoved.addListener(tabId=>{automaticPending.delete(tabId);automaticSeen.delete(tabId);clearAutomaticRetry(tabId);void saveAutomaticRecovery();});
chrome.tabs.onUpdated.addListener((tabId,change)=>{if(change.url || change.status==='loading'){automaticPending.delete(tabId);automaticSeen.delete(tabId);clearAutomaticRetry(tabId);void saveAutomaticRecovery();}});
chrome.storage.onChanged.addListener((changes,area)=>{
  if(area!=='local' || !changes.autoSendStructure)return;
  if(changes.autoSendStructure.newValue===false){
    automaticPending.clear();
    for(const tabId of automaticRetryTimers.keys())clearAutomaticRetry(tabId);
    for(const tabId of automaticRunning)chrome.scripting.executeScript({target:{tabId,allFrames:true},func:()=>globalThis.autoApplyContent?.cancelRepair()}).catch(()=>{});
  }else{
    automaticSeen.clear();
    void saveAutomaticRecovery();
    chrome.tabs.query({}).then(tabs=>tabs.filter(t=>/^https?:/.test(t.url || '')).forEach(t=>queueAutomaticAnalysis(t.id))).catch(()=>{});
  }
});
const submissionTasks=new Map(), submissionWrites=new Map(), submissionRevocations=new Set();
function validSubmissionProof(tabId,binding,proof){
  return Boolean(proof?.id && proof.payload?.application_id!=null && binding && !binding.undone && !binding.applied &&
    !submissionRevocations.has(tabId) && binding.matched?.application_id===proof.payload.application_id &&
    binding.matched?.run_id===proof.payload.run_id && binding.url===(proof.bindingUrl || proof.payload.url) &&
    (!binding.pendingSubmissionId || binding.pendingSubmissionId===proof.id));
}
async function retireSubmissionProof(tabId){
  if(tabId==null)return;
  submissionRevocations.add(tabId);
  const {autofillTabs={},pendingSubmissions={}}=await chrome.storage.session.get(['autofillTabs','pendingSubmissions']);
  if(autofillTabs[tabId]){
    autofillTabs[tabId].undone=true;delete autofillTabs[tabId].submitAt;delete autofillTabs[tabId].pendingSubmissionId;
  }
  delete pendingSubmissions[tabId];
  await chrome.storage.session.set({autofillTabs,pendingSubmissions});
}
function sameWorkdayPosting(before,after){
  try{
    const a=new URL(before),b=new URL(after);
    const job=path=>path.replace(/\/apply(?:\/.*)?$/i,'').replace(/\/$/,'');
    return a.protocol==='https:' && a.origin===b.origin && /^(?:.+\.)?myworkdayjobs\.com$/.test(a.hostname) &&
      /\/job\/.+/i.test(a.pathname) && job(a.pathname)===job(b.pathname);
  }catch{return false;}
}
async function commitSubmission(tabId,proof){
  if(submissionWrites.has(tabId))return submissionWrites.get(tabId);
  const task=(async()=>{
    const initial=await chrome.storage.session.get(['autofillTabs','pendingSubmissions']);
    const binding=initial.autofillTabs?.[tabId];
    if(initial.pendingSubmissions?.[tabId]?.id!==proof.id || !validSubmissionProof(tabId,binding,proof)){
      // Retire invalid proofs rather than reviving them if the tab is assigned
      // back to this application later.
      if(initial.pendingSubmissions?.[tabId]?.id===proof.id){delete initial.pendingSubmissions[tabId];await chrome.storage.session.set({pendingSubmissions:initial.pendingSubmissions});}
      return;
    }
    // The existing endpoint reconciles lost acknowledgements idempotently using
    // the same application/run/evidence payload and duplicate-submission guard.
    await api('/ext/applied',proof.payload);
    const {autofillTabs={},pendingSubmissions={},tabResults={}}=await chrome.storage.session.get(['autofillTabs','pendingSubmissions','tabResults']);
    const authorized=autofillTabs[tabId],result=activeResults.get(tabId) || tabResults[tabId];
    const stillBound=validSubmissionProof(tabId,authorized,proof);
    if(stillBound){authorized.applied=true;delete authorized.pendingSubmissionId;}
    // Save the acknowledgement before any best-effort UI notification. A failed
    // panel update must not cause a second applied request.
    if(pendingSubmissions[tabId]?.id===proof.id)delete pendingSubmissions[tabId];
    await chrome.storage.session.set({autofillTabs,pendingSubmissions});
    const closed=closedTabs.has(tabId) || !await chrome.tabs.get(tabId).catch(()=>null);
    if(closed && stillBound){
      await runReady;
      const {chromeRun:run}=await chrome.storage.session.get('chromeRun');
      const owner=run?.tabId===tabId && run.current?.application_id===proof.payload.application_id && run.current?.run_id===proof.payload.run_id;
      // Reconcile the acknowledged owner before removing its close-race marker.
      if(owner)await runStatus(run,runEpoch).catch(()=>{});
      if(!owner || ownedRun?.tabId!==tabId){
        delete autofillTabs[tabId];delete tabResults[tabId];activeResults.delete(tabId);
        await chrome.storage.session.set({autofillTabs,tabResults});
      }
    }
    if(stillBound && !closed && result?.matched?.application_id===proof.payload.application_id && result?.matched?.run_id===proof.payload.run_id){
      result.applied=true;result.trackingError='';result.progress='';await report(result).catch(()=>{});
    }
  })();
  submissionWrites.set(tabId,task);
  try{return await task;}finally{if(submissionWrites.get(tabId)===task)submissionWrites.delete(tabId);}
}
const confirmingTabs=new Set();
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
  if(sender.id!==chrome.runtime.id||!sender.tab?.id)return;
  const tabId=sender.tab.id;
  if(message.type==='sidebar-layout-state' && sender.frameId===0){chrome.runtime.sendMessage({...message,tabId}).catch(()=>{});return;}
  if(message.type==='sidebar-ready' && sender.frameId===0)restoreSidebar(tabId).catch(console.error);
  if(message.type==='application-state-changed'){
    // Footer state is read-only, refreshed even with automatic repair disabled.
    readFrames({target:{tabId,frameIds:[sender.frameId || 0]},func:()=>globalThis.autoApplyContent?.runtimeState()}).then(async frames=>{
      let result=activeResults.get(tabId);const state=frames[0]?.result;
      if(!result && (state?.application || state?.navigation?.kind==='entry')){
        result={tabId,url:sender.tab.url,page:{},fields:[],pending:[],writing:[],completed:[],summary:{filled:0,skipped:0,needs_input:[],needs_writing:[],errors:[]}};
        activeResults.set(tabId,result);
      }
      if(state?.account){
        const auth=(await accountFrames(tabId)).find(f=>f.result?.active);
        if(auth && result){result.sign_in=true;result.accountMode=auth.result.mode;result.accountReady=auth.result.actionReady;await report(result);}
        scheduleAutoAccount(tabId);return;
      }
      if(result && state && sender.frameId===0){if(result.sign_in){result.waiting=false;result.accountPreview=false;result.progress=result.detail='Review the application.';}result.sign_in=false;delete result.accountMode;result.next_action=state.navigation;await report(result);}
      if(state?.application)queueAutomaticAnalysis(tabId);
    }).catch(()=>{});
  }
  if(message.type==='sidebar-ready' || message.type==='account-state-changed')scheduleAutoAccount(tabId);
  if(message.type==='sidebar-state' && sender.frameId===0){showSidebar(tabId,message.state).catch(console.error);return;}
  if(message.type==='sidebar-control'&&sender.url?.startsWith(chrome.runtime.getURL(''))){showSidebar(tabId,message.state).then(()=>respond({ok:true}));return true;}
  if(!['ask-ai','sidebar-ready','sidebar-state','user-answer','submit-intent','submission-confirmation'].includes(message.type)||sender.url?.startsWith(chrome.runtime.getURL('')))return;
  const submissionMessage=['submit-intent','submission-confirmation'].includes(message.type);
  const prior=submissionTasks.get(tabId),epoch=runEpoch;
  const task=(async()=>{
    if(submissionMessage)await prior?.catch(()=>{});
    const {autofillTabs={},tabResults={},chromeRun,chromeRunPaused=false}=await chrome.storage.session.get(['autofillTabs','tabResults','chromeRun','chromeRunPaused']);
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
    if(!authorized||sender.frameId!==0||cancelledTabs.has(tabId) || staleRun(epoch) || chromeRunPaused ||
       (chromeRun?.active || chromeRun?.current) && (chromeRun.tabId!==tabId ||
        chromeRun.current?.application_id!==authorized.matched?.application_id || chromeRun.current?.run_id!==authorized.matched?.run_id))return;
    if(message.type==='submit-intent'){
      if(message.url!==sender.tab.url || result?.url!==sender.tab.url ||
         result.pageIdentities?.[0]?.documentId!==sender.documentId)return;
      const [baseline]=await readFrames({target:{tabId,documentIds:[sender.documentId]},func:()=>globalThis.autoApplyContent?.submissionBaseline()}).catch(()=>[]);
      if(staleRun(epoch) || cancelledTabs.has(tabId))return;
      submissionRevocations.delete(tabId);delete authorized.undone;
      // A native click can navigate before the original document can answer a
      // read. Its already document-bound intent carries only the empty baseline
      // needed for handoff; receipt evidence is always reread in the new DOM.
      authorized.submitBefore=baseline?.documentId===sender.documentId?baseline.result:message.before===''?'':undefined;
      authorized.submitAt=Date.now();authorized.submitUrl=sender.tab.url;
      authorized.submitDocumentId=sender.documentId;authorized.submitApplicationId=authorized.matched?.application_id;
      authorized.submitRunId=authorized.matched?.run_id;
      await chrome.storage.session.set({autofillTabs});return;
    }
    if(authorized.applied||authorized.undone||submissionRevocations.has(tabId)||result?.siteConfirmed||confirmingTabs.has(tabId))return;
    const url=new URL(sender.tab.url),strong=(/^(?:.+\.)?(?:greenhouse\.io|lever\.co)$/.test(url.hostname)&&/\/(confirmation|thanks)(?:[/?#]|$)/i.test(url.pathname))||/^(?:.+\.)?ashbyhq\.com$/.test(url.hostname)&&/\/application\/.+\/submitted(?:[/?#]|$)/i.test(url.pathname);
    const replacement=authorized.submitDocumentId!==sender.documentId;
    if(!strong&&(!authorized.submitAt||Date.now()-authorized.submitAt>120000 ||
       authorized.submitApplicationId!==authorized.matched?.application_id ||
       authorized.submitRunId!==authorized.matched?.run_id))return;
    // Only a recent, receipt-free explicit intent on this canonical Workday
    // posting can cross a document boundary. Origin alone is insufficient.
    if(!strong && replacement && (authorized.submitBefore!=='' ||
       !sameWorkdayPosting(authorized.submitUrl,sender.tab.url) || !sameWorkdayPosting(authorized.url,sender.tab.url)))return;
    if(!message.evidence||message.url!==sender.tab.url || !result ||
       result.matched?.application_id!==authorized.matched?.application_id || result.matched?.run_id!==authorized.matched?.run_id ||
       new URL(authorized.url).origin!==url.origin)return;
    const tab=await chrome.tabs.get(tabId).catch(()=>null);
    if(!tab || tab.url!==sender.tab.url)return;
    const [proof]=await readFrames({target:{tabId,documentIds:[sender.documentId]},func:()=>({evidence:globalThis.autoApplyContent?.confirmationEvidence(),errors:globalThis.autoApplyContent?.validationErrors()})});
    if(!proof || proof.documentId!==sender.documentId || proof.result?.errors?.length ||
       !strong && (!proof.result?.evidence || proof.result.evidence!==message.evidence || proof.result.evidence===authorized.submitBefore) || staleRun(epoch))return;
    const latest=await chrome.storage.session.get(['autofillTabs','chromeRunPaused']);
    const binding=latest.autofillTabs?.[tabId];
    if(confirmingTabs.has(tabId)||result.siteConfirmed || latest.chromeRunPaused || submissionRevocations.has(tabId) ||
       !binding || binding.undone || binding.applied || binding.submitAt!==authorized.submitAt ||
       binding.url!==authorized.url || binding.matched?.application_id!==authorized.matched?.application_id ||
       binding.matched?.run_id!==authorized.matched?.run_id || staleRun(epoch))return;
    confirmingTabs.add(tabId);
    try{
      if(!authorized.matched?.application_id){result.siteConfirmed=true;result.submitting=false;result.progress='';result.trackingError='No matching application to track.';await report(result);return;}
      const pending={id:crypto.randomUUID(),documentId:sender.documentId,bindingUrl:binding.url,payload:{application_id:authorized.matched.application_id,
        run_id:authorized.matched.run_id,url:message.url,evidence:message.evidence}};
      const {pendingSubmissions={}}=await chrome.storage.session.get('pendingSubmissions');pendingSubmissions[tabId]=pending;
      binding.pendingSubmissionId=pending.id;binding.submitDocumentId=sender.documentId;
      latest.autofillTabs[tabId]=binding;
      await chrome.storage.session.set({pendingSubmissions,autofillTabs:latest.autofillTabs});
      result.siteConfirmed=true;result.submitting=false;result.progress='';await report(result);
      await commitSubmission(tabId,pending);
    }catch(error){result.trackingError='Tracking failed: '+error.message+'. Retry applied tracking.';await report(result);throw error;}finally{confirmingTabs.delete(tabId);}
  })();
  if(submissionMessage){
    submissionTasks.set(tabId,task);
    task.finally(()=>{if(submissionTasks.get(tabId)===task)submissionTasks.delete(tabId);}).catch(()=>{});
  }
  task.then(respond,error=>respond({error:error.message}));return true;
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
