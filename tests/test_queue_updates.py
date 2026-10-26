"""Offline T074 checks: actual HTTP handler + Chromium, no listening server."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import time

import pytest

from test_dashboard import dashboard, http_dashboard, queue, request, offline_descriptions
from test_extension_api import ext, web


def test_revision_is_cheap_and_tracks_all_queue_mutations(http_dashboard, config, db, listing, monkeypatch):
    server, dashboard = http_dashboard
    app_id = queue(db, config, listing, 1)[0]
    def forbidden(*args, **kwargs):
        raise AssertionError('revision must not read lists, profiles or run discovery')
    for name in ('priority', 'manual', 'failed', 'applied'):
        monkeypatch.setattr(dashboard, name, forbidden)
    monkeypatch.setattr('autoapply.dashboard.discover', forbidden)
    monkeypatch.setattr('autoapply.dashboard.scan', forbidden)
    monkeypatch.setattr(config, 'profile_snapshot', forbidden)
    def revision():
        status, body, headers = request(server, 'GET', '/api/queue-revision')
        assert status == 200 and headers['Cache-Control'] == 'no-store'
        assert dashboard.token not in body['revision']
        return body['revision']
    before = revision()
    assert revision() == before
    for endpoint, payload in [('/api/applied', dict(application_id=app_id)),
                              ('/api/unapplied', dict(application_id=app_id)),
                              ('/api/not-eligible', dict(application_id=app_id, reason='Other', note='Synthetic fixture')),
                              ('/api/eligible', dict(application_id=app_id))]:
        assert web(http_dashboard, endpoint, payload)[0] == 200
        after = revision()
        assert after != before
        assert revision() == after
        before = after
    assert request(server, 'GET', '/api/queue-revision', {'Host': 'evil.test'})[0] == 403


@pytest.mark.browser
@pytest.mark.parametrize('batch', [False, True])
def test_confirmed_submission_updates_open_dashboard_and_rejects_stale_response(http_dashboard, config, db, listing, batch):
    from playwright.sync_api import sync_playwright, expect
    server, dashboard = http_dashboard
    ids = queue(db, config, listing, 32)
    payload = dict(application_id=ids[0], url='https://example.test/thanks', evidence='Thank you for applying')
    if batch:
        assert web(http_dashboard, '/api/ext-run', dict(application_ids=ids[:2]))[0] == 200
        claimed = ext(http_dashboard, 'GET', '/ext/run/next')[1]
        assert claimed['application_id'] == ids[0]
        payload['run_id'] = claimed['run_id']
    reads, pending, hold = Counter(), [], [False]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chromium', headless=True)
        try:
            page = browser.new_page(viewport=dict(width=1440, height=700))
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            def respond(route):
                incoming = route.request
                if not incoming.url.startswith('http://127.0.0.1:8765/'):
                    route.abort(); return
                path = incoming.url.removeprefix('http://127.0.0.1:8765')
                reads[path] += 1
                with ThreadPoolExecutor(max_workers=1) as pool:
                    result = pool.submit(request, server, incoming.method, path,
                                         incoming.all_headers(), incoming.post_data).result()
                if path == '/api/priority' and hold[0]:
                    hold[0] = False
                    pending.append((route, result)); return
                fulfill(route, result)
            def fulfill(route, result):
                status, body, headers = result
                route.fulfill(status=status, body=body if isinstance(body, (str, bytes)) else json.dumps(body), headers=headers)
            page.route('**/*', respond)
            page.goto('http://127.0.0.1:8765/#priority')
            row = page.locator(f'#rows [data-application-id="{ids[0]}"]')
            expect(row).to_be_visible()
            # Bootstrap reads can persist metadata/history once. Wait for its revision
            # refresh to finish, then measure a genuinely unchanged revision.
            page.wait_for_function('()=>Object.keys(loads).length===0 && !refreshBusy')
            page.evaluate('checkQueueRevision()')
            page.wait_for_function('()=>Object.keys(loads).length===0 && !revisionLoading')
            page.wait_for_timeout(1100)
            page.wait_for_function('()=>Object.keys(loads).length===0 && !revisionLoading')
            snapshot = {path: reads[path] for path in ['/api/priority','/api/manual','/api/failed','/api/applied']}
            page.wait_for_timeout(2200)
            assert reads['/api/queue-revision'] >= 2
            assert snapshot == {path: reads[path] for path in snapshot}
            search = page.get_by_role('searchbox', name='Filter Queue by company, role or ID')
            search.fill('Company')
            page.get_by_role('checkbox', name=f'Select #{ids[1]} for Chrome', exact=True).check()
            # Exercise a retained scroll position on a full page; pagination is independent.
            page.evaluate("document.querySelector('main').scrollTop=120")
            prior = page.evaluate('''()=>{const main=document.querySelector('main'),top=main.getBoundingClientRect().top;
              const anchor=[...document.querySelectorAll('#rows [data-application-id]')].find(e=>e.dataset.applicationId!=='%s' && e.getBoundingClientRect().top>=top);
              return {page:pages.priority,scroll:main.scrollTop,id:anchor.dataset.applicationId,top:anchor.getBoundingClientRect().top};}''' % ids[0])
            assert prior['scroll'] > 0
            hold[0] = True
            page.evaluate('void load()')
            page.wait_for_function('()=>!!loads.priority')
            assert pending, 'the real pre-submit HTTP result must be held'
            started = time.monotonic()
            assert ext(http_dashboard, 'POST', '/ext/applied', payload)[0] == 200
            expect(row).to_have_count(0, timeout=2100)
            assert time.monotonic()-started < 2.2
            expect(search).to_have_value('Company')
            assert page.evaluate('pages.priority') == prior['page']
            assert page.evaluate(f'chromeSelected.has({ids[1]})')
            # Native scroll anchoring adjusts scrollTop when a preceding row vanishes.
            # The same surviving visible row must remain at the same viewport position.
            anchor = page.locator(f'#rows [data-application-id="{prior["id"]}"]')
            assert abs(anchor.bounding_box()['y']-prior['top']) < 5
            assert page.evaluate("document.querySelector('main').scrollTop") > 0
            fulfill(*pending.pop())
            page.wait_for_timeout(1200)
            expect(row).to_have_count(0)
            assert page.evaluate(f'markedIds.has({ids[0]})')
            assert not errors
            # Cross-client undo and eligibility changes use the same notification path.
            assert ext(http_dashboard, 'POST', '/ext/unapplied', dict(application_id=ids[0]))[0] == 200
            expect(row).to_have_count(1, timeout=2100)
            assert web(http_dashboard, '/api/not-eligible', dict(application_id=ids[0], reason='Other', note='Synthetic fixture'))[0] == 200
            expect(row).to_have_count(0, timeout=2100)
            assert web(http_dashboard, '/api/eligible', dict(application_id=ids[0]))[0] == 200
            expect(row).to_have_count(1, timeout=2100)
            # Exercise non-default pagination across a cross-client mutation.
            page.locator('#pagination-priority-top input').fill('10')
            page.locator('#pagination-priority-top input').press('Tab')
            page.locator('#pagination-priority-top').get_by_role('button', name='Next page', exact=True).click()
            assert page.evaluate('pages.priority') == 2
            assert web(http_dashboard, '/api/applied', dict(application_id=ids[0]))[0] == 200
            page.wait_for_function('id=>markedIds.has(id)', arg=ids[0], timeout=2100)
            assert page.evaluate('pages.priority') == 2
            expect(search).to_have_value('Company')
            assert page.evaluate(f'chromeSelected.has({ids[1]})')
            page.locator('#pagination-priority-top').get_by_role('button', name='Previous page', exact=True).click()
            assert page.evaluate('pages.priority') == 1
        finally:
            browser.close()


# Reuse the actual unpacked MV3 fixture, with all HTTP served in memory.
from test_extension import loaded_extension
from test_repair_queue import capture
from autoapply.repair_queue import RepairQueue


@pytest.mark.browser
def test_actual_mv3_repair_controls_recovery_review_route_and_analysis_guard(http_dashboard, loaded_extension, tmp_path):
    from playwright.sync_api import expect
    server, dashboard = http_dashboard
    # Never target the running dashboard or inspect its runner state.
    server.server_port = 48765
    base = 'http://127.0.0.1:48765'
    dashboard.repairs.close()
    rows, decisions = [], {}
    repairs = RepairQueue(tmp_path / 'repairs', source=tmp_path / 'synthetic-source',
                          inspect=lambda _: (rows, decisions, None))
    dashboard.repairs = repairs
    repairs.configure(dict(enabled=True, paused=True))
    payload = capture()
    payload['diagnostic']['fields'] = []
    payload['diagnostic']['inspections'] = [dict(frame_id=0, status='unsupported_shadow_controls', ready_state='complete',
        roots=[dict(id=0, host='document', kind='document', exposed_controls=0),
               dict(id=1, host='custom-widget', kind='open_shadow', exposed_controls=2)], frames=[])]
    job = ext(http_dashboard, 'POST', '/ext/snapshot', payload)[1]['repair']
    assert job['status'] == 'queued'
    run = dict(id='d'*32, task_id=job['id'], attempt=1, task_state='awaiting_review',
               process_state='exited', pid=None, birth=None)
    context, worker, extension_id = loaded_extension
    hold, pending, calls = [False], [], Counter()
    errors = []

    def fulfill(route, result):
        status, body, headers = result
        route.fulfill(status=status, body=body if isinstance(body, (str, bytes)) else json.dumps(body), headers=headers)

    def respond(route):
        incoming = route.request
        if incoming.url.startswith('chrome-extension://'):
            route.continue_(); return
        if not incoming.url.startswith(base + '/'):
            route.abort(); return
        path = incoming.url.removeprefix(base)
        calls[(incoming.method, path)] += 1
        if path == '/synthetic.html':
            route.fulfill(content_type='text/html', body='<h1>Application</h1><form><label>Full name<input name="full-name"></label><input type="file" aria-label="Resume"><button type="submit">Submit application</button></form><script>window.submissions=0;document.querySelector("form").onsubmit=e=>{e.preventDefault();submissions++}</script>')
            return
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(request, server, incoming.method, path, incoming.all_headers(), incoming.post_data).result()
        if path == '/ext/repairs' and incoming.method == 'GET' and hold[0]:
            hold[0] = False; pending.append((route, result)); return
        fulfill(route, result)

    context.route('**/*', respond)
    worker.evaluate('v=>chrome.storage.local.set(v)', dict(server=base, key=dashboard.settings.extension_key(), autoSendStructure=False))
    page = context.new_page()
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(base + '/synthetic.html')
    tab_id = worker.evaluate('async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id', page.url)
    # Transport fixture: a captured repair notification for the synthetic page.
    # All status reads, controls and navigation use the real panel and backend.
    worker.evaluate('v=>chrome.storage.session.set({tabResults:{[v.tabId]:v}})',
                    dict(tabId=tab_id, url=page.url, fields=[], pending=[],
                         snapshotFeedback=dict(id='synthetic-repair-notice', sent=True, repair=job)))
    worker.evaluate('id=>autoApplySidebar(id,"open")', tab_id)
    panel = page.frame_locator('#autoapply-sidebar iframe')
    note = panel.locator('#analysis-note')
    panel.locator('#analysis-details summary').click()
    expect(note).to_contain_text('Use Resume repairs')
    panel.get_by_role('button', name='Resume repairs', exact=True).click()
    expect(note).to_contain_text('Automatic dispatch enabled')
    assert repairs.status()['enabled'] and not repairs.status()['paused']
    with repairs._db() as db:
        db.execute("UPDATE jobs SET status='awaiting_review',reason='awaiting_review',run_id=?", (run['id'],))
    rows.append(run)
    expect(note).to_contain_text('awaiting supervisor code review', timeout=7000)
    # A terminal status must keep polling and recover without another Analyze.
    run['task_state'] = 'needs_followup'
    decisions[run['id']] = 'needs_followup'
    with repairs._db() as db:
        db.execute("UPDATE jobs SET status='blocked',reason='blocked'")
    expect(note).to_contain_text('Supervisor review requested follow-up', timeout=7000)
    expect(panel.get_by_role('button', name='Analyze again', exact=True)).to_be_visible()
    assert calls[('POST', '/ext/snapshot')] == 0
    assert calls[('GET', '/ext/repairs')] >= 3
    with context.expect_page() as opened:
        panel.get_by_role('button', name='Repair settings / review', exact=True).click()
    review = opened.value
    review.wait_for_load_state()
    assert review.url == base + '/?section=repairs#settings', dict(url=review.url, calls=dict(calls), errors=errors)
    expect(review.locator('#settings-section-repairs')).to_be_visible()
    expect(review.locator('#settings-nav-repairs')).to_have_attribute('aria-current', 'true')
    assert calls[('GET', '/?section=repairs')] == 1
    review.close()
    page.bring_to_front()
    # Hold a real old status response, then start a fresh actual analysis.
    hold[0] = True
    deadline = time.monotonic() + 7
    while not pending and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    assert pending
    panel.get_by_role('button', name='Analyze again', exact=True).click()
    expect(panel.locator('#snapshot-status')).to_have_text('Analyzed', timeout=10000)
    summary = panel.locator('#analysis-summary').inner_text()
    expect(note).to_contain_text('No actionable scanner or fill defect was found')
    fresh_note = note.text_content()
    fulfill(*pending.pop())
    page.wait_for_timeout(300)
    expect(panel.locator('#analysis-summary')).to_have_text(summary)
    expect(note).to_have_text(fresh_note)
    assert 'Supervisor review requested follow-up' not in fresh_note
    assert calls[('POST', '/ext/snapshot')] == 1
    assert page.evaluate('submissions') == 0
    assert not errors
    assert repairs.thread is None and repairs.child is None
