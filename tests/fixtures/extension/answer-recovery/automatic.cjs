// Execute the shipped scheduler with a fake clock and native/API boundaries.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('extension/background.js','utf8');
const section=source.slice(source.indexOf('const automaticPending=new Map()'),source.indexOf('const submissionTasks=new Map()'));
const noop={addListener(){}},session={},result={tabId:7,answerRevision:['profile',0]};
let now=1000,calls=0,next=0,failures=1;const timers=new Map();
const area=data=>({async get(key){return {[key]:data[key]}},async set(values){Object.assign(data,structuredClone(values))}});
const context=vm.createContext({console,Date:{now:()=>now},JSON,Map,Set,
  setTimeout:(fn,ms)=>{timers.set(++next,{fn,at:now+ms});return next},clearTimeout:id=>timers.delete(id),
  running:false,autofillTasks:0,skillsRunning:false,analysisBlockers:0,batchBusy:false,
  manualActionTabs:new Set(),analysisTasks:new Map(),pendingSteps:new Set(),writingTasks:new Map(),
  closedTabs:new Set(),cancelledTabs:new Set(),sidebarStates:new Map(),activeResults:new Map([[7,result]]),
  chrome:{storage:{local:area({autoSendStructure:true}),session:area(session),onChanged:noop},
    tabs:{get:async()=>({id:7,url:'https://fixture.test/job'}),onRemoved:noop,onUpdated:noop}},
  readFrames:async()=>[{frameId:0,documentId:'doc',result:{application:true,shape:'stable'}}],
  action:async()=>{calls++;if(failures>0){failures--;return {automaticRetry:true}}return {automaticRetry:false,automaticSignature:'[["doc","stable"]]'}},
  report:async()=>{},scheduleAutoAccount:()=>{}});
const exported='\nglobalThis.recovery={runAutomaticAnalysis,queueAutomaticAnalysis,automaticSeen,automaticPending,automaticReady};';
vm.runInContext(section+exported,context);
(async()=>{
  let api=context.recovery;await api.automaticReady;
  api.queueAutomaticAnalysis(7);await api.runAutomaticAnalysis();assert.equal(calls,1);
  for(let i=0;i<8;i++){api.queueAutomaticAnalysis(7);await api.runAutomaticAnalysis();}
  assert.equal(calls,1,'signals before retry deadline must not repeat AI');
  // Losing every JS timer is equivalent to suspension/restart. Session state
  // must rearm the outstanding retry, keeping its original attempt count.
  timers.clear();
  const restarted=vm.createContext({...context});
  vm.runInContext(section+exported,restarted);api=restarted.recovery;
  await api.automaticReady;
  assert.equal(api.automaticSeen.get(7).attempts,1);
  assert.ok([...timers.values()].some(timer=>timer.at===session.automaticRecovery[0][1].retryAt));
  now+=16001;
  for(const [id,timer] of [...timers])if(timer.at<=now){timers.delete(id);timer.fn();}
  await api.runAutomaticAnalysis();assert.equal(calls,2);
  for(let i=0;i<8;i++){api.queueAutomaticAnalysis(7);await api.runAutomaticAnalysis();}
  assert.equal(calls,2,'a stable successful revision must not repeat AI');
  assert.equal(session.automaticRecovery[0][1].settled,true);
  api.automaticSeen.clear();failures=10;
  for(let i=0;i<8;i++){now+=16001;api.queueAutomaticAnalysis(7);await api.runAutomaticAnalysis();}
  assert.equal(calls,5,'persistent transient failure permits only three attempts');
  process.stdout.write('bounded retry, signal coalescing, success dedupe, persistent-failure cap passed');
})().catch(error=>{console.error(error);process.exitCode=1});
