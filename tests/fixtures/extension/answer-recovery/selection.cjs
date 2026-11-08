// Execute shipped selection and commit verification without a browser or server.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const baseline=process.argv[2]==='baseline';
const source=fs.readFileSync(baseline?0:'extension/content.js','utf8');
const start=source.indexOf('  function comboBackingInput(');
const selection=source.slice(start<0?source.indexOf('  function comboSelection('):start,source.indexOf('  function referencedLabel('));
const fill=source.slice(source.indexOf('  async function fillCombo('),source.indexOf('  async function phoneValue('));
async function scenario(kind){
  let time=1000,open=false,clicked=0;
  const mirror={value:'',closest:()=>({role:'listbox'})};
  const backing={value:'',closest:()=>null};
  const container={nextElementSibling:null,querySelector:selector=>selector==='input[type="hidden"]'?(kind==='backing-empty'?backing:mirror):null,
    querySelectorAll:()=>kind==='backing-empty'?[backing]:[mirror]};
  const element={dataset:{autoapplyKey:'combo'},value:'',textContent:'Select one',isConnected:true,
    matches:s=>s.startsWith('input')?kind==='input':kind!=='input',
    getAttribute:name=>name==='aria-expanded'?(open?'true':'false'):null};
  const options=['Yes','No'].map(value=>({textContent:value,tagName:'BUTTON',disabled:false,isConnected:true,
    matches:()=>false,getAttribute:()=>null,closest:()=>kind==='submit-option'?{}:null}));
  const context=vm.createContext({Date:{now:()=>time},location:{href:'https://fixture.test/job'},
    records:new Map([['combo',{field:{options:['Yes','No'],options_observed:true}}]]),
    choiceObservations:new WeakMap(),repairExecuting:true,checkRepair:()=>{},sourcePrompt:()=>false,
    isPrompt:()=>false,isSkillsField:()=>false,remoteAutocomplete:()=>false,selectOnlyControl:()=>false,
    clean:v=>String(v||'').trim(),normal:v=>String(v||'').trim().toLowerCase(),text:e=>e?.textContent||'',
    comboContainer:()=>container,comboInput:e=>kind==='input'?e:null,label:()=> 'Synthetic authorization question',
    displayedChoice:()=>false,currentControl:e=>e,interactiveVisible:()=>true,
    trace:()=>{},monthValues:()=>null,pause:async()=>{time+=50},
    comboOptions:()=>open?options:[],settledComboOptions:async()=>options,
    openCombo:async()=>{open=true},closeCombo:()=>{open=false},
    fieldChoice:(e,rows,value)=>rows.find(row=>row.textContent===value),
    pointerClick:(target,guard)=>{
      if(!guard())return false;
      clicked++;open=false;
      element.textContent=element.value=kind==='wrong'?'No':'Yes';
      return true;
    },
    waitFor:async read=>{for(let i=0;i<12;i++){time+=100;if(kind==='revert'&&i===2)element.textContent=element.value='';const result=read();if(result)return result;}return null},
    occupied:()=>false});
  vm.runInContext(selection+fill+'\nglobalThis.verify=()=>fillCombo(element,"Yes");',context);
  context.element=element;
  const committed=await context.verify();
  assert.equal(committed,!baseline && ['button','input'].includes(kind),kind);
  assert.equal(clicked,kind==='submit-option'?0:1,kind+' must activate at most once');
}
(async()=>{
  for(const kind of ['button','input','backing-empty','wrong','revert','submit-option'])await scenario(kind);
  process.stdout.write('popup mirrors, finite input/button selections, backing-value failure, wrong value, reversion and submit guard passed');
})().catch(error=>{console.error(error);process.exitCode=1});
