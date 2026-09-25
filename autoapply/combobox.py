"""State-driven dropdown selection. Never handles final submission controls."""
import asyncio
import json
import time
from playwright.async_api import expect


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
      const menus=ids.length?ids.map(id=>e.ownerDocument.getElementById(id)).filter(Boolean):[...e.ownerDocument.querySelectorAll('[role=listbox]')];
      const menuVisible=menus.some(n=>!!(n.offsetWidth||n.offsetHeight||n.getClientRects().length));
      return {expanded:e.getAttribute('aria-expanded'),closed:menus.length?!menuVisible:e.getAttribute('aria-expanded')!=='true',invalid:e.getAttribute('aria-invalid')==='true',
        value:e.value===value,selected:labels.length===1&&labels[0]===value};
    }''',desired)


async def close_committed(frame, signature, desired, timeout_ms):
    state=await committed_state(frame,signature,desired)
    if not state['selected'] or state['invalid']:
        return False
    if not state['closed']:
        await resolve_field(frame,signature).press('Escape')
        await expect(resolve_field(frame,signature)).to_have_attribute('aria-expanded','false',timeout=timeout_ms)
    state=await committed_state(frame,signature,desired)
    return state['selected'] and state['closed'] and not state['invalid']


async def open_and_select_combobox(frame, signature, desired, timeout_ms=8000):
    """Fresh locators after each UI operation; one option click, no blind retry."""
    started = time.monotonic()
    try:
        await expect(resolve_field(frame, signature)).to_be_visible(timeout=timeout_ms)
        await expect(resolve_field(frame, signature)).to_be_enabled(timeout=timeout_ms)
        tag = await resolve_field(frame, signature).evaluate('e=>e.tagName')
        if tag == 'SELECT':
            await resolve_field(frame, signature).select_option(label=desired)
            selected = await resolve_field(frame, signature).locator('option:checked').all_text_contents()
            expected = desired if isinstance(desired, list) else [desired]
            if sorted(selected) != sorted(expected):
                raise ValueError('Native selection did not commit')
            return {'committed':True,'mechanism':'native_select','elapsed_ms':round((time.monotonic()-started)*1000)}
        if await close_committed(frame,signature,desired,timeout_ms):
            return {'committed':True,'mechanism':'retained_exact_value','elapsed_ms':round((time.monotonic()-started)*1000)}
        if await resolve_field(frame, signature).get_attribute('aria-expanded') != 'true':
            await resolve_field(frame, signature).click(timeout=timeout_ms)
        if await resolve_field(frame, signature).get_attribute('aria-expanded') is not None:
            await expect(resolve_field(frame, signature)).to_have_attribute('aria-expanded','true',timeout=timeout_ms)
        # Filter editable React controls to the intended option so a long menu
        # cannot move the click target as its internal scroll position changes.
        if await resolve_field(frame, signature).is_editable():
            await resolve_field(frame, signature).fill(desired,timeout=timeout_ms)
        await expect((await options_for(frame, signature)).first).to_be_visible(timeout=timeout_ms)
        options = await options_for(frame, signature)
        import re
        matching = options.filter(has_text=re.compile('^'+re.escape(desired)+'$'))
        await expect(matching).to_have_count(1,timeout=timeout_ms)
        await expect(matching).to_be_visible(timeout=timeout_ms)
        await expect(matching).to_be_enabled(timeout=timeout_ms)
        # Re-resolve after search/rerender; Playwright waits for actionable geometry.
        matching = (await options_for(frame, signature)).filter(has_text=re.compile('^'+re.escape(desired)+'$'))
        await matching.click(timeout=timeout_ms)
        deadline=time.monotonic()+timeout_ms/1000
        while time.monotonic()<deadline:
            current=resolve_field(frame,signature)
            if await current.count()==1:
                if await close_committed(frame,signature,desired,timeout_ms):
                    return {'committed':True,'mechanism':'exact_selected_label','elapsed_ms':round((time.monotonic()-started)*1000)}
                state=await committed_state(frame,signature,desired)
                if state['closed'] and not state['invalid'] and (state['value'] and state['expanded']=='false' or state['selected']):
                    return {'committed':True,'mechanism':'exact_option','elapsed_ms':round((time.monotonic()-started)*1000)}
                if state['closed'] and not state['invalid'] and not await (await options_for(frame,signature)).count():
                    # Flag/dial-code renderers hide the text. Reopen only to read
                    # the exact option's selected state; never select a second time.
                    await resolve_field(frame,signature).click(timeout=timeout_ms)
                    exact=(await options_for(frame,signature)).filter(has_text=re.compile('^'+re.escape(desired)+'$'))
                    await expect(exact).to_have_count(1,timeout=timeout_ms)
                    retained=await exact.get_attribute('aria-selected')=='true'
                    await resolve_field(frame,signature).press('Escape')
                    if retained:
                        return {'committed':True,'mechanism':'selected_option_verified','elapsed_ms':round((time.monotonic()-started)*1000)}
                    raise ValueError('Cannot verify custom dropdown selection')
            await asyncio.sleep(.02)  # Bounded state polling, not inter-field pacing.
        raise ValueError('Menu did not close with an exact committed value')
    except Exception as exc:
        diagnostic=await snapshot(frame,signature,str(desired))
        raise DropdownStateError('Cannot verify custom dropdown selection: '+json.dumps({'state':diagnostic,'error':type(exc).__name__})) from exc
