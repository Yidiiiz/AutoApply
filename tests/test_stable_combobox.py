import pytest
from autoapply.browser import Browser
from autoapply.combobox import open_and_select_combobox, committed_state, DropdownStateError


@pytest.mark.browser
async def test_replaced_control_delayed_options_hidden_duplicate_and_commit(config):
    browser=Browser(config)
    try:
        page=await browser.new_page()
        await page.set_content('''<input id="field" role="combobox" aria-expanded="true" aria-controls="menu">
        <div id="menu" role="listbox"></div><div role="option" hidden>Desired option</div>
        <script>window.clicks=0;window.opens=0;
        function wire(){const f=document.querySelector('#field');
          f.onclick=()=>{window.opens++;f.setAttribute('aria-expanded','true')};
          f.oninput=()=>{const replacement=f.cloneNode(true);f.replaceWith(replacement);wire();
            setTimeout(()=>{document.querySelector('#menu').innerHTML='<div role="option">Desired option</div>';
              document.querySelector('#menu>div').onclick=()=>{window.clicks++;setTimeout(()=>{const c=document.querySelector('#field');c.value='Desired option';c.setAttribute('aria-expanded','false');document.querySelector('#menu').innerHTML=''},80)};
            },80)};
        }wire();</script>''')
        result=await open_and_select_combobox(page.main_frame,{'id':'field','label':'Example'},'Desired option')
        assert result['committed']
        assert await page.evaluate('window.clicks')==1
        assert await page.evaluate('window.opens')==0
        assert await page.locator('#field').input_value()=='Desired option'
    finally:
        await browser.close()


@pytest.mark.browser
@pytest.mark.parametrize('failure', ['open', 'wrong', 'invalid', 'no-collapse-evidence'])
async def test_selection_requires_real_commitment(config, failure):
    browser = Browser(config)
    try:
        page = await browser.new_page()
        await page.set_content('''<input id="f" role="combobox" aria-expanded="true" aria-controls="menu">
        <div id="menu" role="listbox"><div role="option">Desired</div></div>
        <script>window.clicks=0;document.querySelector('[role=option]').onclick=()=>{
          window.clicks++;const f=document.querySelector('#f');
          if(window.failure==='wrong')f.value='Wrong';
          if(window.failure==='invalid')f.setAttribute('aria-invalid','true');
          if(window.failure!=='open'){
            document.querySelector('#menu').innerHTML='';
            if(window.failure==='no-collapse-evidence')f.removeAttribute('aria-expanded');
            else f.setAttribute('aria-expanded','false');
          }
        };</script>''')
        await page.evaluate('(v)=>window.failure=v', failure)
        with pytest.raises(DropdownStateError):
            await open_and_select_combobox(page.main_frame, {'id':'f','label':'Test'}, 'Desired', timeout_ms=250)
        assert await page.evaluate('window.clicks') == 1
    finally:
        await browser.close()


@pytest.mark.browser
@pytest.mark.parametrize('expanded,options,hidden,closed', [
    ('false', '', '', True),
    ('false', '<div role="option" hidden>Desired</div>', '', True),
    ('false', '<div role="option" style="visibility:hidden">Desired</div>', '', True),
    ('true', '', '', False),  # Async loading is not a closed menu.
    ('true', '<div role="option">Desired</div>', '', False),
    ('false', '<div role="option">Desired</div>', '', False),  # Contradictory ARIA.
    ('true', '<div role="option">Desired</div>', 'hidden', True),
])
async def test_retained_menu_commitment_evidence(config, expanded, options, hidden, closed):
    browser = Browser(config)
    try:
        page = await browser.new_page()
        await page.set_content(f'''<input id="f" role="combobox" value="Desired"
          aria-expanded="{expanded}" aria-controls="menu">
          <div id="menu" role="listbox" {hidden} style="width:200px;height:30px">{options}</div>''')
        state = await committed_state(page.main_frame, {'id':'f','label':'Test'}, 'Desired')
        assert state['value'] and not state['invalid']
        assert state['closed'] is closed
    finally:
        await browser.close()


@pytest.mark.browser
async def test_missing_option_reports_state_without_click(config):
    browser=Browser(config)
    try:
        page=await browser.new_page()
        await page.set_content('<input id="f" role="combobox" aria-expanded="true"><div role="option">Wrong</div>')
        with pytest.raises(DropdownStateError,match='visible_options'):
            await open_and_select_combobox(page.main_frame,{'id':'f','label':'Test'},'Expected',timeout_ms=200)
    finally:
        await browser.close()


@pytest.mark.browser
async def test_native_select(config):
    browser=Browser(config)
    try:
        page=await browser.new_page()
        await page.set_content('<select id="f"><option>No</option><option>Yes</option></select>')
        assert (await open_and_select_combobox(page.main_frame,{'id':'f','label':'Test'},'Yes'))['committed']
    finally:
        await browser.close()

@pytest.mark.browser
@pytest.mark.parametrize('already_selected',[True,False])
async def test_multiselect_chip_keeps_menu_open(config, already_selected):
    browser=Browser(config)
    try:
        page=await browser.new_page()
        await page.set_content('''<div class="select__control"><div id="chips"></div>
        <input id="f[]" role="combobox" aria-expanded="true" aria-controls="menu"></div>
        <div id="menu" role="listbox"><div role="option">Desired</div></div>
        <script>window.clicks=0;const f=document.querySelector('input');
        function commit(){document.querySelector('#chips').innerHTML='<span class="select__multi-value__label">Desired</span>';document.querySelector('#menu').innerHTML='<div role="option">Other</div>';f.value=''}
        document.querySelector('[role=option]').onclick=()=>{window.clicks++;commit()};
        f.onkeydown=e=>{if(e.key==='Escape'){f.setAttribute('aria-expanded','false');document.querySelector('#menu').hidden=true}};
        </script>''')
        if already_selected:
            await page.evaluate('commit()')
        result=await open_and_select_combobox(page.main_frame,{'id':'f[]','label':'Example'},'Desired')
        assert result['committed']
        assert await page.evaluate('window.clicks') == (0 if already_selected else 1)
        assert await page.locator('input').get_attribute('aria-expanded')=='false'
        assert await page.locator('.select__multi-value__label').text_content()=='Desired'
    finally:
        await browser.close()
