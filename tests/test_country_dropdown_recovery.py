"""Display labels containing dial codes need not be valid menu search queries."""
import hashlib
import json
import pytest
from autoapply.applications import GenericApplicationAdapter
from autoapply.answers import scope_for
from autoapply.engine import Engine
from autoapply.models import Answer, Question


@pytest.mark.parametrize('saved_hint', [False, True])
async def test_country_display_label_can_be_selected_when_search_excludes_dial_code(config, db, saved_hint):
    engine = Engine(config, db, fill_only=True)
    try:
        page = await engine.browser.new_page()
        await page.set_content('''<form><label for="country">Country</label>
        <div><span id="selected"></span><div><input id="country" role="combobox" aria-expanded="false" aria-controls="countries"></div></div>
        <div id="countries" role="listbox" hidden></div></form><script>
        const input=document.querySelector('input'),list=document.querySelector('[role=listbox]');
        const names=['United States +1',...Array.from({length:22},(_,i)=>'Other country '+i+' +2')];
        function render(query){list.replaceChildren();for(const name of names){
          const searchable=name.split(' +')[0];
          if(query && !searchable.toLowerCase().includes(query.toLowerCase()))continue;
          const option=document.createElement('div');option.setAttribute('role','option');option.textContent=name;
          option.onclick=()=>{document.querySelector('#selected').textContent=name;input.value='';list.hidden=true;input.setAttribute('aria-expanded','false')};list.append(option);
        }}
        render('');input.onclick=()=>{list.hidden=false;input.setAttribute('aria-expanded','true')};
        window.queries=[];input.oninput=()=>{window.queries.push(input.value);render(input.value)};
        </script>''')
        adapter = GenericApplicationAdapter(page)
        app = {'id':1,'company':'Example'}
        key = hashlib.sha256(b'0|Country|combobox|0').hexdigest()[:24]
        if saved_hint:
            adapter.answer_hints[key]={'raw_question':'Country','scope':scope_for('Country',app),'answer':json.dumps('United States +1')}
        questions = await adapter.get_questions(app)
        q = questions[0]
        assert len(q.options)==23 and 'United States +1' in q.options
        assert await page.evaluate('window.queries') == []
        await adapter.answer_question(q, Answer('United States +1','user_confirmed'))
        assert await page.locator('#selected').inner_text() == 'United States +1'
        assert await page.evaluate('window.queries') == ['United States +1','']
        assert not db.rows("SELECT * FROM events WHERE kind='SUBMITTING'")
    finally:
        await engine.close()
