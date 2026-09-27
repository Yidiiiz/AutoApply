"""Observe production submit delivery without logging request bodies or field values."""
import json
import re
import uuid
from urllib.parse import urlsplit, parse_qs

from .cursor import ready_cursor
from .models import now
from .security import safe_url
from .scrolling import NavigationError
from .cursor.types import Point
from .cursor.target import box_difference


class SubmitObstructed(NavigationError):
    category = "SUBMIT_ELEMENT_OBSTRUCTED"


BUTTON = """e => {
 const r=e.getBoundingClientRect(),w=e.ownerDocument.defaultView;
 const x=r.left+r.width/2,y=r.top+r.height/2,hit=e.ownerDocument.elementFromPoint(x,y);
 const identify=n=>n?{tag:n.tagName,id:n.id||null,role:n.getAttribute('role'),type:n.getAttribute('type')}:null;
 return {text:(e.innerText||e.getAttribute('aria-label')||'').slice(0,150),
 attributes:identify(e),under_pointer:identify(hit),covered:!(hit===e||e.contains(hit)),
 local_coordinates:{x,y},scroll:{x:w.scrollX,y:w.scrollY},
 active_element:identify(e.ownerDocument.activeElement),focused:e.ownerDocument.hasFocus(),
 viewport:{width:w.innerWidth,height:w.innerHeight},
 inside_viewport:x>=0&&y>=0&&x<w.innerWidth&&y<w.innerHeight,
 disabled:!!e.disabled||e.getAttribute('aria-disabled')==='true'};
}"""

INSTALL = """(e, key) => {
 const d=e.ownerDocument,w=d.defaultView;
 const state={events:[],mutations:0,form_submit:false};
 const send=event=>{state.events.push(event);w[key](event).catch(()=>{});};
 const handler=event=>{const reaches=event.composedPath().includes(e);
   if(['mousedown','mouseup','click'].includes(event.type)) send({type:event.type,
      trusted:event.isTrusted,reaches_button:reaches,x:event.clientX,y:event.clientY,
      target:{tag:event.target.tagName,id:event.target.id||null}});
   if(event.type==='submit' && (!e.form || event.target===e.form)) {
     state.form_submit=true;send({type:'submit',trusted:event.isTrusted});
   }};
 for(const type of ['mousedown','mouseup','click','submit']) d.addEventListener(type,handler,true);
 const root=e.form||e.closest('main,[role=main]')||d.body;
 const observer=new MutationObserver(items=>{state.mutations+=items.filter(m=>
    m.type!=='attributes'||!['style','class','data-autoapply-field'].includes(m.attributeName)).length});
 observer.observe(root,{subtree:true,attributes:true,childList:true,characterData:true});
 state.stop=()=>{observer.disconnect();for(const t of ['mousedown','mouseup','click','submit'])d.removeEventListener(t,handler,true)};
 w[key+'State']=state;
 return {form_present:!!e.form,button_disabled:!!e.disabled,button_text:(e.innerText||'').slice(0,150)};
}"""


