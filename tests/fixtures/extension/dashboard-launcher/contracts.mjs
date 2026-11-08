// Execute the shipped modules with synthetic Chrome/DOM/clock/fetch boundaries.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';

const root = process.argv[2];
const source = name => readFileSync(resolve(root, 'extension', name), 'utf8');
const moduleURL = text => 'data:text/javascript;base64,' + Buffer.from(text).toString('base64');
const apiURL = moduleURL(source('api.js'));
const api = await import(apiURL);
const mod = await import(moduleURL(source('dashboard-launcher.js').replace("'./api.js'", JSON.stringify(apiURL))));
const id = 'a'.repeat(32), base = `chrome-extension://${id}/`;
let local = {server:'http://127.0.0.1:8765', key:'fixture-key'}, session = {}, nativeCalls = [], nativeImpl;
const changes = new Set();
globalThis.chrome = {
  runtime:{id, getURL:path => base + path, sendNativeMessage:async (name, body) => {
    nativeCalls.push({name, body}); return nativeImpl ? nativeImpl() : {status:'starting'};
  }},
  storage:{local:{get:async () => ({...local})}, session:{get:async () => ({...session}), set:async value => Object.assign(session, value)},
    onChanged:{addListener:f => changes.add(f), removeListener:f => changes.delete(f)}}
};
const sender = {id, url:base+'sidepanel.html?tabId=42', origin:base.slice(0,-1)};
for(const server of ['https://127.0.0.1:8765', 'http://remote.test:8765', 'http://127.0.0.1:8765/path',
    'http://localhost:8765/?x=1', 'http://user@localhost:8765', 'http://localhost:8765/#x', 'http://localhost:80', 'file:///tmp/x']) {
  assert.throws(() => mod.localDashboardPort(server));
}
assert.equal(mod.localDashboardPort('http://localhost:9876/'), 9876);
assert.equal(mod.localDashboardPort(), 8765);
for(const rejected of [{...sender, url:'https://fixture.test/'}, {...sender, id:'other'},
    {...sender, url:base+'content.js'}, {...sender, origin:'https://fixture.test'}]) {
  assert.equal(mod.trustedLauncherSender(rejected), false);
  assert.equal((await mod.dashboardStartHandler()({type:'start-dashboard'}, rejected)).error, 'untrusted_sender');
}
assert.equal(nativeCalls.length, 0);
let release;
nativeImpl = () => new Promise(resolve => {release = resolve;});
let handler = mod.dashboardStartHandler();
const first = handler({type:'start-dashboard'}, sender), second = handler({type:'start-dashboard'}, sender);
assert.equal(first, second);
for(let i=0; i<10; i++)await Promise.resolve();
assert.equal(nativeCalls.length, 1);
release({status:'starting'});
await first;
nativeImpl = () => ({status:'starting'});
session.dashboardLaunch = {port:8765, at:Date.now()}; // lease left by the previous extension version
await mod.dashboardStartHandler()({type:'start-dashboard'}, sender); // new worker, same Chrome session
assert.equal(nativeCalls.length, 2); // native child liveness decides, not the old lease
nativeImpl = () => ({status:'already-running'});
assert.equal((await handler({type:'start-dashboard'}, sender)).status, 'already-running');
nativeImpl = () => ({status:'starting'}); // server stopped within the old 150-second lease
assert.equal((await handler({type:'start-dashboard'}, sender)).status, 'starting');
assert.equal(nativeCalls.length, 4); // same worker can explicitly restart immediately
session = {};
await mod.dashboardStartHandler()({type:'start-dashboard'}, sender); // fresh Chrome session
assert.equal(nativeCalls.length, 5);
assert.deepEqual(nativeCalls[0], {name:'com.autoapply.dashboard', body:{action:'start_dashboard', port:8765}});
session = {}; local.server = 'http://remote.test:8765';
assert.equal((await handler({type:'start-dashboard'}, sender)).error, 'local_url_required');
local.server = 'http://127.0.0.1:8765';
assert.equal((await handler({type:'start-dashboard', command:'bad'}, sender)).error, 'invalid_request');
nativeImpl = () => {throw Error('sensitive OS details');};
assert.equal((await handler({type:'start-dashboard'}, sender)).error, 'setup_required');

// Actual read-only probe distinguishes network, authentication, busy and config.
let fetches = [];
globalThis.fetch = async (url, options) => {fetches.push({url, options}); throw TypeError('offline');};
assert.deepEqual(await api.probeDashboard({server:local.server}), {state:'offline', message:'Dashboard offline.'});
assert.equal(fetches[0].options.credentials, 'omit');
assert.deepEqual(fetches[0].options.headers, {});
for(const code of [401, 403, 503]) {
  globalThis.fetch = async () => ({status:code, ok:false});
  assert.equal((await api.probeDashboard(local)).state, code === 503 ? 'busy' : 'pairing');
}
globalThis.fetch = async () => ({status:0, type:'opaqueredirect'});
assert.equal((await api.probeDashboard(local)).state, 'config');
globalThis.fetch = async () => ({status:200, ok:true, json:async () => ({})});
assert.equal((await api.probeDashboard(local)).state, 'busy');
globalThis.fetch = async () => ({status:200, ok:true, json:async () => ({ok:true, name:'AutoApply', profile_name:'Fixture'})});
assert.equal((await api.probeDashboard(local)).state, 'ready');
assert.equal((await api.probeDashboard({server:local.server})).state, 'pairing');
assert.equal((await api.probeDashboard({server:'garbage'})).state, 'config');

