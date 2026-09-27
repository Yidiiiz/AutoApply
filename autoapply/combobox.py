"""State-driven dropdown selection. Never handles final submission controls."""
import asyncio
import json
import time
from playwright.async_api import expect
from .operations import Deadline


class DropdownStateError(RuntimeError):
    category = 'PRE_SUBMIT_DROPDOWN_STATE_FAILURE'


async def signature_for(field, label):
    return await field.evaluate('(e,label)=>({id:e.id,label,token:e.getAttribute("data-autoapply-field")})', label)


def resolve_field(frame, signature):
    if signature.get('id'):
        return frame.locator('[id=' + json.dumps(signature['id']) + ']:visible')
    if signature.get('token'):
        return frame.locator('[data-autoapply-field=' + json.dumps(signature['token']) + ']:visible')
    return frame.get_by_label(signature['label'], exact=True)


async def options_for(frame, signature):
    control = resolve_field(frame, signature)
    ids = (await control.get_attribute('aria-controls') or await control.get_attribute('aria-owns') or '').split()
    if ids:
        roots = frame.locator(','.join('[id=' + json.dumps(i) + ']:visible' for i in ids))
        return roots.get_by_role('option')
    return frame.get_by_role('option')


async def snapshot(frame, signature, desired):
    control = resolve_field(frame, signature)
    result = {'label': signature['label'], 'control_count': await control.count()}
    if result['control_count'] == 1:
        result.update(expanded=await control.get_attribute('aria-expanded'),
                      enabled=await control.is_enabled())
        options = await options_for(frame, signature)
        result.update(visible_options=await options.all_text_contents(),
                      exact_options=await options.filter(has_text=__import__('re').compile('^'+__import__('re').escape(desired)+'$')).count())
    return result


async def committed_state(frame, signature, desired):
    return await resolve_field(frame, signature).evaluate(r'''(e,value)=>{
      const root=e.closest('[class*="select__control"], [class*="select-control"]')||e.parentElement?.parentElement;
      const labels=[...(root?.querySelectorAll('[class*="singleValue"],[class*="single-value"],[class*="multi-value__label"],[class*="multiValueLabel"],[data-selected-value],:scope > span')||[])].map(n=>n.textContent.trim()).filter(Boolean);
      const ids=(e.getAttribute('aria-controls')||e.getAttribute('aria-owns')||'').split(/\s+/).filter(Boolean);
      const scope=e.getRootNode();
      const menus=ids.length?ids.map(id=>scope.getElementById(id)).filter(Boolean):[...scope.querySelectorAll('[role=listbox]')];
      const visible=n=>!!n.getClientRects().length&&getComputedStyle(n).visibility!=='hidden'&&getComputedStyle(n).visibility!=='collapse';
      const options=menus.length?menus.flatMap(n=>[...n.querySelectorAll('[role=option]')]):[...scope.querySelectorAll('[role=option]')];
      const selectable=options.some(n=>visible(n)&&!n.closest('[hidden],[inert],[aria-hidden=true],[aria-disabled=true]')&&!n.matches(':disabled'));
      const menuVisible=menus.some(visible);
      // A hidden menu is affirmative closure evidence even with stale ARIA.
      // A visible empty container requires collapse evidence; an expanded,
      // asynchronously loading menu is still open.
      const closed=!selectable&&(e.getAttribute('aria-expanded')==='false'||
        (menus.length>0&&!menuVisible));
      return {expanded:e.getAttribute('aria-expanded'),closed,invalid:e.getAttribute('aria-invalid')==='true'||!!(e.validity&&!e.validity.valid),
        value:e.value===value,selected:labels.length===1&&labels[0]===value};
    }''',desired)


async def close_committed(frame, signature, desired, deadline):
    state=await committed_state(frame,signature,desired)
    if not state['selected'] or state['invalid']:
        return False
    if not state['closed']:
        await resolve_field(frame,signature).press('Escape',timeout=deadline.milliseconds())
        await expect(resolve_field(frame,signature)).to_have_attribute('aria-expanded','false',timeout=deadline.milliseconds())
    state=await committed_state(frame,signature,desired)
    return state['selected'] and state['closed'] and not state['invalid']


