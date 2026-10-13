import {api} from "./api.js";

async function showSidebar(tabId, mode='open') {
  const tab=tabId===undefined?(await chrome.tabs.query({active:true,currentWindow:true}))[0]:await chrome.tabs.get(tabId);
  if(!tab?.id||!/^https?:/.test(tab.url||''))return;
  const [frame]=await chrome.scripting.executeScript({target:{tabId:tab.id},func:(mode,id)=>globalThis.autoApplyContent?.sidebar(mode,id),args:[mode,tab.id]});
  const {sidebarTabs={}}=await chrome.storage.session.get('sidebarTabs');sidebarTabs[tab.id]=frame?.result||mode;
  await chrome.storage.session.set({sidebarTabs});
}
chrome.action.onClicked.addListener(tab=>showSidebar(tab.id,'toggle').catch(console.error));
globalThis.autoApplySidebar=showSidebar;
let running = false;
const activeResults = new Map(), writingTasks = new Map();
let reporting = Promise.resolve();

function report(result) {
  reporting = reporting.catch(() => {}).then(async () => {
  const {tabResults = {}} = await chrome.storage.session.get("tabResults");
  if (result.tabId) tabResults[result.tabId] = result;
  await chrome.storage.session.set({lastResult: result, tabResults});
  await chrome.runtime.sendMessage({type: "autofill-result", result}).catch(() => {});
  return result;
  });
  return reporting;
}

