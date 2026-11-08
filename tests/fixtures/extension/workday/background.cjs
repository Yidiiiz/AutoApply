// Offline state-machine test. Executes the actual background module with only
// Chrome transport/DOM boundaries replaced; real selectors run in the MV3 tests.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const scenario=process.argv[2],url='https://fixture.myworkdayjobs.com/Jobs/job/NY/Intern/apply';
const events={};
const event=name=>events[name] ||= {listeners:[],addListener(fn){this.listeners.push(fn)}};
const item={application_id:87,run_id:'synthetic',url,state:'opened'};
const result={tabId:7,url,matched:{...item},page:{},fields:[],pending:[],writing:[],completed:[],
  pageIdentities:{0:{documentId:'doc'}},next_action:{kind:'submit',url,step:'Review'},summary:{filled:0,skipped:0,errors:[]}};
const session={chromeRunPaused:true,chromeRun:{active:true,tabId:7,started:true,current:{...item}},
  autofillTabs:{7:{url,matched:{...item}}},tabResults:{7:structuredClone(result)}};
const clone=x=>x===undefined?undefined:structuredClone(x);
const area=data=>({async get(keys){return Object.fromEntries((keys==null?Object.keys(data):Array.isArray(keys)?keys:typeof keys==='string'?[keys]:Object.keys(keys)).filter(k=>k in data).map(k=>[k,clone(data[k])]))},async set(values){Object.assign(data,clone(values))},async remove(keys){for(const k of Array.isArray(keys)?keys:[keys])delete data[k]}});
const standalone=scenario.startsWith('standalone_');
if(standalone){session.chromeRun={active:false};delete item.run_id;delete result.matched.run_id;
  delete session.autofillTabs[7].matched.run_id;delete session.tabResults[7].matched.run_id;}
if(scenario==='persisted_receipt' || scenario==='standalone_restart')session.pendingSubmissions={7:{id:'persisted-proof',documentId:'doc',bindingUrl:url,payload:{application_id:87,run_id:item.run_id,url,evidence:'You have successfully submitted your application'}}};
let closed=false,clicks=0,applied=0,marks=0,canceled=0,release,hold=false,proofErrors=[],failNext=false,lostAck=false;
let documentId='doc',currentUrl=url,baseline='',nativeEvidence='You have successfully submitted your application';
const chrome={runtime:{id:'offline',getURL:p=>'chrome-extension://offline/'+p,onMessage:event('message'),sendMessage:async()=>({})},
  storage:{session:area(session),local:area({autoSendStructure:false}),onChanged:event('storage')},
  tabs:{onRemoved:event('remove'),onUpdated:event('update'),onActivated:event('activate'),
    get:async()=>{if(closed)throw Error('closed');return {id:7,url:currentUrl,status:'complete'}},query:async()=>[],remove:async()=>{}},
  scripting:{async executeScript({func,target}){
    if(target.documentIds && !target.documentIds.includes(documentId))return [];
    const source=String(func);
    if(source.includes('manualSubmit')){clicks++;return [{frameId:0,documentId:'doc',result:{clicked:true,errors:[]}}]}
    if(source.includes('submissionBaseline')){
      if(scenario==='replacement_native_navigation')throw Error('Original document navigated');
      return [{frameId:0,documentId,result:baseline}];
    }
    if(source.includes('confirmationEvidence'))return [{frameId:0,documentId,result:source.includes('errors:')?{evidence:nativeEvidence,errors:proofErrors}:baseline}];
    return [{frameId:0,documentId:'doc',result:true}];
  }},action:{onClicked:event('action'),setBadgeText:async()=>{},setTitle:async()=>{}},
  commands:{onCommand:event('command')},alarms:{onAlarm:event('alarm'),create:()=>{}}};
