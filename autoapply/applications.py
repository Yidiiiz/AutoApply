from .cursor import click_element, native_control
"""Semantic HTML application adapter; site subclasses only add observed entry points."""
import hashlib
import asyncio
import json
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .jobs import ats_identity, canonical_url
from .models import Question
from .form_validation import CHECKBOX_GROUP_JS, VALIDATION_JS

FIELD_SCRIPT = r"""() => {
 const checkboxGroup = __CHECKBOX_GROUP__;
 const root = document.querySelector('form#application, form.application-form, #application form, form[data-application-form]')
   || document.querySelector('form') || document.querySelector('main') || document.body;
 const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
 const text = e => (e?.innerText || e?.textContent || '').replace(/\s+/g, ' ').trim();
 const label = e => {
   const labelled = (e.getAttribute('aria-labelledby') || '').split(' ').filter(Boolean).map(id => text(document.getElementById(id))).join(' ');
   const native = [...(e.labels || [])].map(text).join(' ');
   const parent = e.closest('label') || e.closest('[data-field], .application-question, .field');
   let result = labelled || e.getAttribute('aria-label') || native || text(parent?.querySelector('label, legend')) || e.getAttribute('placeholder') || '';
   const help = (e.getAttribute('aria-describedby') || '').split(' ').filter(Boolean).map(id=>text(document.getElementById(id))).join(' ');
   const limit = help.match(/(?:maximum|max(?:imum)? of|up to|limit(?: of)?)\s*\d+\s*(?:words|characters)|\b\d+\s*(?:words|characters)\s*(?:max(?:imum)?|limit)/i);
   if (limit && !result.includes(limit[0])) result += ' ('+limit[0]+')';
   return result;
 };
 const out = [], radioSeen = new Set();
 for (const [index, e] of [...root.querySelectorAll('input, textarea, select, [role="combobox"], [role="checkbox"]')].entries()) {
   // Ashby's optional autofill uploader is separate from the submitted resume field.
   if (e.closest('.ashby-application-form-autofill-pane')) continue;
   let kind = e.getAttribute('type') || e.tagName.toLowerCase();
   if (e.disabled || ['hidden','submit','button','reset','image'].includes(kind) || ((!visible(e) || e.closest('[aria-hidden="true"]')) && kind !== 'file')) continue;
   if (e.tagName === 'TEXTAREA') kind = 'textarea';
   if (e.tagName === 'SELECT') kind = e.multiple ? 'multiselect' : 'select';
   if (e.getAttribute('role') === 'combobox' && e.tagName !== 'SELECT') kind = 'combobox';
   if (e.getAttribute('role') === 'checkbox') kind = 'checkbox';
   let name = label(e), options = [], required = e.required || e.getAttribute('aria-required') === 'true';
   if (kind === 'file') {
     const documentName = `${e.id || ''} ${e.name || ''}`;
     if (/resume|curriculum_vitae/i.test(documentName)) name = 'Resume';
     else if (/cover_letter/i.test(documentName)) name = 'Cover letter';
     const container = e.closest('.file-upload, [data-field], fieldset');
     required = required || !!container?.querySelector('[aria-required="true"]') || /\*/.test(text(container?.querySelector('legend, .label')));
   }
   let group = [e];
   if (kind === 'radio') {
     const groupKey = e.name || e.closest('[role="radiogroup"], fieldset') || e;
     if (radioSeen.has(groupKey)) continue;
     radioSeen.add(groupKey);
     group = [...root.querySelectorAll('input[type="radio"]')].filter(r => e.name ? r.name === e.name : r.closest('fieldset') === e.closest('fieldset'));
     const groupLabel = e.closest('fieldset')?.querySelector(':scope > legend, :scope > label') || e.closest('[role="radiogroup"]')?.querySelector('label');
     name = text(groupLabel) || name;
     options = group.map(label);
     required = group.some(r => r.required || r.getAttribute('aria-required') === 'true');
     // Ashby custom validation may omit native required. Require a verified choice conservatively.
     if (groupLabel) required = required || /\*/.test(getComputedStyle(groupLabel, '::after').content) || !!groupLabel.closest('[data-field-entry-id]');
   } else if (e.tagName === 'SELECT') {
     options = [...e.options].filter(o => !o.disabled && o.value !== '').map(o => o.text.trim());
   } else if (kind === 'checkbox') options = ['Yes','No'];
   // Required asterisks are common on hosted forms lacking native required attributes.
   required = required || /\*/.test(name);
   if (kind === 'checkbox' && checkboxGroup(e)) required = false;
   const characterLimit = name.match(/(?:maximum|max(?:imum)? of|up to|limit(?: of)?)\s*(\d+)\s*characters|\b(\d+)\s*characters\s*(?:max(?:imum)?|limit)/i);
   const hintedMax = characterLimit ? Number(characterLimit[1]||characterLimit[2]) : null;
   const tokens = group.map((r, n) => { const token = `aa-${index}-${n}`; r.setAttribute('data-autoapply-field', token); return token; });
   out.push({label:name.replace(/\s*\*+\s*$/, '').trim(), kind, required:!!required, options,
     max_length:e.maxLength > 0 ? (hintedMax ? Math.min(e.maxLength,hintedMax) : e.maxLength) : hintedMax,
     id:e.id, value:kind === 'checkbox' ? (e.checked || e.getAttribute('aria-checked') === 'true' ? 'Yes' : 'No') : kind === 'radio' ? (group.some(r=>r.checked) ? label(group.find(r=>r.checked)) : '') : e.value || '', tokens});
 }
 return out;
}""".replace('__CHECKBOX_GROUP__', CHECKBOX_GROUP_JS)


