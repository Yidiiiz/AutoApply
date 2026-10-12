import {api, DEFAULT_SERVER} from "./api.js";

const serverInput = document.querySelector("#server");
const keyInput = document.querySelector("#key");
const status = document.querySelector("#status");
const saved = await chrome.storage.local.get(["server", "key"]);
serverInput.value = saved.server || DEFAULT_SERVER;
keyInput.value = saved.key || "";

async function save() {
  if (!document.querySelector("#pairing").reportValidity()) return false;
  const url = new URL(serverInput.value.trim());
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error("Use an HTTP or HTTPS server URL without credentials, query or fragment.");
  }
  if (!keyInput.value.trim()) throw new Error("Enter your dashboard pairing key.");
  await chrome.storage.local.set({server: url.href.replace(/\/+$/, ""), key: keyInput.value.trim()});
  return true;
}

document.querySelector("#pairing").addEventListener("submit", async event => {
  event.preventDefault();
  try { if (await save()) status.textContent = "Pairing saved."; }
  catch (error) { status.textContent = error.message; }
});
document.querySelector("#test").addEventListener("click", async () => {
  try {
    if (!await save()) return;
    status.textContent = "Connecting…";
    const result = await api("/ext/ping");
    status.textContent = `Connected — ${result.profile_name || result.name || "AutoApply"}`;
  } catch (error) { status.textContent = error.message; }
});
