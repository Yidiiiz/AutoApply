"""Offline contracts run in Node; browser cases are for the root MV3 gate."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_launcher_contracts():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for extension module contracts')
    result = subprocess.run([node, str(ROOT / 'tests/fixtures/extension/dashboard-launcher/contracts.mjs'), str(ROOT)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


# Importing this fixture does not launch Chrome; only the marked cases use it.
from test_extension import loaded_extension


@pytest.mark.browser
def test_mv3_offline_explicit_start_and_recovery(loaded_extension):
    from playwright.sync_api import expect
    context, worker, extension_id = loaded_extension
    server = 'http://127.0.0.1:19765'
    worker.evaluate('server=>chrome.storage.local.set({server,key:"fixture-key"})', server)
    worker.evaluate('''() => {
      globalThis.launchCalls=[];
      chrome.runtime.sendNativeMessage=async (host, message)=>{
        launchCalls.push({host,message}); return {status:'starting'};
      };
    }''')
    state = {'online':False, 'status':200}
    def route(request):
        if not state['online']:
            request.abort('connectionrefused')
        else:
            request.fulfill(status=state['status'], content_type='application/json', body=json.dumps({'ok':True, 'name':'AutoApply', 'profile_name':'Fixture', 'profile_revision':'fixture'}))
    context.route(server + '/ext/ping', route)
    page = context.new_page()
    page.goto(f'chrome-extension://{extension_id}/sidepanel.html')
    start = page.locator('.dashboard-launcher button')
    expect(start).to_be_visible()
    expect(start).to_contain_text('Start dashboard')
    expect(page.locator('#connection')).to_have_text('Dashboard offline.')
    expect(page.locator('#error')).to_have_text('')
    assert worker.evaluate('launchCalls.length') == 0
    start.click()
    expect(start).to_be_disabled()
    expect(start).to_have_text('Starting dashboard…')
    assert worker.evaluate('launchCalls.length') == 1
    page.locator('#error').evaluate("e=>e.textContent='Dashboard is not reachable. Check the server URL and that the dashboard is running.'")
    state['online'] = True
    expect(start).to_be_hidden(timeout=12000)
    expect(page.locator('#connection')).to_have_text('')
    expect(page.locator('#error')).to_have_text('')
    # A stopped server is startable again in this same Chrome session, even
    # immediately after readiness; a durable JS timestamp must not block IPC.
    state['online'] = False
    expect(start).to_be_visible(timeout=20000)
    start.click()
    expect(start).to_be_disabled()
    assert worker.evaluate('launchCalls.length') == 2
    state['online'] = True
    expect(start).to_be_hidden(timeout=12000)
    state['status'] = 403
    # Storage changes trigger an immediate probe, without a native call.
    worker.evaluate('()=>chrome.storage.local.set({key:"rejected-fixture-key"})')
    expect(page.locator('#connection')).to_contain_text('Pairing key rejected')
    expect(start).to_be_hidden()
    assert worker.evaluate('launchCalls.length') == 2
    page.close()


@pytest.mark.browser
def test_mv3_missing_setup_and_web_sender_rejection(loaded_extension):
    from playwright.sync_api import expect
    context, worker, extension_id = loaded_extension
    server = 'http://127.0.0.1:19766'
    worker.evaluate('server=>chrome.storage.local.set({server,key:""})', server)
    worker.evaluate('''() => { globalThis.launchCalls=0;
      chrome.runtime.sendNativeMessage=async ()=>{launchCalls++; throw Error('host missing');}; }''')
    context.route(server + '/ext/ping', lambda route: route.abort('connectionrefused'))
    page = context.new_page()
    page.goto(f'chrome-extension://{extension_id}/options.html')
    start = page.locator('.dashboard-launcher button')
    expect(start).to_be_visible()
    start.click()
    expect(page.locator('.dashboard-launcher')).to_contain_text(f'install --extension-id {extension_id}')
    expect(start).to_be_enabled()
    # MV3 service workers forbid dynamic import. Extension pages allow it.
    rejected = page.evaluate('''async () => {
      const {dashboardStartHandler}=await import('./dashboard-launcher.js');
      return dashboardStartHandler()({type:'start-dashboard'}, {id:chrome.runtime.id,url:'https://fixture.test/'});
    }''')
    assert rejected['error'] == 'untrusted_sender'
    assert worker.evaluate('launchCalls') == 1
    worker.evaluate('()=>chrome.storage.local.set({server:"https://remote.test:8765"})')
    context.route('https://remote.test:8765/ext/ping', lambda route: route.abort('connectionrefused'))
    expect(start).to_be_hidden()
    assert worker.evaluate('launchCalls') == 1
    page.close()
