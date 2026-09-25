import pytest

from autoapply.browser import Browser
from autoapply.security import SecurityDetector


@pytest.mark.asyncio
async def test_ambiguous_401_records_sanitized_provenance(config):
    class Page:
        url = 'https://employer.test/apply'
        main_frame = object()
        def __init__(self):
            self.handlers = {}
        def on(self, event, handler):
            self.handlers[event] = handler
    class Frame:
        url = 'https://security.test/frame?secret=private'
    class Request:
        resource_type = 'fetch'
        method = 'POST'
        frame = Frame()
        url = 'https://security.test/check?token=private'
    class Response:
        request = Request()
        url = request.url
        status = 401
    page = Page()
    browser = Browser(config)
    browser.observe(page)
    await page.handlers['response'](Response())
    observed = browser.observation(page)
    assert observed['status'] == 401  # Ambiguity must still stop, pending diagnosis.
    assert observed['http_failures'] == [dict(url='https://security.test/[redacted-verification-path]',
        method='POST', resource_type='fetch', status=401,
        frame_url='https://security.test/frame', main_frame=False)]
    snapshot = dict(text='', messages=[], markers=[], url=page.url)
    assert SecurityDetector().inspect(snapshot, observed['status']).blocking


@pytest.mark.parametrize('method,resource,status,blocking', [
    ('GET', 'fetch', 401, False), ('GET', 'xhr', 401, False),
    ('POST', 'fetch', 401, True), ('GET', 'document', 401, True),
    ('GET', 'fetch', 403, True), ('GET', 'fetch', 429, True),
])
async def test_background_auth_provenance_does_not_replace_document_status(config, method, resource, status, blocking):
    from types import SimpleNamespace
    class Page:
        url = 'https://employer.test/apply'
        main_frame = SimpleNamespace(url=url)
        def __init__(self):
            self.handlers = {}
        def on(self, event, handler):
            self.handlers[event] = handler
    page = Page()
    browser = Browser(config)
    browser.observe(page)
    browser.observation(page)['status'] = 200
    class Request:
        pass
    request = Request()
    request.method, request.resource_type, request.frame = method, resource, page.main_frame
    request.url = 'https://account.test/users/self'
    response = SimpleNamespace(request=request, url=request.url, status=status)
    await page.handlers['response'](response)
    observation = browser.observation(page)
    assert observation['status'] == (status if blocking else 200)
    assert observation['http_failures'][0]['status'] == status
    assert observation['http_failures'][0]['main_frame']
    snapshot = dict(text='', messages=[], markers=[], url=page.url)
    assert SecurityDetector().inspect(snapshot, observation['status']).blocking is blocking
    # Even an otherwise optional GET cannot suppress a rendered security failure.
    snapshot['messages'] = ['Verify you are human']
    assert SecurityDetector().inspect(snapshot, observation['status']).blocking
