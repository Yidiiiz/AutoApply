"""Upload lifecycle evidence. Never retain payloads, headers, or signed URLs."""
import asyncio
import re
import time
from urllib.parse import parse_qs, urlsplit

from .models import now


def operation(request):
    url = urlsplit(request.url)
    query = parse_qs(url.query)
    name = query.get('op', query.get('operationName', ['']))[0]
    if not name and url.path.endswith('/non-user-graphql'):
        try:
            payload = request.post_data_json
            if isinstance(payload, dict):
                name = payload.get('operationName', '')
        except Exception:  # noqa: BLE001 -- browser may dispose request during navigation
            name = ""  # Multipart bodies are deliberately never inspected.
    return name if isinstance(name, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,100}', name) else ''


class UploadTracker:
    def __init__(self):
        self.requests = {}
        self.events = []
        self.selected = 0
        self.result = {}
        self.revision = 0
        self.receipt = None
        self.deadline = None

    def emit(self, kind, **detail):
        self.events.append(dict(kind=kind, at=now(), **detail))
        if kind in {'UPLOAD_FILE_SELECTED', 'UPLOAD_REQUEST_STARTED', 'UPLOAD_RESPONSE_STATUS',
                    'UPLOAD_METADATA_CONFIRMED', 'UPLOAD_REQUEST_COMPLETED', 'UPLOAD_REQUEST_FAILED'}:
            self.revision += 1
            self.receipt = None

    def select(self):
        self.selected += 1
        self.deadline = time.monotonic() + 180
        self.emit('UPLOAD_FILE_SELECTED', selection=self.selected)

    def started(self, request):
        url = urlsplit(request.url)
        op = operation(request)
        metadata = op == 'ApiSetFormValueToFile'
        greenhouse = (self.selected > 0 and getattr(self, 'greenhouse', False)
                      and request.method == 'POST'
                      and bool(re.fullmatch(r'[a-z0-9.-]+\.s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com', url.hostname or '')))
        smartrecruiters = (self.selected > 0 and getattr(self, 'smartrecruiters', False)
                          and (url.hostname or '').endswith('.smartrecruiters.com')
                          and bool(re.search(r'upload|attachment|resume|/files?(?:/|$)',url.path,re.I)))
        if request.method not in {'POST', 'PUT'} or not (metadata or greenhouse or smartrecruiters or re.search(r'upload|presigned', url.path, re.IGNORECASE)):
            return
        if greenhouse:
            op = 'GreenhouseS3Upload'
        elif smartrecruiters:
            op = 'SmartRecruitersDocumentUpload'
        # Only a known public API path is retained verbatim. Object-store paths
        # can contain private identifiers, even when the query string is removed.
        path = url.path if url.path in {'/api/non-user-graphql', '/upload'} else '/[upload-path]'
        item = dict(method=request.method, domain=url.hostname, path=path,  # noqa: C408
                    operation=op, started_at=now(), ended_at=None, status=None,
                    completed=False, failed=False, inspected=not metadata,
                    metadata=metadata, metadata_confirmed=False, selection=self.selected)
        self.requests[request] = item
        self.emit('UPLOAD_REQUEST_STARTED', **item)
        self.emit('UPLOAD_REQUEST_OPERATION', operation=op)

    async def response(self, response):
        item = self.requests.get(response.request)
        if item is None:
            return
        item['status'] = response.status
        item['failed'] = not 200 <= response.status < 300
        self.emit('UPLOAD_RESPONSE_STATUS', status=response.status, operation=item['operation'])
        if item['metadata'] and not item['failed']:
            try:
                payload = await response.json()
                # GraphQL 200 with errors is a failed write. Store only shape
                # verdicts; response values may contain applicant/file data.
                data = payload.get('data')
                values = list(data.values()) if isinstance(data, dict) else []
                rejected = any(isinstance(v, dict) and (v.get('success') is False or v.get('error')) for v in values)
                item['failed'] = bool(payload.get('errors') or payload.get('error') or rejected)
                item['metadata_confirmed'] = any(v is not None and v is not False for v in values) and not item['failed']
                if item['failed']:
                    item['failure_code'] = 'metadata_write_rejected'
            except Exception:  # noqa: BLE001 -- response disposal must fail closed
                item['metadata_confirmed'] = False
            item['inspected'] = True
            self.emit('UPLOAD_METADATA_CONFIRMED', value=item['metadata_confirmed'])

    def finished(self, request):
        item = self.requests.get(request)
        if item:
            item.update(completed=True, ended_at=now())
            self.emit('UPLOAD_REQUEST_COMPLETED', operation=item['operation'], status=item['status'])

    def failed(self, request):
        item = self.requests.get(request)
        if item:
            failure = getattr(request, 'failure', '') or ''
            code = re.search(r'net::ERR_[A-Z_]+', failure)
            item.update(failed=True, completed=False, ended_at=now(),
                        failure_code=code[0] if code else 'transport_failure')
            self.emit('UPLOAD_REQUEST_FAILED', operation=item['operation'], failure_code=item['failure_code'])

    def verdict(self, ui, *, ashby, expired=False, legacy_pending=False):
        records = list(self.requests.values())
        active = any(not r['ended_at'] or not r['inspected'] for r in records)
        failed = any(r['failed'] for r in records)
        metadata = [r for r in records if r['metadata'] and r['completed'] and r['metadata_confirmed']]
        metadata_ok = bool(metadata) and all(any(r['selection'] == n for r in metadata)
                                           for n in range(1, self.selected + 1))
        greenhouse_ok = not getattr(self, 'greenhouse', False) or (self.selected > 0 and all(
            any(r['operation'] == 'GreenhouseS3Upload' and r['selection'] == n
                and r['completed'] and r['status'] is not None and 200 <= r['status'] < 300
                and not r['failed'] for r in records) for n in range(1, self.selected + 1)))
        ready = (self.selected > 0 and ui['attached'] and not ui['busy'] and not ui['disabled'] and not ui['warning']
                 and not active and not failed and not legacy_pending and greenhouse_ok
                 and (not getattr(self, 'smartrecruiters', False) or ui['file_entry'])
                 and (not ashby or metadata_ok and ui['replace_usable'] and ui['file_entry']))
        if ready:
            category = 'UPLOAD_READY'
        elif failed or not ui['attached']:
            category = 'UPLOAD_FAILED'
        elif expired and not records and ashby:
            category = 'UPLOAD_NOT_STARTED'
        elif expired and (active or records):
            category = 'UPLOAD_STALLED'
        else:
            category = 'UPLOAD_PENDING'
        self.result = dict(category=category, ready=bool(ready), file_selected=self.selected > 0,  # noqa: C408
                           request_started=bool(records), active=active, metadata_confirmed=metadata_ok,
                           ui=ui, requests=[dict(r) for r in records])
        return self.result


