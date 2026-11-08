// Execute shipped queue/cancellation functions with synthetic transport and controls.
const fs=require('node:fs'),assert=require('node:assert/strict');
const content=fs.readFileSync('extension/content.js','utf8');
const background=fs.readFileSync('extension/background.js','utf8');
const panel=fs.readFileSync('extension/sidepanel.js','utf8');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function panelEditors(){
  const classes=new Set(),listeners={},writes=[],store={},appKey='fixture-app';
  let release,fail=false,renders=0;
  const row={classList:{add:(...names)=>names.forEach(name=>classes.add(name)),remove:(...names)=>names.forEach(name=>classes.delete(name))}};
  const input={value:'first',addEventListener:(name,fn)=>listeners[name]=fn,closest:()=>row,matches:()=>true};
  const button={disabled:false},status={textContent:''};
  const field={key:'0:one',label:'Website',kind:'text'};
  const env={document:{activeElement:input,querySelector:()=>classes.has('dirty')?row:null},
    result:{url:'https://fixture.test/',aiAnswers:[{label:'Why this company?',value:'Old draft'}]},histories:{},
    applicationKey:()=>appKey,render:()=>renders++,renderAnswer:()=>{},
    send:async message=>{writes.push(message.value);if(fail)throw Error('Fixture Save transport failed');if(writes.length===1)await new Promise(resolve=>release=resolve);return env.result;},
    chrome:{storage:{session:{get:async()=>structuredClone(store),set:async value=>{if(fail)throw Error('Fixture storage failed');Object.assign(store,structuredClone(value));}}}}};
  const start=panel.indexOf('const editorSaves='),end=panel.indexOf('function inputFor(',start);
  const api=Function('env','with(env){'+panel.slice(start,end)+';return {autosaveEditor,answerEditorsActive,saveGeneratedAnswer};}')(env);
  api.autosaveEditor(input,field,status,button);
  listeners.input();const saving=button.onclick();
  assert.equal(status.textContent,'Saving...');assert.ok(api.answerEditorsActive());
  input.value='middle';listeners.input();input.value='latest';listeners.input();
  release();await saving;
  assert.deepEqual(writes,['first','latest']);assert.equal(status.textContent,'Saved');assert.ok(!classes.has('dirty'));
  // A clean focused editor stays intact, then blur settles the editing class.
  assert.ok(api.answerEditorsActive());env.document.activeElement={matches:()=>false};await listeners.focusout();
  assert.ok(!api.answerEditorsActive(),'Focused Skip/Undo buttons must allow refresh');assert.ok(!classes.has('editing'));assert.ok(renders>0);
  fail=true;input.value='keep failed draft';listeners.input();await button.onclick();
  assert.equal(status.textContent,'Fixture Save transport failed');assert.equal(button.disabled,false);
  assert.ok(api.answerEditorsActive(),'An unfocused failed draft survives reports');assert.equal(input.value,'keep failed draft');
  fail=false;await button.onclick();assert.equal(writes.at(-1),'keep failed draft');assert.ok(!api.answerEditorsActive());
  const generated={key:'ai:'+appKey+':Why this company?',label:'Why this company?',value:'Old draft'};
  env.histories[generated.key.slice(3)]={versions:[{answer:'Earlier version'},{answer:'Old draft',warning:'stale warning'}],index:1};
  store.applicationAnswers={[appKey]:env.result.aiAnswers,other:[{label:generated.label,value:'Other application'}]};
  store.tabResults={1:{matched:{application_id:appKey},aiAnswers:env.result.aiAnswers}};store.lastResult=store.tabResults[1];
  fail=true;await assert.rejects(api.saveGeneratedAnswer(generated,'Unsaved edit'),/storage failed/);
  assert.equal(env.histories[generated.key.slice(3)].versions[1].answer,'Old draft');
  fail=false;await api.saveGeneratedAnswer(generated,'Edited employer narrative');
  assert.equal(env.result.aiAnswers[0].value,'Edited employer narrative');
  assert.equal(store.applicationAnswers[appKey][0].value,'Edited employer narrative');
  assert.equal(store.tabResults[1].aiAnswers[0].value,'Edited employer narrative');
  assert.equal(store.lastResult.aiAnswers[0].value,'Edited employer narrative');
  assert.equal(store.answerHistory[generated.key.slice(3)].versions[1].answer,'Edited employer narrative');
  assert.equal(store.answerHistory[generated.key.slice(3)].versions[1].warning,'');
  assert.equal(store.answerHistory[generated.key.slice(3)].versions[0].answer,'Earlier version');
  assert.equal(store.applicationAnswers.other[0].value,'Other application');
  assert.equal(writes.includes('Edited employer narrative'),false,'Editing an employer draft never promotes a profile fact');
}
async function edits(){
  const element={isConnected:true,value:'first',contains:target=>target===element};
  const record={element,members:[element],bindingElement:element,bindingMembers:[element],pageUrl:'https://fixture.test/',labelAtScan:'Website',field:{key:'one',label:'Website',kind:'text',control_identity:'control-1'}};
  const records=new Map([['one',record]]),writes=[],listeners={};
  let release,fail=false;
  const env={records,location:{href:record.pageUrl},label:()=>record.labelAtScan,robotOnly:()=>false,
    valueOf:r=>r.element.value,interactiveVisible:()=>true,extensionFilling:0,repairSynthetic:false,
    repairEdited:new WeakSet(),native:{eventTarget:event=>event.target},pause,setTimeout,clearTimeout,
    document:{addEventListener:(type,fn)=>listeners[type]=fn},
    chrome:{runtime:{sendMessage:async message=>{
      if(fail)throw Error('offline');writes.push(message);
      if(writes.length===1)await new Promise(resolve=>release=resolve);
      return {ok:true};
    }}}};
  const start=content.indexOf('  const editTimers='),end=content.indexOf("  document.addEventListener('click'",start);
  const api=Function('env','with(env){'+content.slice(start,end)+';return {captureEdit,saveEdit,flushEdits,learn,dirtyAnswers,observedValues};}')(env);
  api.captureEdit(record);const saving=api.saveEdit('one');
  assert.equal(api.observedValues.has('one'),false,'No optimistic acknowledgment');
  element.value='middle';api.captureEdit(record);element.value='latest';api.captureEdit(record);
  release();await saving;
  assert.deepEqual(writes.map(row=>row.value),['first','latest']);
  assert.equal(api.observedValues.get('one'),'"latest"');
  fail=true;element.value='unsaved';api.captureEdit(record);
  await assert.rejects(api.flushEdits(),/offline/);
  assert.equal(api.dirtyAnswers.get('one').value,'unsaved');
  assert.equal(api.observedValues.get('one'),'"latest"');
  fail=false;await api.flushEdits();assert.equal(writes.at(-1).value,'unsaved');
  for(const value of [false,0,['A','B']]){element.value=value;api.captureEdit(record);await api.flushEdits();assert.deepEqual(writes.at(-1).value,value);}
  const count=writes.length;element.value='synthetic';api.learn({isTrusted:false,target:element});await pause(250);
  assert.equal(writes.length,count);
  element.value='stale';api.captureEdit(record);record.element={...element};
  await assert.rejects(api.flushEdits(),/changed/);assert.equal(writes.length,count);
}
async function cancellation(){
  const state={},order=[],activeResults=new Map(),cancelledTabs=new Set();
  let releaseCover,coverCalls=0;
  const result={tabId:7,url:'https://fixture.test/',pageIdentities:{0:{documentId:'doc-1'}},
    fields:[{key:'0:cover',label:'Cover letter',kind:'file',answered:false}],
    writing:[{key:'0:required',label:'Required essay',kind:'textarea',required:true}],
    pending:[],completed:[],summary:{errors:[]}};
  activeResults.set(7,result);
  const env={crypto:require('node:crypto').webcrypto,activeResults,cancelledTabs,
    chrome:{storage:{session:{get:async key=>({[key]:state[key]}),set:async value=>Object.assign(state,value)}},tabs:{get:async()=>({url:result.url})},scripting:{executeScript:async()=>[]}},
    boundField:async()=>({value:''}),context:r=>({url:r.url}),report:async r=>r,
    progress:async(r,message)=>{r.progress=message;},
    api:async(path,payload)=>{
      if(path==='/ext/answers')return {answers:[]};
      if(path==='/ext/write'){order.push('required');return {answer:'Required answer'};}
      if(path==='/ext/cover-letter'){order.push('cover');coverCalls++;return new Promise(resolve=>releaseCover=resolve);}
      throw Error(path);
    },fill:async(r,answers)=>{for(const answer of answers){r.completed.push(answer);r.writing=r.writing.filter(f=>f.key!==answer.key);if(answer.status==='FILE')order.push('upload');}},
    rescanRevealed:async()=>{},matchAndFill:async()=>order.push('optional'),fillMatchedSkills:async()=>order.push('skills')};
  const start=background.indexOf('const coverRequests='),end=background.indexOf('const delay =',start);
  const ws=background.indexOf('async function continueWriting('),we=background.indexOf('async function advanceAfterWriting',ws);
  const api=Function('env','with(env){'+background.slice(start,end)+background.slice(ws,we)+';return {continueWriting,cancelCover,coverLetter};}')(env);
  const run=api.continueWriting(result);
  while(!coverCalls)await pause(1);
  assert.deepEqual(order,['required','cover']);
  await api.cancelCover(result);await run;
  assert.deepEqual(order,['required','cover','optional','skills']);
  releaseCover({filename:'late.pdf',mime:'application/pdf',base64:'late'});await pause(5);
  assert.equal(order.includes('upload'),false,'Late cover response cannot upload');
  await api.coverLetter(result);assert.equal(coverCalls,1,'Cancelled document cannot automatically retry');
  result.fields=[];await api.coverLetter(result,[],true);assert.equal(coverCalls,1,'No unsolicited cover letter');
}
async function submission(){
  let buttons=[],errors=['Required information missing'],clicks=0,flushes=0,account=false;
  const form={application:true},location={href:'https://fixture.test/application'};
  const node=(caption,attributes={},application=form)=>({textContent:caption,form:application,
    getAttribute:key=>attributes[key] || '',click:()=>clicks++});
  const env={location,document:{querySelectorAll:()=>buttons},clean:value=>String(value||'').trim(),text:node=>node?.textContent || '',
    native:{byId:()=>({textContent:'Apply'})},signInPage:()=>account,workdayState:()=>null,
    interactiveVisible:node=>!node.disabled,applicationWritingScope:root=>Boolean(root?.application),writingScope:node=>node.form,
    validationErrors:()=>errors,flushEdits:async()=>{flushes++;},armSubmission:()=>{},announceSubmission:()=>{},detectConfirmation:()=>{},
    applicationEntry:()=>undefined,scan:async()=>({fields:[{required:true,answered:false}]}),pause:async()=>{}};
  const start=content.indexOf('  function navigationButton('),end=content.indexOf('  function fieldState(',start);
  const ns=content.indexOf('  async function nextPage('),ne=content.indexOf('  const writingButtons',ns);
  const api=Function('env','with(env){'+content.slice(start,end)+content.slice(ns,ne)+';return {navigationState,manualSubmit,nextPage};}')(env);
  buttons=[node('',{'aria-label':'Apply'})];
  const expected=api.navigationState();assert.equal(expected.kind,'submit');
  const attempted=await api.manualSubmit(expected);assert.equal(attempted.clicked,true);assert.equal(clicks,1);
  assert.deepEqual(attempted.errors,errors,'Site validation is reported after an actual attempt');
  assert.equal(flushes,1);
  assert.equal((await api.nextPage(true)).clicked,false);assert.equal(clicks,1,'Auto never final-submits');
  assert.equal((await api.nextPage(false)).clicked,false);
  assert.equal((await api.manualSubmit({...expected,url:'https://stale.test/'})).clicked,false);
  buttons=[node('',{'aria-labelledby':'apply-label'})];assert.equal(api.navigationState().kind,'submit');
  buttons=[node('Send application')];assert.equal(api.navigationState().kind,'submit');
  buttons=[node('Apply',{},null)];assert.equal(api.navigationState().kind,'none','Landing Apply is not final');
  buttons=[node('Apply'),node('Submit')];assert.equal(api.navigationState().kind,'none','Ambiguous finals abstain');
  buttons=[node('Apply')];buttons[0].disabled=true;assert.equal(api.navigationState().kind,'none');
  buttons[0].disabled=false;account=true;assert.equal(api.navigationState().kind,'none');
}
Promise.resolve().then(panelEditors).then(edits).then(cancellation).then(submission).then(()=>process.stdout.write('T095 panel drafts/Skip refresh, edits/retries/bindings, required-cover-optional/cancel, and explicit Apply/native validation/no-auto-submit passed\n')).catch(error=>{console.error(error);process.exitCode=1;});