async function scan(tabId) {
  const tab = tabId === undefined ? (await chrome.tabs.query({active: true, currentWindow: true}))[0] : await chrome.tabs.get(tabId);
  if (!tab?.id || !/^https?:\/\//.test(tab.url || "")) throw new Error("Open an HTTP or HTTPS application form first.");
  const frames = (await chrome.scripting.executeScript({target: {tabId: tab.id, allFrames: true}, world: "ISOLATED",
    func: () => globalThis.autoApplyContent?.scan()})).filter(frame => frame.result);
  if (!frames.length) throw new Error("Form scanner is unavailable. Reload the application page and try again.");
  const page = {...(frames.find(frame => frame.frameId === 0) || frames[0]).result.page, url: tab.url};
  const fields = frames.flatMap(frame => frame.result.fields.map(field => ({...field, key: `${frame.frameId}:${field.key}`})));
  if (fields.length > 500) throw new Error("This page has more than 500 fields. Open a smaller form section.");
  return {tabId: tab.id, url: tab.url, page, fields, workday_step: frames.find(frame => frame.frameId === 0)?.result.workday_step, sign_in: frames.some(frame => frame.result.sign_in), manual_entry: frames.some(frame => frame.result.manual_entry), timings: {scan_ms: frames.reduce((sum, frame) => sum + (frame.result.scan_ms || 0), 0)}};
}

const context = result => ({page: result.page, application_id: result.matched?.application_id});

async function fill(result, values, files = {}) {
  if ((await chrome.tabs.get(result.tabId)).url !== result.url) throw new Error("Application page changed. Run autofill again.");
  for (const frameId of new Set(values.map(value => Number(value.key.split(":")[0])))) {
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
  result.coverBusy = true;
  result.coverProgress = "Writing cover letter...";
  await report(result);
  const file = await api("/ext/cover-letter", {...context(result), ...(answers ? {answers} : {})});
  if (activeResults.get(result.tabId) !== result) return;
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
const formWaits = new Map(), cancelledTabs = new Set();
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
      if (Date.now() < end) formWaits.set(result.tabId, setTimeout(tick, 1000));
      else { formWaits.delete(result.tabId); result.waiting = false; result.progress = 'No form appeared. Press Autofill to try again.'; await report(result); }
    } catch { formWaits.delete(result.tabId); }
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
  const scanned = await scan(result.tabId);
  if (automatic && (result.pending.length || result.writing.length || result.summary.errors.length ||
      scanned.sign_in || scanned.fields.some(field => field.required && !field.answered))) return false;
  const [frame] = await chrome.scripting.executeScript({target:{tabId:result.tabId},
    func: automatic => globalThis.autoApplyContent.nextPage(automatic), args:[automatic]});
  if (frame.result.errors.length) { result.summary.errors.push(...frame.result.errors); result.progress = 'Fix the page validation errors before continuing.'; await report(result); return false; }
  if (!frame.result.clicked) { if (!automatic) { result.progress = 'No Next page button is available. Review and submit on the page yourself.'; await report(result); } return false; }
  const after = await scan(result.tabId);
  // An unchanged form means navigation did not succeed; never repeatedly click it.
  return after.url !== scanned.url || after.workday_step !== scanned.workday_step ||
    after.fields.map(field=>field.key).join() !== scanned.fields.map(field=>field.key).join();
}
async function matchAndFill(result, prepare = false) {
    const sectionAnswers=[];
    if (prepare) {
    const profile = await api('/ext/profile-sections');
    const prepared = await chrome.scripting.executeScript({target:{tabId:result.tabId,allFrames:true},world:'ISOLATED',
      func: profile => globalThis.autoApplyContent?.prepareSections(profile), args:[profile]});
    result.sections=[];
    result.fields=prepared.filter(frame=>frame.result).flatMap(frame=>{
      const prefix=frame.frameId+':';
      sectionAnswers.push(...frame.result.answers.map(answer=>({...answer,key:prefix+answer.key})));
      result.sections.push(...frame.result.sections);
      return frame.result.fields.map(field=>({...field,key:prefix+field.key}));
    });
    }
    // Search menus can expose only a first page. Keep those choices for prompts,
    // but let profile matching resolve answers beyond that page as before.
    const fields = result.fields.filter(field=>!field.section_managed).map(field => field.options_partial ? {...field, options: []} : field);
    let {answers} = await api("/ext/answers", {...context(result), url: result.url, fields});
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
    const scanned = await scan(result.tabId);
    const known = new Set(result.fields.map(field=>field.key));
    const added = scanned.fields.filter(field=>!known.has(field.key) && !field.answered);
    for (const name of ['pending','writing']) result[name] = result[name].filter(field=>scanned.fields.some(item=>item.key===field.key&&!item.answered));
    result.fields = scanned.fields;
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
  if (running) return {error: "Autofill is already running."};
  running = true;
  let result = {busy: true, progress: "Scanning application..."};
  try {
    await showSidebar(tabId);
    await report(result);
    let scanned = await scan(tabId);
    if (!scanned.sign_in && !scanned.fields.length) scanned = await openApplication(scanned);
    clearTimeout(formWaits.get(scanned.tabId)); formWaits.delete(scanned.tabId); cancelledTabs.delete(scanned.tabId);
    const wasWaiting = activeResults.get(scanned.tabId)?.waiting;
    pendingSteps.delete(scanned.tabId);
    activeResults.delete(scanned.tabId);
    await showSidebar(scanned.tabId);
    result = {...scanned, completed: [], busy: true, pending: [], writing: [], summary: {filled: 0, skipped: 0, needs_input: [], needs_writing: [], errors: []}};
    activeResults.set(result.tabId, result);
    const {autofillTabs = {}} = await chrome.storage.session.get("autofillTabs");
    const saved = autofillTabs[result.tabId] || {};
    result.matched = typeof application === "object" ? application : application ? {application_id: application} : saved.matched;
    result.matched ||= await api("/ext/match", {url: result.url});
    autofillTabs[result.tabId] = {...saved, matched: result.matched};
    await chrome.storage.session.set({autofillTabs});
    if (result.matched.company) result.page.company = result.matched.company;
    if (result.matched.title) result.page.title = result.matched.title;
    if (!result.sign_in && !result.fields.length && /review/i.test(result.workday_step || '')) {
      result.progress = 'Review the application and submit on the page yourself.';
      return await report({...result,busy:false});
    }
    if (result.sign_in || !result.fields.length) {
      result.state = "needs_input";
      result.detail = result.progress = result.sign_in ? "Sign in to Workday in this tab. Autofill will resume automatically." : 'Waiting for the application form...';
      result.waiting = true;
      pollForForm(result);
      return await report({...result, busy: false});
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
    result.progress = result.workday_step ?
      `Workday step: ${result.workday_step} - filled ${result.summary.filled}, needs input ${result.pending.length}` : result.coverQuestions?.length ? "Answer the cover letter questions below." : "Autofill finished. Review the page.";
  } catch (error) { result.error = error.message; }
  finally { running = false; result.busy = false; }
  await report(result);
  if (!result.error && result.writing?.length) {
    const task = continueWriting(result).then(()=>advanceAfterWriting(result)).catch(error => {
      result.summary.errors.push(error.message);
      return report(result);
    }).finally(() => { if (writingTasks.get(result.tabId) === task) writingTasks.delete(result.tabId); });
    writingTasks.set(result.tabId, task);
  }
  if (!result.error && !result.waiting && advanceCount < 10 && (await advancePreference()).enabled && await clickNext(result, true)) {
    pendingSteps.delete(result.tabId);
    return autofill(result.tabId, result.matched, advanceCount + 1);
  }
  return result;
}

async function continueWriting(result) {
  for (const field of [...result.writing].filter(field => field.kind !== "file")) {
    if (!result.writing.some(item => item.key === field.key)) continue;
    if (activeResults.get(result.tabId) !== result) return;
    result.writingProgress = 'Writing: ' + field.label + '...';
    await report(result);
    try {
      const written = await api("/ext/write", {...context(result), question: field.label, max_length: field.max_length,
        instruction: field.max_length === null ? "" : 'Use at most ' + field.max_length + ' characters.'});
      if (activeResults.get(result.tabId) !== result) return;
      await fill(result, [{key: field.key, status: "RESOLVED", value: written.answer, source: "AI"}]);
      await rescanRevealed(result);
      result.writingProgress = 'Answer filled: ' + field.label;
      if (written.warning) result.summary.errors.push(field.label + ': ' + written.warning);
    } catch (error) { result.writingProgress = field.label + ': ' + error.message; result.summary.errors.push(result.writingProgress); }
    if (activeResults.get(result.tabId) !== result) return;
    await report(result);
  }
  if (activeResults.get(result.tabId) !== result) return;
  if (result.writing.some(field => field.kind === "file" && /cover/i.test(field.label))) {
    try { await coverLetter(result); }
    catch (error) { result.coverProgress = 'Cover letter error: ' + error.message; result.summary.errors.push(result.coverProgress); }
    finally { result.coverBusy = false; }
    if (activeResults.get(result.tabId) === result) await report(result);
  }
}

async function advanceAfterWriting(result) {
  if (!running && activeResults.get(result.tabId) === result && (await advancePreference()).enabled && await clickNext(result,true)) {
    pendingSteps.delete(result.tabId); await autofill(result.tabId,result.matched);
  }
}
async function action(message) {
  if (message.type === "autofill") return autofill(message.tabId);
  if (message.type === "page-context") return scan(message.tabId);
  if (message.type === 'auto-advance-preference') return advancePreference(message.enabled);
  if (message.type === 'cancel-form-wait') {
    const {lastResult} = await chrome.storage.session.get('lastResult');
    const id = message.tabId || lastResult?.tabId; cancelWait(id);
    const result = activeResults.get(id); if (result) {result.waiting=false;result.progress='Form waiting cancelled.';return report(result);} return {};
  }
  if (running) throw new Error("Wait for autofill to finish.");
  const {lastResult, tabResults={}} = await chrome.storage.session.get(['lastResult','tabResults']);
  const stored=message.tabId?tabResults[message.tabId]:lastResult;
  const result=activeResults.get(stored?.tabId) || stored;
  if (result?.tabId) activeResults.set(result.tabId, result);
  if (!result?.page) throw new Error("Run autofill on an application first.");
  if (message.type === 'next-page') {
    if (await clickNext(result)) {pendingSteps.delete(result.tabId);return autofill(result.tabId,result.matched);}
  } else if (message.type === 'load-options') {
    await loadOptions(result, message.key);
  } else if (message.type === "fill-answer") {
    await fill(result, [{key: message.key, status: "RESOLVED", value: message.value, force: true, source: "you"}]);
    const field = result.fields.find(field => field.key === message.key);
    if (message.remember && field && !["textarea","file"].includes(field.kind)) {
      await api("/ext/remember", {...field, ...context(result), url: result.url, value: message.value});
      result.saved = "Saved";
    }
    await rescanRevealed(result);
    if ((await advancePreference()).enabled && await clickNext(result,true)) return autofill(result.tabId,result.matched);
  } else if (message.type === 'undo-applied') {
    await api('/ext/unapplied',{application_id:result.matched.application_id});result.applied=false;
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
  if (running || batchBusy) { stepDrain = setTimeout(drainSteps, 500); return; }
  const tabId = pendingSteps.values().next().value;
  if (tabId === undefined) return;
  pendingSteps.delete(tabId);
  const {autofillTabs = {}} = await chrome.storage.session.get("autofillTabs");
  if (autofillTabs[tabId] && !cancelledTabs.has(tabId)) await autofill(tabId);
  stepDrain = setTimeout(drainSteps, 100);
}
chrome.runtime.onMessage.addListener((message, sender) => {
  if (message.type !== "workday-step" || sender.id !== chrome.runtime.id ||
      !sender.tab?.id || sender.frameId !== 0) return;
  if (activeResults.get(sender.tab.id)?.busy || cancelledTabs.has(sender.tab.id)) return;
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
  if (!["autofill", "page-context", "fill-answer", "skip-answer", "cover-letter", "undo-applied", "next-page", "load-options", "auto-advance-preference", "cancel-form-wait"].includes(message.type)) return;
  action(message).then(respond, error => respond({error: error.message}));
  return true;
});
chrome.commands.onCommand.addListener(command => {
  if(command==='autofill')autofill();
  if(command==='toggle-sidebar')showSidebar(undefined,'toggle').catch(console.error);
});

// Alarms wake a suspended MV3 worker; the short timer advances an active run promptly.
let batchBusy = false;
async function saveRun(run, changePause = false) {
  if (changePause) await chrome.storage.session.set({chromeRunPaused: Boolean(run.paused)});
  const {chromeRunPaused = false} = await chrome.storage.session.get("chromeRunPaused");
  run.paused = chromeRunPaused;
  await chrome.storage.session.set({chromeRun: run});
  await chrome.runtime.sendMessage({type: "run-status", run}).catch(() => {});
}
async function refreshRunStatus() {
  let statusError = "";
  try { await api("/ext/run/status"); }
  catch (error) { statusError = error.message; }
  const {chromeRun: run = {index: 0, total: 0}} = await chrome.storage.session.get("chromeRun");
  run.error = statusError;
  await saveRun(run);
  return run;
}
async function waitForTab(tabId) {
  const end = Date.now() + 30000;
  while (Date.now() < end) {
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
export async function runTick() {
  if (batchBusy || running) return;
  batchBusy = true;
  let {chromeRun: run = {paused: false, index: 0, total: 0}} = await chrome.storage.session.get("chromeRun");
  try {
    const {chromeRunPaused = false} = await chrome.storage.session.get("chromeRunPaused");
    if (chromeRunPaused) return;
    if (!run.current) {
      const item = await api("/ext/run/next");
      if (item.wait) return;
      if (item.done) { run.active = false; await saveRun(run); return; }
      run = {...run, active: true, current: item, index: item.index || run.index + 1, total: item.total || run.total, error: ""};
      await saveRun(run);
    }
    if (!run.result) {
      try {
        if (!run.tabId) {
          const tab = await chrome.tabs.create({url: run.current.url, active: true});
          run.tabId = tab.id;
          await saveRun(run);
        }
        await waitForTab(run.tabId);
        await showSidebar(run.tabId);
        const result = await autofill(run.tabId, run.current);
        await writingTasks.get(result.tabId);
        const prompts = result.pending?.length || result.writing?.length || result.coverQuestions?.length;
        run.result = {application_id: run.current.application_id,
          state: result.state === "needs_input" ? "needs_input" : result.error ? "failed" : prompts ? "needs_input" : result.summary?.errors.length ? "failed" : "filled",
          detail: (result.detail || result.error || result.summary?.errors.join("; ") || result.progress || "").slice(0, 2000)};
      } catch (error) {
        run.result = {application_id: run.current.application_id, state: "failed", detail: error.message.slice(0, 2000)};
      }
      run.result.run_id = run.current.run_id;
      await saveRun(run);
    }
    try { await api("/ext/run/result", run.result); }
    catch (error) {
      // Cancellation (or a replaced completed run) makes this result stale.
      if (!/not open in this run/i.test(error.message)) throw error;
    }
    delete run.current; delete run.tabId; delete run.result;
    await saveRun(run);
  } catch (error) { run.error = error.message; await saveRun(run); }
  finally { batchBusy = false; }
}
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === "chrome-run") runTick(); });
chrome.alarms.create("chrome-run", {periodInMinutes: 0.5});
setInterval(() => {
  if (batchBusy) api("/ext/run/status").catch(() => {});
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
  const {tabResults = {}} = await chrome.storage.session.get("tabResults");
  const {autofillTabs = {}} = await chrome.storage.session.get("autofillTabs");
  cancelWait(tabId);
  activeResults.delete(tabId);
  delete tabResults[tabId]; delete autofillTabs[tabId]; pendingSteps.delete(tabId);
  const {sidebarTabs={}}=await chrome.storage.session.get("sidebarTabs");delete sidebarTabs[tabId];await chrome.storage.session.set({sidebarTabs});
  await chrome.storage.session.set({tabResults, autofillTabs});
});
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || !sender.url?.startsWith(chrome.runtime.getURL("")) || message.type !== "run-pause") return;
  (async () => {
    const {chromeRun: run = {index: 0, total: 0}} = await chrome.storage.session.get("chromeRun");
    run.paused = Boolean(message.paused); await saveRun(run, true);
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
  if(message.type==='sidebar-control'&&sender.url?.startsWith(chrome.runtime.getURL(''))){showSidebar(tabId,message.state).then(()=>respond({ok:true}));return true;}
  if(!['sidebar-ready','sidebar-state','user-answer','submit-intent','submission-confirmation'].includes(message.type)||sender.url?.startsWith(chrome.runtime.getURL('')))return;
  (async()=>{
    const {autofillTabs={},tabResults={},sidebarTabs={}}=await chrome.storage.session.get(['autofillTabs','tabResults','sidebarTabs']);
    if(message.type==='sidebar-ready'){if(sidebarTabs[tabId])await showSidebar(tabId,sidebarTabs[tabId]);return;}
    if(message.type==='sidebar-state'){sidebarTabs[tabId]=message.state;await chrome.storage.session.set({sidebarTabs});return;}
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
    if(!authorized?.matched?.application_id||sender.frameId!==0)return;
    if(message.type==='submit-intent'){delete authorized.undone;authorized.submitAt=Date.now();await chrome.storage.session.set({autofillTabs});return;}
    if(authorized.applied||authorized.undone||confirmingTabs.has(tabId))return;
    const url=new URL(sender.tab.url),strong=(/^(?:.+\.)?(?:greenhouse\.io|lever\.co)$/.test(url.hostname)&&/\/(confirmation|thanks)(?:[/?#]|$)/i.test(url.pathname))||/^(?:.+\.)?ashbyhq\.com$/.test(url.hostname)&&/\/application\/.+\/submitted(?:[/?#]|$)/i.test(url.pathname);
    if(!strong&&(!authorized.submitAt||Date.now()-authorized.submitAt>120000))return;
    if(!message.evidence||message.url!==sender.tab.url)return;
    confirmingTabs.add(tabId);
    try{
      await api('/ext/applied',{application_id:authorized.matched.application_id,url:message.url,evidence:message.evidence});
      authorized.applied=true;await chrome.storage.session.set({autofillTabs});
      if(result){result.applied=true;result.progress='Marked as applied';await report(result);}
    }finally{confirmingTabs.delete(tabId);}
  })().then(respond,error=>respond({error:error.message}));return true;
});
