"""T096 offline ordering gates and a root-runnable rendered dashboard case."""
import ast
import json
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from autoapply.dashboard_ui import PAGE


def queue_rows():
    companies = ['Acme Co', 'Beta', 'Acme Inc', '', ' acME   CO ', 'Gamma',
                 '   ', 'BETA', 'Acme.Co', 'Delta', 'Acme Co', 'X', 'Beta', 'Y']
    return [dict(application_id=i, company=company, title=f'Fixture role {i}',
                 url=f'http://127.0.0.1:8764/fixture-job/{i}', ats='Lever',
                 posted_at=f'2026-10-{i:02d}', description=f'Synthetic job {i}')
            for i, company in enumerate(companies, 1)]


GROUPED = [1, 5, 11, 2, 8, 13, 3, 4, 6, 7, 9, 10, 12, 14]


def test_grouping_before_pagination_selection_and_run_order_node(tmp_path):
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Node is unavailable')
    # Reuse the dashboard's existing DOM harness, executing current production JS.
    tree = ast.parse(Path(__file__).with_name('test_dashboard.py').read_text(encoding='utf-8'))
    fn = next(f for f in tree.body if isinstance(f, ast.FunctionDef)
              and f.name == 'test_javascript_display_formatting_and_renderers')
    assignment = next(n for n in fn.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'harness' for t in n.targets))
    harness = ast.literal_eval(assignment.value)
    script = PAGE.split('<script nonce="__TOKEN__">', 1)[1].split('</script>', 1)[0]
    script = script[:script.index("document.getElementById('tab-settings').addEventListener")]
    checks = r"""
(async()=>{
const ids=items=>items.map(a=>a.application_id),original=JSON.stringify(sample);
perPage=10;render({rows:sample,sweep:{}});
assert.deepEqual(ids(sortedItems('priority',rankedItems('priority'))),expected);
assert.equal(JSON.stringify(sample),original,'Grouping must not mutate models');
const body=document.getElementById('rows'),footer=document.getElementById('pagination-priority');
const displayed=()=>body.children.slice(1).map(row=>row.dataset.applicationId);
assert.deepEqual(displayed(),expected.slice(0,10));
footer.children[2].click();assert.deepEqual(displayed(),expected.slice(10));
const selection=document.getElementById('chrome-selection-priority');
selection.children[0].children[0].value='4';selection.children[1].click();
assert.deepEqual([...chromeSelected],expected.slice(0,4),'Top N starts before pagination');
let posted;post=async(action,body)=>{posted={action,body};return {items:[]};};
chromeSelected.clear();for(const id of [13,11,5,2,1])chromeSelected.add(id);
await startChromeRun();assert.deepEqual(posted.body.application_ids,[1,5,11,2,13]);
sorts.priority={key:'rank',direction:'descending'};draw('priority');
assert.deepEqual(ids(chromeCandidates('priority')),sample.map(a=>a.application_id).reverse());
selection.children[1].click();assert.deepEqual([...chromeSelected],[14,13,12,11]);
sorts.priority={key:'posted_at',direction:'ascending'};draw('priority');
assert.deepEqual(ids(chromeCandidates('priority')),sample.map(a=>a.application_id));
await startChromeRun();assert.deepEqual(posted.body.application_ids,[11,12,13,14]);
sorts.priority={key:'rank',direction:'ascending'};
filters.priority='Acme';assert.deepEqual(ids(chromeCandidates('priority')),[1,5,11,3,9]);
filters.priority='';sample[0].url='';render({rows:sample,sweep:{}});
assert.deepEqual(ids(chromeCandidates('priority')),expected.filter(id=>id!==1),
 'A non-openable company anchor cannot reorder remaining candidates');
// Empty names remain separate; punctuation and corporate suffixes remain significant.
assert.deepEqual(ids(companyGroups(sample)),expected);
sorts['needs-you']={key:'company',direction:'ascending'};
assert.equal(sortedItems('needs-you',sample).length,sample.length);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    path = tmp_path/'queue-grouping.js'
    path.write_text(harness + script + '\nconst sample=' + json.dumps(queue_rows())
                    + ';const expected=' + json.dumps(GROUPED) + ';\n' + checks, encoding='utf-8')
    result = subprocess.run([executable, str(path)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.browser
@pytest.mark.parametrize('theme', ['dark', 'light'])
def test_rendered_queue_grouping_paging_topn_explicit_sort_and_individual_actions(theme):
    """Root gate: actual dashboard JS and DOM; every request is fixture-fulfilled."""
    rows, posts, unexpected = queue_rows(), [], []
    initial = dict(rows=rows, sweep={})
    html = (PAGE.replace('__TOKEN__', 'fixture').replace('__BATCH_SIZE__', '5')
            .replace('__QUEUE_REVISION__', 'fixture').replace('__INITIAL__', json.dumps(initial)))

    def fixture(route):
        request = route.request
        path = urlsplit(request.url).path
        if path == '/' and request.method == 'GET':
            route.fulfill(body=html, content_type='text/html')
        elif path.startswith('/fixture-job/') and request.method == 'GET':
            route.fulfill(body='<h1>Fixture job</h1>', content_type='text/html')
        elif request.method == 'GET' and path in responses:
            route.fulfill(json=responses[path])
        elif request.method == 'POST' and path == '/api/ext-run':
            posts.append(request.post_data_json)
            route.fulfill(json={'items': []})
        else:
            unexpected.append((request.method, request.url))
            route.abort()

    responses = {'/api/priority': initial, '/api/status': {'applications': []},
                 '/api/manual': {'applications': []}, '/api/failed': {'applications': []},
                 '/api/applied': {'applications': [], 'not_eligible': []},
                 '/api/ext-run': {'items': []}, '/api/queue-revision': {'revision': 'fixture'}}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chromium', headless=True,
                                             args=['--disable-background-networking'])
        try:
            context = browser.new_context(color_scheme=theme, viewport={'width': 1280, 'height': 720})
            context.route('**/*', fixture)
            context.add_init_script("localStorage.setItem('autoapply.perPage','10')")
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://127.0.0.1:8764/')
            page.locator('#tab-priority').click()
            expect(page.locator('#refresh')).to_be_enabled()
            items = page.locator('#rows .item')

            def displayed():
                return items.evaluate_all('rows=>rows.map(row=>Number(row.dataset.applicationId))')

            assert displayed() == GROUPED[:10]
            for view in ('grid', 'list'):
                page.locator(f'[data-view="{view}"]').click()
                assert displayed() == GROUPED[:10]
            page.locator('#pagination-priority-top').get_by_role('button', name='Next page', exact=True).click()
            assert displayed() == GROUPED[10:]
            top = page.get_by_role('spinbutton', name='Select top N in Queue', exact=True)
            top.fill('4')
            page.get_by_role('button', name='Select top N in Queue', exact=True).click()
            page.locator('#chrome-run-priority').click()
            expect(page.locator('#chrome-run-priority')).to_be_enabled()
            assert posts[-1] == {'application_ids': GROUPED[:4]}
            page.locator('#pagination-priority-top').get_by_role('button', name='Previous page', exact=True).click()
            # Each sibling job keeps its own checkbox and links, with no group collapse.
            page.get_by_role('checkbox', name='Select #5 for Chrome', exact=True).uncheck()
            expect(page.get_by_role('checkbox', name='Select #1 for Chrome', exact=True)).to_be_checked()
            expect(page.get_by_role('checkbox', name='Select #11 for Chrome', exact=True)).to_be_checked()
            row = page.locator('#rows .item[data-application-id="5"]')
            for target in (row.locator('.role a').first, row.locator('h3')):
                with context.expect_page() as opened:
                    target.click()
                popup = opened.value
                popup.wait_for_url('**/fixture-job/5')
                popup.close()
            page.locator('#rows .list-head').get_by_role('button', name='Rank', exact=False).click()
            assert displayed() == list(range(14, 4, -1))
            page.get_by_role('button', name='Select top N in Queue', exact=True).click()
            page.locator('#chrome-run-priority').click()
            expect(page.locator('#chrome-run-priority')).to_be_enabled()
            assert posts[-1] == {'application_ids': [14, 13, 12, 11]}
            page.locator('#rows .list-head').get_by_role('button', name='Posted', exact=True).click()
            assert displayed() == list(range(1, 11))
            page.reload()
            expect(page.locator('#refresh')).to_be_enabled()
            assert displayed() == list(range(1, 11)), 'Explicit sort survives reload'
            assert not errors and not unexpected
        finally:
            browser.close()
