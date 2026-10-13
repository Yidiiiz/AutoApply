import {api} from "./api.js";

const element = id => document.getElementById(id);
let result, source, histories = (await chrome.storage.session.get("answerHistory")).answerHistory || {};
let messages = [], aiBusy = false, chatBusy = false, downloadURL;
const send = message => chrome.runtime.sendMessage(message);
const question = () => element("question").value.trim();
const history = () => histories[question()] || {versions: [], index: 0};
function node(tag, text) { const item = document.createElement(tag); item.textContent = text; return item; }
function button(text, action) { const item = node("button", text); item.type = "button"; item.onclick = action; return item; }
async function connection() {
  try { const value = await api("/ext/ping"); element("connection").textContent = `Connected — ${value.profile_name || value.name}`; }
  catch (error) { element("connection").textContent = error.message; }
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
function prompts() {
  element("needs-input").replaceChildren();
  for (const field of result?.pending || []) {
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
      row.append(actions, status);
    }
    actions.append(button("Skip", () => perform({type: "skip-answer", key: field.key}).catch(showError)));
    if (!actions.parentElement) row.append(actions);
    element("needs-input").append(row);
  }
  if (!result?.pending?.length) element("needs-input").append(node("p", "None"));
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
function render(value) {
  if (!value) return;
  result = value;
  element("error").textContent = value.error || "";
  element("company").textContent = value.matched?.company || value.page?.company || "Current application";
  element("job").textContent = value.matched?.title || value.page?.title || value.url || "";
  element("progress").textContent = value.progress || "";
  element("autofill").disabled = element("cover-letter").disabled = Boolean(value.busy);
  element("results").hidden = !value.summary;
  if (value.summary) {
    element("filled").textContent = `Filled ${value.summary.filled}`;
    element("skipped").textContent = `Skipped ${value.summary.skipped} (including existing answers)`;
    element("upload-errors").textContent = value.summary.errors.join("\n");
  }
  prompts();
  element("needs-writing").replaceChildren(...(value.writing || []).filter(field => field.kind !== "file").map(field => {
    const item = node("li", ""); item.append(button(field.label, () => {
      source = {...field, tabId: value.tabId}; element("question").value = field.label;
      element("ask-section").open = true; renderAnswer(); element("question").focus();
    })); return item;
  }));
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
  return {page: result.page, application_id: result.matched?.application_id};
}
function renderAnswer() {
  const current = history(), answer = current.versions[current.index];
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
  if (aiBusy || !question()) return;
  const key = question(), current = history(), previous = current.versions[current.index];
  aiBusy = true; renderAnswer(); element("ai-status").textContent = "Writing... This can take up to 3 minutes.";
  try {
    const answer = await api("/ext/write", {...await pageContext(), question: key,
      ...(source?.label === key && source.max_length !== null ? {max_length: source.max_length} : {}),
      instruction: refine ? element("refinement").value : source?.label === key && source.max_length !== null ? `Use at most ${source.max_length} characters.` : "",
      previous: refine ? previous.answer : ""});
    current.versions.push(answer); current.index = current.versions.length - 1; histories[key] = current;
    await chrome.storage.session.set({answerHistory: histories}); element("ai-status").textContent = "Ready.";
  } catch (error) { element("ai-status").textContent = error.message; }
  finally { aiBusy = false; renderAnswer(); }
}
function renderMessages() {
  element("messages").replaceChildren(...messages.map(message => node("p", `${message.role === "user" ? "You" : "AI"}: ${message.text}`)));
}
async function chat() {
  const text = element("chat-input").value.trim();
  if (!text || chatBusy) return;
  chatBusy = true; element("send").disabled = element("clear-chat").disabled = true;
  messages.push({role: "user", text}); element("chat-input").value = ""; renderMessages();
  element("chat-status").textContent = "Thinking... This can take up to 3 minutes.";
  try {
    const reply = await api("/ext/chat", {...await pageContext(), messages: messages.slice(-12)});
    messages.push({role: "assistant", text: reply.reply}); renderMessages(); element("chat-status").textContent = "";
  } catch (error) { messages.pop(); element("chat-input").value = text; renderMessages(); element("chat-status").textContent = error.message; }
  finally { chatBusy = false; element("send").disabled = element("clear-chat").disabled = false; }
}
element("settings").onclick = () => chrome.runtime.openOptionsPage();
element("autofill").onclick = () => perform({type: "autofill"}).catch(showError);
element("cover-letter").onclick = () => perform({type: "cover-letter"}).catch(showError);
element("generate").onclick = () => write(false);
element("refine").onclick = () => write(true);
element("question").oninput = renderAnswer;
for (const [id, delta] of [["previous", -1], ["next", 1]]) element(id).onclick = async () => {
  history().index += delta; renderAnswer(); await chrome.storage.session.set({answerHistory: histories});
};
element("copy").onclick = () => navigator.clipboard.writeText(element("answer").value).then(() => { element("ai-status").textContent = "Copied."; }, showError);
element("fill-answer").onclick = () => perform({type: "fill-answer", key: source.key, value: element("answer").value}).catch(showError);
element("send").onclick = chat;
element("chat-input").onkeydown = event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); chat(); } };
element("clear-chat").onclick = () => { messages = []; renderMessages(); element("chat-status").textContent = ""; };
chrome.runtime.onMessage.addListener(message => { if (message.type === "autofill-result") render(message.result); });
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && changes.lastResult) render(changes.lastResult.newValue);
  if (area === "local") connection();
});
render((await chrome.storage.session.get("lastResult")).lastResult); renderAnswer(); connection();

let runPaused = false;
function renderRun(run = {}) {
  runPaused = Boolean(run.paused);
  element("chrome-run-status").textContent = `Run: ${run.index || 0}/${run.total || 0}${runPaused ? " (paused)" : run.active ? "" : " (idle)"}${run.error ? " · " + run.error : ""}`;
  element("run-pause").textContent = runPaused ? "Resume run" : "Pause run";
}
element("run-pause").onclick = async () => {
  try { renderRun(await send({type: "run-pause", paused: !runPaused})); } catch (error) { showError(error); }
};
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && changes.chromeRun) renderRun(changes.chromeRun.newValue);
});
renderRun((await chrome.storage.session.get("chromeRun")).chromeRun);
send({type: "run-status-refresh"}).then(renderRun).catch(showError);
