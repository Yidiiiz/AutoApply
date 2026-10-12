from .cursor import click_element, native_control
"""Semantic HTML application adapter; site subclasses only add observed entry points."""
import hashlib
import asyncio
import json
import re
import time
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .jobs import ats_identity, canonical_url
from .models import Question
from .form_validation import CHECKBOX_GROUP_JS, VALIDATION_JS
from .inspection import step_inventory
from .patience import TransitionPatience, page_policy
from .form_controls import CONTROL_SELECTOR, READY_SELECTOR, DOM_JS, application_frame

FIELD_SCRIPT = r"""() => {
 __DOM__
 const checkboxGroup = __CHECKBOX_GROUP__;
 const root = document.querySelector('form#application, form.application-form, #application form, form[data-application-form]')
   || document.querySelector('form') || document.querySelector('main') || document.body;
 const text = e => {
   if (!e) return '';
   const copy=e.cloneNode(true);
   copy.querySelectorAll('input,textarea,select,button,[role="listbox"]').forEach(n=>n.remove());
   return (copy.textContent || '').replace(/\s+/g, ' ').trim();
 };
 const label = e => {
   const scope = e.getRootNode();
   const labelled = (e.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean).map(id => text(scope.getElementById(id))).join(' ');
   const native = [...(e.labels || [])].map(text).join(' ');
   const parent = e.closest('label') || e.closest('[data-field], .application-question, .field');
   let result = labelled || e.getAttribute('aria-label') || native || text(parent?.querySelector('label, legend')) || e.getAttribute('placeholder') || '';
   const help = (e.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean).map(id=>text(scope.getElementById(id))).join(' ');
   const limit = help.match(/(?:maximum|max(?:imum)? of|up to|limit(?: of)?)\s*\d+\s*(?:words|characters)|\b\d+\s*(?:words|characters)\s*(?:max(?:imum)?|limit)/i);
   if (limit && !result.includes(limit[0])) result += ' ('+limit[0]+')';
   return result;
 };
 const out = [], radioSeen = new Set();
 const controls = deepQuery(root, '__CONTROLS__');
 for (const [index, e] of controls.entries()) {
   // Ashby's optional autofill uploader is separate from the submitted resume field.
   if (e.closest('.ashby-application-form-autofill-pane')) continue;
   let kind = e.tagName === 'INPUT' ? e.type : e.getAttribute('type') || e.tagName.toLowerCase();
   const customSelect = e.getAttribute('role') === 'combobox' || e.matches('button[aria-haspopup="listbox"]');
   const customChoice = e.matches('[role="radio"],[role="checkbox"],[role="switch"]');
   if (e.disabled || e.getAttribute('aria-disabled') === 'true' || e.form && e.tagName === 'BUTTON' && e.type === 'submit' || composedClosest(e,'[inert],[aria-hidden="true"]') ||
       ['hidden','submit','reset','image'].includes(kind) || kind === 'button' && !customSelect && !customChoice || !visible(e) && kind !== 'file') continue;
   if (e.tagName === 'TEXTAREA') kind = 'textarea';
   if (e.tagName === 'SELECT') kind = e.multiple ? 'multiselect' : 'select';
   if (customSelect && e.tagName !== 'SELECT') kind = 'combobox';
   if (e.matches('[role="checkbox"],[role="switch"]')) kind = 'checkbox';
   if (e.getAttribute('role') === 'radio') kind = 'radio';
   if (e.isContentEditable) kind = 'textarea';
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
     const groupRoot = e.closest('[role="radiogroup"], fieldset');
     const groupKey = groupRoot || e.name || e;
     if (radioSeen.has(groupKey)) continue;
     radioSeen.add(groupKey);
     group = controls.filter(r => r.matches('input[type="radio"],[role="radio"]') && !r.disabled && r.getAttribute('aria-disabled') !== 'true' && visible(r) &&
       (groupRoot ? r.closest('[role="radiogroup"],fieldset') === groupRoot : e.name ? r.name === e.name && r.getRootNode() === e.getRootNode() : r === e));
     const groupLabel = groupRoot?.querySelector(':scope > legend, :scope > label');
     name = (groupRoot && label(groupRoot)) || text(groupLabel) || name;
     options = group.map(r => label(r) || text(r));
     required = groupRoot?.getAttribute('aria-required') === 'true' || group.some(r => r.required || r.getAttribute('aria-required') === 'true');
     // Ashby custom validation may omit native required. Require a verified choice conservatively.
     if (groupLabel) required = required || /\*/.test(getComputedStyle(groupLabel, '::after').content) || !!groupLabel.closest('[data-field-entry-id]');
   } else if (e.tagName === 'SELECT') {
     options = [...e.options].filter(o => !o.disabled && o.value !== '').map(o => o.text.trim());
   } else if (kind === 'checkbox') options = ['Yes','No'];
   else if (kind === 'combobox') {
     const ids = (e.getAttribute('aria-controls') || e.getAttribute('aria-owns') || '').split(/\s+/).filter(Boolean);
     options = ids.flatMap(id => {const menu=e.getRootNode().getElementById(id);return menu ? deepQuery(menu,'[role="option"]') : [];})
       .filter(o=>o.getAttribute('aria-disabled') !== 'true').map(o=>text(o)).filter(Boolean);
   }
   // Required asterisks are common on hosted forms lacking native required attributes.
   required = required || /\*/.test(name);
   if (kind === 'checkbox' && checkboxGroup(e)) required = false;
   const characterLimit = name.match(/(?:maximum|max(?:imum)? of|up to|limit(?: of)?)\s*(\d+)\s*characters|\b(\d+)\s*characters\s*(?:max(?:imum)?|limit)/i);
   const hintedMax = characterLimit ? Number(characterLimit[1]||characterLimit[2]) : null;
   const tokens = group.map((r, n) => { const token = `aa-${index}-${n}`; r.setAttribute('data-autoapply-field', token); return token; });
   out.push({label:name.replace(/\s*\*+\s*$/, '').trim(), kind, required:!!required, options,
     max_length:e.maxLength > 0 ? (hintedMax ? Math.min(e.maxLength,hintedMax) : e.maxLength) : hintedMax,
     id:e.id, value:kind === 'password' ? '' : kind === 'checkbox' ? (checked(e) ? 'Yes' : 'No') : kind === 'radio' ? (group.some(checked) ? (label(group.find(checked)) || text(group.find(checked))) : '') : e.isContentEditable ? e.textContent : e.value || '', tokens});
 }
 return out;
}""".replace('__CHECKBOX_GROUP__', CHECKBOX_GROUP_JS).replace('__DOM__', DOM_JS).replace('__CONTROLS__', CONTROL_SELECTOR)


