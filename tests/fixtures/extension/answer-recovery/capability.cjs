// Execute the shipped repair capability, with only its native boundaries mocked.
// The MV3 tests exercise the same cases against actual HTML/DOM controls.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(0,'utf8');
const block=source.slice(source.indexOf('  // Shared document-bound repair capability'),source.indexOf('  function visible(element)'));
assert.ok(block.includes('async function repairFill'));
async function scenario(kind){
  const listeners={},form={tagName:'FORM',id:'application',isConnected:true,matches:()=>false,getAttribute:()=>null};
  const element=(id)=>({id,tagName:'INPUT',isConnected:true,type:'text',value:'',disabled:false,readOnly:false,required:true,
    getAttribute(name){return name==='name'?id:null;},matches:()=>false,closest:()=>null});
  const name=element('name'),email=element('email');
  const records=new Map([['name',{element:name,members:[name],labelAtScan:'name',choiceQuestionAtScan:null,field:{key:'name',label:'Full name',kind:'text',options:[]}}],
    ['email',{element:email,members:[email],labelAtScan:'email',choiceQuestionAtScan:null,field:{key:'email',label:'Email',kind:'email',options:[]}}]]);
  let controls=[form,name,email];
  const sandbox={records,location:{href:'https://fixture.test/job'},document:{addEventListener:(type,fn)=>listeners[type]=fn},
    native:{read:fn=>fn(),query:selector=>selector==='form,h1,h2,h3'?[form]:controls,
      parent:()=>null,closest:()=>null,eventTarget:e=>e.target},
    clean:s=>String(s||'').trim(),label:e=>e.id,questionText:e=>e.id,
    workdayState:()=>null,diagnosticExcluded:()=>false,scan:async()=>({page:{}}),
    structureOf:()=>({}),signInPage:()=>false,writableRecord:()=>true,isSkillsField:()=>false,
    leverResumeProxy:()=>false,noSubmitProxyChoice:()=>true,isPrompt:()=>false,sourcePrompt:()=>false,
    sectionField:()=>'',analyzeResume:()=>false,extensionFilling:0,skillsState:{busy:false},analysisIdentity:null,
    occupied:r=>Boolean(r.element.value),valueOf:r=>r.element.value,refreshRecord:r=>r,
    pageInfo:()=>({}),navigationState:()=>({kind:'none'}),pause:async()=>{},
    fillFields:async answers=>{
      const completed=[];
      for(const a of answers){records.get(a.key).element.value=a.value;completed.push(a);}
      return {completed:kind==='unverified'?[]:completed};
    },controlSelector:'input,select',chrome:{runtime:{sendMessage:async()=>{}}}};
  vm.createContext(sandbox);
  vm.runInContext(block+'\nglobalThis.capability={repairStart,repairFill,repairFinish,check:assertRepair};',sandbox);
  const api=sandbox.capability;
  await api.repairStart('test');
  if(kind==='menu'){
    // Public observed widget structure: asynchronous native option/mirror mounts.
    controls.push(element('menu-mirror'));
  }else if(kind==='edit'){
    name.value='Manually chosen';
    listeners.input({isTrusted:true,target:name});
  }
  try{
    await api.repairFill('test',[{key:'name',status:'RESOLVED',value:'Stale name'},
      {key:'email',status:'RESOLVED',value:'synthetic@example.test'}]);
    const done=api.repairFinish('test');
    assert.equal(email.value,'synthetic@example.test');
    if(kind==='edit')assert.equal(name.value,'Manually chosen');
    if(kind==='unverified'){
      assert.equal(done.completed.length,0);
      assert.ok(done.fields.every(f=>!f.answered && f.repair_status==='verification_failed'),
        'an occupied but unverified write must remain pending');
    }
    return {stopped:false,filled:done.filled};
  }catch(error){
    if(!/changed/.test(error.message))throw error;
    return {stopped:true,filled:0};
  }
}
(async()=>{
  const result={menu:await scenario('menu'),edit:await scenario('edit')};
  if(process.argv[2]==='baseline')assert.ok(result.menu.stopped && result.edit.stopped);
  else{assert.equal(result.menu.filled,2);assert.equal(result.edit.filled,1);await scenario('unverified');}
  process.stdout.write(JSON.stringify(result));
})().catch(error=>{console.error(error);process.exitCode=1});
