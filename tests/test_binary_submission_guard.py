import gzip
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from autoapply.batch_approval import is_final_submission
from autoapply.fill_batch import FillOnlyInvariant


class BinaryRequest:
    method = 'POST'
    url = 'https://example.test/telemetry'

    def __init__(self, body):
        self.post_data_buffer = body

    @property
    def post_data(self):
        # Playwright's text property raises exactly this error for binary bodies.
        return self.post_data_buffer.decode('utf-8')


@pytest.mark.parametrize('body', [gzip.compress(b'{"action":"observe"}'), b'\x00\xffbinary-document'], ids=['gzip-telemetry', 'binary-document'])
async def test_binary_nonfinal_route_and_observer_do_not_decode_text(body):
    guard = FillOnlyInvariant.__new__(FillOnlyInvariant)
    guard.submission_requests_blocked = guard.employer_submission_requests = 0
    request = BinaryRequest(body)
    route = SimpleNamespace(request=request, abort=AsyncMock(), fallback=AsyncMock())
    await guard.route(route)
    guard.observed_request(request)
    route.fallback.assert_awaited_once()
    route.abort.assert_not_awaited()
    assert guard.submission_requests_blocked == guard.employer_submission_requests == 0


@pytest.mark.parametrize('body', [
    gzip.compress(b'{"operationName":"SubmitApplication"}'),
    gzip.compress(b'{"action":"finalizeApplication"}'),
    b'\xff\x00{"action":"submitApplication"}',
], ids=['gzip-operation', 'gzip-action', 'binary-action'])
async def test_binary_final_payload_remains_blocked(body):
    guard = FillOnlyInvariant.__new__(FillOnlyInvariant)
    guard.submission_requests_blocked = guard.employer_submission_requests = 0
    request = BinaryRequest(body)
    route = SimpleNamespace(request=request, abort=AsyncMock(), fallback=AsyncMock())
    await guard.route(route)
    route.abort.assert_awaited_once_with('blockedbyclient')
    route.fallback.assert_not_awaited()
    assert guard.submission_requests_blocked == 1


def test_binary_body_cannot_hide_final_url_or_trigger_get_submission():
    body = b'\xff\x00binary'
    assert is_final_submission('https://example.test/submitapplication', 'POST', body)
    assert not is_final_submission('https://example.test/submitapplication', 'GET', body)


@pytest.mark.parametrize('body', [b'\x1f\x8bnot-a-valid-gzip', gzip.compress(b'x' * (2 * 1024 * 1024 + 1))], ids=['malformed', 'oversized'])
def test_uninspectable_compressed_mutation_fails_closed(body):
    assert is_final_submission('https://example.test/graphql', 'POST', body)


async def test_real_browser_binary_telemetry_keeps_checkpoint_healthy(config, db, listing):
    from autoapply.engine import Engine
    from autoapply.fill_batch import checkpoint

    db.set_setting('auto_submit', False)
    app_id, _ = db.ingest(replace(listing, url='http://127.0.0.1:8123/binary'), config)
    engine = Engine(config, db, fill_only=True)
    requests = []
    form = '<form><label>First Name<input name="first_name" required></label><label>Resume<input name="resume" type="file" required></label><button type="submit">Submit application</button></form>'
    try:
        await engine.browser.start()
        async def fixture(route):
            if route.request.method == 'POST':
                requests.append(route.request.url)
                await route.fulfill(body='ok')
            else:
                await route.fulfill(content_type='text/html', body=form)
        await engine.browser.context.route('**/*', fixture)
        await engine.browser.context.route('**/*', engine.fill_invariant.route)
        await engine.process_one(app_id)
        page = engine.retained_pages[app_id]
        body = list(gzip.compress(b'{"action":"observe"}'))
        assert await page.evaluate('async body => (await fetch("/telemetry", {method:"POST",body:new Uint8Array(body)})).text()', body) == 'ok'
        await checkpoint(engine, app_id, page)
        saved = json.loads((config.private / 'fill-only-checkpoints' / f'{app_id}.json').read_text())
        assert saved['form_filled'] and saved['upload']['ready']
        final_body = list(gzip.compress(b'{"operationName":"SubmitApplication"}'))
        assert await page.evaluate('async body => {try {await fetch("/graphql", {method:"POST",body:new Uint8Array(body)});return "sent"} catch {return "blocked"}}', final_body) == 'blocked'
        assert requests == ['http://127.0.0.1:8123/telemetry']
        assert engine.fill_invariant.submission_requests_blocked == 1
        assert engine.fill_invariant.employer_submission_requests == 0
        assert engine.fill_invariant.final_submit_clicks == 0
        assert db.application(app_id)['submit_intent_at'] is None
    finally:
        await engine.close()