class UnsupportedForm(RuntimeError):
    pass


class InventoryChanged(UnsupportedForm):
    """A fresh read rejected stale metadata before any field input was sent."""
    category = 'FIELD_INVENTORY_CHANGED'


def description_text(node):
    """Keep block boundaries without splitting qualifications at inline markup."""
    from bs4 import Comment, Declaration, Doctype, NavigableString, ProcessingInstruction
    blocks = {'p', 'div', 'section', 'main', 'article', 'li', 'ul', 'ol',
              'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'form', 'fieldset', 'label', 'legend', 'br'}

    def render(item, inline=False):
        if isinstance(item, (Comment, Declaration, Doctype, ProcessingInstruction)):
            return ''
        if isinstance(item, NavigableString):
            return re.sub(r'\s+', ' ', str(item)) if inline else str(item)
        if item.name in {'script', 'style'}:
            return ''
        inline = inline or item.name in {'p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'label', 'legend'}
        content = ''.join(render(child, inline) for child in item.children)
        return '\n' + content + '\n' if item.name in blocks else content

    return '\n'.join(line for raw in render(node).splitlines()
                     if (line := re.sub(r'\s+', ' ', raw).strip()))


class GenericApplicationAdapter:
    name = "generic"
    structured_inventory = False
    requires_explicit_narrative_policy = False
    requires_verified_resume = False
    requires_demographic_answer = False
    field_script = FIELD_SCRIPT
    defer_upload_until_present = False

    def __init__(self, page):
        self.page = page
        self.controls = {}
        self.answer_hints = {}
        self.profile = {}
        self.signatures = {}
        self.live_committed = {}
        self.uploads = getattr(page, '_autoapply_attached_documents', {})
        page._autoapply_attached_documents = self.uploads
        self.emit = lambda kind, detail: None

    async def inspect(self):
        # A hydrated listing can initially expose an empty main element. Observe
        # metadata within the existing page budget before assessing eligibility;
        # an unavailable description is an engineering hold, not an applicant
        # qualification that can be confirmed with Yes.
        policy = page_policy(self.page)
        try:
            async with asyncio.timeout(policy.timeout):
                while True:
                    result = await self.inspect_snapshot()
                    if result['description'].strip():
                        return result
                    await asyncio.sleep(policy.poll_seconds)
        except TimeoutError:
            raise self.transition_error('JOB_DESCRIPTION_UNAVAILABLE',
                'Public job description did not become observable within metadata budget') from None

    async def inspect_snapshot(self):
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
        description = soup.select_one('#content, #job-description, .job-description, .jv-job-detail-description, .posting-page .content, [data-testid="job-description"], main')
        # Jobvite's H1 is site branding; the observed job-detail H2 is the role.
        jobvite_title = soup.select_one('h2.jv-header') if soup.select_one('.jv-job-detail-description') else None
        title = jobvite_title or soup.h1
        result = {"description": description_text(description if description else soup),
                  "title": title.get_text(" ", strip=True) if title else "", "company": "", "location": ""}
        if posting:
            result["description"] = description_text(BeautifulSoup(posting.get("description", ""), "html.parser")) or result["description"]
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
        for frame in self.page.frames:
            if not application_frame(self.page, frame):
                continue
            if await frame.locator('input[type="email"]:visible, input[type="file"]:visible, input[name="first_name"]:visible').count():
                await self.wait_form_controls()
                return
        ambiguous_entry = False
        for role in ["button", "link"]:
            button = self.page.get_by_role(role, name=re.compile(r"^(Apply for this job|Apply now|Apply to this job|Apply)$", re.I))
            if await button.count() > 1 and role == 'link':
                # Duplicate header/footer links (Lever) are one entry when they share a target.
                hrefs = {await button.nth(n).evaluate("e => e.href || ''") for n in range(await button.count())}
                visible = button.filter(visible=True)
                if len(hrefs) == 1 and '' not in hrefs and await visible.count():
                    button = visible.first
            ambiguous_entry |= await button.count() > 1
            if await button.count() == 1 and await button.is_visible():
                if not getattr(self.page, '_autoapply_generic_entry_attempted', False):
                    self.page._autoapply_generic_entry_attempted = True
                    self.page._autoapply_entry_interaction_at = time.monotonic()
                    await click_element(self.page, button)
                await self.wait_form_controls()
                return
        if ambiguous_entry:
            raise self.transition_error('APPLICATION_ENTRY_MODE_UNRESOLVED',
                'Multiple application entry controls; unrelated page fields cannot establish applicant-form readiness')
        # The form may hydrate without an explicit entry control.
        await self.wait_form_controls()

    async def transition_sample(self):
        from .browser import classify_page_condition
        from .security import SecurityDetector
        security, snapshot = await SecurityDetector().detect(self.page, getattr(self.page, '_autoapply_observation', None))
        condition, reason = classify_page_condition(security, snapshot)
        if condition:
            raise self.transition_error('AUTH_REQUIRED' if str(condition) == 'AUTH_REQUIRED' else
                                        'SECURITY_CONTROL' if security.blocking else 'FORM_TERMINAL_FAILURE', reason)
        sample = await self.page.evaluate('''() => {
          const visible=e=>!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
          // Scroll/reading progress bars and collapsed (zero-area) indicators are not loading.
          const busy=e=>{const r=e.getBoundingClientRect();return visible(e)&&r.width>0&&r.height>0&&!/scroll|reading/i.test(e.getAttribute('aria-label')||'');};
          return {loading:[...document.querySelectorAll('[aria-busy=true],[role=progressbar],[class*=spinner],[class*=skeleton],[data-automation-id*=loading]')].some(busy),
                  fields:[...document.querySelectorAll('input:not([type=hidden]):not([type=password]),textarea,select')].filter(visible).length,
                  documentReady:document.readyState};
        }''')
        # Playwright locators pierce open shadow roots; plain querySelector does not.
        sample['fields'] = 0
        for frame in self.page.frames:
            if not application_frame(self.page, frame):
                continue
            sample['fields'] += await frame.locator(','.join(part + ':visible' for part in READY_SELECTOR.split(','))).count()
            sample['loading'] |= await frame.locator('[aria-busy=true]:visible,[role=progressbar]:not([aria-label*=scroll i]):not([aria-label*=reading i]):visible,[class*=spinner]:visible,[class*=skeleton]:visible').count() > 0
        return sample

    async def settled_questions(self, app):
        """Reconcile conditional fields before holding or moving to another step.

        Observe only: no repeated field input, upload or Next click. Busy frames
        receive the normal patience budget; schema stability ignores values and
        animation mutations. A stable empty inventory never means a ready form.
        """
        from playwright.async_api import Error as BrowserError
        from .form_progress import inventory_schema
        wait = TransitionPatience(page_policy(self.page))
        previous, stable_since = None, None
        try:
            async with asyncio.timeout(wait.hard_remaining):
                while wait.remaining:
                    try:
                        sample = await self.transition_sample()
                        questions = await self.get_questions(app) if sample['fields'] and not sample['loading'] else []
                    except BrowserError as exc:
                        if 'Execution context was destroyed' not in str(exc):
                            raise
                        previous, stable_since = None, None
                        await asyncio.sleep(wait.policy.poll_seconds)
                        continue
                    schema = inventory_schema(questions)
                    wait.observe(progress=(sample['loading'], len(questions)),
                                 failure='INVENTORY_PENDING',
                                 summary={'loading': sample['loading'], 'fields': len(questions)})
                    if not sample['loading'] and questions:
                        if schema != previous or stable_since is None:
                            stable_since = time.monotonic()
                        if time.monotonic() - stable_since >= wait.policy.stable_seconds:
                            wait.ready()
                            return questions
                    else:
                        stable_since = None
                    previous = schema
                    await asyncio.sleep(min(wait.policy.poll_seconds, max(.001, wait.remaining)))
        except TimeoutError:
            pass
        error = self.transition_error('FORM_NOT_READY', 'Bounded conditional inventory observation exhausted')
        error.transient_readiness = wait.telemetry()
        raise error

    async def wait_form_controls(self, *, before=None, timeout=None, interaction_at=None):
        """Observe an entry/step once delivered; never repeat its input."""
        from playwright.async_api import Error as BrowserError
        from .inspection import dom_revision, wait_for_dom_change
        wait = TransitionPatience(page_policy(self.page), timeout=timeout, interaction_at=interaction_at or
                                 getattr(self.page, '_autoapply_entry_interaction_at', None))
        previous, stable_since = None, None
        try:
            async with asyncio.timeout(wait.hard_remaining):
                while wait.remaining:
                    try:
                        marker = await dom_revision(self.page)
                        sample = await self.transition_sample()
                        signature = await self.step_signature()
                        action, _ = await self.action()
                    except BrowserError as exc:
                        if 'Execution context was destroyed' not in str(exc):
                            raise
                        previous, stable_since = None, None
                        await asyncio.sleep(wait.policy.poll_seconds)
                        continue
                    summary = dict(sample, action=action)
                    wait.observe(progress=(urlsplit(self.page.url).path, tuple(summary.values())),
                                 failure='READINESS_PENDING', summary=summary)
                    candidate = not sample['loading'] and (sample['fields'] > 0 or action == 'submit')
                    candidate &= before is None or signature != before
                    if candidate:
                        # Busy state and signature are separate observations;
                        # hydration can finish between them. A newly ready
                        # candidate must start stability even with the same DOM.
                        if previous != signature or stable_since is None:
                            stable_since = time.monotonic()
                        if stable_since is not None and time.monotonic()-stable_since >= .2:
                            wait.ready()
                            self.emit('BROWSER_TRANSITION_READINESS', wait.telemetry())
                            return
                    else:
                        stable_since = None
                    previous = signature
                    await wait_for_dom_change(self.page, marker, min(wait.policy.poll_seconds, max(.001,wait.remaining))*1000)
        except TimeoutError:
            pass
        self.emit('BROWSER_TRANSITION_READINESS', wait.telemetry())
        error = self.transition_error('STEP_TRANSITION_UNCONFIRMED' if before is not None else 'FORM_NOT_READY',
                                      'Bounded readiness observation exhausted; no blind retry')
        error.transient_readiness = wait.telemetry()
        raise error

    async def external_application(self):
        # Only explicit application links are followed, never arbitrary job-description links.
        for role in ["link"]:
            links = self.page.get_by_role(role, name=re.compile(r"^(Apply on company (?:website|site)|Apply externally|External application)$", re.I))
            if await links.count() == 1:
                href = await links.get_attribute("href")
                if href and href.startswith("https://"):
                    return href
        return None

    @step_inventory
    async def get_questions(self, app):
        from .answers import scope_for
        from .consent import dismiss_overlay
        await dismiss_overlay(self.page, click_element)
        questions, occurrences = [], {}
        self.controls = {}
        for frame_index, frame in enumerate(self.page.frames):
            if not application_frame(self.page, frame):
                continue
            for item in await frame.evaluate(self.field_script):
                if not item["label"]:
                    if not item["required"]:
                        continue  # Optional unlabeled extras (Lever card textareas) are left blank.
                    raise UnsupportedForm("An application field has no reliable accessible label")
                if item["kind"] in {"password", "range", "color"}:
                    raise UnsupportedForm("Unsupported or authentication field: " + item["kind"])
                signature = f"{frame_index}|{item['label']}|{item['kind']}"
                occurrence = occurrences.get(signature, 0)
                occurrences[signature] = occurrence + 1
                key = hashlib.sha256(f"{signature}|{occurrence}".encode()).hexdigest()[:24]
                self.signatures[key] = {"id":item.get("id"),"token":item["tokens"][0],"label":item["label"]}
                if item["kind"] == "combobox":
                    await self.observe_dropdown(frame, key, item, app)
                if item["kind"] == "radio" and item["label"].strip() in item["options"]:
                    # Lever custom questions: the group "label" is just an option; the
                    # question text lives in the enclosing application-question block.
                    question = await frame.locator('[data-autoapply-field="' + item["tokens"][0] + '"]').evaluate(
                        """e => { const card = e.closest('.application-question') || e.closest('[class*="application-question"]') || e.closest('fieldset');
                                  const label = card && (card.querySelector('.application-label, legend, [class*="label"]:not(label)') || card);
                                  const field = card && card.querySelector('.application-field, ul');
                                  let text = (label && label.innerText) || '';
                                  if (field && label === card) text = text.replace(field.innerText, '');
                                  return text.replace(/[\\u2731*]/g, '').replace(/\\s+/g, ' ').trim(); }""")
                    if question and question not in item["options"]:
                        item["label"] = question
                if item["kind"] == "radio" and len(item["options"]) > 1:
                    # Workable labels each radio "<question> YES"; strip the shared
                    # question so options read YES/NO (token order is unchanged).
                    prefix = item["label"].strip()
                    if prefix and all(o.strip().startswith(prefix) and o.strip() != prefix for o in item["options"]):
                        stripped = [o.strip()[len(prefix):].strip() for o in item["options"]]
                        if all(stripped) and len(set(stripped)) == len(stripped):
                            item["options"] = stripped
                            if item.get("value", "").strip().startswith(prefix):
                                item["value"] = item["value"].strip()[len(prefix):].strip()
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

    async def observe_dropdown(self, frame, key, item, app):
        from .answers import scope_for
        from .security import SecurityDetector
        security, _ = await SecurityDetector().detect(self.page, getattr(self.page, '_autoapply_observation', None))
        if security.blocking:
            raise UnsupportedForm('Interactive security requirement before dropdown inspection')
        # Semantic options participate in Phase 5 answer signatures. Both async
        # preference search and saved-answer search consume this one budget.
        async with asyncio.timeout(10):
            control = frame.locator('[data-autoapply-field="' + item["tokens"][0] + '"]')
            from .combobox import options_for, editable_control
            options = (await options_for(frame, self.signatures[key])).filter(visible=True)
            if not await options.count() and await control.get_attribute('aria-expanded') != 'true':
                # A previously inspected menu is left open for its fill step; it
                # covers later controls, so close other expanded menus first.
                others = frame.locator('[role="combobox"][aria-expanded="true"]:visible')
                for n in range(await others.count()):
                    other = others.nth(n)
                    if await other.get_attribute('data-autoapply-field') != item['tokens'][0]:
                        await other.press('Escape')
                await control.click()
                options = (await options_for(frame, self.signatures[key])).filter(visible=True)
                # Remote-search menus (School, Degree) populate after opening.
                for _ in range(30):
                    if await options.count():
                        break
                    await asyncio.sleep(.1)
            from .dropdowns import query_for, choose_option
            query = query_for(item['label'], self.profile)
            texts = [t.strip() for t in await options.all_text_contents() if t.strip()]
            if query and not choose_option(item['label'], texts, self.profile) and await editable_control(control):
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
                    # Options can arrive in several asynchronous batches.
                    # Observe a bounded quiet interval, waking on mutation.
                    from .inspection import wait_for_dom_change, dom_revision
                    marker = await dom_revision(frame)
                    await wait_for_dom_change(frame, marker, 100)
            hint = self.answer_hints.get(key)
            if hint and hint['raw_question'] == item['label'] and hint['scope'] == scope_for(item['label'], app):
                value = json.loads(hint['answer'])
                exact = frame.get_by_role('option', name=value, exact=True)
                if isinstance(value, str) and await editable_control(control) and await exact.count() == 0:
                    await control.fill(value)
                    try:
                        await exact.wait_for(state='visible', timeout=5000)
                    except Exception:
                        raise UnsupportedForm('Saved answer is not an available exact dropdown option: ' + item['label']) from None
            if await options.count():
                item["options"] = await options.all_text_contents()
                item["options"] = [s.strip() for s in item["options"] if s.strip()]
            # Semantic school/city options are needed by the resolver.
            # Leave this owned menu open for the immediately following
            # fill workflow; do not close and reopen it just for inventory.

    async def answer_question(self, q, answer):
        from .answers import validate_answer
        from .security import SecurityDetector
        security, _ = await SecurityDetector().detect(self.page, getattr(self.page, '_autoapply_observation', None))
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
            if q.kind in {'select', 'multiselect'} and q.key in self.signatures:
                live = await field.evaluate("e=>[...e.options].filter(o=>!o.disabled&&o.value!=='').map(o=>o.text.trim())")
                if live != q.options:
                    raise InventoryChanged('Dropdown options changed after inventory; resolve the current question again')
            if q.kind == 'combobox' and q.options and q.key in self.signatures:
                from .combobox import options_for
                live = [s.strip() for s in await (await options_for(frame, signature)).filter(visible=True).all_text_contents() if s.strip()]
                if live and live != q.options:
                    raise InventoryChanged('Dropdown options changed after inventory; resolve the current question again')
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
            if not await target.evaluate("e => !!e.checked || e.getAttribute('aria-checked') === 'true'"):
                await target.click()
            if not await target.evaluate("e => !!e.checked || e.getAttribute('aria-checked') === 'true'"):
                raise UnsupportedForm("Radio answer was not retained")
        elif q.kind == "checkbox":
            async def checked():
                return await field.evaluate("e => 'checked' in e ? e.checked : e.getAttribute('aria-checked') === 'true'")
            if await checked() != (value == "Yes"):
                await field.click()
            if await checked() != (value == "Yes"):
                raise UnsupportedForm("Checkbox answer was not retained")
        else:
            async def text_value():
                return await field.evaluate("e => e.isContentEditable ? e.textContent : e.value")
            def retained(current):
                if current == value:
                    return True
                # Phone widgets reformat (spaces, dashes, parentheses, +country code);
                # the same digits, optionally with a 1-3 digit country prefix, are retained.
                if q.kind == 'tel' or re.search(r'phone|mobile|cell', q.label, re.I):
                    want, got = re.sub(r'\D', '', value), re.sub(r'\D', '', current or '')
                    return len(want) >= 7 and (got == want or (got.endswith(want) and len(got) - len(want) <= 3)
                                               or (want.endswith(got) and len(want) - len(got) <= 3 and len(got) >= 7))
                return ' '.join(str(current or '').split()) == ' '.join(str(value).split())
            if not retained(await text_value()):
                await field.fill(value)
            if not retained(await text_value()):
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
              return {value:e.isContentEditable?e.textContent:e.value||'',checked:!!e.checked,ariaChecked:e.getAttribute('aria-checked'),
                expanded:e.getAttribute('aria-expanded'),invalid:e.getAttribute('aria-invalid'),
                selected:[...(root?.querySelectorAll('[class*="singleValue"],[class*="single-value"],[data-selected-value],[class*="multi-value__label"],[class*="multiValueLabel"],input[type=hidden]')||[])].map(n=>n.value||n.textContent.trim())};
            }'''))
        return result

    async def upload_documents(self, q, path):
        # Selection, ATS acceptance and subsequent readiness share this deadline.
        from .operations import FileSnapshot
        snapshot = FileSnapshot.read(path, force=True)
        receipts = getattr(self.page, '_autoapply_document_snapshots', {})
        self.page._autoapply_document_snapshots = receipts
        if q.key in self.uploads:
            if receipts.get(q.key) != snapshot:
                tracker = getattr(self.page, '_autoapply_uploads', None)
                if tracker:
                    tracker.receipt = None
                    tracker.result = dict(ready=False, category='UPLOAD_FAILED', reason='Resume changed; fresh upload required')
                raise UnsupportedForm('Resume changed; fresh upload required in a new document')
            return
        async with asyncio.timeout(180):
            await self._upload_documents(q, path)
        if FileSnapshot.read(path, force=True) != snapshot:
            raise UnsupportedForm('Resume changed during upload; fresh upload required')
        receipts[q.key] = snapshot

    async def _upload_documents(self, q, path):
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
            }''', arg=q.key, timeout=max(1,(tracker.deadline-time.monotonic())*1000) if tracker else 180000)
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
            if not application_frame(self.page, frame):
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

    async def step_signature(self):
        data = [self.page.url]
        for frame in self.page.frames:
            if not application_frame(self.page, frame):
                continue
            data.append(await frame.locator(CONTROL_SELECTOR + ',form,h1,h2,h3,[aria-current=step]').evaluate_all(
                """es=>{
                  if(!window.__autoapplyStepNodes)window.__autoapplyStepNodes={ids:new WeakMap(),next:0};
                  const g=window.__autoapplyStepNodes;
                  return es.filter(e=>e.getClientRects().length).map(e=>{
                    if(!g.ids.has(e))g.ids.set(e,++g.next);
                    return [g.ids.get(e),e.tagName,e.type,e.id,e.name,e.getAttribute('aria-label'),
                      e.matches('h1,h2,h3,[aria-current=step]')?e.textContent:''];
                  });
                }"""))
        return json.dumps(data, sort_keys=True)

    def transition_error(self, category, detail):
        error = UnsupportedForm(category + ': ' + detail)
        error.category = category
        return error

    async def _stable_step(self, expected, seconds, deadline):
        from .inspection import dom_revision, wait_for_dom_change
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            marker = await dom_revision(self.page)
            await wait_for_dom_change(self.page, marker, min(end-time.monotonic(), deadline.remaining)*1000)
            if await self.step_signature() != expected:
                return False
        return True

    def transitioned(self):
        self.live_committed.clear()
        self.controls = {}
        self._step_snapshot = None

    async def advance(self, timeout_seconds=None):
        from .operations import Deadline
        deadline = Deadline.after(max(page_policy(self.page).grace, timeout_seconds or page_policy(self.page).timeout))
        delivered = False
        try:
            async with asyncio.timeout(deadline.remaining):
                before = await self.step_signature()
                while not await self._stable_step(before, .15, deadline):
                    await self.transition_sample()  # Security/auth can stop even during stabilization.
                    before = await self.step_signature()
                self.emit('STEP_NEXT_INTENT', {})
                kind, target = await self.action()
                if kind != 'next':
                    raise self.transition_error('MULTI_STEP_NAVIGATION', 'Fresh Next control missing')
                for event in ('STEP_NEXT_TARGET_FOUND', 'STEP_NEXT_VISIBLE', 'STEP_NEXT_ENABLED'):
                    self.emit(event, {})
                # Mark delivery as ambiguous BEFORE awaiting input. Never retry.
                delivered = True
                interaction_at = time.monotonic()
                try:
                    await click_element(self.page, target)
                except Exception as exc:
                    self.emit('STEP_NEXT_FAILURE', {'error_type':type(exc).__name__, 'retry_performed':False})
                    raise self.transition_error('MULTI_STEP_NAVIGATION',
                        f'{type(exc).__name__}; inspect delivery and current step before retry') from exc
                self.emit('STEP_NEXT_CLICK_DELIVERED', {})
            # Start the observation window after input finishes; time spent
            # resolving/clicking must not consume the minimum failure grace.
            await self.wait_form_controls(before=before, timeout=timeout_seconds, interaction_at=interaction_at)
            self.transitioned()
            self.emit('STEP_TRANSITION_OBSERVED', {})
            self.emit('STEP_ADVANCED', {})
            return
        except TimeoutError as exc:
            raise self.transition_error('STEP_TRANSITION_UNCONFIRMED' if delivered else 'STEP_UNSTABLE',
                'Next delivery/transition is unconfirmed; inspect before retry') from exc
        raise self.transition_error('STEP_TRANSITION_UNCONFIRMED', 'No stable transition observed; inspect before retry')


