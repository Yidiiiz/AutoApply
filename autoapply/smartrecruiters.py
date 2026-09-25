"""SmartRecruiters semantic controls, explicit capabilities and step navigation."""
import asyncio
import hashlib
import json
import re

from .applications import GenericApplicationAdapter, UnsupportedForm
from .field_mapping import FieldMapper, UNKNOWN_FIELD, normalize, signature
from .models import Question

# Per-control evaluation deliberately avoids choosing the first <form>. Locator
# discovery also pierces open component roots; references are scoped to each root.
METADATA = r'''e => {
 const root=e.getRootNode(), text=n=>{if(!n)return '';const c=n.cloneNode(true);c.querySelectorAll('input,select,textarea,button').forEach(x=>x.remove());return (c.textContent||'').replace(/\s+/g,' ').trim();};
 const labelled=(e.getAttribute('aria-labelledby')||'').split(/\s+/).filter(Boolean).map(id=>text(root.getElementById(id))).join(' ');
 const native=[...(e.labels||[])].map(text).join(' ');
 const container=e.closest('fieldset,[data-field],.field,.form-group,.input-field');
 const legend=text(container?.querySelector('legend,label'));
 let label=labelled||e.getAttribute('aria-label')||native||legend||e.getAttribute('placeholder')||'';
 let kind=e.type||e.tagName.toLowerCase();
 if(e.tagName==='TEXTAREA')kind='textarea';
 if(e.tagName==='SELECT')kind=e.multiple?'multiselect':'select';
 if(e.getAttribute('role')==='combobox'&&e.tagName!=='SELECT')kind='combobox';
 if(e.getAttribute('role')==='checkbox')kind='checkbox';
 if(kind==='file'&&!label&&/resume|curriculum.?vitae/i.test(e.id+' '+e.name))label='Resume';
 const section=text(e.closest('section')?.querySelector('h1,h2,h3,h4'));
 const group=e.closest('fieldset,[role=radiogroup]');
 return {label,kind,required:!!e.required||e.getAttribute('aria-required')==='true'||/\*/.test(label+legend),
 options:e.tagName==='SELECT'?[...e.options].filter(o=>!o.disabled&&o.value!=='').map(o=>o.text.trim()):kind==='checkbox'?['Yes','No']:[],
 id:e.id,name:e.name||'',autocomplete:e.autocomplete||'',placeholder:e.getAttribute('placeholder')||'',aria_label:e.getAttribute('aria-label')||'',
 section,group_label:text(group?.querySelector('legend'))||group?.getAttribute('aria-label')||'',
 value:kind==='checkbox'?(e.checked||e.getAttribute('aria-checked')==='true'?'Yes':'No'):e.value||'',
 checked:!!e.checked,disabled:!!e.disabled||e.getAttribute('aria-disabled')==='true',
 invalid:e.getAttribute('aria-invalid')==='true'||!!(e.validity&&!e.validity.valid),
 max_length:e.maxLength>0?e.maxLength:null};
}'''

CAPABILITIES = {'FIELD_DISCOVERY', 'TEXT_INPUT', 'RADIO', 'CHECKBOX', 'NATIVE_SELECT',
                'SEARCHABLE_SELECT', 'AUTOCOMPLETE', 'TEXTAREA', 'FILE_UPLOAD',
                'MULTI_STEP_NAVIGATION', 'VALIDATION_INSPECTION', 'SECURITY_INSPECTION', 'FINAL_SUBMIT', 'CONFIRMATION_DETECTION'}
INPUT_CAPABILITY = {'text':'TEXT_INPUT','email':'TEXT_INPUT','tel':'TEXT_INPUT','url':'TEXT_INPUT',
                    'number':'TEXT_INPUT','date':'TEXT_INPUT','radio':'RADIO','checkbox':'CHECKBOX',
                    'select':'NATIVE_SELECT','multiselect':'NATIVE_SELECT','combobox':'SEARCHABLE_SELECT',
                    'textarea':'TEXTAREA','file':'FILE_UPLOAD','attestation':'CHECKBOX'}


class CapabilityFailure(UnsupportedForm):
    def __init__(self, category, detail):
        self.category = category
        super().__init__(category + ': ' + detail)


