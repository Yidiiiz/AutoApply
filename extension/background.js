import {api} from "./api.js";

chrome.sidePanel.setPanelBehavior({openPanelOnActionClick: true}).catch(console.error);
let running = false;

async function report(result) {
  const {tabResults = {}} = await chrome.storage.session.get("tabResults");
  if (result.tabId) tabResults[result.tabId] = result;
  await chrome.storage.session.set({lastResult: result, tabResults});
  await chrome.runtime.sendMessage({type: "autofill-result", result}).catch(() => {});
  return result;
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
  await progress(result, "Generating cover letter... This can take up to 3 minutes.");
  const file = await api("/ext/cover-letter", {...context(result), ...(answers ? {answers} : {})});
  if (file.questions) {
    result.coverQuestions = file.questions;
    result.coverAnswers = answers || [];
    result.progress = "Cover letter needs your input. Blank answers are allowed.";
  } else {
    result.coverQuestions = [];
    result.coverFile = file;
    const fields = result.fields.filter(field => field.kind === "file" && /cover/i.test(field.label) && !field.answered);
    if (fields.length) await fill(result, fields.map(field => ({key: field.key, status: "FILE", value: "cover_letter"})), {cover_letter: file});
    result.progress = fields.length ? "Cover letter generated. Check the attachment on the page." : "Cover letter ready to download.";
  }
}

export async function autofill(tabId, application) {
  if (running) return {error: "Autofill is already running."};
  running = true;
  let result = {busy: true, progress: "Scanning application..."};
  try {
    await report(result);
    let scanned = await scan(tabId);
    if (!scanned.sign_in && !scanned.fields.length) scanned = await openApplication(scanned);
    result = {...scanned, busy: true, pending: [], writing: [], summary: {filled: 0, skipped: 0, needs_input: [], needs_writing: [], errors: []}};
    const {autofillTabs = {}} = await chrome.storage.session.get("autofillTabs");
    const saved = autofillTabs[result.tabId] || {};
    result.matched = typeof application === "object" ? application : application ? {application_id: application} : saved.matched;
    result.matched ||= await api("/ext/match", {url: result.url});
    autofillTabs[result.tabId] = {matched: result.matched};
    await chrome.storage.session.set({autofillTabs});
    if (result.matched.company) result.page.company = result.matched.company;
    if (result.matched.title) result.page.title = result.matched.title;
    if (result.sign_in) {
      result.state = "needs_input";
      result.detail = result.progress = "Sign in to Workday in this tab. Autofill will resume automatically.";
      return await report({...result, busy: false});
    }
    if (!result.fields.length) throw new Error("No visible editable fields found on this page.");
    await progress(result, "Matching profile answers...");
    // Search menus can expose only a first page. Keep those choices for prompts,
    // but let profile matching resolve answers beyond that page as before.
    const fields = result.fields.map(field => field.options_partial ? {...field, options: []} : field);
    let {answers} = await api("/ext/answers", {...context(result), url: result.url, fields});
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
    await report(result);
    for (const field of [...result.writing].filter(field => field.kind !== "file")) {
      await progress(result, `Writing: ${field.label} ... This can take up to 3 minutes.`);
      try {
        const written = await api("/ext/write", {...context(result), question: field.label, max_length: field.max_length,
          instruction: field.max_length === null ? "" : `Use at most ${field.max_length} characters.`});
        await fill(result, [{key: field.key, status: "RESOLVED", value: written.answer}]);
        if (written.warning) result.summary.errors.push(`${field.label}: ${written.warning}`);
      } catch (error) { result.summary.errors.push(`${field.label}: ${error.message}`); }
      await report(result);
    }
    if (result.writing.some(field => field.kind === "file" && /cover/i.test(field.label))) {
      try { await coverLetter(result); } catch (error) { result.summary.errors.push(error.message); }
    }
    result.progress = result.workday_step ?
      `Workday step: ${result.workday_step} - filled ${result.summary.filled}, needs input ${result.pending.length}` : result.coverQuestions?.length ? "Answer the cover letter questions below." : "Autofill finished. Review the page.";
  } catch (error) { result.error = error.message; }
  finally { running = false; result.busy = false; }
  return report(result);
}

async function action(message) {
  if (message.type === "autofill") return autofill(message.tabId);
  if (message.type === "page-context") return scan(message.tabId);
  if (running) throw new Error("Wait for autofill to finish.");
  const {lastResult: result} = await chrome.storage.session.get("lastResult");
  if (!result?.page) throw new Error("Run autofill on an application first.");
  if (message.type === "fill-answer") {
    await fill(result, [{key: message.key, status: "RESOLVED", value: message.value}]);
    if (message.remember) {
      const field = result.fields.find(field => field.key === message.key);
      await api("/ext/remember", {...field, ...context(result), url: result.url, value: message.value});
    }
  } else if (message.type === "skip-answer") {
    result.pending = result.pending.filter(field => field.key !== message.key);
    result.summary.needs_input = result.pending.map(field => field.label);
  } else if (message.type === "cover-letter") {
    running = true; result.busy = true;
    try { await coverLetter(result, message.answers); }
    catch (error) { result.error = error.message; }
    finally { running = false; result.busy = false; }
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
  if (autofillTabs[tabId]) await autofill(tabId);
  stepDrain = setTimeout(drainSteps, 100);
}
chrome.runtime.onMessage.addListener((message, sender) => {
  if (message.type !== "workday-step" || sender.id !== chrome.runtime.id ||
      !sender.tab?.id || sender.frameId !== 0) return;
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
  if (!["autofill", "page-context", "fill-answer", "skip-answer", "cover-letter"].includes(message.type)) return;
  action(message).then(respond, error => respond({error: error.message}));
  return true;
});
chrome.commands.onCommand.addListener(command => { if (command === "autofill") autofill(); });

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
        const result = await autofill(run.tabId, run.current);
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
  delete tabResults[tabId]; delete autofillTabs[tabId]; pendingSteps.delete(tabId);
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
