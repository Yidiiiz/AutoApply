import {api} from "./api.js";

chrome.sidePanel.setPanelBehavior({openPanelOnActionClick: true}).catch(console.error);
let running = false;

async function report(result) {
  await chrome.storage.session.set({lastResult: result});
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
  return {tabId: tab.id, url: tab.url, page, fields};
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

export async function autofill(tabId) {
  if (running) return {error: "Autofill is already running."};
  running = true;
  let result = {busy: true, progress: "Scanning application..."};
  try {
    await report(result);
    result = {...await scan(tabId), busy: true, pending: [], writing: [], summary: {filled: 0, skipped: 0, needs_input: [], needs_writing: [], errors: []}};
    if (!result.fields.length) throw new Error("No visible editable fields found on this page.");
    await progress(result, "Matching profile answers...");
    result.matched = await api("/ext/match", {url: result.url});
    // Search menus can expose only a first page. Keep those choices for prompts,
    // but let profile matching resolve answers beyond that page as before.
    const fields = result.fields.map(field => field.options_partial ? {...field, options: []} : field);
    const {answers} = await api("/ext/answers", {...context(result), url: result.url, fields});
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
    result.progress = result.coverQuestions?.length ? "Answer the cover letter questions below." : "Autofill finished. Review the page.";
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
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || !sender.url?.startsWith(chrome.runtime.getURL(""))) return;
  if (!["autofill", "page-context", "fill-answer", "skip-answer", "cover-letter"].includes(message.type)) return;
  action(message).then(respond, error => respond({error: error.message}));
  return true;
});
chrome.commands.onCommand.addListener(command => { if (command === "autofill") autofill(); });
