import {DEFAULT_SERVER, isDashboardTransportError, probeDashboard} from './api.js';

export const NATIVE_HOST = 'com.autoapply.dashboard';
export function localDashboardPort(server = DEFAULT_SERVER) {
  const url = new URL(server);
  const port = Number(url.port || 80);
  if(url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname) ||
      url.username || url.password || url.pathname !== '/' || url.search || url.hash ||
      !Number.isInteger(port) || port < 1024 || port > 65535) {
    throw Error('Start dashboard requires an HTTP localhost or 127.0.0.1 URL with a port from 1024 to 65535 and no path.');
  }
  return port;
}

export function trustedLauncherSender(sender) {
  if(sender.id !== chrome.runtime.id)return false;
  try {
    const url = new URL(sender.url);
    return ['sidepanel.html', 'options.html'].some(page => url.href.split(/[?#]/)[0] === chrome.runtime.getURL(page)) &&
      (!sender.origin || sender.origin === chrome.runtime.getURL('').replace(/\/$/, ''));
  } catch {return false;}
}

// One flight for every sidebar in this service worker. Across worker/browser
// restarts, the native host checks its child's liveness and the dashboard lock.
export function dashboardStartHandler() {
  let flight;
  return (message, sender) => {
    if(!trustedLauncherSender(sender))return Promise.resolve({status:'error', error:'untrusted_sender'});
    if(!message || message.type !== 'start-dashboard' || Object.keys(message).some(key => key !== 'type'))return Promise.resolve({status:'error', error:'invalid_request'});
    if(flight)return flight;
    flight = (async () => {
      const {server = DEFAULT_SERVER} = await chrome.storage.local.get('server');
      let port;
      try {port = localDashboardPort(server);} catch {return {status:'error', error:'local_url_required'};}
      try {
        const response = await chrome.runtime.sendNativeMessage(NATIVE_HOST, {action:'start_dashboard', port});
        if(['starting', 'already-running'].includes(response?.status)) {
          return {status:response.status};
        }
        const error = ['venv_missing', 'start_failed', 'invalid_request'].includes(response?.error) ? response.error : 'start_failed';
        return {status:'error', error};
      } catch {return {status:'error', error:'setup_required'};}
    })().finally(() => {flight = undefined;});
    return flight;
  };
}

const launchErrors = {
  local_url_required:'Start dashboard supports only a local HTTP dashboard URL with a port from 1024 to 65535 and no path. Check Pairing settings.',
  venv_missing:'The local .venv Python is missing. Create the project virtual environment, then reinstall the dashboard launcher.',
  start_failed:'Dashboard could not start. Check data/private/launcher/dashboard.log, then retry.',
  invalid_request:'The launcher rejected this request. Reinstall the dashboard launcher.',
  untrusted_sender:'Open Start dashboard from AutoApply settings or the sidebar.'
};

export function createDashboardStarter(status, {onReady = () => {}, onState = () => {}, probe = probeDashboard} = {}) {
  const row = document.createElement('div');
  row.className = 'dashboard-launcher';
  status.after(row);
  const start = document.createElement('button');
  start.type = 'button'; start.textContent = '☾ Start dashboard'; start.hidden = true;
  row.append(start);
  const detail = document.createElement('p');
  detail.className = 'muted'; detail.hidden = true; detail.setAttribute('role', 'status'); row.append(detail);
  let active = true, generation = 0, targetVersion = 0, timer, controller, starting = false, startedAt = 0, launchError = '', currentState;
  function cancel() {generation++; clearTimeout(timer); controller?.abort();}
  function clearNetworkErrors() {
    for(const node of document.querySelectorAll('#error, #ai-status, #chat-status, #status')) {
      if(isDashboardTransportError(node.textContent))node.textContent = '';
    }
  }
  async function refresh() {
    cancel();
    if(!active)return;
    const version = generation;
    controller = new AbortController();
    const saved = await chrome.storage.local.get(['server', 'key']);
    if(version !== generation)return;
    const value = await probe(saved, controller.signal);
    if(version !== generation || !active)return;
    currentState = value.state;
    const online = value.state !== 'offline';
    if(online) {starting = false; launchError = '';}
    if(starting && Date.now() - startedAt >= 150000) {
      starting = false; launchError = 'Dashboard is still offline. Check data/private/launcher/dashboard.log or retry Start dashboard. Connection checks will continue.';
    }
    let local = true;
    try {localDashboardPort(saved.server);} catch {local = false;}
    status.textContent = value.state === 'ready' ? '' : starting ? 'Starting dashboard… Checking connection.' : value.message;
    status.classList.toggle('connected', value.state === 'ready');
    status.title = value.state === 'ready' ? `Connected — ${value.data.profile_name || value.data.name || 'AutoApply'}` : status.textContent;
    status.setAttribute('aria-label', status.title);
    start.hidden = online || !local;
    start.disabled = starting;
    start.textContent = starting ? 'Starting dashboard…' : '☾ Start dashboard';
    detail.textContent = !online && !local ? launchErrors.local_url_required : launchError;
    detail.hidden = !detail.textContent;
    onState(value);
    clearNetworkErrors();
    if(value.state === 'ready')onReady(value.data);
    timer = setTimeout(refresh, value.state === 'ready' ? 15000 : 3000);
  }
  start.addEventListener('click', async () => {
    if(starting || currentState !== 'offline')return;
    starting = true; startedAt = Date.now(); launchError = ''; start.disabled = true; start.textContent = 'Starting dashboard…';
    status.textContent = 'Starting dashboard… Checking connection.';
    const target = targetVersion;
    let response;
    try {response = await chrome.runtime.sendMessage({type:'start-dashboard'});} catch {response = {error:'setup_required'};}
    // A probe may have completed while IPC was pending. Never replace healthy UI.
    if(!active || target !== targetVersion || currentState !== 'offline')return;
    if(response?.status !== 'starting' && response?.status !== 'already-running') {
      starting = false;
      launchError = response?.error === 'setup_required' ?
        `One-time Windows setup: from the repository run .\\.venv\\Scripts\\python.exe -m autoapply.dashboard_launcher install --extension-id ${chrome.runtime.id}, then reload the extension.` :
        launchErrors[response?.error] || launchErrors.start_failed;
    }
    // Refresh always reads current settings; no old URL can restore stale state.
    await refresh();
  });
  const changed = (changes, area) => {
    if(area === 'local' && (changes.server || changes.key)) {targetVersion++; starting = false; launchError = ''; refresh();}
  };
  chrome.storage.onChanged.addListener(changed);
  const stop = () => {active = false; cancel(); chrome.storage.onChanged.removeListener(changed);};
  globalThis.addEventListener('pagehide', stop, {once:true});
  refresh();
  return {refresh, stop, row, handlesConnectionError:message => active && isDashboardTransportError(message),
    setActive(value) {active = value; if(value)refresh(); else cancel();}};
}