class SmartRecruitersAdapter(GenericApplicationAdapter):
    name = 'smartrecruiters'
    capabilities = CAPABILITIES

    def __init__(self, page):
        super().__init__(page)
        self.mapper = FieldMapper(self.name)
        self.inventory = []
        self.counts = {}
        self.emit = lambda kind, detail: None

    async def inspect(self):
        if '/oneclick-ui/' in self.page.url and getattr(self.page, '_autoapply_listing', None):
            return self.page._autoapply_listing
        result = await super().inspect()
        if '/oneclick-ui/' not in self.page.url:
            self.page._autoapply_listing = result
        return result

    async def begin(self):
        await super().begin()
        # The entry link may replace the whole application root asynchronously.
        await self.page.locator('input[type=email]:visible').first.wait_for(timeout=20000)

    async def upload_documents(self, q, path):
        if q.key in self.uploads:
            return
        tracker = getattr(self.page, '_autoapply_uploads', None)
        if tracker:
            tracker.smartrecruiters = True
            if tracker.selected and q.key not in self.uploads:
                raise CapabilityFailure('FILE_UPLOAD_SELECTION_ALREADY_ATTEMPTED', 'Inspect the attachment before selecting another file')
        await super().upload_documents(q, path)
        from .uploads import SessionAttachment
        self.uploads[q.key] = (SessionAttachment(self.page,'[data-autoapply-field="'+self.controls[q.key][1][0]+'"]'),path.name,path.stat().st_size)
        self.emit('UPLOAD_SELECTED', {'document':'resume.pdf','selection_count':tracker.selected if tracker else 0})

    async def answer_question(self, q, answer):
        await super().answer_question(q, answer)
        if q.kind == 'combobox':
            frame, tokens = self.controls[q.key]
            field = frame.locator('[data-autoapply-field="'+tokens[0]+'"]')
            exact = frame.get_by_role('option', name=answer.value, exact=True)
            selected = await exact.count() == 1 and await exact.get_attribute('aria-selected') == 'true'
            if await field.get_attribute('aria-expanded') == 'true' and not selected:
                raise CapabilityFailure('AUTOCOMPLETE_VALUE_NOT_COMMITTED',q.label)
            if await field.get_attribute('aria-invalid') == 'true':
                raise CapabilityFailure('VALIDATION_INSPECTION',q.label)

    async def get_questions(self, app):
        from .answers import scope_for
        self.controls, self.inventory = {}, []
        questions, seen, radio_groups = [], {}, {}
        candidates = 0
        for frame_index, frame in enumerate(self.page.frames):
            if frame != self.page.main_frame and not re.search(r'^https://[^/]*\.smartrecruiters\.com/', frame.url):
                continue
            fields = frame.locator('input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]),textarea,select,[role=combobox]:not(input):not(select),[role=checkbox]:not(input)')
            for field in await fields.all():
                item = await field.evaluate(METADATA)
                if item['kind'] != 'file' and not await field.is_visible():
                    continue
                if await field.evaluate('e=>!!e.closest("[aria-hidden=true]")'):
                    continue
                candidates += 1
                item['label'] = item['label'].rstrip(' *').strip()
                if item['kind'] == 'radio':
                    group_key = (frame_index, item['name'], item['group_label'])
                    if not item['name'] and not item['group_label']:
                        raise CapabilityFailure('UNSUPPORTED_REQUIRED', 'Radio group has no stable identity')
                    group = radio_groups.setdefault(group_key, [])
                    group.append((field, item))
                    continue
                await self._add(frame, frame_index, [field], item, questions, seen, app, scope_for)
            for group_key, group in list(radio_groups.items()):
                if group_key[0] != frame_index:
                    continue
                item = dict(group[0][1])
                item.update(label=item['group_label'] or item['label'], options=[v['label'] for _, v in group],
                            required=any(v['required'] for _, v in group),
                            value=next((v['label'] for _, v in group if v['checked']), ''))
                await self._add(frame, frame_index, [f for f, _ in group], item, questions, seen, app, scope_for)
            body = await frame.locator('body').inner_text()
            for match in re.finditer(r'by (?:clicking.{0,50}|submitting.{0,80}|sending.{0,50}).{0,120}(?:certif\w*|attest\w*|agree\w*|consent\w*|authoriz\w*|acknowledge\w*)[^\n]{0,700}',body,re.I):
                declaration = match[0].strip()
                key = 'declaration-' + hashlib.sha256(declaration.encode()).hexdigest()[:16]
                q = Question(key,'Do you affirm this submission declaration? '+declaration,'attestation',True,['Yes','No'],
                             scope=f"application:{app['id']}",semantic_key=UNKNOWN_FIELD)
                questions.append(q)
                self.inventory.append(dict(field_id=key,rendered_question=q.label,normalized_question=normalize(q.label),
                                           control_type=q.kind,required=True,options=q.options,semantic_key=UNKNOWN_FIELD,
                                           mapping_source='ats_adapter',confidence=0,current_state='unanswered',
                                           supported_for_input=True,support='SUPPORTED',validation_state='requires_verified_answer'))
        self.counts = dict(visible_candidates=candidates, extracted=len(questions), mapped=sum(q.semantic_key!=UNKNOWN_FIELD for q in questions),
                           required=sum(q.required for q in questions), unresolved_required=sum(q.required and q.semantic_key==UNKNOWN_FIELD for q in questions))
        resumes = [q for q in questions if q.kind == 'file' and re.search(r'resume|curriculum vitae|\bcv\b', q.label, re.I)]
        if len(resumes) > 1:
            raise CapabilityFailure('FILE_UPLOAD_AMBIGUOUS','Multiple resume controls; distinguish profile import from application attachment before selecting')
        if candidates and not questions:
            raise CapabilityFailure('ADAPTER_DISCOVERY_FAILURE', 'Rendered controls exist but extraction returned zero')
        if not questions:
            raise CapabilityFailure('ADAPTER_DISCOVERY_FAILURE', 'No usable controls; step must be inspected')
        self.emit('STEP_DISCOVERED', dict(counts=self.counts, controls=self.inventory))
        return questions

    async def _add(self, frame, frame_index, fields, item, questions, seen, app, scope_for):
        item['label'] = item['label'].rstrip(' *').strip()
        if item['kind'] == 'password' or re.search(r'captcha|verification code|one.time (?:code|password)',item['label'],re.I):
            raise CapabilityFailure('SECURITY_CONTROL','Authentication or human intervention control requires manual handling')
        identity = signature(self.name, item)
        ordinal = seen.get(identity, 0)
        seen[identity] = ordinal + 1
        key = hashlib.sha256(f'{frame_index}:{identity}:{ordinal}'.encode()).hexdigest()[:24]
        tokens = []
        for n, field in enumerate(fields):
            token = f'sr-{key}-{n}'
            await field.evaluate('(e,t)=>e.setAttribute("data-autoapply-field",t)', token)
            tokens.append(token)
        mapping = await self.mapper.map(item)
        supported = item['kind'] in INPUT_CAPABILITY and not item['disabled'] and bool(item['label'])
        support = 'SUPPORTED' if supported else 'UNSUPPORTED_REQUIRED' if item['required'] else 'UNSUPPORTED_OPTIONAL'
        self.inventory.append(dict(field_id=key, rendered_question=item['label'], normalized_question=normalize(item['label']),
                                   control_type=item['kind'], required=item['required'], options=item['options'],
                                   **mapping, current_state='populated' if item['value'] else 'empty',
                                   supported_for_input=supported, support=support,
                                   validation_state='invalid' if item['invalid'] else 'valid'))
        if not supported:
            if item['required']:
                raise CapabilityFailure('UNSUPPORTED_REQUIRED', f"{item['kind']}: {item['label'] or 'unlabelled field'}")
            return
        if item['kind'] == 'combobox':
            # Discovery must not open menus or type. Options are resolved at fill time.
            item['options'] = []
        q = Question(key,item['label'],item['kind'],item['required'],item['options'],item['max_length'],item['value'],
                     scope_for(item['label'],app),mapping['semantic_key'])
        self.controls[key] = (frame, tokens)
        questions.append(q)

    async def action(self):
        # Resolve role/text again on every call. Filter hidden duplicates before
        # choosing a locator so a hidden node cannot capture the action.
        for kind, expression in [('next',r'^(Next|Continue|Review application|Save and continue)$'),
                                 ('submit',r'^(Submit application|Submit my application|Send application|Submit)$')]:
            matches = []
            for frame in self.page.frames:
                controls = frame.get_by_role('button', name=re.compile(expression,re.I)).filter(visible=True)
                if await controls.count():
                    matches.append(controls)
            if sum([await c.count() for c in matches]) > 1:
                raise CapabilityFailure('MULTI_STEP_NAVIGATION' if kind=='next' else 'FINAL_SUBMIT','Multiple visible action candidates')
            if matches:
                if not await matches[0].is_enabled():
                    raise CapabilityFailure('VALIDATION_INSPECTION','Action is disabled')
                return kind, matches[0]
        return None, None

    async def step_signature(self):
        data = []
        for frame in self.page.frames:
            if frame == self.page.main_frame or 'smartrecruiters.com' in frame.url:
                data.append(await frame.locator('input:not([type=hidden]),textarea,select,h1,h2,h3,[aria-current=step]').evaluate_all(
                    "es=>es.filter(e=>e.getClientRects().length).map(e=>[e.tagName,e.id,e.name,e.getAttribute('aria-label'),e.matches('h1,h2,h3,[aria-current=step]')?e.textContent:''])"))
        return json.dumps(data, sort_keys=True)

    async def advance(self):
        from .cursor import click_element
        before = await self.step_signature()
        await asyncio.sleep(.15)
        if before != await self.step_signature():
            raise CapabilityFailure('STEP_UNSTABLE', 'Form changed before navigation')
        self.emit('STEP_NEXT_INTENT', {})
        kind, target = await self.action()
        if kind != 'next':
            raise CapabilityFailure('MULTI_STEP_NAVIGATION','Fresh Next control missing')
        for event in ('STEP_NEXT_TARGET_FOUND','STEP_NEXT_VISIBLE','STEP_NEXT_ENABLED'):
            self.emit(event, {})
        # Existing cursor path performs scroll, fresh geometry, stability and hit
        # testing before physical input. No retry after any delivery ambiguity.
        try:
            await click_element(self.page, target)
        except Exception as exc:
            from .cursor import get_cursor
            cursor = get_cursor(self.page)
            self.emit('STEP_NEXT_FAILURE', {'error_type':type(exc).__name__,
                                           'cursor_events':[e['event'] for e in cursor.events[-20:]],
                                           'retry_performed':False})
            raise CapabilityFailure('MULTI_STEP_NAVIGATION',f'{type(exc).__name__}; inspect delivery and current step before retry') from exc
        self.emit('STEP_NEXT_CLICK_DELIVERED', {})
        for _ in range(100):
            await asyncio.sleep(.1)
            after = await self.step_signature()
            if after != before:
                await asyncio.sleep(.2)
                if after == await self.step_signature():
                    from .uploads import SessionAttachment
                    for attachment, _, _ in self.uploads.values():
                        if isinstance(attachment, SessionAttachment):
                            attachment.advance()
                    self.controls = {}
                    self.emit('STEP_TRANSITION_OBSERVED', {})
                    self.emit('STEP_ADVANCED', {})
                    return
        raise CapabilityFailure('STEP_TRANSITION_UNCONFIRMED','Next was delivered but no stable transition observed; inspect before retry')

    def support_report(self, questions):
        needed = {'FIELD_DISCOVERY','FILE_UPLOAD','MULTI_STEP_NAVIGATION','VALIDATION_INSPECTION','SECURITY_INSPECTION','FINAL_SUBMIT','CONFIRMATION_DETECTION'}
        needed.update(INPUT_CAPABILITY.get(q.kind, 'UNSUPPORTED_'+q.kind) for q in questions)
        missing = sorted(needed-self.capabilities)
        return dict(required=sorted(needed), supported=sorted(self.capabilities), missing=missing,
                    score=(len(needed)-len(missing))/len(needed), autonomous=not missing)