UI = r"""(e, expected) => {
 const w=e.ownerDocument.defaultView;
 if(!w.__autoapplyAttachments)w.__autoapplyAttachments={ids:new WeakMap(),next:0,roots:new WeakMap()};
 const g=w.__autoapplyAttachments;
 if(!g.ids.has(e))g.ids.set(e,++g.next);
 const identity=w.performance.timeOrigin+':'+g.ids.get(e);
 const revision=root=>{
   if(!g.roots.has(root)) {
     const r={value:0};g.roots.set(root,r);
     new MutationObserver(ms=>{
       if(ms.some(m=>m.type!=='attributes'||!m.attributeName.startsWith('data-autoapply-')))r.value++;
     }).observe(root,{subtree:true,childList:true,attributes:true,characterData:true});
   }
   return g.roots.get(root).value;
 };
 const visible=n=>!!(n.offsetWidth||n.offsetHeight||n.getClientRects().length);
 if(e.matches('.file-upload[data-autoapply-upload]')) {
   const entry=e.querySelector('.file-upload__filename');
   const remove=entry?.querySelector('button[aria-label="Remove file"]');
   const busy=[...e.querySelectorAll('[role=progressbar],[aria-busy=true]')].some(visible);
   const error=[...e.querySelectorAll('.helper-text--error,[role=alert]')].some(n=>visible(n)&&n.textContent.trim());
   const accepted=!!entry&&visible(entry)&&entry.textContent.trim()===expected.name&&!!remove&&!remove.disabled;
   return {identity,revision:revision(e),attached:accepted,busy,disabled:!!remove?.disabled,warning:error,
     replace_visible:!!remove,replace_usable:!!remove&&!remove.disabled,file_entry:accepted};
 }
 let root=e.parentElement;
 for(let n=root,i=0;n&&i<6;n=n.parentElement,i++) {
   if(n.querySelectorAll('input[type=file]').length>1) break;
   root=n;
   if([...n.querySelectorAll('button')].some(b=>/^replace$/i.test((b.innerText||'').trim()))) break;
 }
 const replace=[...root.querySelectorAll('button')].filter(b=>visible(b)&&/^replace$/i.test((b.innerText||'').trim()));
 const disabled=b=>b.disabled||b.getAttribute('aria-disabled')==='true';
 return {identity,revision:revision(root),attached:e.files?.length===1&&e.files[0].name===expected.name&&e.files[0].size===expected.size,
 warning:[...root.querySelectorAll('[role=alert],.error,.helper-text--error')].some(n=>visible(n)&&/upload|file|attachment|failed|error/i.test(n.textContent||'')),
 busy:[...root.querySelectorAll('[aria-busy=true],[role=progressbar]')].some(visible),
 disabled:!!e.disabled||replace.some(disabled),replace_visible:replace.length>0,
 replace_usable:replace.some(b=>!disabled(b)),file_entry:root.innerText.includes(expected.name)};
}"""