class GreenhouseAdapter(GenericApplicationAdapter):
    name = "greenhouse"


class LeverAdapter(GenericApplicationAdapter):
    name = "lever"

    async def begin(self):
        link = self.page.get_by_role("link", name=re.compile(r"^Apply for this job$", re.I))
        if await link.count() == 1 and await link.is_visible():
            if not getattr(self.page, '_autoapply_generic_entry_attempted', False):
                self.page._autoapply_generic_entry_attempted = True
                self.page._autoapply_entry_interaction_at = time.monotonic()
                await click_element(self.page, link)
            await self.wait_form_controls()
        else:
            await super().begin()


class AshbyAdapter(GenericApplicationAdapter):
    name = "ashby"

    async def begin(self):
        tab = self.page.get_by_role("tab", name="Application", exact=True)
        try:
            await tab.wait_for(state="visible", timeout=page_policy(self.page).timeout * 1000)
        except Exception:
            tab = None
        if tab is not None:
            await click_element(self.page, tab)
            await self.wait_form_controls(interaction_at=time.monotonic())
            return
        button = self.page.get_by_role("button", name="Application", exact=True)
        if await button.count() == 1 and await button.is_visible():
            await click_element(self.page, button)
            await self.wait_form_controls(interaction_at=time.monotonic())
        else:
            await super().begin()


def adapter_for(page):
    name, _ = ats_identity(page.url)
    from .providers import detect_ats
    if name == 'workday' or detect_ats(page.url).provider == 'workday':
        from .workday import WorkdayAdapter
        return WorkdayAdapter(page)
    if name == 'smartrecruiters':
        from .smartrecruiters import SmartRecruitersAdapter
        return SmartRecruitersAdapter(page)
    cls = {"greenhouse": GreenhouseAdapter, "lever": LeverAdapter, "ashby": AshbyAdapter}.get(name, GenericApplicationAdapter)
    return cls(page)
