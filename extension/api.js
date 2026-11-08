export const DEFAULT_SERVER = "http://127.0.0.1:8765";
const DASHBOARD_TRANSPORT_ERROR = 'Dashboard is not reachable. Check the server URL and that the dashboard is running.';
export const isDashboardTransportError = value => (typeof value === 'string' ? value : value?.message) === DASHBOARD_TRANSPORT_ERROR;

// Read-only, auth-aware health check; an unpaired dashboard can still be online.
export async function probeDashboard({server = DEFAULT_SERVER, key}, signal) {
  let url;
  try {
    url = new URL(server);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw Error();
  } catch { return {state:'config', message:'Check the dashboard server URL in Pairing settings.'}; }
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, {once:true});
  if(signal?.aborted)controller.abort();
  const timer = setTimeout(abort, 3000);
  try {
    const response = await fetch(url.href.replace(/\/+$/, '') + '/ext/ping', {
      headers:key?.trim() ? {'X-AutoApply-Key':key} : {}, signal:controller.signal, credentials:'omit', cache:'no-store', redirect:'manual'
    });
    if(response.type === 'opaqueredirect' || response.status >= 300 && response.status < 400)return {state:'config', message:'Dashboard redirected the connection check. Check the server URL in Pairing settings.'};
    if(response.status === 401 || response.status === 403) return {state:'pairing', message:key?.trim() ?
      'Pairing key rejected. Copy the current key from dashboard Settings.' : 'Not paired. Open Pairing settings and save your dashboard key.'};
    if(!response.ok)return {state:'busy', message:`Dashboard request failed (${response.status}). Try again shortly.`};
    const data = await response.json().catch(() => null);
    if(!data || data.ok !== true || data.name !== 'AutoApply')return {state:'busy', message:'Dashboard returned an unexpected response.'};
    if(!key?.trim())return {state:'pairing', message:'Not paired. Open Pairing settings and save your dashboard key.'};
    return {state:'ready', data};
  } catch { return {state:'offline', message:'Dashboard offline.'}; }
  finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
}

export async function api(path, payload) {
  const {server = DEFAULT_SERVER, key} = await chrome.storage.local.get(["server", "key"]);
  if (!key?.trim()) throw new Error("Not paired. Open Pairing settings and save your dashboard key.");
  const controller = new AbortController();
  const writing = ["/ext/write", "/ext/chat", "/ext/cover-letter"].includes(path);
  const timer = setTimeout(() => controller.abort(), writing ? 210000 : 15000);
  // Keep MV3 alive while a local AI request is pending.
  const heartbeat = setInterval(() => chrome.runtime.getPlatformInfo().catch(() => {}), 20000);
  try {
    const response = await fetch(server.replace(/\/+$/, "") + path, {
      method: payload === undefined ? "GET" : "POST",
      headers: {"X-AutoApply-Key": key, ...(payload === undefined ? {} : {"Content-Type": "application/json"})},
      ...(payload === undefined ? {} : {body: JSON.stringify(payload)}),
      signal: controller.signal,
      credentials: "omit"
    });
    const data = await response.json().catch(() => ({}));
    if (response.status === 401 || response.status === 403) throw new Error("Pairing key rejected. Copy the current key from dashboard Settings.");
    if (!response.ok) throw new Error(data.error || `Dashboard request failed (${response.status}).`);
    return data;
  } catch (error) {
    if (error.name === "AbortError" && writing) throw new Error("AI request timed out. Please retry.");
    if (error.name === "AbortError" || error instanceof TypeError) {
      throw new Error(DASHBOARD_TRANSPORT_ERROR);
    }
    throw error;
  } finally {
    clearTimeout(timer);
    clearInterval(heartbeat);
  }
}
