// Offline lifecycle contract for the real background wait helper. The actual
// scanner/root traversal and writes are covered by the separate MV3 cases.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[2],'utf8');
const helper='async function waitForApplicationInputs('+source.split('async function waitForApplicationInputs(')[1].split('\nfunction cancelWait(')[0];
async function run(scenario){
  let now=0,reads=0;
  const reports=[],owner={},cancelledTabs=new Set(),closedTabs=new Set(),sidebarStates=new Map(),sidebarVersions=new Map();
  const missing=scenario.startsWith('fresh-'),mode=missing?scenario.slice(6):scenario;
  let passiveOwner;
  const late=()=>now>=600;
  const env={Date:{now:()=>now},runEpoch:0,activeResults:new Map(missing?[]:[[1,owner]]),sidebarStates,sidebarVersions,
    cancelledTabs,closedTabs,staleRun:()=>scenario==='epoch'&&late(),
    snapshots:{preference:async()=>({enabled:!(scenario==='preference'&&late())})},
    delay:async ms=>{now+=ms;if(late()){
      if(mode==='reassigned')env.activeResults.set(1,{});
      if(scenario==='closed')closedTabs.add(1);
      if(scenario==='cancel')cancelledTabs.add(1);
      if(scenario==='sidebar'){sidebarStates.set(1,'closed');sidebarVersions.set(1,1);}
    }},
    report:async value=>reports.push(value),
    chrome:{tabs:{get:async()=>({url:scenario==='navigation'&&late()?'https://fixture.test/changed':'https://fixture.test/apply'})},
      storage:{session:{get:async()=>({chromeRunPaused:scenario==='pause'&&late()})}}},
    compactApplicationState:async()=>{reads++;
      if(missing){
        // The real application-state-changed footer listener initializes only
        // an absent record, even while the same document is still hydrating.
        if(!env.activeResults.has(1))env.activeResults.set(1,{fields:[],summary:{filled:0}});
        passiveOwner=env.activeResults.get(1);
      }
      return [{frameId:0,documentId:scenario==='document'&&late()?'new':'original',result:{readiness:{
      revision:scenario==='edit'&&late()?1:0,url:'https://fixture.test/apply',
      controls:mode==='visible'||mode==='late'&&late()?1:0,
      private:mode==='private',navigation:{kind:'none'},
      initializing:scenario!=='blocked',status:scenario==='blocked'?'access_blocked':'loading',message:'Waiting for inputs.'
    }}}];}
  };
  vm.createContext(env);vm.runInContext(helper+'\nglobalThis.wait=waitForApplicationInputs;',env);
  const invalid=mode==='reassigned'||['epoch','closed','cancel','sidebar','navigation','document','edit','pause','preference'].includes(scenario);
  if(invalid)await assert.rejects(env.wait(1,true),/changed while waiting/);
  else {
    const result=await env.wait(1);
    assert.equal(result.owner,env.activeResults.get(1));
    if(missing)assert.equal(result.owner,passiveOwner);
    if(['visible','private','blocked'].includes(mode)){
      assert.equal(result.waited,false);assert.equal(now,0);assert.equal(reads,1);
    } else {
      assert.equal(result.waited,true);assert.equal(result.empty,mode==='empty');
      if(mode==='late'){assert.equal(now,600);await result.check();}
      else assert.equal(now,8000);
      assert.equal(reports[0].busy,true);assert.equal(reports.at(-1).busy,false);
    }
  }
  assert.ok(now<=8000);
}
(async()=>{
  for(const scenario of ['visible','private','blocked','late','empty','epoch','closed','cancel','sidebar','navigation','document','edit','pause','preference'])await run(scenario);
  for(const scenario of ['fresh-late','fresh-empty','fresh-visible','fresh-private','fresh-reassigned','reassigned'])await run(scenario);
  process.stdout.write('readiness lifecycle passed (20 scenarios)\n');
})().catch(error=>{console.error(error);process.exitCode=1;});