class UnsupportedForm(RuntimeError):
    pass


class GenericApplicationAdapter:
    name = "generic"

    def __init__(self, page):
        self.page = page
        self.controls = {}
        self.answer_hints = {}
        self.profile = {}
        self.signatures = {}
        self.live_committed = {}
        self.uploads = getattr(page, '_autoapply_attached_documents', {})
        page._autoapply_attached_documents = self.uploads

    async def inspect(self):
        html = await self.page.content()
        soup = BeautifulSoup(html, "html.parser")
        posting = None
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or script.get_text())
                items = data if isinstance(data, list) else data.get("@graph", [data])
                posting = next((x for x in items if isinstance(x, dict) and x.get("@type") == "JobPosting"), posting)
            except (ValueError, AttributeError):
                pass
        description = soup.select_one('#content, #job-description, .job-description, .posting-page .content, [data-testid="job-description"], main')
        result = {"description": description.get_text("\n", strip=True) if description else soup.get_text("\n", strip=True),
                  "title": soup.h1.get_text(" ", strip=True) if soup.h1 else "", "company": "", "location": ""}
        if posting:
            result["description"] = BeautifulSoup(posting.get("description", ""), "html.parser").get_text("\n", strip=True) or result["description"]
            result["title"] = posting.get("title", result["title"])
            organization = posting.get("hiringOrganization", {})
            if isinstance(organization, dict):
                result["company"] = organization.get("name", "")
            locations = posting.get("jobLocation", [])
            if isinstance(locations, dict):
                locations = [locations]
            names = []
            for location in locations:
                address = location.get("address", {}) if isinstance(location, dict) else {}
                if isinstance(address, dict):
                    names.append(", ".join(str(address[k]) for k in ["addressLocality", "addressRegion", "addressCountry"] if address.get(k)))
            result["location"] = "; ".join(names)
            if posting.get("jobLocationType") == "TELECOMMUTE" and not result["location"]:
                result["location"] = "Remote"  # country remains unverified
        return result

    async def begin(self):
        if await self.page.locator('input[type="email"], input[type="file"], input[name="first_name"]').count():
            return
        for role in ["button", "link"]:
            button = self.page.get_by_role(role, name=re.compile(r"^(Apply for this job|Apply now|Apply to this job|Apply)$", re.I))
            if await button.count() == 1 and await button.is_visible():
                await click_element(self.page, button)
                await self.page.wait_for_load_state("domcontentloaded")
                return

    async def external_application(self):
        # Only explicit application links are followed, never arbitrary job-description links.
        for role in ["link"]:
            links = self.page.get_by_role(role, name=re.compile(r"^(Apply on company (?:website|site)|Apply externally|External application)$", re.I))
            if await links.count() == 1:
                href = await links.get_attribute("href")
                if href and href.startswith("https://"):
                    return href
        return None

    async def get_questions(self, app):
        from .answers import scope_for
        questions, occurrences = [], {}
        self.controls = {}
        for frame_index, frame in enumerate(self.page.frames):
            if frame != self.page.main_frame and not re.search(r"greenhouse|lever|ashby|application", frame.url, re.I):
                continue
            for item in await frame.evaluate(FIELD_SCRIPT):
                if not item["label"]:
                    raise UnsupportedForm("An application field has no reliable accessible label")
                if item["kind"] in {"password", "range", "color"}:
                    raise UnsupportedForm("Unsupported or authentication field: " + item["kind"])
                signature = f"{frame_index}|{item['label']}|{item['kind']}"
                occurrence = occurrences.get(signature, 0)
                occurrences[signature] = occurrence + 1
                key = hashlib.sha256(f"{signature}|{occurrence}".encode()).hexdigest()[:24]
                self.signatures[key] = {"id":item.get("id"),"token":item["tokens"][0],"label":item["label"]}
                hint = self.answer_hints.get(key)
                saved_options = json.loads(hint["options"]) if hint and hint.get("options") else []
                if item["kind"] == "combobox" and hint and json.loads(hint["answer"]) in saved_options:
                    item["options"] = saved_options
                elif item["kind"] == "combobox":
                    control = frame.locator('[data-autoapply-field="' + item["tokens"][0] + '"]')
                    if await control.get_attribute('aria-expanded') != 'true':
                        await control.click()
                    options = frame.get_by_role("option")
                    from .dropdowns import query_for, choose_option
                    query = query_for(item['label'], self.profile)
                    if query and await control.is_editable():
                        await control.fill(query)
                        # Menus commonly populate asynchronously after a remote search.
                        previous_options = None
                        stable_options = 0
                        for _ in range(50):
                            texts = [t.strip() for t in await options.all_text_contents() if t.strip()]
                            stable_options = stable_options + 1 if texts == previous_options else 0
                            previous_options = texts
                            if choose_option(item['label'], texts, self.profile) and stable_options >= 3:
                                break
                            await asyncio.sleep(.1)
                    hint = self.answer_hints.get(key)
                    if hint and hint['raw_question'] == item['label'] and hint['scope'] == scope_for(item['label'], app):
                        value = json.loads(hint['answer'])
                        exact = frame.get_by_role('option', name=value, exact=True)
                        if isinstance(value, str) and await control.is_editable() and (await exact.count() == 0 or await options.count() > 20):
                            await control.fill(value)
                            try:
                                await exact.wait_for(state='visible', timeout=5000)
                            except Exception:
                                raise UnsupportedForm('Saved answer is not an available exact dropdown option: ' + item['label']) from None
                    if await options.count():
                        item["options"] = await options.all_text_contents()
                        item["options"] = [s.strip() for s in item["options"] if s.strip()]
                    await control.press("Escape")
                q = Question(key, item["label"], item["kind"], item["required"], item["options"], item["max_length"], item["value"], scope_for(item["label"], app))
                self.controls[key] = (frame, item["tokens"])
                questions.append(q)
            # A click-through legal declaration may have no checkbox at all.
            body = await frame.locator("body").inner_text()
            for match in re.finditer(r"by (?:clicking.{0,50}|submitting.{0,80}|sending.{0,50}).{0,120}(?:certif\w*|attest\w*|agree\w*|consent\w*|authoriz\w*|acknowledge\w*)[^\n]{0,700}", body, re.I):
                declaration = match[0].strip()
                key = "declaration-" + hashlib.sha256(declaration.encode()).hexdigest()[:16]
                questions.append(Question(key, "Do you affirm this submission declaration? " + declaration,
                                          "attestation", True, ["Yes", "No"], scope=f"application:{app['id']}"))
        if not questions:
            candidates = await self.page.locator('input:not([type="hidden"]):visible,textarea:visible,select:visible,input[type="file"]').count()
            if candidates:
                raise UnsupportedForm(f'ADAPTER_DISCOVERY_FAILURE: {candidates} candidate controls but zero extracted fields')
        return questions

    async def answer_question(self, q, answer):
        from .answers import validate_answer
        from .security import SecurityDetector
        security, _ = await SecurityDetector().detect(self.page)
        if security.blocking:
            raise UnsupportedForm('Interactive security requirement before field input')
        validate_answer(q, answer.value)
        if q.kind == "attestation":
            if answer.value != "Yes":
                raise UnsupportedForm("User did not affirm the submission declaration")
            return
        frame, tokens = self.controls[q.key]
        field = frame.locator('[data-autoapply-field="' + tokens[0] + '"]')
        value = answer.value
        if q.kind in {"select", "multiselect", "combobox"}:
            from .combobox import open_and_select_combobox, signature_for, DropdownStateError
            signature = self.signatures.get(q.key) or await signature_for(field,q.label)
            try:
                evidence = await open_and_select_combobox(frame,signature,value)
            except DropdownStateError as exc:
                error = UnsupportedForm(str(exc))
                error.category = exc.category
                raise error from exc
            self.live_committed[q.key] = {'value':value,'signature':signature,'evidence':evidence}
        elif q.kind == "radio":
            if q.options.count(value) != 1:
                raise UnsupportedForm("Ambiguous radio options")
            target = frame.locator('[data-autoapply-field="' + tokens[q.options.index(value)] + '"]')
            if not await target.is_checked():
                await target.click()
            if not await target.is_checked():
                raise UnsupportedForm("Radio answer was not retained")
        elif q.kind == "checkbox":
            async def checked():
                return await field.evaluate("e => 'checked' in e ? e.checked : e.getAttribute('aria-checked') === 'true'")
            if await checked() != (value == "Yes"):
                await field.click()
            if await checked() != (value == "Yes"):
                raise UnsupportedForm("Checkbox answer was not retained")
        else:
            if await field.input_value() != value:
                await field.fill(value)
            if await field.input_value() != value:
                raise UnsupportedForm("Field did not retain the exact answer")

        if q.key not in self.live_committed:
            self.live_committed[q.key] = {'value':value,'evidence':{'committed':True,'mechanism':q.kind}}
        self.live_committed[q.key]['state'] = await self.control_state(q.key)

    async def control_state(self, key):
        frame,tokens=self.controls[key]
        from .combobox import resolve_field
        if key in self.signatures:
            fields=[resolve_field(frame,self.signatures[key])] if len(tokens)==1 else [frame.locator('[data-autoapply-field="'+t+'"]') for t in tokens]
        else:
            fields=[frame.locator('[data-autoapply-field="'+t+'"]') for t in tokens]
        result=[]
        for field in fields:
            result.append(await field.evaluate('''e=>{
              const root=e.closest('[class*="select__control"], [class*="select-control"]')||e.parentElement?.parentElement;
              return {value:e.value||'',checked:!!e.checked,ariaChecked:e.getAttribute('aria-checked'),
                expanded:e.getAttribute('aria-expanded'),invalid:e.getAttribute('aria-invalid'),
                selected:[...(root?.querySelectorAll('[class*="singleValue"],[class*="single-value"],[data-selected-value],[class*="multi-value__label"],[class*="multiValueLabel"],input[type=hidden]')||[])].map(n=>n.value||n.textContent.trim())};
            }'''))
        return result

    async def upload_documents(self, q, path):
        if q.key in self.uploads:
            # Same-page resumption retains the attachment. Normal validation and
            # fresh upload readiness still check it before submission.
            return
        frame, tokens = self.controls[q.key]
        field = frame.locator('[data-autoapply-field="' + tokens[0] + '"]')
        tracker = getattr(self.page, '_autoapply_uploads', None)
        greenhouse = (urlsplit(self.page.url).hostname in {'job-boards.greenhouse.io', 'job-boards.eu.greenhouse.io'}
                      and await field.locator('xpath=ancestor::div[contains(concat(" ", normalize-space(@class), " "), " file-upload ")]').count() == 1)
        if greenhouse:
            await field.evaluate('(e, key) => e.closest(".file-upload").setAttribute("data-autoapply-upload", key)', q.key)
            container = frame.locator('[data-autoapply-upload="' + q.key + '"]')
            if tracker:
                tracker.greenhouse = True
        if tracker:
            tracker.select()
        await native_control(self.page, lambda: field.set_input_files(str(path)), "native file upload")
        if greenhouse:
            # React removes the input while uploading. Its accepted attachment
            # component, plus the tracked successful S3 request, is the evidence.
            await frame.wait_for_function('''key => {
                const e=document.querySelector('[data-autoapply-upload="'+key+'"]');
                return e && (e.querySelector('.file-upload__filename') || e.querySelector('.helper-text--error'));
            }''', arg=q.key, timeout=180000)
            self.uploads[q.key] = (container, path.name, path.stat().st_size)
            from .uploads import UI
            state = await container.evaluate(UI, {'name':path.name,'size':path.stat().st_size})
            if not state['attached'] or state['warning']:
                raise UnsupportedForm('Greenhouse did not accept the resume attachment')
            return
        files = await field.evaluate("e => [...e.files].map(f => ({name:f.name,size:f.size}))")
        if len(files) != 1 or files[0]["name"] != path.name or files[0]["size"] != path.stat().st_size:
            raise UnsupportedForm("Resume upload could not be verified")
        self.uploads[q.key] = (field, path.name, path.stat().st_size)

    async def validate(self):
        issues = []
        for key, receipt in self.live_committed.items():
            if 'state' in receipt and await self.control_state(key) != receipt['state']:
                issues.append('Live control changed after verified commitment: '+key)
        for field, name, size in self.uploads.values():
            from .uploads import attachment_state
            state = await attachment_state(field, {"name": name, "size": size})
            if not state['attached'] or state.get('warning'):
                issues.append("Required uploaded document is no longer attached")
        for frame in self.page.frames:
            if frame != self.page.main_frame and not re.search(r"greenhouse|lever|ashby|application", frame.url, re.I):
                continue
            issues.extend(await frame.evaluate(VALIDATION_JS))
            alerts = await frame.locator('[role="alert"]:visible').all_text_contents()
            issues.extend(a.strip() for a in alerts if a.strip())
        return issues

    async def action(self):
        final = re.compile(r"^(Submit application|Submit my application|Send application|Submit)$", re.I)
        next_step = re.compile(r"^(Next|Continue|Review application|Save and continue)$", re.I)
        for pattern, kind in [(final, "submit"), (next_step, "next")]:
            candidates = []
            for frame in self.page.frames:
                buttons = frame.get_by_role("button", name=pattern)
                for button in await buttons.all():
                    if await button.is_visible() and await button.is_enabled():
                        candidates.append(button)
            if len(candidates) == 1:
                return kind, candidates[0]
            if len(candidates) > 1:
                raise UnsupportedForm("Multiple possible application actions")
        return None, None

    async def verify_submission(self):
        from .security import SubmissionClassifier
        return (await SubmissionClassifier().classify(self.page)).confirmation