const api=async(path,payload)=>{
  if(path==='/ext/applied'){
    applied++;if(failNext){failNext=false;throw Error('synthetic 503')}
    if(item.state!=='submitted')marks++;item.state='submitted';
    if(lostAck){lostAck=false;throw Error('synthetic lost acknowledgement')}
    if(hold)await new Promise(r=>release=r);return {ok:true};
  }
  if(path==='/ext/unapplied'){item.state='opened';marks=0;return {ok:true}}
  if(path==='/ext/run/cancel'){canceled++;item.state='skipped';return {ok:true}}
  if(path==='/ext/run/skip'){item.state='skipped';return {ok:true}}
  if(path==='/ext/run/status')return {items:[{...item}]};
  if(path==='/ext/run/next')return {done:true};
  if(path==='/ext/run/result')return {ok:true};
  throw Error('Unexpected endpoint '+path);
};
const context=vm.createContext({chrome,api,snapshotCollector:()=>({preference:async()=>({enabled:false})}),
  URL,console,structuredClone,TextEncoder,crypto:require('node:crypto').webcrypto,
  setTimeout:(fn,ms)=>{const timer=setTimeout(fn,ms);timer.unref();return timer},clearTimeout,setInterval:()=>0});
const source=fs.readFileSync('extension/background.js','utf8').replace(/^import .*;\r?\n/gm,'').replace(/^export /gm,'');
vm.runInContext(source+`\nscan=async()=>(${JSON.stringify({...result,sign_in:false})});
activeResults.set(7,${JSON.stringify(result)});
globalThis.test={action,runTick,checks:submissionTasks,closedTabs,setPoll:value=>batchBusy=value};`,context);
const keepAlive=setInterval(()=>{},1000);
const wait=ms=>new Promise(r=>setTimeout(r,ms));
const until=async test=>{for(let i=0;i<100;i++){if(test())return;await wait(5)}throw Error('Offline state did not settle')};
const message=(type,extra={})=>new Promise(resolve=>{
  const sender={id:'offline',tab:{id:7,url:currentUrl},frameId:0,documentId:extra.documentId || documentId,url:currentUrl};
  const msg={type,url:currentUrl,evidence:'You have successfully submitted your application',...extra};
  let pending=false;
  for(const listener of events.message.listeners)if(listener(msg,sender,resolve)===true)pending=true;
  if(!pending)resolve();
});
const command=(type,extra={})=>new Promise(resolve=>{
  for(const fn of events.message.listeners)fn({type,...extra},{id:'offline',url:'chrome-extension://offline/sidepanel.html'},resolve);
});
async function close(){closed=true;return Promise.all(events.remove.listeners.map(fn=>fn(7)))}
(async()=>{
  await wait(10);session.chromeRunPaused=false;
  if(scenario==='owned_submit' || scenario==='foreign_submit' || scenario==='poll_submit'){
    if(scenario==='foreign_submit')session.chromeRun.current.application_id=88;
    if(scenario==='poll_submit'){context.test.setPoll(true);setTimeout(()=>context.test.setPoll(false),30)}
    let error;
    try{await context.test.action({type:'manual-submit',tabId:7,expected:result.next_action})}catch(e){error=e}
    assert.equal(clicks,scenario==='foreign_submit'?0:1);
    assert.equal(Boolean(error),scenario==='foreign_submit');assert.equal(applied,0);
  }else if(scenario==='persisted_receipt' || scenario==='standalone_restart'){
    closed=true;await context.test.runTick();
    assert.equal(applied,1);assert.equal(canceled,0);assert.equal(session.chromeRun.current,undefined);
    assert.equal(Object.keys(session.pendingSubmissions).length,0);
    assert.equal(session.autofillTabs[7],undefined);
    await context.test.runTick();assert.equal(applied,1);
  }else if(standalone){
    await message('submit-intent');
    failNext=scenario!=='standalone_lost_ack';lostAck=scenario==='standalone_lost_ack';
    await message('submission-confirmation');
    assert.equal(applied,1);assert.ok(session.pendingSubmissions[7]);
    if(scenario==='standalone_undo')await context.test.action({type:'undo-applied',tabId:7});
    if(scenario==='standalone_reassigned')session.autofillTabs[7].matched.application_id=88;
    if(scenario==='standalone_url_changed')session.autofillTabs[7].url=url.replace('Intern','Other');
    if(['standalone_retry','standalone_lost_ack','standalone_paused_retry'].includes(scenario)){
      await close();assert.ok(session.autofillTabs[7]);assert.ok(session.pendingSubmissions[7]);
    }
    if(scenario==='standalone_paused_retry'){
      session.chromeRunPaused=true;await context.test.runTick();assert.equal(applied,1);assert.ok(session.pendingSubmissions[7]);
      session.chromeRunPaused=false;
    }
    await context.test.runTick();
    const revoked=['standalone_undo','standalone_reassigned','standalone_url_changed'].includes(scenario);
    assert.equal(applied,revoked?1:2);assert.equal(marks,revoked?0:1);
    assert.equal(Object.keys(session.pendingSubmissions).length,0);
    if(!revoked)assert.equal(session.autofillTabs[7],undefined);
    await context.test.runTick();assert.equal(applied,revoked?1:2);
  }else if(scenario.startsWith('replacement_')){
    if(scenario==='replacement_preexisting')baseline=nativeEvidence;
    if(scenario!=='replacement_no_intent')await message('submit-intent',scenario==='replacement_native_navigation'?{before:''}:{});
    documentId='replacement';
    if(scenario==='replacement_other_job')currentUrl=url.replace('Intern','Other');
    if(scenario==='replacement_other_origin')currentUrl=url.replace('fixture.','other.');
    if(scenario==='replacement_expired')session.autofillTabs[7].submitAt=Date.now()-120001;
    if(scenario==='replacement_invalid')proofErrors=['Required information missing'];
    if(scenario==='replacement_review')nativeEvidence='';
    if(scenario==='replacement_paused')session.chromeRunPaused=true;
    if(scenario==='replacement_pause_resume'){
      await command('run-pause',{paused:true});await command('run-pause',{paused:false});
    }
    if(scenario==='replacement_reassigned')session.autofillTabs[7].matched.application_id=88;
    await message('submission-confirmation');
    const accepted=['replacement_valid','replacement_native_navigation'].includes(scenario);
    assert.equal(applied,accepted?1:0);
    if(accepted)assert.equal(session.autofillTabs[7].submitDocumentId,documentId);
    await message('submission-confirmation');assert.equal(applied,accepted?1:0);
  }else if(scenario==='cancel_pending' || scenario==='skip_pending'){
    await message('submit-intent');failNext=true;await message('submission-confirmation');assert.ok(session.pendingSubmissions[7]);
    await command(scenario==='cancel_pending'?'run-cancel':'run-skip');
    await context.test.runTick();assert.equal(applied,1);assert.equal(Object.keys(session.pendingSubmissions).length,0);
  }else if(scenario==='undo_inflight'){
    await message('submit-intent');hold=true;const confirmation=message('submission-confirmation');
    await until(()=>release);
    const undo=context.test.action({type:'undo-applied',tabId:7});await until(()=>session.autofillTabs[7].undone);
    release();await confirmation;await undo;
    assert.equal(marks,0);assert.equal(session.autofillTabs[7].applied,undefined);
    assert.equal(Object.keys(session.pendingSubmissions).length,0);
    await context.test.runTick();assert.equal(applied,1);
  }else if(scenario==='close_only'){
    await close();assert.equal(canceled,1);assert.equal(applied,0);
  }else{
    await message('submit-intent');
    if(scenario==='invalid')proofErrors=['Required information missing'];
    hold=scenario==='receipt_close';
    const confirmation=message('submission-confirmation',scenario==='stale'?{documentId:'old'}:{});
    if(hold){
      await until(()=>release && session.pendingSubmissions?.[7]);
      const closing=close();await wait(10);
      assert.equal(canceled,0);assert.equal(applied,1);
      release();await confirmation;await closing;
      assert.equal(canceled,0);assert.equal(applied,1);assert.equal(session.chromeRun.current,undefined);
      assert.equal(Object.keys(session.pendingSubmissions).length,0);
      await context.test.runTick();assert.equal(session.chromeRun.active,false);
    }else{
      await confirmation;assert.equal(applied,0);
      await close();assert.equal(canceled,1);
    }
  }
  process.stdout.write('passed '+scenario+'\n');
})().catch(error=>{console.error(error);process.exitCode=1}).finally(()=>clearInterval(keepAlive));
