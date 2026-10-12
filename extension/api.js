export const DEFAULT_SERVER = "http://127.0.0.1:8765";

export async function api(path, payload) {
  const {server = DEFAULT_SERVER, key} = await chrome.storage.local.get(["server", "key"]);
  if (!key?.trim()) throw new Error("Not paired. Open Pairing settings and save your dashboard key.");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
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
    if (error.name === "AbortError" || error instanceof TypeError) {
      throw new Error("Dashboard is not reachable. Check the server URL and that the dashboard is running.");
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