class GreenhouseAdapter(GenericApplicationAdapter):
    name = "greenhouse"


class LeverAdapter(GenericApplicationAdapter):
    name = "lever"

    async def begin(self):
        link = self.page.get_by_role("link", name=re.compile(r"^Apply for this job$", re.I))
        if await link.count() == 1 and await link.is_visible():
            await click_element(self.page, link)
            await self.page.wait_for_load_state("domcontentloaded")
        else:
            await super().begin()


class AshbyAdapter(GenericApplicationAdapter):
    name = "ashby"

    async def begin(self):
        tab = self.page.get_by_role("tab", name="Application", exact=True)
        try:
            await tab.wait_for(state="visible", timeout=10000)
        except Exception:
            tab = None
        if tab is not None:
            await click_element(self.page, tab)
            await self.page.locator('input:not([type="hidden"]), textarea, select').first.wait_for(state="visible")
            return
        button = self.page.get_by_role("button", name="Application", exact=True)
        if await button.count() == 1 and await button.is_visible():
            await click_element(self.page, button)
        else:
            await super().begin()


def adapter_for(page):
    name, _ = ats_identity(page.url)
    if name == 'smartrecruiters':
        from .smartrecruiters import SmartRecruitersAdapter
        return SmartRecruitersAdapter(page)
    cls = {"greenhouse": GreenhouseAdapter, "lever": LeverAdapter, "ashby": AshbyAdapter}.get(name, GenericApplicationAdapter)
    return cls(page)