class SubmissionProbe:
    def __init__(self, db, app_id, page, button):
        self.db, self.app_id, self.page, self.button = db, app_id, page, button
        self.key = 'autoapplySubmit' + uuid.uuid4().hex
        self.data = dict(run_id=self.key, started_at=now(), events=[], network=[],
                         click_call_executed=False, click_call_returned=False,
                         delivered=False, navigation_started=False, form_state_changed=False,
                         confirmation_observed=False, intent_created=False)
        self.requests = {}
        self.frame = None
        self.armed = False

    def record(self, kind, detail=None):
        # Observation callbacks must not spend the confirmation window exporting
        # bundles/statistics. SQLite evidence and its dirty marker commit now.
        with self.db.transaction(sync_history=False):
            self.db.event(self.app_id, kind, json.dumps(detail or {}, ensure_ascii=True))

    def save(self):
        with self.db.transaction(sync_history=False):
            self.db.set_setting(f'submit_probe:{self.app_id}:{self.key}', self.data)
            self.db.set_setting(f'latest_submit_probe:{self.app_id}', self.key)

    async def describe(self):
        if await self.button.count() != 1:
            return {'found':False}
        detail = await self.button.evaluate(BUTTON)
        detail.update(found=True, visible=await self.button.is_visible(), enabled=await self.button.is_enabled(),
                      bounding_box=await self.button.bounding_box(),selector=str(self.button),url=safe_url(self.page.url))
        handle = await self.button.element_handle()
        try:
            frame = await handle.owner_frame()
            detail.update(frame_name=frame.name,frame_url=safe_url(frame.url))
        finally:
            await handle.dispose()
        return detail

    async def prepare(self):
        await self.page.bring_to_front()
        cursor = await ready_cursor(self.page)
        await cursor.resolver.scroll(self.button, guard=cursor.security_guard)
        detail = await self.describe()
        box = detail.get('bounding_box')
        if not box:
            raise SubmitObstructed('Submit has no visible bounding box')
        point = Point(box['x']+box['width']/2, box['y']+box['height']/2)
        detail['coordinates'] = vars(point)
        detail['hit_test_valid'] = await cursor.resolver.hit_test.valid(self.button,point)
        self.data['before'] = detail
        for name, value in [('SUBMIT_BUTTON_FOUND',detail['found']),('SUBMIT_BUTTON_VISIBLE',detail['visible']),('SUBMIT_BUTTON_ENABLED',detail['enabled'])]:
            self.record(name, {'value':value})
        self.record('SUBMIT_BUTTON_UNOBSTRUCTED', {'value':detail['hit_test_valid'] and not detail['covered']})
        if not (detail['visible'] and detail['enabled'] and detail['bounding_box'] and detail['hit_test_valid'] and detail['inside_viewport'] and not detail['covered']):
            raise SubmitObstructed('Submit button failed visibility, geometry, or hit-test checks')
        self.save()
        self.db.flush_history()

    async def physical_click(self):
        if getattr(self.db.lifecycle, "fill_only", False):
            raise SubmitObstructed("Fill-only forbids final submission")
        """One mouse call; never fall back to a locator click or retry after intent."""
        if self.db.automation_retired(self.app_id):
            raise SubmitObstructed('User-reported submission permanently excludes further Submit interaction')
        if self.data['click_call_executed']:
            raise RuntimeError('Submit interaction already attempted')
        cursor = await ready_cursor(self.page)
        box = await self.button.bounding_box()
        if not box or not await self.button.is_visible() or not await self.button.is_enabled():
            raise SubmitObstructed('Submit is unavailable before physical interaction')
        point = Point(box['x']+box['width']/2, box['y']+box['height']/2)
        self.record('SUBMIT_MOUSE_MOVE_STARTED', vars(point))
        await self.page.mouse.move(point.x, point.y)
        cursor.current_position, cursor.position_known = point, True
        self.record('SUBMIT_MOUSE_MOVE_COMPLETED', vars(point))
        if await cursor.security_guard():
            raise SubmitObstructed('Security state changed before Submit; no click issued')
        fresh = await self.button.bounding_box()
        valid = bool(fresh and box_difference(box, fresh) <= .5
                     and await self.button.is_visible() and await self.button.is_enabled()
                     and await cursor.resolver.hit_test.valid(self.button, point))
        self.data['pre_click'] = dict(bounding_box=fresh, previous_box=box,
                                      coordinates=vars(point), hit_test_valid=valid)
        self.record('SUBMIT_BUTTON_UNOBSTRUCTED', {'value':valid})
        self.save()
        if not valid:
            raise SubmitObstructed('Submit moved or an overlay intercepted its center; no click issued')
        self.click_started()
        await self.page.mouse.click(point.x, point.y)
        await self.click_returned()

    async def arm(self):
        async def received(source, event):
            if source['frame'] != self.frame:
                return
            self.data['events'].append(event)
            if event['type'] in {'mousedown','mouseup','click'} and event.get('reaches_button'):
                self.record({'mousedown':'SUBMIT_MOUSEDOWN_OBSERVED',
                             'mouseup':'SUBMIT_MOUSEUP_OBSERVED',
                             'click':'SUBMIT_CLICK_OBSERVED'}[event['type']], event)
            if event['type']=='click' and event.get('trusted') and event.get('reaches_button'):
                self.data['delivered'] = True
                self.record('SUBMIT_CLICK_DISPATCHED',event)
            elif event['type']=='mousedown':
                self.record('SUBMIT_MOUSE_DOWN',event)
            self.save()
        await self.page.expose_binding(self.key,received)
        handle = await self.button.element_handle()
        try:
            self.frame = await handle.owner_frame()
        finally:
            await handle.dispose()
        self.data['baseline'] = await self.button.evaluate(INSTALL,self.key)
        self.page.on('request',self.on_request)
        self.page.on('response',self.on_response)
        self.page.on('framenavigated',self.on_navigation)
        self.armed=True
        self.save()

    def on_request(self, request):
        if not self.data['click_call_executed'] or request.method not in {'POST','PUT','PATCH'}:
            return
        url=urlsplit(request.url)
        # Payloads, headers, query strings, and opaque IDs are never inspected.
        if url.hostname != urlsplit(self.page.url).hostname and not re.search(r'(?:^|\.)(?:ashbyhq|greenhouse|lever)\.(?:com|co|io)$',url.hostname or ''):
            return
        if re.search(r'analytics|telemetry|metrics|tracking|sentry|segment',url.path,re.I):
            return
        path=re.sub(r'[0-9a-f]{8}-[0-9a-f-]{27,}|[A-Za-z0-9_-]{40,}','[id]',url.path,flags=re.I)
        operation=parse_qs(url.query).get('op',[''])[0]
        operation=operation if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,100}',operation) else ''
        item=dict(method=request.method,domain=url.hostname,path=path,status=None,
                  operation=operation, submission_candidate=bool(re.search(r'submit|application|apply|candidate',path+' '+operation,re.I)))
        self.requests[id(request)]=item
        self.data['network'].append(item)
        self.record('NETWORK_SUBMIT_REQUEST_OBSERVED' if item['submission_candidate'] else 'NETWORK_MUTATION_REQUEST_OBSERVED',item)
        if item['submission_candidate']:
            self.record('SUBMIT_NETWORK_REQUEST_OBSERVED',item)
        self.save()

    def on_response(self, response):
        item=self.requests.get(id(response.request))
        if item is not None:
            item['status']=response.status
            self.record('NETWORK_SUBMIT_RESPONSE_OBSERVED',item)
            if item['submission_candidate']:
                self.record('SUBMIT_NETWORK_RESPONSE',item)
            self.save()

    def on_navigation(self, frame):
        if self.data['click_call_executed'] and frame in {self.page.main_frame,self.frame}:
            self.data['navigation_started']=True
            self.record('NAVIGATION_STARTED',{'url':safe_url(frame.url)})
            self.save()

    def intent(self):
        self.data['intent_created']=True
        self.record('SUBMIT_INTENT_CREATED')
        self.save()

    def click_started(self):
        self.data['click_call_executed']=True
        self.record('SUBMIT_CLICK_CALL_EXECUTED')
        self.save()

    async def click_returned(self):
        self.data['click_call_returned']=True
        self.record('SUBMIT_CLICK_CALL_RETURNED')
        await self.sample('immediate_after')

    async def sample(self, name='after'):
        self.data['url_after']=safe_url(self.page.url)
        try:
            detail=await self.describe()
            state=await self.frame.evaluate("key => {const s=window[key+'State'];return s?{events:s.events,mutations:s.mutations,form_submit:s.form_submit}:null}",self.key)
            if state:
                self.data['dom_observation']=state
                self.data['delivered']=self.data['delivered'] or any(e['type']=='click' and e.get('trusted') and e.get('reaches_button') for e in state['events'])
                self.data['form_state_changed']=self.data['form_state_changed'] or bool(state['mutations'] or state['form_submit'])
            if not detail['found'] or detail.get('disabled') != self.data['before'].get('disabled') or detail.get('text') != self.data['before'].get('text'):
                self.data['form_state_changed']=True
            self.data[name]=detail
        except Exception as exc:
            self.data[name]={'unavailable':type(exc).__name__}
        self.save()

    @property
    def has_effect(self):
        return bool(self.data['navigation_started'] or self.data['form_state_changed'] or self.data['network'])

    def confirmed(self,evidence):
        self.data['confirmation_observed']=True
        self.record('CONFIRMATION_OBSERVED',{'evidence':evidence})
        self.save()

    async def finish(self):
        await self.sample()
        if self.data['form_state_changed']:
            self.record('FORM_STATE_CHANGED')
        if self.armed:
            self.page.remove_listener('request',self.on_request)
            self.page.remove_listener('response',self.on_response)
            self.page.remove_listener('framenavigated',self.on_navigation)
            try:
                await self.frame.evaluate("key => window[key+'State']?.stop()",self.key)
            except Exception:
                pass
        self.save()
        self.db.flush_history()
