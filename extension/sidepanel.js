import {api} from "./api.js";

const element = id => document.getElementById(id);
async function connection() {
  try {
    const result = await api("/ext/ping");
    element("connection").textContent = `Connected — ${result.profile_name || result.name}`;
  } catch (error) { element("connection").textContent = error.message; }
}
function list(id, values) {
  element(id).replaceChildren(...(values.length ? values : ["None"]).map(value => {
    const item = document.createElement("li"); item.textContent = value; return item;
  }));
}
function render(result) {
  if (!result) return;
  element("error").textContent = result.error || "";
  element("company").textContent = result.matched?.company || "Unmatched application";
  element("job").textContent = result.matched?.title || result.url || "";
  element("results").hidden = !result.summary;
  if (!result.summary) return;
  element("filled").textContent = `Filled ${result.summary.filled}`;
  element("skipped").textContent = `Skipped ${result.summary.skipped} (including existing answers)`;
  list("needs-input", result.summary.needs_input);
  list("needs-writing", result.summary.needs_writing);
}
element("settings").addEventListener("click", () => chrome.runtime.openOptionsPage());
element("autofill").addEventListener("click", async () => {
  element("autofill").disabled = true;
  element("error").textContent = "";
  try { render(await chrome.runtime.sendMessage({type: "autofill"})); }
  catch (error) { element("error").textContent = error.message; }
  finally { element("autofill").disabled = false; }
});
chrome.runtime.onMessage.addListener(message => { if (message.type === "autofill-result") render(message.result); });
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && changes.lastResult) render(changes.lastResult.newValue);
  if (area === "local") connection();
});
render((await chrome.storage.session.get("lastResult")).lastResult);
connection();
