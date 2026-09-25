import pytest
from autoapply.browser import Browser
from autoapply.applications import GreenhouseAdapter
from autoapply.models import Answer


@pytest.mark.browser
async def test_open_async_city_menu_is_committed_without_toggle(config):
    browser = Browser(config)
    try:
        page = await browser.new_page()
        await page.set_content('''<form><label for="city">Location (City)</label>
        <input id="city" role="combobox" aria-expanded="true" aria-controls="choices" required>
        <div id="choices" role="listbox"></div><input id="committed" type="hidden">
        <script>
        const city=document.querySelector('#city'), menu=document.querySelector('#choices');
        window.toggles=0;
        city.onclick=()=>{window.toggles++;city.setAttribute('aria-expanded',city.getAttribute('aria-expanded')==='true'?'false':'true')};
        city.oninput=()=>setTimeout(()=>{
          menu.innerHTML='';
          for(const name of ['College Park, Georgia, United States','College Park, Maryland, United States']){
            const o=document.createElement('div');o.role='option';o.textContent=name;
            o.onclick=()=>{city.value=name;document.querySelector('#committed').value=name;city.setAttribute('aria-expanded','false');menu.innerHTML=''};menu.append(o);
          }
        },200);
        city.onkeydown=e=>{if(e.key==='Escape'){city.setAttribute('aria-expanded','false');menu.innerHTML=''}};
        </script></form>''')
        adapter=GreenhouseAdapter(page)
        adapter.profile={'contact':{'city':'College Park','state':'Maryland'}}
        qs=await adapter.get_questions({'id':1})
        assert qs[0].options == ['College Park, Georgia, United States','College Park, Maryland, United States']
        assert await page.evaluate('window.toggles') == 0
        await adapter.answer_question(qs[0],Answer('College Park, Maryland, United States','USER_PROVIDED'))
        assert await page.locator('#committed').input_value() == 'College Park, Maryland, United States'
        assert not await adapter.validate()
    finally:
        await browser.close()