// Minimal DOM boundary tests the real controller, including time >120 seconds.
class Element {
  constructor(tag='p') {this.tag=tag; this.children=[]; this.textContent=''; this.hidden=false; this.listeners={}; this.classList={toggle:()=>{}};}
  after(...elements) {this.afterElements=elements;}
  append(...elements) {this.children.push(...elements);}
  setAttribute() {}
  addEventListener(name, fn) {this.listeners[name]=fn;}
  async click() {await this.listeners.click?.();}
}
const networkError = new Element(), validationError = new Element();
networkError.textContent = 'Dashboard is not reachable. Check the server URL and that the dashboard is running.';
const transportMessage = networkError.textContent;
validationError.textContent = 'Review the required field.';
for(const message of ['Pairing key rejected. Copy the current key from dashboard Settings.', 'Not paired. Open Pairing settings and save your dashboard key.',
    'Dashboard request failed (503).', 'AI request timed out. Please retry.', 'Check the dashboard server URL in Pairing settings.',
    'Dashboard is not reachable. Application validation failed.', validationError.textContent]) {
  assert.equal(api.isDashboardTransportError(message), false);
}
globalThis.document = {createElement:tag => new Element(tag), querySelectorAll:() => [networkError, validationError]};
globalThis.addEventListener = () => {};
let now=1000, nextTimer=1, timers=new Map();
Date.now = () => now;
globalThis.setTimeout = (fn, ms) => {const key=nextTimer++; timers.set(key, {fn, due:now+ms}); return key;};
globalThis.clearTimeout = key => timers.delete(key);
const drain = async () => {for(let i=0;i<30;i++)await Promise.resolve();};
async function advance(ms) {
  const end = now+ms;
  while(true) {
    const next = [...timers].sort((a,b)=>a[1].due-b[1].due)[0];
    if(!next || next[1].due > end)break;
    now=next[1].due; timers.delete(next[0]); next[1].fn(); await drain();
  }
  now=end; await drain();
}
let state='offline', probes=0, uiCalls=0, ready=0, pendingNative;
const probe = async () => {probes++; return {state, message:state==='offline' ? 'Dashboard offline.' : 'Pairing required.', data:{profile_name:'Fixture'}};};
chrome.runtime.sendMessage = async message => {assert.deepEqual(message, {type:'start-dashboard'}); uiCalls++; return new Promise(resolve => {pendingNative=resolve;});};
const status = new Element();
const ui = mod.createDashboardStarter(status, {probe, onReady:()=>ready++});
await drain();
const [button, detail] = ui.row.children;
assert.equal(uiCalls, 0); assert.equal(button.hidden, false);
assert.equal(status.textContent, 'Dashboard offline.');
assert.equal(networkError.textContent, ''); // widget owns the duplicate during offline, too
assert.equal(ui.handlesConnectionError(new Error(transportMessage)), true);
assert.equal(ui.handlesConnectionError('Pairing key rejected.'), false);
const click = button.click(); await drain(); await button.click();
assert.equal(uiCalls, 1); assert.equal(button.disabled, true);
pendingNative({status:'starting'}); await click;
await advance(125000);
assert.equal(button.disabled, true); assert.match(status.textContent, /Starting dashboard/);
assert.ok(probes > 35); assert.equal(uiCalls, 1);
state='ready'; await advance(3000);
assert.equal(button.hidden, true); assert.equal(status.textContent, ''); assert.ok(ready);
assert.equal(networkError.textContent, ''); assert.equal(validationError.textContent, 'Review the required field.');
assert.equal(uiCalls, 1); // no autofill, writing or submit replay
state='offline'; await ui.refresh();
const restart=button.click(); await drain();
assert.equal(uiCalls, 2); assert.equal(button.disabled, true);
pendingNative({status:'starting'}); await restart;
state='ready'; await ui.refresh(); assert.equal(button.hidden, true);
state='pairing'; await ui.refresh(); assert.equal(button.hidden, true);
state='busy'; await ui.refresh(); assert.equal(button.hidden, true);
state='offline'; await ui.refresh();
chrome.runtime.sendMessage = async () => ({status:'starting'});
await button.click(); await advance(151000);
assert.equal(button.disabled, false); assert.match(detail.textContent, /still offline/);
chrome.runtime.sendMessage = async () => ({status:'error', error:'setup_required'});
await button.click(); assert.match(detail.textContent, new RegExp(`install --extension-id ${id}`));
assert.equal(button.disabled, false);
local.server='http://remote.test:8765'; await ui.refresh(); assert.equal(button.hidden, true);
assert.match(detail.textContent, /only a local HTTP/);
local.server='http://127.0.0.1:8765'; await ui.refresh();
chrome.runtime.sendMessage = async () => new Promise(resolve => {pendingNative=resolve;});
const staleClick=button.click(); await drain();
local.server='http://localhost:9999';
for(const fn of changes)fn({server:{newValue:local.server}}, 'local');
await drain(); pendingNative({status:'error', error:'setup_required'}); await staleClick;
assert.equal(detail.hidden, true); // old launch cannot overwrite new target
const count=probes; ui.stop(); await advance(200000); assert.equal(probes, count);
assert.equal(ui.handlesConnectionError(transportMessage), false);

// A late response from an aborted/changed target never replaces the new result.
let oldProbe;
const status2=new Element();
const late=mod.createDashboardStarter(status2, {probe:() => new Promise(resolve => {oldProbe=resolve;})});
await drain(); const resolveOld=oldProbe;
// Replace the probe through its next call, then complete the newer one first.
const refreshing=late.refresh(); await drain();
oldProbe({state:'ready', data:{name:'New'}}); await refreshing;
resolveOld({state:'offline', message:'stale offline'}); await drain();
assert.equal(status2.textContent, ''); late.stop();
console.log('Launcher contracts passed: strict capability, single flight, auth-aware health, slow readiness and stale-state recovery.');
