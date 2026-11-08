import {api, DEFAULT_SERVER} from "./api.js";
import {createDashboardStarter} from './dashboard-launcher.js';

// Both settings surfaces use the same initialization, validation and save path.
export async function initializePairing(root = document, {launcher = true, onConnectionError = () => false} = {}) {
  const form = root.querySelector('#pairing'), server = root.querySelector('#server');
  const key = root.querySelector('#key'), status = root.querySelector('#status');
  const controls = form.querySelectorAll('input, button');
  try {
    const saved = await chrome.storage.local.get(['server', 'key']);
    server.value = saved.server || DEFAULT_SERVER;
    key.value = saved.key || '';
    let starter;
    if(launcher) {
      const connection = document.createElement('p');
      connection.setAttribute('role', 'status'); status.after(connection);
      starter = createDashboardStarter(connection);
    }
    async function save() {
      const url = new URL(server.value.trim());
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
        throw new Error('Use an HTTP or HTTPS server URL without credentials, query or fragment.');
      }
      if (!key.value.trim()) throw new Error('Enter your dashboard pairing key.');
      await chrome.storage.local.set({server:url.href.replace(/\/+$/, ''), key:key.value.trim()});
      return true;
    }
    async function perform(test) {
      if (!form.reportValidity()) return;
      controls.forEach(control => { control.disabled = true; });
      try {
        if (!await save()) return;
        status.textContent = test ? 'Connecting…' : 'Pairing saved.';
        if (test) { const result = await api('/ext/ping'); status.textContent = 'Connected — ' + (result.profile_name || result.name || 'AutoApply'); }
      } catch (error) { status.textContent = starter?.handlesConnectionError(error) || onConnectionError(error) ? '' : error.message; }
      finally { controls.forEach(control => { control.disabled = false; }); }
    }
    form.addEventListener('submit', event => { event.preventDefault(); perform(false); });
    root.querySelector('#test').addEventListener('click', () => perform(true));
    controls.forEach(control => { control.disabled = false; });
    return true;
  } catch { status.textContent = 'Could not load pairing. Reopen settings to retry.'; return false; }
}