async def _select_combobox(frame, signature, desired, deadline):
    """Fresh locators after each UI operation; one option click, no blind retry."""
    started = time.monotonic()
    try:
        await expect(resolve_field(frame, signature)).to_be_visible(timeout=deadline.milliseconds())
        await expect(resolve_field(frame, signature)).to_be_enabled(timeout=deadline.milliseconds())
        tag = await resolve_field(frame, signature).evaluate('e=>e.tagName')
        if tag == 'SELECT':
            await resolve_field(frame, signature).select_option(label=desired, timeout=deadline.milliseconds())
            selected = await resolve_field(frame, signature).locator('option:checked').all_text_contents()
            expected = desired if isinstance(desired, list) else [desired]
            if sorted(selected) != sorted(expected):
                raise ValueError('Native selection did not commit')
            return {'committed':True,'mechanism':'native_select','elapsed_ms':round((time.monotonic()-started)*1000)}
        if await close_committed(frame,signature,desired,deadline):
            return {'committed':True,'mechanism':'retained_exact_value','elapsed_ms':round((time.monotonic()-started)*1000)}
        if await resolve_field(frame, signature).get_attribute('aria-expanded') != 'true':
            await resolve_field(frame, signature).click(timeout=deadline.milliseconds())
        # Filter editable React controls to the intended option so a long menu
        # cannot move the click target as its internal scroll position changes.
        if await resolve_field(frame, signature).is_editable():
            await resolve_field(frame, signature).fill(desired,timeout=deadline.milliseconds())
        if await resolve_field(frame, signature).get_attribute('aria-expanded') is not None:
            await expect(resolve_field(frame, signature)).to_have_attribute('aria-expanded','true',timeout=deadline.milliseconds())
        await expect((await options_for(frame, signature)).first).to_be_visible(timeout=deadline.milliseconds())
        options = await options_for(frame, signature)
        import re
        matching = options.filter(has_text=re.compile('^'+re.escape(desired)+'$'))
        await expect(matching).to_have_count(1,timeout=deadline.milliseconds())
        await expect(matching).to_be_visible(timeout=deadline.milliseconds())
        await expect(matching).to_be_enabled(timeout=deadline.milliseconds())
        # Re-resolve after search/rerender; Playwright waits for actionable geometry.
        matching = (await options_for(frame, signature)).filter(has_text=re.compile('^'+re.escape(desired)+'$'))
        await matching.click(timeout=deadline.milliseconds())
        while deadline.remaining:
            current=resolve_field(frame,signature)
            if await current.count()==1:
                if await close_committed(frame,signature,desired,deadline):
                    return {'committed':True,'mechanism':'exact_selected_label','elapsed_ms':round((time.monotonic()-started)*1000)}
                state=await committed_state(frame,signature,desired)
                if state['closed'] and not state['invalid'] and (state['value'] and state['expanded']=='false' or state['selected']):
                    return {'committed':True,'mechanism':'exact_option','elapsed_ms':round((time.monotonic()-started)*1000)}
                if state['closed'] and not state['invalid'] and not await (await options_for(frame,signature)).count():
                    # Flag/dial-code renderers hide the text. Reopen only to read
                    # the exact option's selected state; never select a second time.
                    await resolve_field(frame,signature).click(timeout=deadline.milliseconds())
                    exact=(await options_for(frame,signature)).filter(has_text=re.compile('^'+re.escape(desired)+'$'))
                    await expect(exact).to_have_count(1,timeout=deadline.milliseconds())
                    retained=await exact.get_attribute('aria-selected')=='true'
                    await resolve_field(frame,signature).press('Escape',timeout=deadline.milliseconds())
                    verified=await committed_state(frame,signature,desired)
                    if retained and verified['closed'] and not verified['invalid']:
                        return {'committed':True,'mechanism':'selected_option_verified','elapsed_ms':round((time.monotonic()-started)*1000)}
                    raise ValueError('Cannot verify custom dropdown selection')
            await asyncio.sleep(.02)  # Bounded state polling, not inter-field pacing.
        raise ValueError('Menu did not close with an exact committed value')
    except Exception as exc:
        diagnostic={'label':signature['label'],'visible_options':[], 'deadline_exhausted':not bool(deadline.remaining)}
        if deadline.remaining:
            try:
                async with asyncio.timeout(deadline.remaining):
                    diagnostic=await snapshot(frame,signature,str(desired))
            except TimeoutError:
                pass
        raise DropdownStateError('Cannot verify custom dropdown selection: '+json.dumps({'state':diagnostic,'error':type(exc).__name__})) from exc


async def open_and_select_combobox(frame, signature, desired, timeout_ms=8000):
    """One absolute budget covers resolution, actionability, search and commit."""
    deadline = Deadline.after(timeout_ms / 1000)
    try:
        async with asyncio.timeout(deadline.remaining):
            return await _select_combobox(frame, signature, desired, deadline)
    except TimeoutError as exc:
        # Diagnostics cannot restart an exhausted browser operation.
        raise DropdownStateError('Cannot verify custom dropdown selection: '+json.dumps({
            'state':{'label':signature['label'],'visible_options':[], 'deadline_exhausted':True},
            'error':'TimeoutError'})) from exc
