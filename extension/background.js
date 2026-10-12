import {api} from "./api.js";

chrome.sidePanel.setPanelBehavior({openPanelOnActionClick: true}).catch(console.error);
let running = false;

async function report(result) {
  await chrome.storage.session.set({lastResult: result});
  await chrome.runtime.sendMessage({type: "autofill-result", result}).catch(() => {});
  return result;
}

// ISOLATED is the same world used by the manifest content script, on every frame.
async function inFrames(tabId, func, args = []) {
  return chrome.scripting.executeScript({target: {tabId, allFrames: true}, world: "ISOLATED", func, args});
}

export async function autofill(tabId) {
  if (running) return {error: "Autofill is already running."};
  running = true;
  let matched = null;
  try {
    const tab = tabId === undefined ? (await chrome.tabs.query({active: true, currentWindow: true}))[0] : await chrome.tabs.get(tabId);
    if (!tab?.id || !/^https?:\/\//.test(tab.url || "")) throw new Error("Open an HTTP or HTTPS application form first.");
    const frames = (await inFrames(tab.id, () => globalThis.autoApplyContent?.scan())).filter(frame => frame.result);
    if (!frames.length) throw new Error("Form scanner is unavailable. Reload the application page and try again.");
    const page = {...(frames.find(frame => frame.frameId === 0) || frames[0]).result.page, url: tab.url};
    const fields = frames.flatMap(frame => frame.result.fields.map(field => ({...field, key: `${frame.frameId}:${field.key}`})));
    if (!fields.length) throw new Error("No visible editable fields found on this page.");
    if (fields.length > 500) throw new Error("This page has more than 500 fields. Open a smaller form section.");
    matched = await api("/ext/match", {url: tab.url});
    const {answers} = await api("/ext/answers", {url: tab.url, application_id: matched.application_id, page, fields});
    const files = {};
    if (answers.some(answer => answer.status === "FILE" && answer.value === "resume")) files.resume = await api("/ext/resume");
    const summary = {filled: 0, needs_input: [], needs_writing: [], skipped: 0};
    for (const frame of frames) {
      const prefix = `${frame.frameId}:`;
      const localAnswers = answers.filter(answer => answer.key.startsWith(prefix)).map(answer => ({...answer, key: answer.key.slice(prefix.length)}));
      if (!localAnswers.length) continue;
      const [filled] = await chrome.scripting.executeScript({target: {tabId: tab.id, frameIds: [frame.frameId]}, world: "ISOLATED",
        func: (values, attachments) => globalThis.autoApplyContent.fill(values, attachments), args: [localAnswers, files]});
      summary.filled += filled.result.filled;
      summary.skipped += filled.result.skipped;
      summary.needs_input.push(...filled.result.needs_input);
      summary.needs_writing.push(...filled.result.needs_writing);
    }
    return await report({tabId: tab.id, url: tab.url, matched, summary});
  } catch (error) {
    return await report({matched, error: error.message});
  } finally { running = false; }
}

// Also provides a direct entry point for local service-worker integration tests.
globalThis.autoApplyAutofill = autofill;

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || !sender.url?.startsWith(chrome.runtime.getURL("")) || message.type !== "autofill") return;
  autofill().then(respond);
  return true;
});
chrome.commands.onCommand.addListener(command => { if (command === "autofill") autofill(); });
