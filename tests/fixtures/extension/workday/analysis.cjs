// Offline test of the actual Analyze ordering; native DOM boundaries are mocked.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const offered=process.argv[2]!=='unoffered',events=[],requests=[];
const url='https://fixture.myworkdayjobs.com/Jobs/job/NY/Intern/apply';
const fields=[{key:'email',label:'Email',kind:'email',options:[],answered:false},
  {key:'source',label:'How Did You Hear About Us?',kind:'combobox',options:[],answered:false,multiple:true},
  {key:'eligible',label:'Work authorization',kind:'combobox',options:[],answered:false}];
const completed=[];
const event={addListener(){}};
const session={chromeRunPaused:true,autofillTabs:{7:{url,matched:{application_id:87}}}};
const area=data=>({async get(keys){return Object.fromEntries((Array.isArray(keys)?keys:[keys]).filter(k=>k in data).map(k=>[k,structuredClone(data[k])]))},
  async set(values){Object.assign(data,structuredClone(values))}});
const result=value=>[{frameId:0,documentId:'doc',result:structuredClone(value)}];
const chrome={runtime:{id:'offline',onMessage:event,getURL:path=>'chrome-extension://offline/'+path,sendMessage:async()=>({})},
  storage:{session:area(session),local:area({autoSendStructure:false}),onChanged:event},
  tabs:{onUpdated:event,onRemoved:event,onActivated:event,get:async()=>({id:7,url,status:'complete'})},
  action:{onClicked:event},commands:{onCommand:event},alarms:{onAlarm:event,create(){}},
  scripting:{async executeScript({func,args}){
    const source=String(func);
    if(source.includes('autoApplyCredentials?.describe'))return result({active:false});
    if(args?.[0]==='repairStart')return result({fields,before:[],page:{}});
    if(args?.[0]==='repairCheck')return result(true);
    if(args?.[0]==='repairFill'){
      for(const answer of args[1][1]){
        const field=fields.find(f=>f.key===answer.key);events.push('fill:'+answer.key);
        if(field.kind==='combobox')assert.ok(field.options.includes(answer.value),'Choice written before observation or without an exact offer');
        field.answered=true;completed.push({...field,...answer});
      }
      return result({ok:true});
    }
    if(args?.[0]==='repairOptions'){
      assert.deepEqual(Array.from(args[1][1]).sort(),['eligible','source']);
      assert.deepEqual(events,['fast','fill:email']);
      events.push('discover');
      fields[1].options=[offered?'Other':'Employee referral'];fields[2].options=['Yes','No'];
      return result(fields.filter(f=>!f.answered));
    }
    if(args?.[0]==='repairFinish')return result({fields,completed,filled:completed.length,stopped:'none',page:{},next_action:{kind:'none'}});
    if(source.includes('api.inspectPage'))return result({identity:'shape',edit_revision:0,nodes:[],inspection:{},
      diagnostic:{fields:fields.map(f=>({...f,reasons:[]})),responsive:true,inspection_ms:0}});
    if(source.includes('repairContext'))return result({identity:'shape',revision:0});
    if(source.includes('diagnostic:{fields:[]'))return result({diagnostic:{fields:[],busy:false},state:{application:true}});
    throw Error('Unexpected native boundary '+source);
  }}};
const api=async(path,payload)=>{
  if(path==='/ext/answers'){
    requests.push(payload);events.push(payload.allow_ai===false?'fast':'grounded');
    const answers=payload.fields.map(f=>({key:f.key,status:'RESOLVED',value:f.kind==='email'?'synthetic@example.test':f.key.endsWith('source')?'Other':'Yes',source:'profile_rule',reason_code:'answer_available'}));
    return {answers:answers.filter(a=>payload.allow_ai===false || !a.key.endsWith('source') || offered)};
  }
  if(path==='/ext/snapshot')return {ok:true};
  throw Error('Unexpected endpoint '+path);
};
const context=vm.createContext({chrome,api,URL,TextEncoder,structuredClone,console,crypto:require('node:crypto').webcrypto,
  snapshotCollector:()=>({preference:async()=>({enabled:false})}),setTimeout,clearTimeout,setInterval:()=>0});
const source=fs.readFileSync('extension/background.js','utf8').replace(/^import .*;\r?\n/gm,'').replace(/^export /gm,'');
vm.runInContext(source+'\nglobalThis.testAction=action;',context);
(async()=>{
  // Let the paused startup tick drain before the explicit Analyze action.
  await new Promise(resolve=>setTimeout(resolve,5));
  const response=await context.testAction({type:'page-snapshot',tabId:7});
  assert.equal(response.ok,true);
  assert.equal(requests.length,2);assert.equal(requests[0].allow_ai,false);
  assert.equal(requests[1].fields.length,2);
  assert.deepEqual(events,offered?['fast','fill:email','discover','grounded','fill:source','fill:eligible']:
    ['fast','fill:email','discover','grounded','fill:eligible']);
  assert.equal(session.tabResults[7].completed.length,offered?3:2);
  process.stdout.write('passed '+(offered?'offered':'unoffered')+'\n');
})().catch(error=>{console.error(error);process.exitCode=1});
