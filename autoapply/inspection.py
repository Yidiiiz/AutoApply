"""Immutable, generation-scoped form metadata; never an authority for input."""
import functools
import json
import asyncio
from dataclasses import asdict, dataclass

from .models import Question


DOM_REVISION = r'''() => {
 if(!window.__autoapplyChanges) {
   const g=window.__autoapplyChanges={count:0};
   new MutationObserver(()=>g.count++).observe(document,{subtree:true,childList:true,characterData:true,attributes:true});
 }
 return window.__autoapplyChanges.count;
}'''


async def dom_revision(frame):
    return await frame.evaluate(DOM_REVISION)


async def wait_for_dom_change(frame, marker, timeout_ms):
    from playwright.async_api import TimeoutError as PlaywrightTimeout
    try:
        await frame.wait_for_function('n => !window.__autoapplyChanges || window.__autoapplyChanges.count !== n',
                                      arg=marker, timeout=max(1, timeout_ms))
    except PlaywrightTimeout:
        pass


# A lightweight identity/state read also finds controls in newly attached open
# roots. Observers cover text/attributes/options, not just input events. Element
# identities live in a browser WeakMap, never in Python ElementHandles.
REVISION = r'''es => {
 const w=window;
 if(!w.__autoapplyInspection) {
   const g=w.__autoapplyInspection={document:performance.timeOrigin+':'+performance.now(),revision:0,ids:new WeakMap(),next:0,roots:new WeakSet()};
   g.observe=root=>{
     if(g.roots.has(root))return;
     g.roots.add(root);
     new MutationObserver(ms=>{
       if(ms.some(m=>m.type!=='attributes'||!m.attributeName.startsWith('data-autoapply-')))g.revision++;
     }).observe(root,{subtree:true,childList:true,characterData:true,attributes:true});
   };
   g.observe(document);
 }
 const g=w.__autoapplyInspection;
 const states=es.map(e=>{
   g.observe(e.getRootNode());
   if(!g.ids.has(e))g.ids.set(e,++g.next);
   // Password/challenge values are never read by this observation.
   const sensitive=e.type==='password'||/captcha|verification|one.time/i.test(e.name+' '+e.id+' '+e.getAttribute('autocomplete'));
   return [g.ids.get(e),e.getClientRects().length>0,getComputedStyle(e).visibility,
     e.disabled||false,e.required||false,sensitive?'':e.value||'',!!e.checked||e.getAttribute('aria-checked')==='true',
     e.validity?e.validity.valid:null,e.validationMessage||'',e.getAttribute('data-autoapply-field')];
 });
 return [g.document,g.revision,location.href,states];
}'''


async def generation(page):
    result = []
    for frame in page.frames:
        # Reading metadata is allowed only in application frames. Other frames
        # still contribute identity/URL, so attachment/navigation invalidates.
        from .providers import detect_ats
        from .security import PROVIDERS
        import re
        from urllib.parse import urlsplit
        protected = any(re.search(pattern, frame.url, re.I) for _, pattern, _ in PROVIDERS)
        allowed = (frame == page.main_frame or not protected and (
                   urlsplit(frame.url).netloc == urlsplit(page.url).netloc or
                   detect_ats(frame.url).provider not in {'UNKNOWN', 'custom'}))
        if allowed:
            state = await asyncio.wait_for(
                frame.locator('input,textarea,select,[role=combobox],[role=checkbox]').evaluate_all(REVISION), timeout=5)
            result.append((id(frame), state))
        else:
            result.append((id(frame), frame.url))
    observation = getattr(page, '_autoapply_observation', {})
    tracker = getattr(page, '_autoapply_uploads', None)
    result.append(('browser', {'upload_revision':getattr(tracker, 'revision', None),
                              'status':observation.get('status'),
                              'dialog':observation.get('dialog_open'),
                              'messages':observation.get('messages', [])}))
    return json.dumps(result, separators=(',', ':'))


@dataclass(frozen=True)
class FormSnapshot:
    generation: str
    schema_generation: str
    context: str
    questions_json: str

    def questions(self):
        return [Question(**q) for q in json.loads(self.questions_json)]


def schema_generation(revision):
    data = json.loads(revision)
    for _, state in data:
        if isinstance(state, list):
            state[3] = [control[:5] for control in state[3]]
    return json.dumps(data, separators=(',', ':'))


def current_questions(adapter, saved, revision):
    values = {}
    for frame_id, state in json.loads(revision):
        if isinstance(state, list):
            values.update(((frame_id, control[-1]), control) for control in state[3])
    questions = saved.questions()
    for q in questions:
        if q.key not in adapter.controls:
            continue  # Declarations have no interactive control.
        frame, tokens = adapter.controls[q.key]
        controls = [values[(id(frame), token)] for token in tokens]
        if q.kind == 'radio':
            q.value = next((option for option, control in zip(q.options, controls) if control[6]), '')
        elif q.kind == 'checkbox':
            # ARIA state changes invalidate the schema through the observer.
            q.value = 'Yes' if controls[0][6] else 'No'
        else:
            q.value = controls[0][5]
    return questions


def step_inventory(method):
    """Cache only completed unchanged observations; mutations never use it."""
    @functools.wraps(method)
    async def inspect(self, app):
        context = repr((dict(app), self.profile, self.answer_hints))
        before = await generation(self.page)
        schema = schema_generation(before)
        saved = getattr(self, '_step_snapshot', None)
        if saved and saved.generation == before and saved.context == context:
            return saved.questions()
        if saved and saved.schema_generation == schema and saved.context == context:
            # Expected value changes need a fresh state snapshot, not labels,
            # mapping, option metadata and declarations rediscovered from scratch.
            questions = current_questions(self, saved, before)
            self._step_snapshot = FormSnapshot(before, schema, context, json.dumps([asdict(q) for q in questions]))
            return questions
        self._step_snapshot = None
        questions = await method(self, app)
        after = await generation(self.page)
        # Inventories that search semantic options deliberately mutate the DOM.
        # Their next lookup must rediscover, unless the metadata was stable.
        if schema == schema_generation(after):
            self._step_snapshot = FormSnapshot(after, schema, context, json.dumps([asdict(q) for q in questions]))
        return questions
    return inspect