class SessionAttachment:
    """An accepted attachment carried across an observed step in this page only.

    This receipt is never restored from database state or a new browser. A newly
    present file control must still show the file; disappearance on the same step
    invalidates it. Late network failures remain blockers in UploadTracker.
    """
    def __init__(self, page, selector):
        self.page, self.selector = page, selector
        self.accepted = None
        self.carried = False
        self.last_state = None
        self.navigation = 0
        self.carried_navigation = None
        def navigated(frame):
            if frame == page.main_frame:
                self.navigation += 1
        page.on('framenavigated', navigated)

    async def state(self, expected):
        field = self.page.locator(self.selector)
        if await field.count() == 1:
            result = await field.evaluate(UI, expected)
            self.last_state = result
            if (not result.get('attached') or result.get('warning') or result.get('busy') or result.get('disabled')
                    or self.accepted and result.get('identity') != self.accepted.get('identity')):
                self.accepted = None
            return result
        if self.carried and self.accepted and self.navigation == self.carried_navigation and not self.page.is_closed():
            return dict(self.accepted, evidence_source='same_session_accepted_attachment_prior_step')
        return {'attached':False}

    def confirm(self):
        if self.last_state and self.last_state.get('attached') and self.last_state.get('file_entry'):
            self.accepted = dict(self.last_state)

    def advance(self):
        if not self.accepted:
            raise ValueError('No verified attachment receipt to carry across step')
        self.carried = True
        self.carried_navigation = self.navigation


async def attachment_state(field, expected):
    if isinstance(field, SessionAttachment):
        return await field.state(expected)
    return await field.evaluate(UI, expected) if await field.count() == 1 else {'attached':False}


async def _wait_for_uploads(browser, page, controls, deadline):
    tracker = page._autoapply_uploads
    ashby = urlsplit(page.url).hostname == 'jobs.ashbyhq.com'
    stable = None
    stable_key = None
    prior = None
    while True:
        marker = await browser.change_marker(page)
        from .security import SecurityDetector
        security, _ = await SecurityDetector().detect(page, browser.observation(page))
        if security.blocking:
            tracker.receipt = None
            tracker.result = dict(tracker.result, ready=False, category='UPLOAD_PENDING')
            return False
        ui = dict(attached=bool(controls), busy=False, disabled=False, warning=False,  # noqa: C408
                  replace_visible=True, replace_usable=True, file_entry=True)
        identities = []
        for field, name, size in controls.values():
            state = await attachment_state(field, {'name':name, 'size':size})
            identities.append((state.get('identity'), state.get('revision'), name, size, state.get('evidence_source')))
            for key in ('attached', 'replace_visible', 'replace_usable', 'file_entry'):
                ui[key] &= bool(state.get(key))
            for key in ('busy', 'disabled', 'warning'):
                ui[key] |= bool(state.get(key))
        ui['warning'] |= await page.locator('body').evaluate(r"""e => [...e.querySelectorAll('[role=status],[role=alert],[aria-live],button')].some(n =>
          !!(n.offsetWidth||n.offsetHeight||n.getClientRects().length) && /uploading|updating your application/i.test(n.innerText||''))""")
        if ui != prior:
            for kind, key in [('UPLOAD_CONTROL_BUSY','busy'), ('UPLOAD_CONTROL_DISABLED','disabled'),
                              ('UPLOAD_REPLACE_VISIBLE','replace_visible'), ('UPLOAD_WARNING_VISIBLE','warning')]:
                tracker.emit(kind, value=ui[key])
            prior = dict(ui)
        expired = time.monotonic() >= deadline
        result = tracker.verdict(ui, ashby=ashby and bool(controls), expired=expired,
                                 legacy_pending=bool(browser.observation(page).get('pending_uploads')))
        browser.observation(page)['upload_result'] = result
        key = (page.url, tracker.revision, tuple(controls), tuple(identities), tuple(sorted(ui.items())))
        if result['ready']:
            if tracker.receipt == key:
                return True  # Fresh DOM/network/security evidence agrees with the receipt.
            if stable is None or stable_key != key:
                stable = time.monotonic()
                stable_key = key
            if time.monotonic() - stable >= .5:
                for field, _, _ in controls.values():
                    if isinstance(field, SessionAttachment):
                        field.confirm()
                tracker.emit('UPLOAD_READY', value=True)
                tracker.receipt = key
                return True
        else:
            stable = None
            tracker.receipt = None
        if expired or result['category'] == 'UPLOAD_FAILED':
            tracker.emit('UPLOAD_READY', value=False, category=result['category'])
            return False
        # Wake on DOM change; a bounded wake also observes network-only state.
        delay = .5 if result['ready'] else .1
        await browser.wait_for_change(page, marker, min(delay, max(.001, deadline-time.monotonic()))*1000)


async def wait_for_uploads(browser, page, controls, timeout_seconds):
    tracker = page._autoapply_uploads
    deadline = time.monotonic() + timeout_seconds
    if tracker.deadline and not tracker.receipt:
        deadline = min(deadline, tracker.deadline)
    try:
        async with asyncio.timeout(max(.001, deadline-time.monotonic())):
            return await _wait_for_uploads(browser, page, controls, deadline)
    except TimeoutError:
        tracker.receipt = None
        tracker.result = dict(tracker.result, ready=False, category='UPLOAD_STALLED')
        browser.observation(page)['upload_result'] = tracker.result
        tracker.emit('UPLOAD_READY', value=False, category='UPLOAD_STALLED')
        return False
