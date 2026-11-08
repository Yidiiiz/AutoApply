(function initialize() {
  const singleton=Symbol.for('autoapply.content');
  if(globalThis[singleton]){globalThis.autoApplyContent=globalThis[singleton];return;}
  if (globalThis.autoApplyContent) return;
  if(!document.body){
    const pending=Symbol.for('autoapply.pending');
    if(!globalThis[pending]){globalThis[pending]=true;document.addEventListener('DOMContentLoaded',()=>{globalThis[pending]=false;initialize();},{once:true});}
    return;
  }
  const native=globalThis.autoApplyNativeRoots;
  let nextKey = 0, extensionFilling = 0;
  const traces = [];
  function beginTrace(kind) {
    const existing = traces.find(run => run.kind === kind);
    if (kind === 'autofill' && existing) return existing;
    const run = {host: location.hostname, kind, started: new Date().toISOString(), entries: [], bytes: 0, truncated: false};
    traces.push(run);
    trace('run', 'started');
    return run;
  }
  function trace(step, action, element, details = {}) {
    if (!traces.length || globalThis.autoApplyCredentials?.active() || globalThis.autoApplyCredentials?.sensitive(element)) return;
    const field = element ? {
      label: label(element), element_id: element.id,
      automation_id: element.getAttribute('data-automation-id') ||
        element.closest('[data-automation-id^="formField"]')?.getAttribute('data-automation-id')
    } : {};
    const entry = JSON.parse(JSON.stringify(globalThis.autoApplyCredentials?.redact({timestamp: new Date().toISOString(), step, action, ...field, ...details}) || {timestamp: new Date().toISOString(), step, action, ...field, ...details},
      (_, value) => typeof value === 'string' ? value.slice(0, 1000) : value));
    const bytes = new TextEncoder().encode(JSON.stringify(entry)).length;
    for (const run of traces) {
      if (run.entries.length >= 2000 || run.bytes + bytes > 850000) { run.truncated = true; continue; }
      run.entries.push(entry);
      run.bytes += bytes;
    }
  }
  async function endTrace(run = traces.find(run => run.kind === 'autofill')) {
    if (!run) return;
    trace('run', 'finished');
    traces.splice(traces.indexOf(run), 1);
    const {bytes, ...payload} = run;
    return chrome.runtime.sendMessage({type: 'save-trace', trace: {...payload, ended: new Date().toISOString()}}).catch(() => {});
  }
  async function traced(kind, operation) {
    const run = beginTrace(kind);
    try { return await operation(); }
    catch (error) { trace(kind, 'error', null, {error: error.message, stack: error.stack}); throw error; }
    finally { await endTrace(run); }
  }
  let sidebarHost, dockStyle, dockRoot, layoutPreference='overlay';
  const dockAttribute='data-autoapply-dock-t076';
  function syncSidebarLayout() {
    const root=document.documentElement;
    const docked=layoutPreference==='docked' && innerWidth>=900 && sidebarHost?.isConnected && sidebarHost.dataset.state==='open';
    if(dockRoot && (dockRoot!==root || !docked))dockRoot.removeAttribute(dockAttribute);
    if(!docked){dockStyle?.remove();dockRoot=undefined;}
    else {
      dockStyle ||= document.createElement('style');
      // Cascade only: employer inline values AND priorities are never written.
      // Viewport units avoid the panel reserving space against its own width.
      dockStyle.textContent=`html[${dockAttribute}]{width:calc(100vw - 400px)!important;max-width:calc(100vw - 400px)!important;}`;
      root.setAttribute(dockAttribute,'');dockRoot=root;
      if(!dockStyle.isConnected)root.append(dockStyle);
    }
    const state={preferred:layoutPreference,effective:docked?'docked':'overlay'};
    if(sidebarHost)sidebarHost.dataset.layout=state.effective;
    chrome.runtime.sendMessage({type:'sidebar-layout-state',...state}).catch(()=>{});
    return state;
  }
  async function sidebarLayout(mode) {
    if(['overlay','docked'].includes(mode)){
      layoutPreference=mode;
      await chrome.storage.local.set({sidebarLayout:mode});
    }else layoutPreference=(await chrome.storage.local.get('sidebarLayout')).sidebarLayout || 'overlay';
    return syncSidebarLayout();
  }
  if(window===window.top){
    chrome.storage.local.get('sidebarLayout').then(saved=>{layoutPreference=saved.sidebarLayout||'overlay';syncSidebarLayout();});
    chrome.storage.onChanged.addListener((changes,area)=>{if(area==='local' && changes.sidebarLayout){layoutPreference=changes.sidebarLayout.newValue||'overlay';syncSidebarLayout();}});
    window.addEventListener('resize',syncSidebarLayout);
  }
  let sidebarObserver, sidebarRoot, sidebarRepairTimer, sidebarRepairWindow=0, sidebarRepairs=0;
  const sidebarVisible=()=>sidebarHost && ['open','minimized'].includes(sidebarHost.dataset.state);
  function observeSidebarRoot() {
    sidebarObserver.disconnect();
    sidebarRoot=document.documentElement;
    // The host is a direct root child. Never observe the application subtree.
    sidebarObserver.observe(document,{childList:true});
    if(sidebarRoot)sidebarObserver.observe(sidebarRoot,{childList:true});
  }
  function watchSidebar() {
    clearTimeout(sidebarRepairTimer);
    sidebarObserver?.disconnect();
    if(!sidebarVisible())return;
    sidebarRepairWindow=0;sidebarRepairs=0;
    sidebarObserver ||= new MutationObserver(()=>{
      if(!sidebarVisible())return;
      if(sidebarRoot!==document.documentElement)observeSidebarRoot();
      syncSidebarLayout();
      if(sidebarHost.isConnected || sidebarRepairTimer!==undefined)return;
      // Yield/coalesce hydration changes; read the latest state when repairing.
      sidebarRepairTimer=setTimeout(()=>{
        sidebarRepairTimer=undefined;
        if(!sidebarVisible() || sidebarHost.isConnected || !document.documentElement)return;
        const now=performance.now();
        if(now-sidebarRepairWindow>1000){sidebarRepairWindow=now;sidebarRepairs=0;}
        // A page actively removing our host must not create an endless fight.
        // A later explicit sidebar intent rearms recovery.
        if(++sidebarRepairs>5){sidebarObserver.disconnect();syncSidebarLayout();return;}
        document.documentElement.append(sidebarHost);syncSidebarLayout();
      },0);
    });
    sidebarRepairTimer=undefined;
    if(!sidebarHost.isConnected && document.documentElement)document.documentElement.append(sidebarHost);
    observeSidebarRoot();
  }
  function sidebar(mode = 'toggle', tabId) {
    if (window !== window.top) return;
    if (!sidebarHost) {
      const old=document.getElementById('autoapply-sidebar');
      if(old?.shadowRoot?.querySelector('iframe')?.src.startsWith(chrome.runtime.getURL('sidepanel.html')))old.remove();
      sidebarHost = document.createElement('div');
      sidebarHost.id = 'autoapply-sidebar';
      sidebarHost.style.cssText = 'all:initial!important;position:fixed!important;inset:0 0 0 auto!important;width:min(400px,100vw)!important;height:100dvh!important;z-index:2147483647!important;display:block!important;';
      const shadow = sidebarHost.attachShadow({mode:'open'});
      const style = document.createElement('style');
      style.textContent = ':host{color-scheme:dark} .panel{height:100%;background:Canvas;box-shadow:-8px 0 32px #0002;animation:slide .18s ease-out}iframe{border:0;width:100%;height:100%;display:block} .collapsed{position:fixed;right:0;top:40%;display:flex;align-items:center;background:#191919;color:#eee;border:0;outline:1px solid #555;border-radius:6px 0 0 6px;overflow:hidden;padding:0;box-shadow:0 2px 8px #0003}.collapsed button{min-height:30px;border:0;background:transparent;color:inherit;font:12px ui-sans-serif,system-ui;cursor:pointer;padding:0 8px}.collapsed button:hover{background:#303030}.collapsed button:focus-visible{outline:2px solid currentColor;outline-offset:-2px}.restore svg{width:14px;height:14px;vertical-align:-2px;margin-right:5px;fill:none;stroke:currentColor;stroke-width:1.5}@media(prefers-color-scheme:light){.collapsed{background:#fff;color:#202020;outline-color:#aaa}.collapsed button:hover{background:#e8e8e8}} [hidden]{display:none!important}@keyframes slide{from{transform:translateX(100%)}to{transform:translateX(0)}}@media(prefers-reduced-motion:reduce){.panel{animation:none}}';
      const panel = document.createElement('div'); panel.className = 'panel';
      const frame = document.createElement('iframe'); frame.src = chrome.runtime.getURL('sidepanel.html')+(tabId?'?tabId='+tabId:''); frame.title = 'AutoApply application assistant';
      panel.append(frame);
      const restore = document.createElement('button'); restore.className='restore'; restore.innerHTML='<svg class="moon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 15.5A9 9 0 0 1 8.5 4 9 9 0 1 0 20 15.5Z"/></svg> AutoApply'; restore.setAttribute('aria-label','Restore AutoApply sidebar');
      const collapsed = document.createElement('div'); collapsed.className='collapsed';
      const dismiss = document.createElement('button'); dismiss.className='dismiss'; dismiss.textContent='×';
      dismiss.setAttribute('aria-label','Dismiss minimized AutoApply'); dismiss.title='Dismiss AutoApply';
      dismiss.onclick=event=>{event.stopPropagation();sidebar('closed');chrome.runtime.sendMessage({type:'sidebar-state',state:'closed'}).catch(()=>{});};
      collapsed.append(restore,dismiss);
      const positionKey = 'flagPosition:' + location.hostname;
      const clamp = top => Math.max(0, Math.min(innerHeight - collapsed.offsetHeight, top));
      let drag, dragged = false;
      chrome.storage.local.get(positionKey).then(saved => { if (Number.isFinite(saved[positionKey])) collapsed.style.top = clamp(saved[positionKey]) + 'px'; });
      restore.style.touchAction = 'none';
      restore.onpointerdown = event => { drag = {y:event.clientY, top:collapsed.getBoundingClientRect().top}; dragged=false; restore.setPointerCapture(event.pointerId); };
      restore.onpointermove = event => { if (!drag) return; if (Math.abs(event.clientY-drag.y)>3) dragged=true; if (dragged) collapsed.style.top=clamp(drag.top+event.clientY-drag.y)+'px'; };
      restore.onpointerup = () => { if (dragged) chrome.storage.local.set({[positionKey]:clamp(collapsed.getBoundingClientRect().top)}); drag=null; };
      restore.onpointercancel = () => { drag=null; };
      window.addEventListener('resize', () => { if (collapsed.style.top) collapsed.style.top=clamp(parseFloat(collapsed.style.top))+'px'; });
      restore.onclick=event=>{if(dragged && event.detail!==0){dragged=false;return;}dragged=false;sidebar('open');chrome.runtime.sendMessage({type:'sidebar-state',state:'open'}).catch(()=>{});};
      shadow.append(style,panel,collapsed); document.documentElement.append(sidebarHost);
      sidebarHost.dataset.state='closed';
    }
    const state = mode === 'toggle' ? sidebarHost.dataset.state === 'open' ? 'closed' : 'open' : mode;
    sidebarHost.dataset.state=state;
    if(state==='closed'){chatEpoch++;repairRevision++;repairCancellation++;}
    sidebarHost.style.setProperty('display',state==='closed'?'none':'block','important');
    sidebarHost.style.setProperty('width',state==='minimized'?'0':'min(400px,100vw)','important');
    sidebarHost.shadowRoot.querySelector('.panel').hidden=state!=='open';
    sidebarHost.shadowRoot.querySelector('.collapsed').hidden=state!=='minimized';
    syncSidebarLayout();watchSidebar();
    return state;
  }
  const controlChecked=element=>Boolean(element.checked || element.getAttribute('aria-checked')==='true');
  const choiceKind=element=>{
    if(element.matches('input'))return element.type;
    const role=element.getAttribute('role');
    return ['checkbox','radio','switch'].includes(role)?role==='switch'?'checkbox':role:element.type || role;
  };
  function valueOf({element,members,field,savedDate}) {
    if (savedDate) return savedDate;
    const proxy=yesNoProxy(element);if(proxy)return proxy.value || '';
    if (field.kind==='file') return Array.from(element.files || [],file=>file.name).join(', ') || attachedFile(element);
    if (['radio','checkbox'].includes(field.kind)) return field.kind==='checkbox'&&members.length===1 ? controlChecked(element) : members.filter(controlChecked).map(label)[field.kind==='radio'?'shift':'slice']();
    if (field.kind==='select') return element.multiple ? Array.from(element.selectedOptions,o=>clean(o.text)) : clean(element.selectedOptions[0]?.text);
    if (field.kind==='combobox') return comboSelection(element) || clean(comboInput(element)?.value);
    return element.isContentEditable || !('value' in element) ? element.textContent : element.value;
  }
  const editTimers=new Map(), observedValues=new Map(), dirtyAnswers=new Map(), editWrites=new Map(), userControls=new WeakSet();
  const cancelledCoverUploads=new Set();
  for(const type of ['beforeinput','pointerdown'])document.addEventListener(type,event=>{
    if(event.isTrusted && !repairSynthetic)userControls.add(native.eventTarget(event));
  },true);
  function boundRecord(record) {
    const current=records.get(record.field.key);
    return current && current.element===record.element && record.pageUrl===location.href &&
      record.element===record.bindingElement && record.members.every((member,index)=>member===record.bindingMembers?.[index]) &&
      record.element.isConnected && record.labelAtScan===label(record.element) &&
      current.field.control_identity===record.field.control_identity && !robotOnly(record.element);
  }
  async function saveEdit(key) {
    if(editWrites.has(key))return editWrites.get(key);
    const task=(async()=>{
      while(dirtyAnswers.has(key)){
        const draft=dirtyAnswers.get(key);
        if(!boundRecord(draft.record))throw Error('Question changed. Edit the current question again.');
        let response;
        for(let attempt=0;attempt<3;attempt++){
          try{response=await chrome.runtime.sendMessage({type:'user-answer',key,value:draft.value,
            url:location.href,label:draft.record.field.label,control_identity:draft.record.field.control_identity});
            if(!response?.ok)throw Error(response?.error || 'Answer was not saved.');break;
          }catch(error){if(dirtyAnswers.get(key)!==draft)break;if(attempt===2)throw error;await pause(200*(attempt+1));}
        }
        if(dirtyAnswers.get(key)===draft){observedValues.set(key,draft.encoded);dirtyAnswers.delete(key);}
      }
      return {ok:true};
    })().finally(()=>editWrites.delete(key));
    editWrites.set(key,task);return task;
  }
  async function flushEdits() {
    for(const [key,timer] of editTimers){clearTimeout(timer);editTimers.delete(key);captureEdit(records.get(key));}
    await Promise.all([...dirtyAnswers.keys()].map(saveEdit));
    return {ok:true};
  }
  function captureEdit(record) {
    if(!record || !boundRecord(record))return;
    const value=valueOf(record),encoded=JSON.stringify(value);
    if(JSON.stringify(globalThis.autoApplyCredentials?.redact(value) ?? value)!==encoded)return;
    if(observedValues.get(record.field.key)===encoded && !dirtyAnswers.has(record.field.key))return;
    dirtyAnswers.set(record.field.key,{record,value,encoded});
  }
  function learn(event) {
    if(!event.isTrusted || repairSynthetic || globalThis.autoApplyCredentials?.active()) return;
    const target=native.eventTarget(event);
    const record=Array.from(records.values()).find(r=>r.members.includes(target)||r.element.contains(target));
    if(!record || extensionFilling && !userControls.has(target) || globalThis.autoApplyCredentials?.sensitive(target) ||
       ['file','password','hidden'].includes(record.field.kind)||robotOnly(target)||!interactiveVisible(record.element)) return;
    for(const member of record.members)repairEdited.add(member);
    captureEdit(record);
    clearTimeout(editTimers.get(record.field.key));
    editTimers.set(record.field.key,setTimeout(()=>{
      editTimers.delete(record.field.key);captureEdit(record);
      saveEdit(record.field.key).catch(error=>chrome.runtime.sendMessage({type:'user-answer-error',error:error.message}).catch(()=>{}));
    },200));
  }
  for(const type of ['change','blur','focusout'])document.addEventListener(type,learn,true);
  document.addEventListener('click',event=>{
    const button=native.closest(native.eventTarget(event),'button,input[type="submit"],[role="button"]');
    if(event.isTrusted && button && visible(button)) {
      if(button===navigationButton(true))announceSubmission();
    }
  },true);
  document.addEventListener('submit',event=>{
    if(event.isTrusted && event.submitter===navigationButton(true) &&
       (!submissionIntent || location.href!==submissionIntent.url || Date.now()-submissionIntent.at>1000))announceSubmission();
  },true);
  let confirmationTimer, submissionIntent, confirmationSent='';
  function confirmationEvidence() {
    const pattern=/thank you for applying|application (?:has been |was )?(?:successfully )?submitted|we have received your application|application received|you (?:have )?successfully submitted your application|your application has been successfully submitted/i;
    return Array.from(document.querySelectorAll('h1,h2,h3,#confirmation,[role="dialog"],[role="status"],[data-automation-id="congratulations"],[data-automation-id="applicationConfirmation"],main p'))
      .filter(el=>visible(el)&&!el.closest('#autoapply-sidebar,[data-automation-id="jobPostingDescription"],.job-description,#job-description,[itemprop="description"]'))
      .filter(el=>!/^(?:.+\.)?myworkdayjobs\.com$/.test(location.hostname) ||
        el.matches('#confirmation,[role="dialog"],[role="status"],[data-automation-id="congratulations"],[data-automation-id="applicationConfirmation"]') ||
        el.closest('#confirmation,[role="dialog"],[role="status"],[data-automation-id="congratulations"],[data-automation-id="applicationConfirmation"]') ||
        el.matches('h1,h2,h3') && el.closest('main') && !document.querySelector('[data-automation-id="jobPostingDescription"]'))
      .map(el=>clean(el.innerText).slice(0,2000)).find(value=>pattern.test(value)) || "";
  }
  function armSubmission() {submissionIntent={url:location.href,at:Date.now(),before:confirmationEvidence()};confirmationSent='';}
  function announceSubmission(){armSubmission();chrome.runtime.sendMessage({type:'submit-intent',url:location.href,before:submissionIntent.before}).catch(()=>{});}

  function detectConfirmation() {
    const url=location.href;
    const strong=/greenhouse\.io|lever\.co/.test(location.hostname)&&/\/(confirmation|thanks)(?:[/?#]|$)/i.test(location.pathname)||/ashbyhq\.com/.test(location.hostname)&&/\/application\/.+\/submitted(?:[/?#]|$)/i.test(location.pathname);
    const evidence=confirmationEvidence();
    // A fresh document can offer a receipt candidate; only the background's
    // persisted explicit intent and native recheck can authorize that handoff.
    if(!strong && (submissionIntent?Date.now()-submissionIntent.at>120000 || evidence===submissionIntent.before:!evidence))return;
    const invalid=Array.from(document.querySelectorAll('[role="alert"],[aria-invalid="true"]')).some(el=>visible(el)&&(/error|required|invalid|missing|correct/i.test(text(el))||el.getAttribute('aria-invalid')==='true'));
    const signature=JSON.stringify([url,evidence]);
    if(!invalid&&(strong||evidence) && confirmationSent!==signature){
      confirmationSent=signature;
      chrome.runtime.sendMessage({type:'submission-confirmation',url,evidence:evidence||'Confirmation URL',strong:Boolean(strong)}).catch(()=>{confirmationSent='';});
    }
  }
  new MutationObserver(()=>{
    // Receipt proof must not be starved by a continuously mutating SPA.
    detectConfirmation();
    if(!confirmationTimer)confirmationTimer=setTimeout(()=>{confirmationTimer=undefined;detectConfirmation();},300);
  }).observe(document.body,{childList:true,subtree:true,characterData:true});
  setTimeout(detectConfirmation,300);
  let records = new Map();
  const clean = value => String(value || "").replace(/\s+/g, " ").trim();
  const text = element => clean(element?.textContent);
  const normal = value => clean(value).toLowerCase();

  // Shared document-bound repair capability for explicit and permitted recovery.
  // Values stay in this isolated document, never in the structural report.
  let repairSession, repairExecuting=false, repairSynthetic=false, repairRevision=0,repairCancellation=0;
  const repairEdited=new WeakSet();
  const repairNodes=new WeakMap();let repairNodeId=0;
  for(const event of ['beforeinput','input','change','keydown']) document.addEventListener(event,e=>{
    if(e.isTrusted && !repairSynthetic && !e.target.closest?.('#autoapply-sidebar,.autoapply-ask-ai')){
      repairRevision++;
      if(event!=='keydown')repairEdited.add(native.eventTarget(e));
      if(event!=='keydown' && analysisIdentity && !repairExecuting){
        analysisIdentity=undefined;chrome.runtime.sendMessage({type:'analysis-changed'}).catch(()=>{});
      }
    }
  },true);
  document.addEventListener('click',event=>{
    if(!event.isTrusted || repairSynthetic)return;
    const target=native.eventTarget(event);
    if(native.closest(target,'#autoapply-sidebar,.autoapply-ask-ai'))return;
    let control=native.closest(target,'[role="checkbox"],[role="radio"],[role="switch"],[role="combobox"]');
    const option=native.closest(target,'[role="option"]');
    if(!control && option)control=Array.from(records.values()).find(r=>r.field.kind==='combobox' && comboOptions(r.element).includes(option))?.element;
    if(control){repairEdited.add(control);repairRevision++;}
  },true);
  function repairShape(...args){return native.read(()=>repairShapeRead(...args));}
  function repairShapeRead(nodes){
    return JSON.stringify([location.href,workdayState()?.step,Array.from(nodes || native.query('form,h1,h2,h3,'+controlSelector))
      .filter(el=>!diagnosticExcluded(el) && !native.closest(el,'[role="listbox"],[role="menu"]')).map(el=>{
        if(!repairNodes.has(el))repairNodes.set(el,++repairNodeId);
        return [repairNodes.get(el),el.isConnected,el.tagName,el.id,el.getAttribute('name'),el.getAttribute('type'),
          el.matches('h1,h2,h3')?questionText(el):''];
      })]);
  }
  function assertRepair(token=repairSession?.token){
    if(!repairSession || token!==repairSession.token || repairSession.shape!==repairShape(repairSession.nodes) ||
       repairSession.cancellation!==repairCancellation)throw Error('Application or answer changed. Analyze page again.');
  }
  function editedRecord(record){return record?.members.some(e=>repairEdited.has(e));}
  function checkRepair(){if(repairExecuting){assertRepair();if(editedRecord(repairSession.current))throw Error('Field answer changed.');}}
  function repairDispatch(operation){
    // Native input/change events from a scripted click can themselves be trusted.
    const previous=repairSynthetic;repairSynthetic=true;
    try{return operation();}finally{repairSynthetic=previous;}
  }
  async function repairOperation(token,operation){
    assertRepair(token);repairExecuting=true;
    try{return await operation();}finally{repairExecuting=false;}
  }
  async function repairStart(token){
    if(extensionFilling || skillsState.busy)throw Error('Application is busy. Wait and retry.');
    analysisIdentity=undefined;
    const nodes=native.read(()=>Array.from(native.query('form,h1,h2,h3')).filter(el=>!diagnosticExcluded(el) && !native.closest(el,'[role="listbox"],[role="menu"]')));
    repairSession={token,nodes,shape:repairShape(nodes),revision:repairRevision,cancellation:repairCancellation,verified:new Map(),outcomes:new Map(),reasons:new Map(),menus:new Map()};
    return repairOperation(token,async()=>{
      const result=await scan({repair:true});assertRepair(token);
      const fields=repairFields();
      const before=fields.map(field=>{
        const record=records.get(field.key),ancestors=[];
        for(let parent=native.parent(record.element);parent && !parent.matches('body,html') && ancestors.length<4;parent=native.parent(parent))ancestors.push(structureOf(parent));
        return {label:field.label.slice(0,300),kind:field.kind,required:field.required,detected:true,answered:field.answered,
          options:field.options.slice(0,20).map(o=>o.slice(0,200)),control:structureOf(record.element),ancestors,reasons:[]};
      });
      return {...result,fields,before};
    });
  }
  function repairFields(){
    if(signInPage())return [];
    return Array.from(records.values()).filter(r=>!editedRecord(r) && r.members.every(e=>e.isConnected) &&
      !diagnosticExcluded(r.element) && writableRecord(r) && !isSkillsField(r.element) &&
      (!r.element.closest('a[href]') || leverResumeProxy(r.element)) &&
      (r.element.tagName!=='BUTTON' || r.element.type==='button' ||
       ['checkbox','radio'].includes(r.field.kind) && noSubmitProxyChoice(r.element)) &&
      (!isPrompt(r.element) || sourcePrompt(r.element)) && !r.dateElements && (!/_date$/.test(sectionField(r.element)) || r.field.date_context) &&
      (['text','textarea','email','tel','url','number','date','select','radio','checkbox','combobox'].includes(r.field.kind) ||
       r.field.kind==='file' && analyzeResume(r.element)))
      .map(r=>({...r.field,answered:occupied(r)}));
  }
  async function repairOptions(token,eligible){
    return repairOperation(token,async()=>{
      const keys=repairFields().filter(f=>!f.answered && f.kind==='combobox' && (!eligible || eligible.includes(f.key))).map(f=>f.key);
      await collectOptions(keys);assertRepair(token);
      return repairFields().filter(f=>!f.answered && !repairSession.verified.has(f.key));
    });
  }
  async function repairFill(token,answers,files={}){
    return repairOperation(token,async()=>{
      if(answers.length && answers.every(a=>editedRecord(records.get(a.key))))throw Error('Application answer changed.');
      const allowed=new Set(repairFields().filter(f=>!f.answered).map(f=>f.key));
      for(const answer of answers){
        assertRepair(token);
        if(!allowed.has(answer.key))continue;
        const record=records.get(answer.key);
        if(editedRecord(record))continue;
        repairSession.outcomes.set(record.element,answer.status==='OPTIONAL_SKIP' && ['optional_skip','user_skip'].includes(answer.reason_code)?'optional_skip':'unresolved');
        repairSession.reasons.set(record.element,answer.reason_code || 'needs_fact');
        const resume=answer.status==='FILE' && answer.value==='resume' && record.field.kind==='file' &&
          analyzeResume(record.element);
        if(answer.status!=='RESOLVED' && !resume)continue;
        const offers=record.field.kind==='select'?Array.from(record.element.options).filter(o=>!o.disabled && o.value!=='').map(o=>clean(o.text)):
          ['checkbox','radio'].includes(record.field.kind)?record.members.map(label):record.field.options;
        if((['combobox','select','radio'].includes(record.field.kind) || record.field.kind==='checkbox' && Array.isArray(answer.value)) &&
          !(Array.isArray(answer.value)?answer.value:[answer.value]).every(value=>offers.includes(value))){
          repairSession.outcomes.set(record.element,'verification_failed');continue;
        }
        // Only the explicit saved resume association can upload during Analyze.
        // No force, cover letters, writing, or section preparation.
        repairSession.current=record;
        let result;
        try{result=await fillFields([{key:answer.key,status:resume?'FILE':'RESOLVED',value:answer.value,source:answer.source,batch_settlement:true}],resume?{resume:files.resume}:{});}
        catch(error){if(!editedRecord(record))throw error;}
        finally{repairSession.current=undefined;}
        if(['combobox','file'].includes(record.field.kind))await pause(400);
        assertRepair(token);refreshRecord(record);
        if(editedRecord(record)){repairSession.outcomes.set(record.element,'user_edited');continue;}
        const done=result?.completed[0];
        if(done && (occupied(record) || record.field.kind==='checkbox' && answer.value===false) &&
           JSON.stringify(valueOf(record))===JSON.stringify(done.value)){
          repairSession.verified.set(answer.key,{record,value:done.value,completed:done});
          repairSession.outcomes.set(record.element,'verified');
        }else repairSession.outcomes.set(record.element,'verification_failed');
      }
      // Native events run immediately; settle once for the entire batch, then
      // verify every retained value. Custom widgets keep their own async guards.
      if(repairSession.verified.size)await pause(350);
      assertRepair(token);
      for(const [key,entry] of repairSession.verified){
        refreshRecord(entry.record);
        if(editedRecord(entry.record) || !entry.record.element.isConnected || JSON.stringify(valueOf(entry.record))!==JSON.stringify(entry.value)){
          repairSession.verified.delete(key);repairSession.outcomes.set(entry.record.element,editedRecord(entry.record)?'user_edited':'verification_failed');
        }
      }
      return {ok:true};
    });
  }
  function repairFinish(token){
    assertRepair(token);
    const completed=[];
    for(const {record,value,completed:answer} of repairSession.verified.values()){
      refreshRecord(record);
      const retained=!editedRecord(record) && record.element.isConnected && record.labelAtScan===label(record.element) &&
        (record.choiceQuestionAtScan===null || record.choiceQuestionAtScan===choiceQuestion(record.element)) &&
        JSON.stringify(valueOf(record))===JSON.stringify(value);
      repairSession.outcomes.set(record.element,retained?'verified':'verification_failed');
      if(retained)completed.push({...answer});
    }
    const filled=completed.length;
    for(const record of records.values())if(record.members.some(e=>repairEdited.has(e))){
      repairSession.outcomes.set(record.element,'user_edited');
      if(occupied(record))completed.push({...record.field,value:valueOf(record),source:'you'});
    }
    // Ordinary result data stays separate from the sanitized structural capture.
    // Reuse these records; scanning again could reopen menus or replace keys.
    return {filled,completed,page:pageInfo(),next_action:navigationState(),fields:Array.from(records.values(),record=>({...record.field,
      answered:repairSession.outcomes.get(record.element)!=='verification_failed' &&
        (occupied(record)||completed.some(answer=>answer.key===record.field.key)),
      reason_code:repairSession.reasons.get(record.element),repair_status:repairSession.outcomes.get(record.element)})),
      stopped:'none'};
  }

  function visible(element) {
    if(!element)return false;
    if (element.disabled || element.readOnly || element.matches(":disabled") || element.getAttribute("aria-disabled") === "true" || element.getAttribute("aria-readonly") === "true" || native.closest(element,"[hidden], [inert], [aria-hidden='true'], [aria-disabled='true'], [disabled]")) return false;
    const rect = element.getBoundingClientRect();
    if (!rect.width || !rect.height || rect.right + scrollX <= 0 || rect.bottom + scrollY <= 0) return false;
    for (let node = element; node instanceof Element; node = native.parent(node)) {
      const style = getComputedStyle(node);
      if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
    }
    return true;
  }

  // A hidden boolean alone cannot distinguish unanswered from an offered No.
  // Require both scoped visible offers and an explicit accessible selection state.
  function noSubmitProxyChoice(choice){
    // Omitted/invalid button types default to submit. The runtime form property
    // also detects form= associations outside the button's ancestors.
    return !choice.matches('button') || choice.type==='button' ||
      choice.type==='submit' && !(choice.form instanceof HTMLFormElement);
  }
  function yesNoProxy(element){
    if(!element?.matches('input[type="checkbox"]') || element.disabled || element.readOnly)return null;
    const wrapper=element.closest('.ashby-application-form-input-yesno');
    if(!wrapper || !visible(wrapper) || wrapper.querySelectorAll('input').length!==1)return null;
    const choices=Array.from(wrapper.querySelectorAll('button,[role="radio"],[role="button"]')).filter(el=>visible(el) && !el.matches('a[href],input'));
    if(choices.length!==2 || !choices.every(el=>/^(yes|no)$/i.test(text(el))) || new Set(choices.map(el=>normal(text(el)))).size!==2 ||
       choices.some(el=>!noSubmitProxyChoice(el) || el.matches(':disabled,[aria-disabled="true"]') || !['aria-checked','aria-pressed'].some(a=>/^(true|false)$/.test(el.getAttribute(a)||''))))return null;
    const selected=el=>el.getAttribute('aria-checked')==='true'||el.getAttribute('aria-pressed')==='true';
    const chosen=choices.filter(selected);
    return {wrapper,choices,selected,value:chosen.length===1 && element.checked===(normal(text(chosen[0]))==='yes')?text(chosen[0]):null};
  }
  function interactiveVisible(element) {
    if(yesNoProxy(element))return true;
    if (selectOnlyControl(element)) return true;
    if (visible(element)) return true;
    if (!element.matches('input[type="checkbox"],input[type="radio"]') ||
        !element.isConnected || element.disabled || element.matches(':disabled') ||
        element.closest('[hidden],[inert],[aria-hidden="true"],[aria-disabled="true"]') ||
        element.getAttribute('aria-readonly')==='true') return false;
    const rect=element.getBoundingClientRect(), style=getComputedStyle(element);
    if(rect.width<=1 || rect.height<=1 || style.display==='none' || style.visibility==='hidden' ||
        !visible(element.parentElement)) return false;
    // Only the native input may be transparent. A visible associated visual is required.
    return [...Array.from(element.labels || []), element.closest('label'),
      element.parentElement.querySelector('[data-automation-id="checkbox"],[role="checkbox"]'),
      element.parentElement.querySelectorAll('input[type="radio"],input[type="checkbox"]').length===1 &&
        !element.parentElement.querySelector('button,a[href]') && questionText(element.parentElement)?element.parentElement:null]
      .some(node=>node && visible(node) && (questionText(node) || node.getBoundingClientRect().width>1));
  }
  function selectOnlyControl(element) {
    // React Select's non-searchable input is a focus proxy, not a writable field.
    if (!element.matches('input[role="combobox"][aria-haspopup]') ||
        !(element.readOnly || element.getAttribute('aria-readonly') === 'true') ||
        !(element.inputMode === 'none' || element.getAttribute('aria-autocomplete') === 'none') ||
        !element.isConnected || element.matches(':disabled') ||
        element.closest('[hidden],[inert],[aria-hidden="true"],[aria-disabled="true"],[disabled]')) return null;
    const control = element.closest('[class*="__control"],[class*="-control"]');
    const style = getComputedStyle(element);
    for (let node = element.parentElement; node && node !== control; node = node.parentElement) {
      const parentStyle = getComputedStyle(node);
      if (parentStyle.display === 'none' || parentStyle.visibility === 'hidden' || parentStyle.opacity === '0') return null;
    }
    return control && visible(control) && style.display !== 'none' && style.visibility !== 'hidden' ? control : null;
  }
  function sameChoiceScope(a,b) {
    return a.getRootNode()===b.getRootNode() && a.form===b.form &&
      (a.form || (choiceContainer(a) || a.parentElement || a.getRootNode()) === (choiceContainer(b) || b.parentElement || b.getRootNode()));
  }
  function choiceContainer(element) {
    if (['current','fluent'].includes(sectionField(element))) return element;
    const disability=cc305Group(element);if(disability)return disability;
    const jazz=element.closest('.form-group');
    if(jazz?.querySelector('label[id^="resumator-questionnaire-"]'))return jazz;
    return native.closest(element,'[data-automation-id$="-CheckboxGroup"],[role="radiogroup"],fieldset,[data-automation-id^="formField"],[role="group"]');
  }
  function questionContainer(container) {
    if(!container) return container;
    for(let node=container;node && !node.matches('form,body');node=node.parentElement) {
      if (Array.from(node.children).some(child=>child.matches('legend,label,[data-automation-id="formLabel"],[data-automation-id="richText"]') && usableLabel(questionText(child))) ||
          usableLabel(referencedLabel(node,'aria-labelledby')) || usableLabel(node.getAttribute('aria-label'))) return node;
    }
    return container;
  }
  const controlRoots=new WeakMap();
  function currentControl(element) {
    if(element.isConnected)return element;
    const oldRoot=controlRoots.get(element) || element.getRootNode();
    const root=oldRoot===document?document:oldRoot.host?.isConnected?oldRoot.host.shadowRoot:null;
    const replacement=(element.id ? root?.getElementById?.(element.id) : element.dataset.autoapplyKey ? root?.querySelector('[data-autoapply-key="'+CSS.escape(element.dataset.autoapplyKey)+'"]') : null);
    if(replacement)controlRoots.set(replacement,root);
    return replacement || element;
  }
  function refreshRecord(record) {
    if(!record) return record;
    record.element=currentControl(record.element);
    record.members=record.members.map(currentControl);
    return record;
  }

  function usableLabel(value) {
    value = clean(value).replace(/(?:\s*\*|\s+(?:required|optional))+$|^(?:required|optional)$/ig, '').trim();
    const tokens = normal(value).replace(/[^a-z0-9]+/g, ' ').trim();
    if (!tokens || /^(?:(?:select|one|choose|please|required|optional|search)\s*)+$/.test(tokens)) return '';
    if (!/\s/.test(value) && (/--|_|^(?:formField|input|field|question)[-:]|[a-f0-9]{16,}|^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(value))) return '';
    return value;
  }
  function questionText(element) {
    if (!element) return '';
    const copy=element.cloneNode(true);
    copy.querySelectorAll('input,textarea,select,[contenteditable],script,style,[data-automation-id="selectedItem"],[class*="single-value"],[class*="singleValue"]').forEach(node=>node.remove());
    return text(copy);
  }
  function leverQuestionLabel(element) {
    const question=element.closest('li.application-question');
    if(!question || !element.closest('.application-field'))return null;
    // Bound the label to this question. Upload/location status text and a
    // previous question or company heading are not the applicant's question.
    const node=Array.from(question.children).find(child=>child.matches('label,.application-label'));
    if(!node)return 'Unlabelled field';
    const copy=node.cloneNode(true);
    copy.querySelectorAll('.application-field,input,textarea,select,[role="status"],[role="alert"],.error,.loading').forEach(child=>child.remove());
    return usableLabel(text(copy)) || 'Unlabelled field';
  }
  function label(element) {
    const disclosure=cc305Option(element);if(disclosure)return disclosure;
    const lever=leverQuestionLabel(element);
    if(lever!==null)return lever;
    const sources = [Array.from(element.labels || []).map(questionText).join(' '), questionText(element.closest('label')),
      referencedLabel(element, 'aria-labelledby')];
    const container = element.closest('[data-automation-id^="formField"],fieldset,[role="group"]');
    if (container) {
      sources.push(referencedLabel(container, 'aria-labelledby'));
      sources.push(...Array.from(container.querySelectorAll('legend,label,[data-automation-id="formLabel"],[data-automation-id="richText"]'))
        .filter(node => !node.contains(element) && visible(node) &&
          Boolean(node.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING)).map(questionText));
    }
    sources.push(element.getAttribute('aria-label'));
    // Walk nearby preceding text before considering a placeholder; never expose control IDs.
    for (let node = element, depth = 0; node && node !== document.body && depth < 5; node = node.parentElement, depth++) {
      for (let sibling = node.previousElementSibling; sibling; sibling = sibling.previousElementSibling) {
        if (sibling.matches('input,select,textarea,button') || sibling.querySelector('input,select,textarea,button')) break;
        if (visible(sibling)) sources.push(questionText(sibling));
      }
      if (node === container) break;
    }
    sources.push(element.getAttribute('placeholder'));
    const found=sources.map(usableLabel).find(Boolean);
    if(!found || /^(?:address|contact address)\s*\*?$/i.test(found)) {
      const semantic=normal([element.id,element.name,element.placeholder].join(' ')).replace(/[_-]/g,' ');
      if(/\bcity\b/.test(semantic))return 'City';
      if(/\b(state|province)\b/.test(semantic))return 'State/Province';
      if(/\b(postal|zip)\b/.test(semantic))return 'Postal code';
    }
    return found || 'Unlabelled field';
  }

  function choiceQuestion(element) {
    const options=new Set(['yes','no']);
    const option=clean(Array.from(element.labels||[]).map(questionText).find(value=>options.has(normal(value))) || '');
    let question=(element.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>questionText(native.byId(element,id)))
      .filter(value=>value && !options.has(normal(value))).join(' ');
    // Pinpoint puts the question and option in one hidden aria label.
    if(option && question.toLowerCase().endsWith(' '+option.toLowerCase()))question=question.slice(0,-option.length).trim();
    return question;
  }
  function groupLabel(container, fallback) {
    container=questionContainer(container);
    return [...Array.from(container?.querySelectorAll('legend,[data-automation-id="formLabel"],[data-automation-id="richText"],label') || []).filter(node=>!node.querySelector('input,select,textarea') && (node.tagName!=='LABEL' || !node.htmlFor || native.byId(node,node.htmlFor)?.type==='hidden')).map(questionText), container?.getAttribute('aria-label'),
      container ? referencedLabel(container, 'aria-labelledby') : '',
      text(container?.querySelector('h1,h2,h3,h4,[role="heading"]')), fallback].map(usableLabel).find(Boolean) || 'Unlabelled field';
  }

  function commonContainer(members) {
    let container = members[0].parentElement;
    while (container && !members.every(member => container.contains(member))) container = container.parentElement;
    return container;
  }

  function nearbyQuestion(element) {
    // Find the closest question without borrowing text across another control.
    for(let node=element,depth=0;node && !node.matches('form,body,html') && depth<6;node=node.parentElement,depth++){
      const named=referencedLabel(node,'aria-labelledby') || node.getAttribute('aria-label');
      if(named && usableLabel(named) && !/^(?:yes|no|application questions)$/i.test(clean(named)))return usableLabel(named);
      for(let sibling=node.previousElementSibling;sibling;sibling=sibling.previousElementSibling){
        if(sibling.matches(controlSelector) || sibling.querySelector(controlSelector))break;
        const value=usableLabel(questionText(sibling));
        if(visible(sibling) && value && !/^(?:yes|no|application questions)$/i.test(value))return value;
      }
    }
    return '';
  }
  function recoveredChoiceLabel(element,container,fallback) {
    const grouped=choiceQuestion(element) || groupLabel(container,fallback);
    return /^(?:application questions|unlabelled field|yes|no)$/i.test(clean(grouped)) || grouped===fallback ? nearbyQuestion(container || element) || grouped : grouped;
  }

  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function waitFor(read, timeout = 1000) {
    const end = Date.now() + timeout;
    do {
      const value = read();
      if (value) return value;
      await pause(40);
    } while (Date.now() < end);
    return null;
  }
  function keypress(element, key) {
    checkRepair();
    trace("keyboard","keydown "+key,element);
    for (const type of ["keydown", "keyup"]) element.dispatchEvent(new KeyboardEvent(type, {key, code: key, bubbles: true}));
  }
  function comboContainer(element) {
    const workday = element.closest('[data-automation-id="multiSelectContainer"],[data-automation-id^="formField"]');
    if (workday) return workday;
    const control = element.closest('[class*="-control"],[class*="__control"]');
    if (control) return control.parentElement;
    // Some react-select themes omit control classes and nest the input separately.
    for (let container = element.parentElement; container && !container.matches("form,body"); container = container.parentElement) {
      const inputs = container.querySelectorAll('input:not([type="hidden"]),textarea,select,[role="combobox"]');
      if (Array.from(inputs).some(input => input !== element && !element.contains(input))) break;
      if (container.querySelector('[class*="singleValue"],[class*="single-value"]')) return container;
    }
    return element.parentElement || element;
  }
  const openedCombos=new WeakSet(), comboBindings=new WeakMap();
  let comboOwner;
  const popupSelector='[data-automation-id="responsiveMonikerPrompt"],[data-automation-id="activeListContainer"],[role="listbox"]:not([data-automation-id="selectedItemList"])';
  function visiblePopups(element){
    const nodes=Array.from(element.getRootNode().querySelectorAll(popupSelector)).filter(node=>visible(node) &&
      !node.closest('[data-automation-id="selectedItemList"]') &&
      Array.from(node.querySelectorAll('[role="option"],[data-automation-id="menuItem"],[data-automation-id="promptOption"]')).some(visible));
    return nodes.filter(node=>!nodes.some(parent=>parent!==node && parent.contains(node)));
  }
  const settledChoiceNodes=new WeakMap();
  const choiceObservations=new WeakMap();
  let choiceRevision=0;
  for (const type of ['input','change']) document.addEventListener(type,event=>{
    if (!event.target.closest?.('#autoapply-sidebar')) choiceRevision++;
  },true);
  function rememberChoices(record) {
    choiceObservations.set(record.element,{url:location.href,identity:snapshotIdentity(),revision:choiceRevision,
      label:record.field.label,options:[...record.field.options]});
  }
  function comboOptions(element) {
    const ids = clean(element.getAttribute("aria-controls") || element.getAttribute("aria-owns") || element.getAttribute("list")).split(" ");
    const lists = ids.map(id => native.byId(element,id)).filter(node=>node && (node.tagName==='DATALIST' || visible(node)));
    if (!lists.length) {
      const container=comboContainer(element);
      if(!container.matches('body,html,form,main'))lists.push(container);
      const binding=comboBindings.get(element);
      if(binding && comboOwner===element && openedCombos.has(element)){
        if(binding.popup?.isConnected && visible(binding.popup))lists.push(binding.popup);
        else {
          const candidates=visiblePopups(element).filter(node=>!binding.before.has(node));
          // An unlabelled body portal belongs only to the control that opened
          // it, and only when exactly one new popup appeared. Old portals stay excluded.
          if(candidates.length===1){binding.popup=candidates[0];lists.push(binding.popup);}
        }
      }
    }
    const selector = '[role="option"],option,[data-automation-id="menuItem"],[data-automation-id="promptOption"]' +
      (selectOnlyControl(element) ? ',.react-select__menu .react-select__option' : '');
    return [...new Set(lists.flatMap(list => Array.from(list.querySelectorAll(selector)).map(option=>option.closest('[data-automation-id="menuItem"]') || option)))]
      .filter(option => !option.closest('[data-automation-id="selectedItemList"]') && !option.disabled && option.getAttribute("aria-disabled") !== "true" &&
        (text(option) || option.getAttribute('aria-label') || option.getAttribute('data-automation-label')) && !/^(loading\b|no (?:options|results|items)\b)/i.test(text(option)) &&
        (option.tagName === "OPTION" || visible(option)));
  }
  async function settledComboOptions(element, timeout = 6000) {
    const end = Date.now() + timeout;
    let previous = "", stableSince = Date.now();
    do {
      const options = comboOptions(element);
      const signature = JSON.stringify(options.map(text));
      if (!options.length || signature !== previous) stableSince = Date.now();
      else if (Date.now() - stableSince >= 250){settledChoiceNodes.set(element,options);trace("dropdown","options seen",element,{texts:options.slice(0,30).map(text),total:options.length});return options;}
      previous = signature;
      await pause(50);
    } while (Date.now() < end);
    trace('dropdown','skipped',element,{reason:'options did not settle'});
    return [];
  }
  function bindCombo(element){
    if(comboOwner && comboOwner!==element)closeCombo(comboOwner);
    if(!openedCombos.has(element))comboBindings.set(element,{before:new Set(visiblePopups(element))});
    comboOwner=element;openedCombos.add(element);
  }
  async function openCombo(element) {
    checkRepair();
    trace("dropdown","opened",element);
    bindCombo(element);
    // Some button menus ignore Escape. Reuse the same still-visible observation
    // rather than activating the control again and mounting duplicate portals.
    const retained=settledChoiceNodes.get(element), current=comboOptions(element);
    if (!remoteAutocomplete(element) && retained?.length && retained.length===current.length &&
        retained.every((option,index)=>option===current[index])) return;
    const selectOnly = selectOnlyControl(element);
    if (selectOnly) {
      if (element.getAttribute('aria-expanded') !== 'true') pointerClick(selectOnly);
      return;
    }
    if (element.readOnly || element.getAttribute('aria-readonly') === 'true') return;
    element.focus();
    if (element.matches('button:is([aria-haspopup="listbox"],[id$="--degree"])')) { element.click(); return; }
    keypress(element, "ArrowDown");
    if (!element.getAttribute("list") && !await waitFor(() => comboOptions(element).length, 120) && element.getAttribute('aria-expanded') !== 'true') {
      checkRepair();element.click();
      if (await waitFor(() => comboOptions(element).length, 120) || element.getAttribute('aria-expanded') === 'true') return;
      const control = element.closest('[class*="-control"],[class*="__control"]') || comboContainer(element) || element;
      const toggle = control.querySelector('button[type="button"][aria-label*="Toggle"],button[type="button"][aria-label*="Open"],[class*="dropdown-indicator"],[class*="indicatorContainer"]');
      const target = toggle && visible(toggle) ? toggle : control;
      checkRepair();target.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, button: 0}));
      target.click();
    }
  }
  function closeCombo(element) {
    checkRepair(); keypress(element, "Escape"); comboInput(element)?.blur();element.blur();
    // Target the inert body surface itself, never a neighbouring control or pill.
    if(comboOwner===element && comboOptions(element).length){
      for(const type of ['mousedown','mouseup','click'])repairDispatch(()=>document.body.dispatchEvent(new MouseEvent(type,{bubbles:true,button:0})));
    }
    openedCombos.delete(element);comboBindings.delete(element);settledChoiceNodes.delete(element);
    if(comboOwner===element)comboOwner=undefined;
  }
  function comboInput(element) {
    return element.matches("input,textarea") ? element : element.querySelector("input,textarea") ||
      (element.isContentEditable ? element :
        comboContainer(element).querySelector('input[data-automation-id="searchBox"],input[role="searchbox"]'));
  }
  function searchCombo(element, value) {
    checkRepair();
    const input = comboInput(element);
    if (!input || input.readOnly || input.disabled || input.getAttribute('aria-readonly') === 'true') return false;
    input.focus();
    if (input instanceof HTMLInputElement) {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, value);
    } else if ("value" in input) nativeSet(input, "value", value);
    else input.textContent = value;
    // No Enter key or change event: searching must never submit the form.
    input.dispatchEvent(new InputEvent("input", {bubbles: true, composed: true,
      inputType: value ? "insertText" : "deleteContentBackward", data: value || null}));
    trace("search","typed",element,{value});
    return true;
  }
  function comboBackingInput(container) {
    // Popup search/mirror inputs are not the control's submitted selection.
    return Array.from(container.querySelectorAll('input[type="hidden"]')).find(input=>
      !input.closest('[role="listbox"],[role="menu"],[role="option"],.react-select__menu'));
  }
  function comboSelection(element) {
    const container = comboContainer(element);
    const hidden=clean(comboBackingInput(container)?.value);
    const accessible=clean(element.getAttribute('aria-valuetext'));
    return text(container.querySelector('[class*="singleValue"],[class*="single-value"]')) ||
      (accessible && !/^(select|choose|search)(?:\s*one)?(?:\.{3}|…)?$/i.test(accessible)?accessible:'') ||
      (hidden && !/^(select|choose|search)(?:\s*one)?(?:\.{3}|…)?$/i.test(hidden) ? hidden : '') ||
      text(container.querySelector('[data-automation-id="selectedItem"],[data-automation-id="selectedItemLabel"]')) ||
      (element.matches('button:is([aria-haspopup="listbox"],[id$="--degree"])') && !/^(select|choose)(\s*one)?(?:\.{3}|…)?$/i.test(text(element)) ? text(element) : "") ||
      (element.matches('input[role="combobox"]') && element.getAttribute('aria-expanded')==='false' &&
        !remoteAutocomplete(element) && !/^(select|choose|search)(?:\s*one)?(?:\.{3}|…)?$/i.test(clean(element.value)) ? clean(element.value) : '');
  }
  function referencedLabel(element, attribute) {
    return (element.getAttribute(attribute) || "").split(/\s+/)
      .map(id => questionText(native.byId(element,id))).filter(Boolean).join(" ");
  }
  function usableFileLabel(value) {
    value = usableLabel(value);
    // Upload buttons describe an action, not the field's purpose.
    if (!/\b(resume|cv|cover)\b/i.test(value) && /^(attach|upload|drop|choose|browse|select|drag)\b/i.test(value)) return "";
    return value;
  }
  function uploadNodes(container, selector) {
    // Composed ancestry owns descendants; IDREFs still resolve in their own tree.
    return native.query(selector).filter(node=>{
      for(let parent=native.parent(node);parent;parent=native.parent(parent))if(parent===container)return true;
      return false;
    });
  }
  function semanticUploadLabel(element) {
    for(let group=native.parent(element);group && !group.matches('form,body,html');group=native.parent(group)) {
      // Never borrow a section title across sibling attachment controls.
      if(uploadNodes(group,'input[type="file"]').length!==1)return '';
      if(!group.matches('fieldset,[role="group"],[role="region"],section[aria-label],section[aria-labelledby]'))continue;
      const title=usableFileLabel(group.getAttribute('aria-label')) ||
        usableFileLabel(referencedLabel(group,'aria-labelledby')) ||
        usableFileLabel(text(group.querySelector(':scope > legend')));
      if(title && visible(group))return title;
    }
    return '';
  }
  function uploadGroupLabel(container, element) {
    if (uploadNodes(container,'input[type="file"]').length > 1) return "";
    const accessible = usableFileLabel(container.getAttribute("aria-label")) ||
      usableFileLabel(referencedLabel(container, "aria-labelledby")) ||
      usableFileLabel(referencedLabel(container, "aria-describedby"));
    if (accessible) return accessible;
    const labels = uploadNodes(container,'label,legend,[role="heading"],h1,h2,h3,h4,[class*="label"],[id^="upload-label-"]')
      .filter(node => node.getRootNode()===element.getRootNode() && node.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING)
      .map(node => usableFileLabel(text(node))).filter(Boolean);
    if (labels.length) return labels[labels.length - 1];
    for (let sibling = container.previousElementSibling; sibling; sibling = sibling.previousElementSibling) {
      if (sibling.matches('input,select,textarea') || sibling.querySelector('input,select,textarea')) break;
      if (sibling.matches('label,legend,[role="heading"],h1,h2,h3,h4,[class*="label"],[id^="upload-label-"]')) {
        const value = usableFileLabel(text(sibling));
        if (value) return value;
      }
    }
    return "";
  }
  function resumeAffordance(element) {
    if(element.id!=='resumator-resume-value' || !element.closest('#resumator-resume'))return null;
    return Array.from(element.closest('#resumator-resume').querySelectorAll('a#resumator-choose-upload,a#resumator-resume-switch-upload'))
      .find(link=>visible(link)&&/^attach resume(?: file)?$/i.test(text(link)));
  }
  function analyzeResume(element) {
    if(!element?.matches('input[type="file"]') || element.disabled || element.readOnly ||
       element.getAttribute('aria-readonly')==='true' ||
       native.closest(native.parent(element),'[hidden],[inert],[aria-hidden="true"],[aria-disabled="true"],[disabled]') ||
       !/\b(resume|cv)\b/i.test(fileLabel(element)) || /\bcover\b/i.test(fileLabel(element)))return false;
    return exposedFile(element);
  }
  function leverResumeProxy(element) {
    if(location.hostname!=='jobs.lever.co' || !element.matches('input[type="file"][name="resume"]#resume-upload-input') ||
       element.disabled || element.readOnly || element.closest('[hidden],[inert],[aria-hidden="true"],[disabled]'))return false;
    const proxy=element.parentElement,question=element.closest('li.application-question.resume');
    if(!proxy?.matches('a.visible-resume-upload') || !question || !visible(proxy) ||
       question.querySelectorAll('input[type="file"]').length!==1 ||
       !/\b(resume|cv)\b/i.test(leverQuestionLabel(element) || ''))return false;
    const rect=element.getBoundingClientRect(),style=getComputedStyle(element);
    return rect.width>0 && rect.height>0 && style.display!=='none' && style.visibility==='visible' &&
      (!proxy.getAttribute('href') || proxy.getAttribute('href')==='#');
  }
  function workdayUploadGroup(element){
    if(element.getAttribute('data-automation-id')!=='file-upload-input-ref')return null;
    const group=native.closest(element,'[data-automation-id="attachments-FileUpload"],[data-automation-id="fileUpload"],[data-automation-id^="formField"]');
    if(!group || !visible(group) || uploadNodes(group,'input[type="file"]').length!==1)return null;
    const buttons=uploadNodes(group,'button[data-automation-id="select-files"]').filter(visible);
    return buttons.length===1 || group.matches('[data-automation-id^="formField"]') && /\b(resume|cv)\b/i.test(uploadGroupLabel(group,element))?group:null;
  }
  function exposedFile(element) {
    if(leverResumeProxy(element))return true;
    if(element.getAttribute('data-automation-id')==='file-upload-input-ref' && !visible(element))return Boolean(workdayUploadGroup(element));
    if(visible(element) || resumeAffordance(element) || attachedFile(element))return true;
    const container=fileContainer(element);
    if(!visible(container) || !/\b(resume|cv|cover)\b/i.test(fileLabel(element)))return false;
    // Workday keeps this input hidden even in the active, visible resume section.
    if(element.getAttribute('data-automation-id')==='file-upload-input-ref')return Boolean(workdayUploadGroup(element));
    return uploadNodes(container,'button,[role="button"],label').some(node=>visible(node) &&
      /^(attach|upload|browse|choose|select)\b/i.test(text(node))) || Array.from(element.labels||[]).some(visible);
  }
  function fileContainer(element) {
    const workday=workdayUploadGroup(element);if(workday)return workday;
    let container = native.parent(element);
    while (container && !container.matches("form,body")) {
      if (uploadGroupLabel(container, element)) return container;
      container = native.parent(container);
    }
    const parent=native.parent(element);
    return parent && !parent.matches('form,body,html')?parent:element;
  }
  function fileLabel(element) {
    const lever=leverQuestionLabel(element);
    if(lever!==null)return lever;
    const direct = [Array.from(element.labels || []).map(text).filter(Boolean).join(" "),
      element.getAttribute("aria-label"), referencedLabel(element, "aria-labelledby"),
      referencedLabel(element, "aria-describedby")].map(usableFileLabel).find(Boolean);
    const container = fileContainer(element);
    const group = uploadGroupLabel(container, element);
    const local=direct || group || '';
    // Specific local attachment purposes take precedence over an outer resume title.
    const semantic=/\b(resume|cv|cover|transcript|photo|picture|certificate|portfolio)\b/i.test(local)?'':semanticUploadLabel(element);
    const identity = normal([element.id, element.name, element.accept, element.getAttribute("aria-label"),
      element.getAttribute("aria-labelledby"), element.getAttribute("aria-describedby"), direct, group, semantic].join(" ")).replace(/[_-]/g, " ");
    const specific=local || semantic;
    if(/\b(transcript|photo|picture|certificate|portfolio)\b/i.test(specific) && !/\b(resume|cv|cover)\b/i.test(specific))return specific;
    const purpose = /\bcover\b/.test(identity) ? "Cover Letter" : /\b(resume|cv)\b/.test(identity) ? "Resume/CV" : "";
    const fieldLabel = direct || group || (purpose?'':semantic);
    return purpose && !/\b(resume|cv|cover)\b/i.test(fieldLabel || "") ? purpose : fieldLabel || purpose ||
      'File attachment';
  }
  function attachedFile(element) {
    const container = fileContainer(element);
    if(uploadStates.get(container)?.confirmed===false || uploadError(element,container))return '';
    const shown = uploadNodes(container,'*').filter(visible).map(text);
    const filename = shown.map(value => value.match(/[^\s<>]+\.(?:pdf|docx?|rtf|txt|odt)\b/i)?.[0]).find(Boolean);
    return filename || (shown.some(value => /successfully uploaded/i.test(value)) ? 'Already attached' : '');
  }
  function filenameShown(element, filename) {
    const container = fileContainer(element);
    return [container, ...uploadNodes(container,'*')].some(node => visible(node) &&
      Array.from(node.childNodes).some(child => child.nodeType === Node.TEXT_NODE && clean(child.textContent).includes(filename)));
  }
  const uploadStates=new WeakMap();
  function uploadError(element, container) {
    const field=native.closest(native.parent(container),'[data-automation-id^="formField"]');
    const region=field && uploadNodes(field,'input[type="file"]').length===1?field:container;
    return uploadNodes(region,'[role="alert"],[class*="error"],[class*="Error"],[data-automation-id="inputAlert"]')
      .find(node=>visible(node) && text(node) && (node.matches('[data-automation-id="inputAlert"]') || /upload|file|attach/i.test(text(node))));
  }
  const sectionHeadings = 'h1,h2,h3,h4,h5,h6,[role="heading"],[data-automation-id="sectionTitle"],[class*="section-title"],[class*="section__title"]';
  function sectionToken(node) {
    const tokens = sectionNormal([node.id, node.getAttribute('class'), node.getAttribute('data-automation-id')].filter(Boolean).join(' '));
    const names = [
      [/\beducation\b/, 'Education'], [/\b(experience|employment|work history)\b/, 'Work experience'],
      [/\bprojects?\b/, 'Projects'], [/\bskills?\b/, 'Skills'], [/\blanguages?\b/, 'Languages'],
      [/\bphone\b/, 'Phone'], [/\baddress\b/, 'Address'], [/\beeo\b/, 'EEO'],
      [/\bdemographics?\b/, 'Demographics'], [/\bself identify\b/, 'Self-identify'], [/\bdisability\b/, 'Disability']
    ];
    return names.find(([pattern]) => pattern.test(tokens))?.[1] || '';
  }
  function sectionHint(element) {
    for (let container = element.parentElement; container && container !== document.body; container = container.parentElement) {
      const hint = sectionToken(container);
      if (hint) return hint;
    }
    for (let fieldset = element.closest('fieldset'); fieldset; fieldset = fieldset.parentElement?.closest('fieldset')) {
      const legend = fieldset.querySelector(':scope > legend');
      if (text(legend)) return text(legend);
    }
    const headings = Array.from(document.querySelectorAll(sectionHeadings)).filter(heading => visible(heading) &&
      !heading.closest('.visually-hidden,.sr-only') && heading.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING);
    return text(headings[headings.length - 1]);
  }

  function pageInfo() {
    if(globalThis.autoApplyCredentials?.active() || globalThis.autoApplyCredentials?.incomplete())return {url:location.origin+location.pathname,title:globalThis.autoApplyCredentials.active()?"Account sign-in":"Application scan incomplete",company:"",description:""};
    const postings=[];
    const postingIdentity=value=>{
      try {
        const url=new URL(value,location.href),path=url.pathname.replace(/\/apply(?:\/.*)?$/i,'');
        const id=url.hostname.endsWith('.myworkdayjobs.com') && /\/job\/[^/]+\/[^/]+_([^/]+)$/.exec(path);
        return id?url.origin+':'+id[1]:url.origin+path;
      } catch {return null;}
    };
    function find(value) {
      if (!value || typeof value !== "object") return;
      if ([value["@type"]].flat().includes("JobPosting")) postings.push(value);
      for (const child of Object.values(value)) if (typeof child === "object") {
        for (const entry of Array.isArray(child) ? child : [child]) find(entry);
      }
    }
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      try { find(JSON.parse(script.textContent)); } catch { /* Malformed metadata is common. */ }
    }
    const matches=postings.filter(p=>typeof p.url==='string' && postingIdentity(p.url)===postingIdentity(location.href));
    const landing=/\/job\//.test(location.pathname) && !/\/apply(?:\/|$)/.test(location.pathname);
    // A recommendation elsewhere in the JSON-LD graph is a different job.
    const posting=matches.length===1?matches[0]:!matches.length && postings.length===1 && !postings[0].url && landing?postings[0]:null;
    // Template contents stay inert, including images embedded in job metadata.
    const description = document.createElement("template");
    description.innerHTML = typeof posting?.description === "string" ? posting.description : "";
    const publicDescription=text(description.content) || text(document.querySelector('[data-automation-id="jobPostingDescription"],.job-description,#job-description,[itemprop="description"]'));
    const jobLocations=[];
    for(const entry of [posting?.jobLocation].flat().filter(Boolean)) {
      const address=entry.address || {}, country=address.addressCountry;
      jobLocations.push({city:clean(address.addressLocality),region:clean(address.addressRegion),country:clean(typeof country==='string'?country:country?.name)});
    }
    // Only the public posting location region, never cities mentioned in form prose.
    if(!jobLocations.length && /\/job\//.test(location.pathname) && !/\/apply(?:\/|$)/.test(location.pathname)) {
      for(const node of document.querySelectorAll('[data-automation-id="locations"],[data-automation-id="location"]')) {
        if(node.closest('form,[role="form"]'))continue;
        const hit=/^(?:US\s*[-,]\s*)?(.+?),\s*([A-Z]{2})(?:,\s*(US|USA|United States))?$/.exec(text(node));
        if(hit)jobLocations.push({city:hit[1],region:hit[2],country:'US'});
      }
    }
    const publicPosting=Boolean(posting || publicDescription && /\/job\//.test(location.pathname) && !/\/apply(?:\/|$)/.test(location.pathname));
    return {
      url: location.href,
      title: clean(posting?.title) || document.title,
      company: clean(document.querySelector('meta[property="og:site_name"]')?.content) || clean(posting?.hiringOrganization?.name) || document.title.split(/\s[|–-]\s/).pop(),
      description: (publicDescription || (!location.hostname.endsWith('.myworkdayjobs.com') ? text(document.querySelector('main,article')) : '')).slice(0,6000),
      ...(publicPosting?{posting_url:typeof posting?.url==='string'?posting.url:location.href.replace(/\/apply(?:\/.*)?$/i,''),context_source:posting?'JobPosting':'workday_public',job_locations:jobLocations.slice(0,20)}:{})
    };
  }

  function signInPage() {
    return Boolean(globalThis.autoApplyCredentials?.active());
  }

  function manualEntry() {
    return Array.from(document.querySelectorAll('[data-automation-id="applyManually"]')).find(visible);
  }

  function applicationEntry() {
    if (signInPage() || /review/i.test(workdayState()?.step || '')) return false;
    const manual = manualEntry();
    // Entry must never replace a real form, including an exposed shadow form.
    const roots=inspectionRoots();
    if(roots.truncated || scannerControls().length || roots.some(({root})=>
      Array.from(root.querySelectorAll(controlSelector)).some(e=>!diagnosticExcluded(e) && visible(e))))return false;
    const activeStep=workdayState()?.step;
    const workdayLanding=!/review/i.test(activeStep || '') && !document.querySelector('[data-automation-id="progressBar"],[data-automation-id="progressBarActiveStep"]') &&
      (/^(?:.+\.)?myworkdayjobs\.com$/.test(location.hostname) && /\/job\//.test(location.pathname) ||
       document.querySelector('[data-automation-id="jobPostingPage"],[data-automation-id="jobPostingHeader"]'));
    const smartLanding=location.hostname==='jobs.smartrecruiters.com' && /^\/[^/]+\/\d+\/?$/.test(location.pathname);
    const keysightLanding=location.hostname==='jobs.keysight.com' && /^\/external\/jobs\/\d+\/?$/.test(location.pathname);
    const entries=manual?[manual]:Array.from(document.querySelectorAll('button,a[href],[role="button"],[role="link"]')).filter(element => {
      const caption=clean(element.getAttribute('aria-label') || element.textContent);
      if(!visible(element) || element.type==='submit' || element.form || element.closest('form,[role="form"]'))return false;
      if(smartLanding && /^i['\u2019]m interested$/i.test(caption)) {
        if(element.tagName!=='A')return false;
        try {
          const target=new URL(element.href,location.href);
          return !target.username && !target.password && target.origin===location.origin &&
            /^\/oneclick-ui\/company\/[^/]+\/publication\/[a-f\d-]+\/?$/i.test(target.pathname) &&
            target.pathname.split('/')[3]===location.pathname.split('/')[1];
        } catch {return false;}
      }
      return /^(apply|apply for this job)$/i.test(caption) ||
        keysightLanding && /^apply now$/i.test(caption) || workdayLanding && /^continue application$/i.test(caption);
    });
    // Repeated anchors to the same entry (sticky/footer/header) are one action.
    const targets=new Set(entries.map(element=>element.tagName==='A'?element.href:element));
    const entry=entries[0];
    if (!entry || targets.size!==1 || entry.type === "submit" || entry.form || entry.closest('form,[role="form"]')) return false;
    if(entry.tagName==='A'){try{const target=new URL(entry.href);if(!/^https?:$/.test(target.protocol) || target.username || target.password || /^(?:.+\.)?myworkdayjobs\.com$/.test(location.hostname) && (target.origin!==location.origin || entry.target && entry.target!=='_self'))return false;}catch{return false;}}
    return entry;
  }
  function enterApplication() {
    const entry=applicationEntry();if(!entry)return false;
    trace('entry', 'click', null, {caption:(entry.getAttribute('aria-label') || entry.textContent).trim().slice(0,80)});
    // Exact entry labels only: never Next, Save and Continue, or Submit.
    if (entry.tagName === 'A') {
      let url;try {url=new URL(entry.href);} catch {return false;}
      if (!/^https?:$/.test(url.protocol) || url.username || url.password) return false;
      if(/^(?:.+\.)?myworkdayjobs\.com$/.test(location.hostname))entry.click();
      else location.assign(url.href);
    } else entry.click();
    return true;
  }

  const controlSelector='input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="combobox"],button:is([aria-haspopup="listbox"],[id$="--degree"]),[role="textbox"],[role="checkbox"],[role="radio"],[role="switch"]';
  function robotOnly(element){
    const attributes=Array.from(element.attributes||[]).filter(a=>/^(?:data-|id$|name$|class$)/i.test(a.name)).map(a=>a.name+' '+a.value).join(' ');
    const warning=[label(element),element.getAttribute('aria-description'),referencedLabel(element,'aria-describedby'),element.placeholder].join(' ');
    return /beecatcher|honey[\s_-]*pot/i.test(attributes) || /robots?\s+only|(?:do\s+not|don.t)\s+(?:enter|fill|complete).*\bhuman\b|leave\s+(?:this\s+)?(?:field\s+)?(?:blank|empty).*\bhuman\b/i.test(warning);
  }
  function writableRecord(record){
    return record.members.every(el=>!robotOnly(el) && !el.disabled && !el.matches(':disabled,[aria-disabled="true"]') &&
      !native.closest(el,'[inert],[aria-hidden="true"],[aria-disabled="true"]') &&
      !native.closest(record.field.kind==='file'?native.parent(el):el,'[hidden]') &&
      (!(el.readOnly || el.getAttribute('aria-readonly')==='true') || selectOnlyControl(el) ||
        el.matches('input[type="radio"],input[type="checkbox"]') && el.getAttribute('aria-readonly')!=='true' && interactiveVisible(el)) &&
      (record.field.kind==='file'?exposedFile(el):interactiveVisible(el)));
  }
  function excludedControl(element){
    return Boolean(native.closest(element,'#autoapply-sidebar,.autoapply-ask-ai,nav,header,[role="navigation"],[role="search"],[data-automation-id="utilityButtonBar"]')) ||
      element.matches('[data-automation-id="utilityMenuButton"],input[type="search"]') ||
      /\bsearch\b/i.test(label(element)) && !isPrompt(element) && !native.closest(element,'form,[role="form"]');
  }
  function supportedControl(element){
    // Text needs native/editable semantics; ARIA choices need explicit checked state and keyboard focus.
    if(element.getRootNode()!==document && !element.matches('input,textarea,select'))return false;
    if(element.matches('input[role="combobox"]') && !['hidden','password','submit','reset','image'].includes(element.type))return true;
    if(element.matches('input'))return ['text','email','tel','url','number','date','checkbox','radio','file'].includes(element.type);
    if(element.matches('[role="checkbox"],[role="radio"],[role="switch"]'))return noSubmitProxyChoice(element) && element.tabIndex>=0 && ['true','false'].includes(element.getAttribute('aria-checked'));
    return element.matches('textarea,select,[role="combobox"],button[aria-haspopup="listbox"],button[id$="--degree"]') || element.isContentEditable;
  }
  function inputInventory(roots=native.discover()) {
    return native.query(controlSelector,roots).filter(element=>!excludedControl(element) && !diagnosticExcluded(element) &&
      !element.parentElement?.closest('[role="combobox"],[contenteditable]:not([contenteditable="false"])'));
  }
  function scannerControls(...args){return native.read(()=>scannerControlsRead(...args));}
  function scannerControlsRead() {
    return inputInventory().filter(element=>supportedControl(element) &&
      (element.type==='file' ? !element.disabled && !element.matches(':disabled') && exposedFile(element) : interactiveVisible(element)));
  }
  const learningRoots=new WeakSet();
  function observeNativeEdits(){
    for(const {root} of native.discover())if(root!==document && !learningRoots.has(root)){
      learningRoots.add(root);
      for(const type of ['change','blur','focusout'])root.addEventListener(type,event=>{if(!event.composed)learn(event);},true);
    }
  }
  async function scan({repair = false} = {}) {
    observeNativeEdits();
    if(globalThis.autoApplyCredentials?.active())return {page:pageInfo(),fields:[],page_identity:snapshotIdentity(),next_action:{kind:"none"},sign_in:true,scan_ms:0};
    if(globalThis.autoApplyCredentials?.incomplete())return {page:pageInfo(),fields:[],page_identity:snapshotIdentity(),next_action:{kind:"none"},sign_in:false,scan_incomplete:true,scan_ms:0};
    const started = performance.now();
    const previousRecords = records;
    records = new Map();
    const fields = [];
    const controls = scannerControls();
    const grouped = new Set();
    const repeated = new Set();
    const keys = new Set();
    for (const element of controls) {
      if (["checkbox","radio"].includes(choiceKind(element)) && !native.closest(element,'form,fieldset,[data-automation-id^="formField"],[role="group"],[role="radiogroup"]') && !yesNoProxy(element) && !cc305Group(element)) continue;
      if (grouped.has(element) || /captcha|honeypot|honey.?pot|fax|website_confirm/i.test([element.name,element.id,label(element)].join(" "))) continue;
      let kind = element.tagName === "SELECT" ? "select" : element.getAttribute("role") === "combobox" || element.matches('button:is([aria-haspopup="listbox"],[id$="--degree"])') || element.getAttribute("data-automation-id") === "multiSelectInput" || isPrompt(element) ? "combobox" :
        element.tagName === "TEXTAREA" || element.isContentEditable ? "textarea" : choiceKind(element) || "text";
      if (!["text", "textarea", "select", "radio", "checkbox", "combobox", "file", "date", "number", "email", "tel", "url"].includes(kind)) kind = "text";
      let members = [element];
      let fieldLabel = kind === "file" ? fileLabel(element) : label(element);
      const section = sectionHint(element);
      const datePart = element.getAttribute('data-automation-id');
      const dateContext=dateBinding(element);
      if (/^dateSection(Month|Day|Year)(?:-input)?$/.test(datePart || '')) {
        const heading = dateHeading(element);
        fieldLabel = (dateContext?.context==='cc305_signed'?'CC305 date signed':heading.replace(/\s*\*$/, '').replace(/^(from|to)$/i, '$1 date')) + ' ' + datePart.match(/Month|Day|Year/)[0].toLowerCase();
      }
      const proxy=yesNoProxy(element);
      let container;
      if (["radio", "checkbox"].includes(kind)) {
        container = choiceContainer(element);
        members = controls.filter(other => choiceKind(other) === kind && sameChoiceScope(element,other) &&
          (kind === "checkbox" ? choiceContainer(other)===container : element.name ? other.name===element.name : choiceContainer(other)===container));
        if (!members.length) members = [element];
        if (!container && members.length > 1) container = commonContainer(members);
        fieldLabel = recoveredChoiceLabel(element,container,fieldLabel);
        members.forEach(member => grouped.add(member));
      }
      if (!element.dataset.autoapplyKey || keys.has(element.dataset.autoapplyKey)) element.dataset.autoapplyKey = `aa-${++nextKey}`;
      const key = element.dataset.autoapplyKey;
      keys.add(key);
      let options = kind === "select" ? Array.from(element.options).filter(option => !option.disabled && option.value !== "").map(option => clean(option.text)) :
        ["radio", "checkbox"].includes(kind) ? members.map(label) : [];
      if(proxy){kind='radio';options=proxy.choices.map(text);}
      if(cc305Group(element)){kind='radio';fieldLabel='Disability status';}
      container=questionContainer(container);
      fields.push({key, label: fieldLabel.slice(0,2000), kind, required: members.some(member => member.required || member.getAttribute("aria-required") === "true") || container?.getAttribute("aria-required") === "true" || /\*|\brequired\b/i.test([element.getAttribute('aria-label'), text(container?.querySelector('legend,label.control-label,[data-automation-id="formLabel"]')), text(element.closest('[data-automation-id^="formField"]')?.querySelector('label,legend,[data-automation-id="formLabel"]')), Array.from(element.labels || []).map(text).join(' ')].join(' ')),
        options, multiple: kind==='select' && element.multiple || kind==='checkbox' && members.length>1 && !options.every(o=>/^(yes|no)$/i.test(o)) || isPrompt(element) || element.getAttribute("data-automation-id") === "multiSelectInput" ||
          element.getAttribute("aria-multiselectable") === "true", ...(kind === "combobox" ? {options_partial: remoteAutocomplete(element)} : {}),
        section, ...(dateContext?{date_context:dateContext.context,date_part:dateContext.part}:{}),
        ...sectionEntry(element), placeholder: element.placeholder || '', max_length: element.maxLength >= 0 ? element.maxLength : null});
      for(const member of members)controlRoots.set(member,member.getRootNode());
      records.set(key, {element, members, bindingElement:element,bindingMembers:[...members],pageUrl:location.href, labelAtScan:label(element),
        choiceQuestionAtScan:['radio','checkbox'].includes(kind) ? choiceQuestion(element) : null, field: fields[fields.length - 1]});
      // A dataset key can be copied onto a replacement step. Bind completion
      // history to the actual controls and logical question within this document.
      fields[fields.length - 1].control_identity=JSON.stringify([members.map(member=>{
        if(!repairNodes.has(member))repairNodes.set(member,++repairNodeId);
        return repairNodes.get(member);
      }),kind,fields[fields.length - 1].label]);
      fields[fields.length - 1].answered = occupied(records.get(key));
      if (kind === 'file' && fields[fields.length - 1].answered) fields[fields.length - 1].attached_file = valueOf(records.get(key));
      if(!dirtyAnswers.has(key) && !observedValues.has(key))observedValues.set(key,JSON.stringify(valueOf(records.get(key))));
    }
    // Preserve composite section dates while rescanning conditional controls.
    for (const [key, old] of previousRecords) {
      if (!old.dateElements || !records.has(key)) continue;
      const current = records.get(key);
      current.field = {...current.field, ...old.field}; current.dateElements = old.dateElements; current.savedDate = old.savedDate;
      for (const date of old.dateElements.slice(1)) { records.delete(date.dataset.autoapplyKey); }
    }
    // Non-searchable widgets must expose their actual menu before profile matching.
    // Their aria-autocomplete=list describes navigation, not a partial search result.
    for (const record of records.values()) {
      const cached=choiceObservations.get(record.element);
      if (record.field.kind==='combobox' && cached?.options.length && cached.url===location.href &&
          cached.identity===snapshotIdentity() && cached.revision===choiceRevision && cached.label===record.field.label) {
        record.field.options=[...cached.options];record.field.options_observed=true;
      }
      if (repair || record.field.kind !== 'combobox' || !selectOnlyControl(record.element) || occupied(record)) continue;
      if (record.field.options_observed) continue;
      try {
        await openCombo(record.element);
        record.field.options = (await settledComboOptions(record.element, 1000)).map(text);
        record.field.options_observed = true;
        rememberChoices(record);
      } finally { closeCombo(record.element); }
    }
    const retained = Array.from(records.values(), record => ({...record.field,
      answered:(repairSession?.outcomes.get(record.element)!=='verification_failed' || editedRecord(record)) && occupied(record)}));
    previousStep = workdayState();
    return {page: pageInfo(), fields: retained, page_identity: snapshotIdentity(), next_action: navigationState(), workday_step: previousStep?.step, skills_available: skillInputs().length > 0, sign_in: signInPage(), manual_entry: Boolean(manualEntry()), scan_ms: Math.round(performance.now() - started)};
  }

  async function collectOptions(keys) {
    const started = performance.now(), fields = [];
    for (const key of keys) {
      checkRepair();
      const record = records.get(key);
      if (!record || !writableRecord(record) || record.field.kind !== "combobox" || occupied(record)) continue;
      if (record.field.options_observed && record.field.options.length) { fields.push({...record.field}); continue; }
      try {
        await openCombo(record.element);
        const options=await settledComboOptions(record.element, 3000);
        record.field.options = sourcePrompt(record.element)?await sourceChoices(record.element):options.map(text);
        record.field.options_observed = true;
        rememberChoices(record);
        if(repairExecuting)repairSession.menus.set(record.element,{options:record.field.options,
          roles:[...new Set(options.map(o=>o.getAttribute('role') || 'none'))].slice(0,10),
          shapes:options.slice(0,3).map(structureOf)});
        record.field.answered = occupied(record) || !record.field.options.length && displayedChoice(record.element);
        fields.push({...record.field});
      } finally { closeCombo(record.element); }
    }
    return {fields, options_ms: Math.round(performance.now() - started)};
  }

  function phoneCountrySelection(element) {
    const container = comboContainer(element);
    const identity = [label(element), element.id, element.name, container.className].join(" ");
    let group = container;
    for (let depth = 0; depth < 5 && group && !group.matches('form,body'); depth++, group = group.parentElement) {
      const phone = /phone|dial|calling|country.code/i.test(identity + " " + group.className) || group.querySelector('input[type="tel"],input[autocomplete="tel"],input[id*="phone"],input[name*="phone"]');
      if (!phone) continue;
      // Popup options are choices, not an already selected phone country.
      const popup = '[role="listbox"],[role="option"],[class*="__menu"],[class*="-menu"]';
      const shown = [element, ...group.querySelectorAll('span,button,[class*="value"],[class*="dial-code"]')]
        .filter(node => visible(node) && !node.closest(popup)).map(text).join(' ');
      if (/\+\d{1,3}\b/.test(shown)) return shown;
      const flag = Array.from(group.querySelectorAll('.iti__flag,.selected-flag,[class*="flag"],img[alt]'))
        .find(node => visible(node) && !node.closest(popup));
      if (flag && visible(flag) ) return flag.getAttribute('title') || flag.getAttribute('alt') || 'Selected country';
    }
    return "";
  }

  function displayedChoice(element) {
    const control=element.closest('[class*="__control"],[class*="-control"]') || element;
    const shown=clean(element.getAttribute('aria-valuetext')) ||
      Array.from(control.querySelectorAll('span,[class*="value"],[class*="selection"]')).filter(visible).map(text).find(value=>
        /[a-z0-9]/i.test(value) && !/^(select|choose|search|loading|no options|no results)\b/i.test(value) && value!==label(element));
    return Boolean(shown && !/^(select|choose|search)\b/i.test(shown));
  }
  function occupied({element, members, field}) {
    const proxy=yesNoProxy(element);if(proxy)return Boolean(proxy.value);
    if (["checkbox", "radio"].includes(field.kind)) return members.some(controlChecked);
    if (field.kind === "file") {
      const container=fileContainer(element),state=uploadStates.get(container);
      if(state && (!element.files.length || element.files[0]?.name===state.filename) &&
         (!state.confirmed || uploadError(element,container)))return false;
      return Boolean(element.files.length || attachedFile(element));
    }
    if (field.kind === "combobox") return Boolean(comboSelection(element) || phoneCountrySelection(element) || displayedChoice(element) || !isPrompt(element) && clean(comboInput(element)?.value) && !/^(select|choose|search)(?:\s*one)?(?:\.{3}|…)?$/i.test(clean(comboInput(element)?.value)) || !comboInput(element) && !/^(select|choose|search)(?:\s*one)?(?:\.{3}|…)?$/i.test(text(element)) && text(element));
    if (field.kind === "select") return Array.from(element.selectedOptions).some(option =>
      !option.disabled && option.value !== "" && !/^(choose|select)(?:\s*one)?(?:\.{3}|…)?$/i.test(clean(option.text)));
    return Boolean(clean(element.isContentEditable || !("value" in element) ? element.textContent : element.value));
  }

  function nativeSet(element, property, value) {
    checkRepair();
    if(robotOnly(element))throw Error("Robot-only field excluded");
    if(property==="value" && !element.matches('input[type="password"],input[type="file"]'))trace("field","typed",element,{value});
    let prototype = Object.getPrototypeOf(element);
    while (prototype) {
      const setter = Object.getOwnPropertyDescriptor(prototype, property)?.set;
      if (setter) { setter.call(element, value); return; }
      prototype = Object.getPrototypeOf(prototype);
    }
    element[property] = value;
  }
  function events(element) {
    checkRepair();
    for (const type of ["input", "change", "blur"]) element.dispatchEvent(new Event(type, {bubbles: true, composed: true}));
  }
  function bachelorDegree(value) {
    const name=sectionNormal(String(value || ''));
    return /^(bachelor|bachelors|bachelor s|bachelor degree|bachelors degree|bachelor s degree|bs|b s|bsc|b sc|bachelor of science|bachelor s? or equivalent first degree isced 6(?: ba bsc beng llb etc)?)$/.test(name);
  }
  function bachelorChoice(options,getText) {
    const choices=options.filter(item=>bachelorDegree(getText(item)));
    const preferred=choices.filter(item=>/^(bachelor s degree|bachelors degree)$/.test(sectionNormal(getText(item))));
    return preferred.length===1 && !choices.some(item=>/\bisced\b/.test(sectionNormal(getText(item))))?
      preferred[0]:choices.length===1?choices[0]:undefined;
  }
  function best(options, value, getText = item => item) {
    if (value === 'highest') {
      const numeric = options.filter(item => /^\s*\d+/.test(getText(item)));
      if (numeric.length) return numeric.reduce((a,b) => parseInt(getText(b),10)>parseInt(getText(a),10)?b:a);
      const rank = item => {const name=normal(getText(item));return /native/.test(name)?5:/fluent|expert/.test(name)?4:/advanced/.test(name)?3:/intermediate/.test(name)?2:/basic|beginner/.test(name)?1:0;};
      return options.filter(item=>rank(item)>0).sort((a,b)=>rank(b)-rank(a))[0];
    }
    if (bachelorDegree(value)) {
      return bachelorChoice(options,getText);
    }
    const exact = options.find(item => clean(getText(item)) === clean(value));
    const folded = options.find(item => normal(getText(item)) === normal(value));
    return exact || folded || (clean(value) ? options.find(item => normal(getText(item)).startsWith(normal(value))) ||
      options.find(item => normal(getText(item)).includes(normal(value))) : undefined);
  }
  const monthNames = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  function monthValues(element, value) {
    const hint = sectionNormal(label(element) + ' ' + element.getAttribute('data-automation-id'));
    if (!/month/.test(hint) || !/_date$/.test(sectionField(element))) return null;
    const index = /^\d{1,2}$/.test(clean(value)) ? Number(value) - 1 :
      monthNames.findIndex(name => [normal(name), normal(name.slice(0, 3))].includes(normal(value)));
    if (index < 0 || index >= monthNames.length) return null;
    const name = monthNames[index];
    return [name, name.slice(0, 3), String(index + 1), String(index + 1).padStart(2, '0')];
  }
  function fieldChoice(element, options, value, getText = text) {
    if(repairExecuting){
      const exact=options.filter(option=>clean(getText(option))===clean(value));
      return exact.length===1?exact[0]:null;
    }
    const months = monthValues(element, value)?.map(normal);
    if (months) return options.find(option => months.includes(normal(getText(option))) ||
      element.tagName === 'SELECT' && months.includes(normal(option.value)));
    if (value === 'highest') return best(options,value,getText);
    if (bachelorDegree(value)) {
      return bachelorChoice(options,getText);
    }
    const exact = options.filter(option => clean(getText(option)) === clean(value));
    const folded = options.filter(option => normal(getText(option)) === normal(value));
    const strict = exact.length === 1 ? exact[0] : !exact.length && folded.length === 1 ? folded[0] : undefined;
    if (exact.length || folded.length) return strict;
    const related=remoteAutocomplete(element) && clean(value) ? options.filter(option=>normal(getText(option)).includes(normal(value))) : [];
    return (related.length===1 ? related[0] : undefined) || (normal(value) === 'mobile' && /^phone (?:device )?type$/i.test(label(element)) ?
      options.find(option => /^(cell|mobile phone)$/i.test(clean(getText(option)))) : undefined);
  }
  function remoteAutocomplete(element) {
    // Search only real vocabulary lookups. Searchable finite demographic/status
    // menus still need their offered options before resolving profile facts.
    return !selectOnlyControl(element) && Boolean(comboInput(element)) &&
      comboInput(element).getAttribute('aria-autocomplete') !== 'none' &&
      (isSkillsField(element) || /^(?:school(?: or university)?|university|college(?: university)?|skills?|company|employer|job title|major|discipline|field of study|city|location city)$/i.test(clean(label(element))));
  }
  function mark(record, color) {
    checkRepair();
    for (const element of record.members) {
      const target = record.field.kind === "file" && !visible(element) ? fileContainer(element) : element;
      target.style.outline = `2px solid ${color}`;
      target.style.outlineOffset = "2px";
    }
  }

  async function fillCombo(element, value, force = false) {
    if(sourcePrompt(element)){if(!force && occupied({element,members:[element],field:{kind:'combobox'}}))return false;return fillSource(element,value);}
    if (isPrompt(element) && isSkillsField(element)) return (await fillSkills(element, [value])).skipped === 0;
    if (!force && occupied({element,members:[element],field:{kind:'combobox'}})) return false;
    const initial=element;
    const initialUrl=location.href, initialLabel=label(element), initialValue=comboInput(element)?.value || '';
    // Only explicit caller force may replace this unchanged initial selection.
    const initialSelection=comboSelection(element), initialDisplayed=displayedChoice(element);
    const observedField=records.get(element.dataset.autoapplyKey)?.field;
    const expectedOptions=observedField?.options_observed ? observedField.options : null;
    const remote=remoteAutocomplete(element);
    const unchanged=()=>location.href===initialUrl && currentControl(initial)===initial &&
      initial.isConnected && interactiveVisible(initial) && label(initial)===initialLabel &&
      (comboInput(initial)?.value || '')===initialValue && comboSelection(initial)===initialSelection && displayedChoice(initial)===initialDisplayed;
    const reacquire=()=>element=currentControl(initial);
    let committed = false;
    try {
      reacquire();
      await openCombo(element);
      if(!repairExecuting && isPrompt(element) && remote) {searchCombo(element,value);promptEnter(comboInput(element));}
      const words = clean(value).split(" ");
      const prefix = words.slice(0, words.length > 3 ? 3 : 2).join(" ");
      const searches = [...new Set(monthValues(element, value) || [clean(value), prefix])];
      const options = await settledComboOptions(element, remote ? 350 : 1500);
      if (remote && observedField) observedField.options_observed=true;
      if (!remote && (!unchanged() || expectedOptions && JSON.stringify(options.map(text))!==JSON.stringify(expectedOptions))) {
        choiceObservations.delete(initial);
        trace('dropdown','skipped',element,{reason:'field or offered options changed'});return false;
      }
      let option = isPrompt(element) && remote ? await promptOption(element,value) : fieldChoice(element, options, value);
      for (const search of option || !remote || repairExecuting ? [] : searches) {
        if (!searchCombo(element, search)) break;
        // Keep waiting for a match even when an unrelated first page stays rendered.
        option = await waitFor(() => isPrompt(element) ? comboOptions(element).find(o=>skillNormal(optionName(o))===skillNormal(value)) : fieldChoice(element, comboOptions(element), value), 2500);
        if (option) break;
      }
      if (!option){trace("dropdown","skipped",element,{reason:"no matching option",value});return false;}
      if(repairExecuting && option.closest('a[href],button:not([type="button"]),input[type="submit"],input[type="image"]'))return false;
      const selected = text(option);
      const container = comboContainer(element);
      const fallback = container.nextElementSibling;
      const submittedValue = selectOnlyControl(element) && fallback?.matches('select') && fallback.id === container.id ?
        Array.from(fallback.options).find(choice => normal(choice.text) === normal(selected))?.value : null;
      const choices = comboOptions(element).map(text);
      if (option.tagName === "OPTION" && element.getAttribute("list")) {
        nativeSet(element, "value", selected);
        events(element);
        committed = element.value === selected;
      } else {
        for (let attempt=0; attempt<3; attempt++) {
          if (isPrompt(element)) {
            option=comboOptions(element).find(row=>skillNormal(optionName(row))===skillNormal(value));
            if (!option?.isConnected) break;
          }
          const target=isPrompt(element) ? option.querySelector('[data-automation-id="radioBtn"],input[type="radio"],[data-automation-id="checkboxPanel"],input[type="checkbox"],[role="checkbox"],[data-automation-id="checkbox"]') || option : option;
          if (target.disabled || target.matches(':disabled') || target.getAttribute('aria-disabled')==='true') break;
          const clicked=pointerClick(target,()=>location.href===initialUrl && !target.disabled && !target.matches(':disabled') &&
            target.getAttribute('aria-disabled')!=='true' && comboOptions(element).includes(option) && text(option)===selected &&
            (remote ? !isPrompt(element) || comboOptions(element).find(row=>skillNormal(optionName(row))===skillNormal(value))===option :
              unchanged() && fieldChoice(element,comboOptions(element),value)===option &&
              JSON.stringify(comboOptions(element).map(text))===JSON.stringify(options.map(text))));
          trace('dropdown',clicked?'clicked':'activation interrupted',element,{target:selected,element_id:option.id});
          if (clicked) break; // A pending commit must never be activated twice.
          await settledComboOptions(element,1000);
        }
        let stableSince=0;
        committed = Boolean(await waitFor(() => {
          reacquire();
          const selection = normal(comboSelection(element));
          const matchesSelected = Boolean(selection && selection === normal(selected));
          if(isPrompt(element)) {
            if (!selectedSkills(element).some(pill=>skillNormal(pill)===skillNormal(value))) {stableSince=0;return false;}
            if (!stableSince) stableSince=Date.now();
            return Date.now()-stableSince>=200;
          }
          // A different displayed option overrides other commit signals.
          if (selection && choices.some(choice => normal(choice) !== normal(selected) &&
            (normal(choice) === selection || !matchesSelected && normal(choice).includes(selection)))) return false;
          const container = comboContainer(element);
          const singleValue = text(container.querySelector('[class*="singleValue"],[class*="single-value"]'));
          const closed = element.getAttribute("aria-expanded") === "false";
          if (selectOnlyControl(element)) {
            const hidden = comboBackingInput(container);
            if (!closed || normal(singleValue) !== normal(selected) || hidden &&
                (!clean(hidden.value) || submittedValue != null && hidden.value !== submittedValue)) {stableSince=0;return false;}
            if (!stableSince) stableSince=Date.now();
            return Date.now()-stableSince>=300;
          }
          const hidden=comboBackingInput(container);
          const retained=(matchesSelected || closed && normal(singleValue)===normal(selected)) && (!hidden || clean(hidden.value));
          if (!retained) {stableSince=0;return false;}
          if (!stableSince) stableSince=Date.now();
          return Date.now()-stableSince>=300;
        }));
      }
      trace("dropdown","verified",element,{verified:committed,value:selected,observed:committed?selected:"uncommitted"});
      return committed;
    } finally {
      if (!committed && remote) {
        searchCombo(element, "");
      }
      closeCombo(element);
    }
  }

  async function phoneValue(element, value, field) {
    if(repairExecuting)return value;
    if(/phone.*extension|extension.*phone/i.test(field.label)) return '';
    if(field.kind!=='tel'&&!/phone|mobile/i.test(field.label)) return value;
    const digits=String(value).replace(/\D/g,'');
    if(digits.length!==11||!digits.startsWith('1')) return value;
    let group=element.closest('[data-automation-id="phoneNumber"],fieldset,[role="group"],.intl-tel-input,.iti')||element.parentElement;
    for(let i=0;i<2&&group?.parentElement&&!group.matches('form,body');i++) {if(group.querySelector('[data-automation-id="countryPhoneCode"],select,[role="combobox"],.iti__flag-container,.flag-container,.selected-flag,.iti__country-container'))break;group=group.parentElement;}
    if(group?.matches("form,body"))group=null;
    const country=Array.from(group?.querySelectorAll('select,[role="combobox"],button:is([aria-haspopup="listbox"],[id$="--degree"]),[data-automation-id="countryPhoneCode"]')||[]).find(el=>el!==element&&/country|dial.code|phone.code/i.test(label(el)+' '+el.getAttribute('data-automation-id')));
    const picker=group?.querySelector('.iti__flag-container,.iti__country-container,.selected-flag,[class*="flag-dropdown"]');
    if(country && !occupied({element:country,members:[country],field:{kind:country.tagName==='SELECT'?'select':'combobox'}})) {
      if(country.tagName==='SELECT') {const option=Array.from(country.options).find(o=>/united states|\+1(?:\D|$)/i.test(o.text));if(option){nativeSet(country,'value',option.value);events(country);}}
      else await fillCombo(country,'United States (+1)');
    }
    if(picker?.getAttribute('title') && !/United States.*\+1/i.test(picker.getAttribute('title')))throw Error('Phone country differs; select the correct country before filling.');
    const hint=element.placeholder||'',pattern=element.getAttribute('pattern')||'';
    const workday=location.hostname.endsWith('.myworkdayjobs.com')||element.closest('[data-automation-id^="formField"]')&&/^phone number/i.test(field.label);
    if(!workday&&!country&&!picker&&(hint.includes('+')||pattern.includes('\\+')||/international/i.test(field.label)&&!hint&&!pattern&&!(element.maxLength>0&&element.maxLength<=12)&&element.inputMode!=='numeric'))return value;
    const national=digits.slice(1);
    if(workday)return national;
    if(/\(\d{3}\)\s*\d{3}[- ]\d{4}/.test(hint))return '('+national.slice(0,3)+') '+national.slice(3,6)+'-'+national.slice(6);
    if(/\d{3}-\d{3}-\d{4}/.test(hint))return national.slice(0,3)+'-'+national.slice(3,6)+'-'+national.slice(6);
    return national;
  }
  async function settledText(record, expected, phone=false) {
    const digits=value=>String(value).replace(/\D/g,'');
    const canonical=value=>phone ? digits(value).replace(/^1(?=\d{10}$)/,'') : String(value);
    let stable=0, last;
    // Observe beyond change/blur and typical deferred widget rerenders, without retyping.
    for(let attempt=0;attempt<6;attempt++) {
      await pause(150);refreshRecord(record);
      if(!record.element.isConnected || !interactiveVisible(record.element))return false;
      const value=valueOf(record);
      if(canonical(value)!==canonical(expected))return false;
      stable=value===last?stable+1:0;last=value;
    }
    return stable>=2;
  }
  async function retainedNative(record, matches) {
    let stableSince=0;
    return Boolean(await waitFor(()=>{
      refreshRecord(record);
      if (!record.element.isConnected || !matches(record)) {stableSince=0;return false;}
      if (!stableSince) stableSince=Date.now();
      return Date.now()-stableSince>=300;
    },1000));
  }
  async function fill(answers, files = {}) {return traced("fill",()=>fillFields(answers,files));}
  async function fillFields(answers, files = {}) {
    const started = performance.now();
    extensionFilling++;
    try {
    const summary = {completed: [], filled: 0, needs_input: [], needs_writing: [], pending: [], writing: [], errors: [], skipped: 0};
    for (const answer of answers) {
      checkRepair();
      const record = refreshRecord(records.get(answer.key));
      if(answer.cover_token && cancelledCoverUploads.has(answer.cover_token))continue;
      const canFill=native.read(()=>{
      if(record?.members.some(member=>robotOnly(member) || !answer.force && repairEdited.has(member)))return false;
      if (!record || record.pageUrl && record.pageUrl!==location.href ||
          (record.labelAtScan!==label(record.element) || record.choiceQuestionAtScan!==null && record.choiceQuestionAtScan!==choiceQuestion(record.element)) ||
          !writableRecord(record) || globalThis.autoApplyCredentials?.active() || globalThis.autoApplyCredentials?.sensitive(record.element) || !record.members.every(member => record.field.kind === "file" ? member.isConnected && !member.disabled && !member.matches(":disabled") : interactiveVisible(member)) || occupied(record) && !answer.force) return false;
      return true;
      });
      if(!canFill){summary.skipped++;continue;}
      let {element, members, field} = record;
      trace("field","found",element);
      if (isSkillsField(element)) { summary.skipped++; continue; }
      if (/\b(?:address(?: line)?\s*2|line\s*2|apt|apartment|suite|unit)\b/i.test(field.label)) {
        if (field.required) {summary.needs_input.push(field.label);summary.pending.push({...field});}
        else summary.skipped++;
        continue;
      }
      if (field.kind === "file" && (occupied(record) || element.files.length) && !(answer.replace_cover_letter && coverFile(element))) {summary.skipped++;continue;}
      if (["combobox","select","radio"].includes(field.kind) && !field.options.length && occupied(record) && !answer.force) { summary.skipped++; continue; }
      const unresolved = writing => {
        trace("field","verified",element,{verified:false,reason:"not committed or unresolved"});
        summary[writing ? "needs_writing" : "needs_input"].push(field.label);
        summary[writing ? "writing" : "pending"].push({...field,reason_code:answer.reason_code || (answer.status==='RESOLVED'?'verification_failed':'needs_fact'),...(answer.reason ? {reason:answer.reason} : {})});
        if (field.required) mark(record, "#999999");
      };
      if (answer.status === "NEEDS_WRITING") { unresolved(true); continue; }
      if (answer.status === "NEEDS_INPUT") { unresolved(false); continue; }
      if (answer.status === "OPTIONAL_SKIP") {
        if(answer.reason_code==='user_skip'){summary.skipped++;continue;}
        // A select-only widget with no matching saved choice stays available in Fill.
        if (selectOnlyControl(element) || element.getRootNode()!==document && element.matches('input,textarea') && !['radio','checkbox'].includes(field.kind)) unresolved(false);
        else summary.skipped++;
        continue;
      }
      if (!["RESOLVED", "FILE"].includes(answer.status)) { unresolved(false); continue; }
      try {
        if (field.kind==='file' && !/\b(resume|cv|cover)\b/i.test(field.label)) {unresolved(false);continue;}
        if (record.dateElements) {
          if (!/^\d{4}-(0[1-9]|1[0-2])(?:-01)?$/.test(String(answer.value))) { unresolved(false); continue; }
          for (const date of record.dateElements) {
            if (occupied({element:date,members:[date],field:{kind:date.tagName==='SELECT'?'select':date.type||'text'}})) continue;
            const value=dateValue(date,String(answer.value));
            if(date.tagName==='SELECT') {const option=best(Array.from(date.options),value,o=>o.text);if(!option)continue;nativeSet(date,'value',option.value);}
            else await typeDate(date,value);
            events(date);date.blur?.();
            if (Number(date.value)!==Number(value) && date.value!==value) throw Error('Date did not commit');
          }
          record.savedDate=String(answer.value).slice(0,7);
        } else if (field.kind === "file") {
          const file = files[answer.value];
          if (!file) { unresolved(answer.value === "cover_letter"); continue; }
          if(!visible(element) && element.id==='resumator-resume-value') {
            const link=resumeAffordance(element);
            if(!link){unresolved(false);continue;}
            link.click();
            if(!await waitFor(()=>visible(currentControl(element)),1000)){unresolved(false);continue;}
            refreshRecord(record);element=record.element;
          }
          const bytes = Uint8Array.from(atob(file.base64), char => char.charCodeAt(0));
          const transfer = new DataTransfer();
          transfer.items.add(new File([bytes], file.filename, {type: file.mime}));
          const container = fileContainer(element);
          const root=element.getRootNode(),pageUrl=location.href,cancellation=repairCancellation;
          const removals=new Set(uploadNodes(container,'button,[role="button"],a').filter(node=>/remove|delete/i.test(label(node))));
          uploadStates.set(container,{filename:file.filename,confirmed:false});
          nativeSet(element, "files", transfer.files);
          events(element);
          let stableSince=0,errorSince=0,retainedControl;
          const asynchronous=Boolean(workdayUploadGroup(element) || native.closest(element,'spl-dropzone,[data-upload],[aria-busy]'));
          const outcome = await waitFor(() => native.read(() => {
            if(location.href!==pageUrl || repairCancellation!==cancellation || editedRecord(record) || !container.isConnected ||
               answer.cover_token && cancelledCoverUploads.has(answer.cover_token))return {error:'Application, cover letter or answer changed.'};
            checkRepair();
            const controls=container===element?[element]:uploadNodes(container,'input[type="file"]');
            const current=controls.length===1?controls[0]:null;
            if(!current || current.getRootNode()!==root || !writableRecord({...record,element:current,members:[current]}) ||
               fileLabel(current)!==field.label)return {error:'Upload control changed.'};
            if(retainedControl!==current){stableSince=0;retainedControl=current;}
            const error=uploadError(current,container);
            if(error){stableSince=0;errorSince ||= Date.now();return Date.now()-errorSince>=800?{error:text(error)}:false;}
            errorSince=0;
            if(uploadNodes(container,'[aria-busy="true"],[role="progressbar"]').some(visible) ||
               container.matches('[aria-busy="true"]')){stableSince=0;return false;}
            const shown=filenameShown(current,file.filename) || uploadNodes(container,'button,[role="button"],a').some(node=>
              !removals.has(node) && visible(node) && /^(remove|delete)(?: (?:file|attachment|resume|cv))?$/i.test(label(node)));
            const retained=shown || !asynchronous && (visible(current) || leverResumeProxy(current)) && current.files[0]?.name===file.filename;
            if(!retained){stableSince=0;return false;}
            stableSince ||= Date.now();
            return Date.now()-stableSince>=800?{ok:true,current}:false;
          }), 10000);
          if (!outcome?.ok) {
            summary.errors.push(outcome?.error ? `Upload failed: ${outcome.error}` : `Upload not yet confirmed: ${file.filename}. Check the page before retrying.`);
            unresolved(false); continue;
          }
          element=record.element=outcome.current;record.members=[element];
          uploadStates.set(container,{filename:file.filename,confirmed:true});
        } else if (field.kind === "combobox") {
          let ok = true;
          for (const value of Array.isArray(answer.value) ? answer.value : [answer.value]) {
            if (!await fillCombo(element, value, answer.force === true)) { ok = false; break; }
          }
          if (!ok) { unresolved(false); continue; }
        } else if (field.kind === "select") {
          if(element.multiple){
            const wanted=Array.isArray(answer.value)?answer.value:[answer.value];
            const options=Array.from(element.options).filter(option=>!option.disabled && option.value!=='');
            const selected=wanted.map(value=>fieldChoice(element,options,value,option=>option.text));
            if(selected.some(option=>!option)){unresolved(false);continue;}
            for(const option of options)nativeSet(option,'selected',selected.includes(option));
            events(element);
            if(!await retainedNative(record,current=>Array.from(current.element.selectedOptions).length===selected.length &&
                Array.from(current.element.selectedOptions).every(option=>wanted.includes(clean(option.text))))){unresolved(false);continue;}
          }else{
          const option = fieldChoice(element, Array.from(element.options).filter(option => !option.disabled && option.value !== ""), answer.value, option => option.text);
          if (!option) { unresolved(false); continue; }
          nativeSet(element, "value", option.value);
          events(element);
          if (answer.batch_settlement ? record.element.value!==option.value : !await retainedNative(record,current=>current.element.value===option.value &&
              Array.from(current.element.selectedOptions).some(selected=>!selected.disabled && text(selected)===text(option)))) {unresolved(false);continue;}
          }
        } else if (yesNoProxy(element)) {
          const proxy=yesNoProxy(element),choice=proxy.choices.find(el=>normal(text(el))===normal(answer.value));
          if(!choice || !noSubmitProxyChoice(choice)){unresolved(false);continue;}
          checkRepair();choice.click();
          if(!await waitFor(()=>normal(yesNoProxy(currentControl(element))?.value)===normal(answer.value),500)) {unresolved(false);continue;}
          await new Promise(resolve=>setTimeout(resolve,150));
          if(normal(yesNoProxy(currentControl(element))?.value)!==normal(answer.value)){unresolved(false);continue;}
        } else if (["radio", "checkbox"].includes(field.kind)) {
          const values = Array.isArray(answer.value) ? answer.value : [answer.value];
          const matches = values.map(value => fieldChoice(element, members.filter(interactiveVisible), value, label));
          if (field.kind === "checkbox" && members.length === 1 && typeof answer.value === "boolean") {
            if (!await setCheckbox(element,answer.value)) {unresolved(false);continue;}
          } else {
            if (matches.some(member => !member) || field.kind === "radio" && matches.length !== 1) { unresolved(false); continue; }
            let verified=true;
            if(answer.force && field.kind==="checkbox") for(const member of members) if(member.checked && !matches.includes(member)) verified = await setCheckbox(currentControl(member),false) && verified;
            for (const member of matches) {
              const current=currentControl(member);
              if(field.kind==='checkbox') verified=await setCheckbox(current,true) && verified;
              else {checkRepair();if(!controlChecked(current)) repairDispatch(()=>current.click());verified=Boolean(await waitFor(()=>controlChecked(currentControl(member)),500)) && verified;}
            }
            refreshRecord(record);
            if(!verified || field.kind==='checkbox' && record.members.some(member=>controlChecked(member)!==values.includes(label(member)))) {unresolved(false);continue;}
          }
          const wanted=field.kind==='checkbox' && members.length===1 && typeof answer.value==='boolean' ?
            member=>controlChecked(member)===answer.value : member=>controlChecked(member)===values.includes(label(member));
          if (answer.batch_settlement ? !record.members.every(wanted) : !await retainedNative(record,current=>current.members.every(wanted))) {unresolved(false);continue;}
        } else {
          let value = String(await phoneValue(element, answer.value ?? "", field));
          if(field.date_context==='cc305_signed') {
            const today=new Date();
            value={month:String(today.getMonth()+1).padStart(2,'0'),day:String(today.getDate()).padStart(2,'0'),year:String(today.getFullYear())}[field.date_part];
          }
          checkRepair();
          if(!writableRecord(record) || record.labelAtScan!==label(element))continue;
          if (field.max_length !== null && value.length > field.max_length) { unresolved(false); continue; }
          if (element.isContentEditable || !("value" in element)){trace("field","typed",element,{value});element.textContent = value;}
          else {
            element.focus();
            if (field.date_context || /_date$/.test(sectionField(element))) await typeDate(element,value);
            else nativeSet(element, "value", value);
          }
          events(element);
          element.blur?.();
          if(!answer.batch_settlement && (field.kind==='tel'||/phone|mobile/i.test(field.label)) && !await settledText(record,value,true)){unresolved(false);continue;}
          if(answer.verify_settled && !await settledText(record,value)){unresolved(false);continue;}
          if(field.date_context && !answer.batch_settlement && !await retainedNative(record,current=>
              current.element.value===value || /^\d+$/.test(value) && Number(current.element.value)===Number(value))){unresolved(false);continue;}
          if (/_date$/.test(sectionField(element)) && element.value !== value &&
              (!/^\d+$/.test(value) || Number(element.value) !== Number(value))) { unresolved(false); continue; }
        }
        trace("field","verified",element,{verified:true,...(field.kind!=="file"?{value:answer.value,observed:valueOf(record)}:{})});
        mark(record, "#888888");
        summary.filled++;
        const value=valueOf(record); if(!dirtyAnswers.has(field.key))observedValues.set(field.key,JSON.stringify(value));
        if(answer.force && answer.source==='you')for(const member of record.members)repairEdited.add(member);
        summary.completed.push({...field,value,source:answer.source||"AI"});
      } catch(error){trace("field","error",element,{error:error.message,stack:error.stack});unresolved(false);}
    }
    summary.fill_ms = Math.round(performance.now() - started);
    return summary;
    } finally { extensionFilling--; }
  }

  const sectionNormal = value => normal(value.replace(/([a-z])([A-Z])/g, '$1 $2')).replace(/[^a-z0-9]+/g, ' ').trim();
  const sectionNames = {
    experience: ['work experience','employment','employment history','work history','professional experience','experience','internships'],
    projects: ['projects','relevant experience','personal projects','technical projects','research','research experience','extracurricular','activities','portfolio'],
    education: ['education'], languages: ['languages','language'], skills: ['skills'], links: ['websites','website','links','web links','social network urls']
  };
  function sectionKind(value) {
    value = sectionNormal(value).replace(/ section$/, '').replace(/ \d+$/, '');
    return Object.keys(sectionNames).find(kind => sectionNames[kind].includes(value));
  }
  function resumeSections() {
    const found = new Map();
    for (const node of document.querySelectorAll(sectionHeadings + ',legend,[aria-label],[data-automation-id],[class],[id]')) {
      const kind = sectionKind(sectionToken(node)) || sectionKind(node.getAttribute('aria-label') || node.getAttribute('data-automation-id') || text(node));
      if (!kind) continue;
      let container = node;
      while (container && !container.matches('form,body') && !container.querySelector('input,select,textarea,button,[role="combobox"]')) container = container.parentElement;
      if (container && !container.matches('form,body') && !found.has(container)) found.set(container, kind);
    }
    return [...found].filter(([container,kind],i,all) => !all.some(([other,k]) => k===kind && other!==container && other.contains(container)));
  }
  function cc305Option(element) {
    if(!element.matches('input[type="checkbox"],input[type="radio"]'))return '';
    const values=[...Array.from(element.labels||[]).map(questionText),element.getAttribute('aria-label'),questionText(element.closest('tr'))];
    return values.map(clean).find(value=>/^(?:Yes, I have a disability, or have had one in the past|No, I do not have a disability and have not had one in the past|I do not want to answer)\.?$/i.test(value)) || '';
  }
  function cc305Group(element) {
    if(!cc305Option(element) || !/^(?:Disability)$|Voluntary Self.Identification of Disability|Form CC.305/i.test(sectionHint(element)))return null;
    for(let node=element.parentElement;node && !node.matches('form,body');node=node.parentElement) {
      const choices=Array.from(node.querySelectorAll('input[type="checkbox"],input[type="radio"]'));
      if(choices.length===3 && choices.every(cc305Option) && new Set(choices.map(cc305Option)).size===3)return node;
      if(choices.length>3)return null;
    }
    return null;
  }
  function dateBinding(element) {
    const part=/^dateSection(Month|Day|Year)(?:-input)?$/.exec(element.getAttribute('data-automation-id') || '');
    const wrapper=element.closest('[data-automation-id="dateInputWrapper"][role="group"]');
    if(!part || !wrapper)return null;
    const identity=[wrapper.id,wrapper.getAttribute('aria-labelledby'),element.id].join(' ');
    if(/selfIdentifiedDisabilityData--dateSignedOn/.test(identity))return {context:'cc305_signed',part:part[1].toLowerCase()};
    if(/\b(?:anticipated|expected|projected)?\s*graduation\b/i.test(dateHeading(element)))return {context:'graduation',part:part[1].toLowerCase()};
    return null;
  }
  function sectionEntry(element) {
    const section=resumeSections().find(([container,kind])=>['experience','education'].includes(kind) && container.contains(element));
    if(!section)return {};
    const [container,kind]=section, group=entryGroups(container,kind).find(node=>node.contains(element));
    if(!group)return {};
    const anchors=kind==='experience'?['company','title']:['school'], entry={};
    for(const input of group.querySelectorAll('input,select,[role="combobox"]')) {
      const key=sectionField(input);
      if(anchors.includes(key))entry[key]=input.matches('[role="combobox"]')?comboSelection(input):clean(input.value);
    }
    return Object.values(entry).some(Boolean)?{section_entry:entry}:{};
  }
  function dateHeading(element) {
    const field=element.closest('[data-automation-id^="formField"]');
    const wrapper=element.closest('[data-automation-id="dateInputWrapper"]') || field;
    const signed=[wrapper?.id,wrapper?.getAttribute('aria-labelledby'),element.id].join(' ');
    if(/selfIdentifiedDisabilityData--dateSignedOn/.test(signed))return 'CC305 date signed';
    let heading=text(field?.querySelector('label,[data-automation-id="formLabel"]')) || text(wrapper?.querySelector('label')) || wrapper?.getAttribute('aria-label');
    if(!heading && wrapper)for(let node=wrapper,depth=0;node && !node.matches('form,body') && depth<4;node=node.parentElement,depth++) {
      heading=referencedLabel(node,'aria-labelledby') || text(node.querySelector(':scope > label,:scope > [data-automation-id="formLabel"],:scope > [data-automation-id="richText"]'));
      if(heading && !/^(?:month|day|year|\d+[\/ -]\d+)/i.test(heading))break;
      heading='';
    }
    if(heading) return heading;
    const id=field?.getAttribute('data-automation-id') || '';
    return /startDate|firstYearAttended/.test(id)?'From':/endDate|lastYearAttended/.test(id)?'To':label(element);
  }
  function sectionField(element) {
    let value = sectionNormal(label(element)).replace(/ star$/, '');
    const part = element.getAttribute('data-automation-id') || '';
    if (/dateSection(Month|Day|Year)/.test(part)) {
      value = sectionNormal(dateHeading(element)) + ' ' + part.match(/Month|Day|Year/)[0].toLowerCase();
    }
    const wrapper = element.closest('[data-automation-id^="formField-"]')?.getAttribute('data-automation-id');
    const known = {jobTitle:'title',companyName:'company',location:'location',currentlyWorkHere:'current',startDate:'start_date',endDate:'end_date',roleDescription:'description',schoolName:'school',school:'school',degree:'degree',fieldOfStudy:'major',gradeAverage:'gpa',firstYearAttended:'start_date',lastYearAttended:'end_date'};
    if (known[wrapper?.replace('formField-', '')]) return known[wrapper.replace('formField-', '')];
    if (/^(current|currently work here|i currently work here|present|still working|current role|current employer|i currently attend|currently enrolled)\b/.test(value)) return 'current';
    if (/^(from|start|start date)\b/.test(value)) return 'start_date';
    if (/^(to|end|end date|graduation)\b/.test(value)) return 'end_date';
    if (/^(job title|title|position|role|project name|name)($| )/.test(value) && !/description/.test(value)) return 'title';
    if (/^(company|employer|organization|organisation)\b/.test(value)) return 'company';
    if (/^(school|school or university|institution|university)\b/.test(value) && !/location/.test(value)) return 'school';
    if (/^(language)($| )/.test(value)) return 'language';
    if (/fluent in this language/.test(value)) return 'fluent';
    if (/^(degree)\b/.test(value)) return 'degree';
    if (/^(discipline|major|field of study)\b/.test(value)) return 'major';
    if (/gpa|overall result/.test(value)) return 'gpa';
    if (/^(location|city|office location|school location)\b/.test(value)) return 'location';
    if (/^(description|responsibilities|role description|achievements|summary|details|relevant coursework)\b/.test(value)) return 'description';
    if (/^(technologies|tools)\b/.test(value)) return 'technologies';
    if (/url|link|website|github|portfolio/.test(value)) return 'url';
    return '';
  }
  function entryGroups(container, kind) {
    const anchors = Array.from(container.querySelectorAll('input,textarea,select,[role="combobox"],button:is([aria-haspopup="listbox"],[id$="--degree"])'))
      .filter(el => interactiveVisible(el) && sectionField(el) === (kind==='education'?'school':kind==='languages'?'language':kind==='links'?'url':'title'));
    return [...new Set(anchors.map(anchor => {
      let group = anchor.parentElement;
      while (group.parentElement && group.parentElement !== container &&
        !anchors.some(other => other!==anchor && group.parentElement.contains(other))) group = group.parentElement;
      return group.querySelectorAll('input,select,textarea,[role="combobox"]').length>1?group:anchors.length===1?container:group;
    }))];
  }
  function trimSection(value, maximum) {
    value = String(value || '').trim();
    if (maximum===null || maximum===undefined || maximum<0 || value.length<=maximum) return value;
    const cut = value.slice(0, maximum), boundary = Math.max(cut.lastIndexOf('\n'), ...Array.from(cut.matchAll(/[.!?](?=\s)/g), m=>m.index+1));
    return cut.slice(0, boundary>0?boundary:cut.lastIndexOf(' ')>0?cut.lastIndexOf(' '):maximum).trim();
  }
  function dateValue(element, value) {
    const match = /^(\d{4})(?:-(\d{2}))?/.exec(value || '');
    if (!match) return '';
    const year=match[1], month=match[2] || '01', hint = sectionNormal(label(element)+' '+element.placeholder+' '+element.getAttribute('data-automation-id'));
    if (/year/.test(hint) || element.type==='number' && Number(element.min)>=1900) return year;
    if (/month/.test(hint) && element.type!=='month') {
      const name = monthNames[Number(month)-1];
      if (!name) return '';
      if (element.tagName==='SELECT') return fieldChoice(element, Array.from(element.options), name, option => option.text)?.text || name;
      if (element.getAttribute('role') === 'combobox' || element.matches('button:is([aria-haspopup="listbox"],[id$="--degree"])')) return name;
      return /dateSectionMonth/.test(element.getAttribute('data-automation-id') || '') ? month : String(Number(month));
    }
    if (element.type==='month') return year+'-'+month;
    if (element.type==='date') return year+'-'+month+'-01';
    if (/mmm[-/ ]yy\b/i.test(element.placeholder || '')) return new Date(Number(year),Number(month)-1,1).toLocaleString('en-US',{month:'short'})+'-'+year.slice(2);
    if (/yyyy/.test(normal(element.placeholder)) && !/mm|dd/.test(normal(element.placeholder))) return year;
    if (/mm[/-]yyyy/i.test(element.placeholder||'') && !/dd/i.test(element.placeholder||'')) return month+'/'+year;
    if (/mm.*yyyy|mm.*dd|date/.test(hint)) return month+'/01/'+year;
    return month+'/'+year;
  }
  async function typeDate(element, value) {
    element.focus();
    if (['date','month'].includes(element.type)) { nativeSet(element,'value',value); return; }
    nativeSet(element, 'value', '');
    element.dispatchEvent(new InputEvent('input', {bubbles:true,inputType:'deleteContentBackward'}));
    for (const digit of value) {
      element.dispatchEvent(new KeyboardEvent('keydown', {key:digit,bubbles:true}));
      nativeSet(element, 'value', element.value + digit);
      element.dispatchEvent(new InputEvent('input', {bubbles:true,inputType:'insertText',data:digit}));
      element.dispatchEvent(new KeyboardEvent('keyup', {key:digit,bubbles:true}));
    }
    if (element.value !== value && Number(element.value) !== Number(value)) throw Error('Date did not commit');
  }
  function isPrompt(element) {
    return Boolean(element.closest('[data-automation-id="multiSelectContainer"]')) || element.matches('input[data-automation-id="searchBox"]') ||
      Boolean(element.querySelector('input[data-automation-id="searchBox"]'));
  }
  function sourcePrompt(element){
    return /^(?:.+\.)?myworkdayjobs\.com$/.test(location.hostname) && isPrompt(element) &&
      element.matches('input[data-automation-id="searchBox"]') &&
      /how did you (?:hear|learn|find)|(?:application|candidate|job) source/i.test(label(element));
  }
  const sourcePaths=new WeakMap();
  const sourceBranch=row=>!row.querySelector('[data-automation-id="checkboxPanel"],input[type="checkbox"],input[type="radio"],[role="checkbox"]') &&
    !/ (?:not )?checked$/i.test(row.getAttribute('aria-label') || '') &&
    (row.matches('[aria-haspopup="listbox"],[aria-haspopup="true"],[aria-expanded]') ||
     Boolean(row.querySelector('[data-automation-id="chevronRight"],[aria-haspopup="listbox"]')));
  const safePromptRow=row=>!row.closest('a[href],button:not([type="button"]),input[type="submit"],input[type="image"]');
  async function sourceChoices(element){
    const before=JSON.stringify(selectedSkills(element)),paths=new Map();
    const roots=await settledComboOptions(element,1500);
    if(roots.length>40)return [];
    const branches=roots.filter(sourceBranch).map(optionName);
    if(branches.length>8)return [];
    const add=(row,path)=>{const name=optionName(row);if(!name)return;
      if(paths.has(name))paths.set(name,null);else paths.set(name,path);};
    roots.filter(row=>!sourceBranch(row)).forEach(row=>add(row,[]));
    for(const category of branches){
      closeCombo(element);await openCombo(element);
      const rows=await settledComboOptions(element,1000),matches=rows.filter(row=>sourceBranch(row) && optionName(row)===category);
      if(matches.length!==1 || !safePromptRow(matches[0]))continue;
      const branch=matches[0],signature=JSON.stringify(rows.map(optionName));
      if(!pointerClick(branch,()=>comboOptions(element).includes(branch) && sourceBranch(branch)))continue;
      if(!await waitFor(()=>JSON.stringify(comboOptions(element).map(optionName))!==signature,600))continue;
      const leaves=await settledComboOptions(element,1000);
      if(leaves.length>40 || JSON.stringify(selectedSkills(element))!==before)break;
      leaves.filter(row=>!sourceBranch(row)).forEach(row=>add(row,[category]));
    }
    const unique=new Map([...paths].filter(([,path])=>path));
    sourcePaths.set(element,{url:location.href,label:label(element),paths:unique});
    return [...unique.keys()];
  }
  async function fillSource(element,value){
    const saved=sourcePaths.get(element);
    if(!saved || saved.url!==location.href || saved.label!==label(element) || !saved.paths.has(value))return false;
    const before=JSON.stringify(selectedSkills(element));
    try{
      await openCombo(element);
      for(const category of saved.paths.get(value)){
        const rows=await settledComboOptions(element,1000),matches=rows.filter(row=>sourceBranch(row) && optionName(row)===category);
        if(matches.length!==1 || !safePromptRow(matches[0]))return false;
        const branch=matches[0],signature=JSON.stringify(rows.map(optionName));
        if(!pointerClick(branch,()=>comboOptions(element).includes(branch) && sourceBranch(branch)))return false;
        if(!await waitFor(()=>JSON.stringify(comboOptions(element).map(optionName))!==signature,600))return false;
      }
      const matches=(await settledComboOptions(element,1000)).filter(row=>!sourceBranch(row) && optionName(row)===value);
      if(matches.length!==1 || !safePromptRow(matches[0]) || JSON.stringify(selectedSkills(element))!==before)return false;
      const row=matches[0],target=row.querySelector('[data-automation-id="checkboxPanel"],input[type="checkbox"],[role="checkbox"]') || row;
      if(!pointerClick(target,()=>comboOptions(element).includes(row) && !target.disabled && target.getAttribute('aria-disabled')!=='true'))return false;
      let since=0;
      return Boolean(await waitFor(()=>{
        if(!element.isConnected || saved.url!==location.href || saved.label!==label(element) || !selectedSkills(element).some(pill=>normal(pill)===normal(value))){since=0;return false;}
        since ||= Date.now();return Date.now()-since>=200;
      },1500));
    }finally{closeCombo(element);}
  }
  function promptEnter(input) {
    // React handlers still receive Enter, but it cannot submit the form.
    const stop = event => { if (event.key === 'Enter') event.preventDefault(); };
    input.addEventListener('keydown', stop);
    keypress(input, 'Enter');
    input.removeEventListener('keydown', stop);
  }
  async function promptOption(element, value) {
    const exact = option => skillNormal(optionName(option)) === skillNormal(value);
    if (!comboOptions(element).length) return null;
    const lists = new Set();
    for (const option of comboOptions(element)) {
      for (let node=option.parentElement; node && !node.matches('body,form'); node=node.parentElement) {
        if (node.scrollHeight > node.clientHeight && /auto|scroll/.test(getComputedStyle(node).overflowY)) lists.add(node);
      }
    }
    for (const list of lists) { list.scrollTop=0; list.dispatchEvent(new Event('scroll', {bubbles:true})); }
    await settledComboOptions(element,1000);
    for (let step=0; step<80; step++) {
      const option=comboOptions(element).find(exact);
      if (option) {
        option.scrollIntoView({block:'nearest'});
        // Scrolling a virtual list can replace the option node.
        await settledComboOptions(element,1000);
        const shown=comboOptions(element).find(exact);
        if (shown?.isConnected) return shown;
      }
      let moved=false;
      for (const list of lists) {
        const before=list.scrollTop;
        list.scrollTop += Math.max(40,list.clientHeight * 0.8);
        list.dispatchEvent(new Event('scroll', {bubbles:true}));
        moved ||= list.scrollTop > before;
        trace('dropdown','scrolled',element,{scrollTop:list.scrollTop});
      }
      if (!moved) break;
      await settledComboOptions(element,1000);
    }
    return null;
  }
  function selectedSkills(element) {
    const container = comboContainer(element);
    return Array.from(container.querySelectorAll('[data-automation-id="selectedItem"],[data-automation-id="selectedItemLabel"],[class*="multiValueLabel"],[class*="multi-value__label"],[class*="token"],[data-skill]')).map(node=>{const copy=node.cloneNode(true);copy.querySelectorAll('button,[role="button"]').forEach(el=>el.remove());return normal(copy.getAttribute('data-automation-label') || text(copy.querySelector('[data-automation-id="selectedItemLabel"]')) || text(copy));});
  }
  const skillNormal = value => normal(value).replace(/[^a-z0-9+#]/g, '');
  function isSkillsField(element) {
    const container=comboContainer(element);
    return /skills/i.test(label(element)+' '+container.getAttribute('data-automation-id')) &&
      (isPrompt(element) || element.getAttribute('role')==='combobox' || element.getAttribute('data-automation-id')==='multiSelectInput' ||
       element.getAttribute('data-allow-create')==='true' || /token|creatable/i.test(container.className));
  }
  function skillInputs() {
    return Array.from(document.querySelectorAll('input,[role="combobox"]')).filter(el=>visible(el) && isSkillsField(el) && !el.parentElement?.closest('[role="combobox"]'));
  }
  function pointerClick(element, current=()=>true) {
    checkRepair();
    for (const type of ['pointerover','pointerdown','mousedown','pointerup','mouseup']) {
      const EventType=type.startsWith('pointer')?PointerEvent:MouseEvent;
      if (!element.isConnected || !current()) return false;
      checkRepair();repairDispatch(()=>element.dispatchEvent(new EventType(type,{bubbles:true,button:0,pointerId:1,pointerType:'mouse'})));
    }
    if (!element.isConnected || !current()) return false;
    checkRepair();repairDispatch(()=>element.click());
    return true;
  }
  let skillsCancelled=false, skillsState={busy:false,progress:''};
  const optionName = option => text(option.querySelector('[data-automation-id="promptOption"]')) ||
    clean(option.getAttribute('data-automation-label')) || text(option) || clean(option.getAttribute('aria-label')).replace(/ (?:not )?checked$/i,'');
  const skillRequest = async (type, payload={}) => {
    try {const response=await chrome.runtime.sendMessage({type,...payload});if(response?.error)trace(type,'error',null,{error:response.error});return response;}catch(error){trace(type,'error',null,{error:error.message,stack:error.stack});return {}; }
  };
  function splitSkill(skill) {
    const expanded = {js:'JavaScript',ts:'TypeScript'};
    return [...new Set(skill.split(/\s*(?:\/|&|,|\band\b)\s*/i).filter(Boolean)
      .map(part=>expanded[normal(part)] || part.trim()))];
  }
  function skillResults(element, previousLists) {
    const rows=comboOptions(element);
    const lists=Array.from(element.getRootNode().querySelectorAll('[data-automation-id="activeListContainer"],[role="listbox"]'))
      .filter(list=>visible(list) && !list.matches('[data-automation-id="selectedItemList"]') && rows.some(row=>list.contains(row)));
    return {rows,lists};
  }
  async function selectSkillOption(element, name, previousLists, cancelled) {
    // Never retain virtualized rows or synthesize opaque Workday IDs.
    const currentRow=()=>skillResults(element,previousLists).rows.find(row=>skillNormal(optionName(row))===skillNormal(name));
    const evidence=()=>{
      const row=currentRow(), ariaLabel=row?.getAttribute('aria-label') || '';
      const checked=row?.getAttribute('aria-checked')==='true' ||
        Boolean(row?.querySelector('[role="checkbox"][aria-checked="true"]')) ||
        / checked$/i.test(ariaLabel) && !/ not checked$/i.test(ariaLabel);
      const pill=selectedSkills(element).some(value=>skillNormal(value)===skillNormal(name))?name:'';
      return {committed:Boolean(pill || checked),pill,aria_label:ariaLabel,
        aria_checked:row?.getAttribute('aria-checked') || '',
        checkbox_checked:Boolean(row?.querySelector('input[type="checkbox"]')?.checked)};
    };
    let activated=false;
    for(let attempt=0;attempt<3 && !cancelled();attempt++) {
      let row=currentRow();
      // A checked native property prevents toggling off, but cannot prove commit.
      if(evidence().committed || row?.querySelector('input[type="checkbox"]')?.checked) break;
      if(!row) {await pause(60);continue;}
      row.scrollIntoView({block:'nearest'});
      await pause(60);
      row=currentRow();
      if(!row) continue;
      if(evidence().committed || row.querySelector('input[type="checkbox"]')?.checked) break;
      const target=row.querySelector('input[type="checkbox"]') || row.querySelector('[role="checkbox"]') ||
        row.querySelector('[data-automation-id="checkbox"]') || row;
      if(target.disabled || target.matches(':disabled') || target.getAttribute('aria-disabled')==='true') break;
      const details={target:name,element_id:row.id,target_tag:target.tagName.toLowerCase(),
        target_automation_id:target.getAttribute('data-automation-id') || '',target_id:target.id};
      activated=true;
      const clicked=pointerClick(target,()=>currentRow()===row && !evidence().committed && !target.checked);
      trace('skills',clicked?'clicked':'activation interrupted',element,details);
      if(clicked) break; // Never repeat an activation while a commit may be pending.
    }
    let stableSince=0;
    const committed=await waitFor(()=>{
      if(cancelled()) return false;
      const state=evidence();
      if(!state.committed) {stableSince=0;return false;}
      if(!stableSince) stableSince=Date.now();
      return Date.now()-stableSince>=200 && state;
    },2000);
    const final=evidence(), verified=Boolean(committed && final.committed && !cancelled());
    trace('skills','verified',element,{target:name,verified,...final,stable_ms:verified?Date.now()-stableSince:0});
    return {verified,activated};
  }
  async function skillOptions(element, previousLists) {
    const end=Date.now()+4000;
    let signature='', stableSince=Date.now(), popup=false;
    do {
      const {rows,lists}=skillResults(element,previousLists);
      popup ||= lists.length>0;
      const next=JSON.stringify(rows.map(optionName));
      if(next!==signature || !rows.length) stableSince=Date.now();
      else if(Date.now()-stableSince>=250){trace("options","options seen",element,{texts:rows.slice(0,30).map(optionName),total:rows.length});return {rows,popup,lists};}
      signature=next;
      await pause(50);
    } while(Date.now()<end);
    return {rows:[],popup};
  }
  async function fillSkills(element, skills, progress=()=>{}, cancelled=()=>false) {
    let added=0, skipped=0;
    const response=await skillRequest('skill-aliases');
    const aliases=response?.aliases || {};
    const selected=skill=>selectedSkills(element).some(value=>skillNormal(value)===skillNormal(skill));
    async function add(skill,index,split=true) {
      if(cancelled()) return false;
      if(/^c\s*\/\s*c\+\+$/i.test(skill)) {
        const c=await add('C',index,false);
        const cpp=await add('C++',index,false);
        const success=c && cpp;
        progress(index,skill,success?'added':'skipped');
        return success;
      }
      const alias=aliases[skillNormal(skill)];
      if(selected(skill) || alias && selected(alias)){trace('skills','skipped',element,{reason:'already selected',value:skill});progress(index,skill,'already added');return true;}
      let choice, status='added', ok=false;
      for(let attempt=0;attempt<2 && !cancelled();attempt++) {
        let popup=false;
        try {
          progress(index,skill,'pending');
          const previous=new Set(Array.from(document.querySelectorAll('[role="listbox"]')).filter(visible));
          bindCombo(element);comboInput(element)?.focus(); searchCombo(element,''); searchCombo(element,skill);
          if(isPrompt(element)) promptEnter(comboInput(element)); else keypress(comboInput(element),'ArrowDown');
          const options=await skillOptions(element,previous); popup=options.popup;
          trace("skills",popup?"opened":"skipped",element,{reason:popup?"results ready":"no results popup"});
          if(cancelled()) break;
          const collected=new Map();
          const remember=rows=>rows.forEach(row=>collected.set(optionName(row),true));
          const exact=rows=>rows.find(row=>skillNormal(optionName(row))===skillNormal(skill)) || (alias && rows.find(row=>skillNormal(optionName(row))===skillNormal(alias)));
          let rows=options.rows,still=0;remember(rows);choice=exact(rows);
          const lists=options.lists || [];
          while(!choice && lists.length && still<2 && !cancelled()){
            const count=collected.size;
            for(const list of lists){list.scrollTop+=Math.max(40,list.clientHeight);list.dispatchEvent(new Event('scroll',{bubbles:true}));trace('skills','scrolled',element,{scrollTop:list.scrollTop,rows:rows.length});}
            await pause(300);rows=(await skillOptions(element,previous)).rows;remember(rows);choice=exact(rows);still=collected.size===count?still+1:0;
          }
          trace('skills','options collected',element,{texts:[...collected.keys()].slice(0,30),total:collected.size});
          if(choice && alias && skillNormal(optionName(choice))===skillNormal(alias))status='alias used';
          if(!choice && collected.size && !cancelled()){
            const result=await skillRequest('skill-choose',{skill,options:[...collected.keys()]});
            if(collected.has(result?.choice)){
              for(const list of lists){list.scrollTop=0;list.dispatchEvent(new Event('scroll',{bubbles:true}));}
              while(!choice && !cancelled()){
                await pause(300);rows=(await skillOptions(element,previous)).rows;choice=rows.find(row=>optionName(row)===result.choice);if(choice)break;
                let moved=false;for(const list of lists){const before=list.scrollTop;list.scrollTop+=Math.max(40,list.clientHeight);list.dispatchEvent(new Event('scroll',{bubbles:true}));moved ||= before!==list.scrollTop;}if(!moved)break;
              }
              if(choice){status='AI picked';aliases[skillNormal(skill)]=optionName(choice);}
            }
          }
          if(choice && !cancelled()) {
            const name=optionName(choice);
            if(selected(name)) ok=true;
            else {
              const selection=await selectSkillOption(element,name,previous,cancelled);
              ok=selection.verified;
              if(ok && selection.activated) added++;
              else if(ok) status='already added';
            }
          }
        } catch(error){trace("skills","error",element,{error:error.message,stack:error.stack});ok=false;}
        finally {closeCombo(element);searchCombo(element,'');}
        if(ok || popup || cancelled()) break; // Retry only when no results popup appeared.
      }
      if(cancelled()) return false;
      if(!ok && split) {
        let parts=splitSkill(skill);
        if(parts.length>1) {
          let success=false;
          for(const part of parts) {
            const partOK=await add(part,index,false);success ||= partOK;
          }
          progress(index,skill,success?'added':'skipped');return success;
        }
      }
      if(!ok){trace('skills','skipped',element,{reason:'No verified matching option',value:skill});skipped++;}
      progress(index,skill,ok?status:'skipped');
      await pause(180);
      return ok;
    }
    const unique=skills.filter((skill,index)=>skills.findIndex(other=>skillNormal(other)===skillNormal(skill))===index);
    for(const [index,skill] of unique.entries()) {
      if(cancelled()) break;
      await add(skill,index+1);
    }
    return {added,skipped};
  }
  async function autofillSkills(skills) {return traced("skills",()=>runSkills(skills));}
  async function runSkills(skills) {
    if(skillsState.busy) throw Error('Skills autofill is already running.');
    skillsCancelled=false;skillsState={busy:true,progress:'Starting skills…',items:{}};extensionFilling++;
    try {
      const element=skillInputs()[0];
      if(!element) throw Error('No skills tag field found.');
      const counts=await fillSkills(element,skills,(index,skill,status)=>{
        skillsState.progress=index+'/'+skills.length+': '+skill+' - '+status;skillsState.items[skill]=status;
      },()=>skillsCancelled);
      skillsState={...skillsState,...counts,busy:false,progress:(skillsCancelled?'Cancelled. ':'')+counts.added+' added / '+counts.skipped+' skipped'};
      return skillsState;
    } finally {skillsState.busy=false;extensionFilling--;}
  }
  const checked = input => input.checked || input.getAttribute('aria-checked')==='true';
  async function setCheckbox(input, value) {
    checkRepair();
    input=currentControl(input);
    if(Boolean(checked(input))===value) return true;
    checkRepair();repairDispatch(()=>input.click());
    if(await waitFor(()=>Boolean(checked(currentControl(input)))===value,150)) return true;
    const wrapper=input.closest('[data-automation-id^="formField"]') || input.parentElement;
    const targets=[...Array.from(input.labels||[]),input.closest('label'),wrapper.querySelector('[role="checkbox"]'),input.nextElementSibling].filter(el=>el && visible(el) &&
      (!repairExecuting || el.matches('label,[role="checkbox"],[data-automation-id="checkbox"]')));
    for(const target of new Set(targets)) {
      pointerClick(target);
      if(await waitFor(()=>Boolean(checked(currentControl(input)))===value,300)) return true;
    }
    return false;
  }
  async function ensureCurrent(group) {
    const input=Array.from(group.querySelectorAll('input[type="checkbox"]')).find(el=>sectionField(el)==='current' || /currentlyWorkHere/i.test(el.getAttribute('data-automation-id')||''));
    if(!input) return false;
    for(let attempt=0;attempt<2;attempt++) {
      await setCheckbox(input,true);
      if(await waitFor(()=>checked(input) && Array.from(group.querySelectorAll('input,select')).filter(el=>sectionField(el)==='end_date').every(el=>!visible(el)),1000)) return true;
    }
    return false;
  }
  async function selfIdentify(profile, managed) {
    if(!/Voluntary Self.Identification of Disability|Form CC.305/i.test(document.body.innerText)) return [];
    const errors=[], today=new Date(), month=String(today.getMonth()+1).padStart(2,'0'), day=String(today.getDate()).padStart(2,'0'), year=String(today.getFullYear());
    const name=profile.identity?.full_name || profile.full_name;
    for(const record of records.values()) {
      const el=record.element, field=el.closest('[data-automation-id^="formField"]');
      if(!field) continue;
      const heading=normal(text(field.querySelector('label,[data-automation-id="formLabel"]')) || label(el)).replace(/\s*\*$/,'');
      if(/employee id/.test(heading)) {managed.add(el);continue;}
      if(/^(name|full name)$/.test(heading)) {managed.add(el);if(!clean(el.value)&&name){nativeSet(el,'value',name);events(el);}continue;}
      if(/^language$/.test(heading)) {managed.add(el);if(el.tagName==='SELECT'){const option=Array.from(el.options).find(o=>normal(o.text)==='english');if(option){nativeSet(el,'value',option.value);events(el);}}else if(record.field.kind==='combobox') await fillCombo(el,'English');continue;}
    }
    // Dates and the exclusive CC305 disclosure use the shared bound records.
    return errors;
  }
  async function prepareSections(profile) {return traced("section",()=>fillSections(profile));}
  async function fillSections(profile) {
    const sections=[], supplied=new Map(), managed=new Set(), completed=[], errors=[];
    const containers=resumeSections();
    extensionFilling++;
    try {
      for (const [container,kind] of containers) {
        const entries = kind==='links'?Object.entries(profile.links||{}).filter(([,url])=>url).map(([title,url])=>({title,url})):profile[kind] || [];
        if (kind==='skills') continue;
        const cap = entries.length;
        let groups=entryGroups(container,kind);
        const findAdd = () => Array.from(container.querySelectorAll('button,a,[role="button"],input[type="button"]')).find(el=>visible(el) &&
          (el.type!=='submit' || el.getAttribute('data-automation-id')==='add-button') && /^(add(?: another)?(?: (?:work experience|experience|employment|project|education|language|website|link))?|\+)$/.test(sectionNormal(text(el) || el.value || el.getAttribute('aria-label'))) &&
          (el.tagName!=='A' || !el.getAttribute('href') || el.getAttribute('href')==='#'));
        let added=0;
        for (let index=0; index<Math.min(entries.length,cap); index++) {
          if (index>=groups.length) {
            const add=findAdd();
            if (!add || groups.length>=entries.length) break;
            const before=groups.length;
            const prevent=event=>event.preventDefault();
            add.addEventListener('click',prevent); add.click(); add.removeEventListener('click',prevent);
            if (!await waitFor(()=>entryGroups(container,kind).length>before,5000)) break;
            groups=entryGroups(container,kind);
          }
          const group=groups[index], entry=entries[index];
          const current=Boolean(entry.current || entry.start_date && !entry.end_date);
          const skipEnd=current && ['experience','projects'].includes(kind);
          const elements=Array.from(group.querySelectorAll('input,textarea,select,[role="combobox"],button:is([aria-haspopup="listbox"],[id$="--degree"])')).filter(interactiveVisible);
          const isOccupied=el=>occupied({element:el,members:[el],field:{kind:el.tagName==='SELECT'?'select':el.matches('[role="combobox"],button:is([aria-haspopup="listbox"],[id$="--degree"])') || isPrompt(el)?'combobox':el.type||'text'}});
          const occupiedGroup=elements.some(el=>el.type!=='hidden' && (el.type!=='button' || el.matches('button:is([aria-haspopup="listbox"],[id$="--degree"])')) && sectionField(el)!=='current' && !(kind==='education' && sectionField(el)==='degree' && el.closest('[data-automation-id="formField-degree"]')) && isOccupied(el));
          const anchorKey=kind==='education'?'school':kind==='languages'?'language':kind==='links'?'url':'title';
          const anchor=elements.find(el=>sectionField(el)===anchorKey);
          const anchorValue=anchor?.matches('[role="combobox"],button:is([aria-haspopup="listbox"],[id$="--degree"])')?comboSelection(anchor):isPrompt(anchor || document.body)?comboSelection(anchor):anchor?.value;
          const matchesEntry=Boolean(entry[anchorKey] && normal(anchorValue)===normal(entry[anchorKey]));
          if(current && skipEnd && (!occupiedGroup || matchesEntry) && !await ensureCurrent(group)) errors.push('I currently work here: could not verify checkbox and To date');
          for (const el of elements) {
            managed.add(el);
            // Only complete a partially filled entry when its anchor matches the profile.
            const degreeReplace=kind==='education' && !repairEdited.has(el) && sectionField(el)==='degree' && Boolean(el.closest('[data-automation-id="formField-degree"]')) && !best([comboSelection(el) || el.value],entry.degree);
             if((occupiedGroup && !matchesEntry) || isOccupied(el) && !degreeReplace){trace('section','skipped',el,{reason:'occupied entry or field'});continue;}
             trace('section','found',el);
            const key=sectionField(el); if(!key && kind!=='languages') continue;
            let value=entry[key];
            if(kind==='languages' && key!=='language' && key!=='fluent' && (el.tagName==='SELECT' || el.getAttribute('role')==='combobox' || el.matches('button:is([aria-haspopup="listbox"],[id$="--degree"])'))) value=entry.proficiency;
            if(kind==='education' && key==='degree' && (el.tagName==='SELECT' || el.getAttribute('role')==='combobox' || el.matches('button:is([aria-haspopup="listbox"],[id$="--degree"])'))) {
              const options=el.tagName==='SELECT'?Array.from(el.options).filter(o=>o.value && !o.disabled):await (openCombo(el),settledComboOptions(el,350));
              if(!best(options,value,el.tagName==='SELECT'?o=>o.text:text) && best(options,entry.major,el.tagName==='SELECT'?o=>o.text:text)) value=entry.major;
              if(el.tagName!=='SELECT') closeCombo(el);
            }
            if(key==='company') value=entry.company || entry.organization;
            if(kind==='links' && key==='url') {const named=Object.keys(profile.links||{}).find(name=>sectionNormal(label(el)).includes(sectionNormal(name)));if(named)value=profile.links[named];}
            if(key==='title' && /^(role|position)$/.test(sectionNormal(label(el))) && kind==='projects') value=entry.organization || entry.title;
            if(key==='technologies') value=(entry.technologies||[]).join(', ');
            if(key==='current') value=current;
            if(key==='fluent') value=Boolean(entry.fluent);
            if(occupiedGroup && el.type==='checkbox' && value===el.checked) continue;
            if(key==='end_date' && skipEnd) value='';
            else if(/_date$/.test(key)) value=dateValue(el,value);
            if(value!==undefined && value!=='') supplied.set(el,{value:trimSection(value,el.maxLength>=0?el.maxLength:null),boolean:key==='current'||key==='fluent',force:degreeReplace});
          }
          if(!occupiedGroup || matchesEntry) {
            // Commit this entry before clicking Add for the next one.
            await scan();
            const entryAnswers=[];
            for(const record of records.values()) {
              const chosen=supplied.get(record.element);
              if(group.contains(record.element) && chosen) entryAnswers.push({key:record.field.key,status:'RESOLVED',value:chosen.boolean?chosen.value==='true':chosen.value,force:chosen.force,source:'profile:sections'});
            }
            entryAnswers.sort((a,b)=>Number(records.get(b.key)?.field.kind==='checkbox')-Number(records.get(a.key)?.field.kind==='checkbox'));
            const filled=await fill(entryAnswers);
            completed.push(...filled.completed);
            const endInputs=elements.filter(el=>sectionField(el)==='end_date');
            const currentBox=elements.find(el=>sectionField(el)==='current' && el.type==='checkbox');
            const currentVerified=!current || !currentBox || await waitFor(()=>currentBox.checked && (!skipEnd || endInputs.every(el=>!visible(el))),1000);
            if (!filled.pending.length && !filled.errors.length && (filled.filled || matchesEntry) && currentVerified) added++;
            else break;
          }
        }
        sections.push({name:kind,added,skipped:0,total:entries.length});
      }
      await scan();
      for(const [container,kind] of containers) {
        if(kind==='skills' && profile.skills?.length) for(const record of records.values()) {
          if(!container.contains(record.element) || !/skills/i.test(record.field.label)) continue;
          managed.add(record.element);
          if(record.field.kind==='combobox') {
            sections.push({name:kind,message:'Skills: run Autofill skills'});
          } else if(['textarea','text'].includes(record.field.kind) && !occupied(record)) {
            const included=[];
            for(const skill of profile.skills) {
              if(record.field.max_length!==null && [...included,skill].join(', ').length>record.field.max_length) break;
              included.push(skill);
            }
            supplied.set(record.element,{value:included.join(', ')});
            sections.push({name:kind,added:included.length,skipped:profile.skills.length-included.length});
          }
        }
        if(kind==='projects' && profile.projects?.length && !entryGroups(container,kind).length) for(const record of records.values()) {
          if(!container.contains(record.element) || record.field.kind!=='textarea' || /tell|describe|why|how|essay|\?/.test(normal(record.field.label))) continue;
          managed.add(record.element);
          const value=(profile.projects||[]).map(p=>[p.title,p.organization,(p.technologies||[]).join(', ')].filter(Boolean).join(' - ')+': '+clean(p.description)).join('\n');
          supplied.set(record.element,{value:trimSection(value,record.field.max_length)});
          sections.push({name:kind,added:(profile.projects||[]).length,skipped:0});
        }
      }
      // One stable question per absent required start date, shared by month/year controls.
      for(const [container,kind] of containers.filter(([,k])=>['experience','projects','education'].includes(k))) {
        for(const [index,group] of entryGroups(container,kind).entries()) {
          const entry=(profile[kind]||[])[index]; if(!entry || entry.start_date) continue;
          const dates=Array.from(records.values()).filter(r=>group.contains(r.element) && sectionField(r.element)==='start_date' && !occupied(r));
          if(!dates.some(r=>r.field.required)) continue;
          const first=dates[0];
          first.dateElements=dates.map(r=>r.element);
          first.field={...first.field,label:kind==='education'?entry.school+': start date':entry.title+' at '+(entry.company||entry.organization||'')+': start date',kind:'text',options:[],required:true,max_length:null,section:''};
          first.field.section_missing_date=true;
          dates.slice(1).forEach(r=>records.delete(r.field.key));
        }
      }
      for(const record of records.values()) if(isSkillsField(record.element)) managed.add(record.element);
      if(skillInputs().length && !sections.some(s=>s.name==='skills')) sections.push({name:'skills',message:'Skills: run Autofill skills'});
      errors.push(...await selfIdentify(profile,managed));
      const answers=[];
      for(const record of records.values()) {
        const chosen=supplied.get(record.element);
        if(managed.has(record.element) && !record.field.section_missing_date) {
          record.field.section_managed=true;
          answers.push({key:record.field.key,status:chosen?'RESOLVED':'OPTIONAL_SKIP',value:chosen?.boolean?chosen.value==='true':chosen?.value,source:'profile:sections'});
        }
      }
      return {fields:Array.from(records.values(),r=>({...r.field,answered:occupied(r)})),answers,sections,completed,errors};
    } finally { extensionFilling--; }
  }

  function workdayState() {
    if (!location.hostname.endsWith('.myworkdayjobs.com') &&
        !document.querySelector('[data-automation-id="progressBar"],[data-automation-id="progressBarActiveStep"],[data-automation-id="signInContent"]')) return null;
    const active = document.querySelector('[data-automation-id="progressBarActiveStep"]') ||
      document.querySelector('[data-automation-id="progressBar"] [aria-current="step"]');
    const step = text(active) || text(document.querySelector('main h1,main h2,form h1,form h2,[data-automation-id="heading"]'));
    const fields = Array.from(document.querySelectorAll('input,textarea,select,button:is([aria-haspopup="listbox"],[id$="--degree"])'))
      .filter(el => el.type === 'file' || visible(el));
    return {step, sign_in: signInPage(), fields};
  }
  let previousStep = workdayState(), stepTimer;
  function detectStep() {
    const state = workdayState();
    if (!state) { previousStep = null; return; }
    // Compare element identity, not values or menu contents changed by filling.
    const replaced = state.fields.length && previousStep?.fields.length &&
      previousStep.fields.every(el => !el.isConnected);
    const changed = !previousStep || state.step !== previousStep.step ||
      state.sign_in !== previousStep.sign_in || replaced ||
      state.fields.length > 0 && previousStep.fields.length === 0;
    previousStep = state;
    if (changed && !state.sign_in && state.fields.length) {
      chrome.runtime.sendMessage({type: 'workday-step', step: state.step}).catch(() => {});
    }
  }
  new MutationObserver(() => {
    clearTimeout(stepTimer);
    stepTimer = setTimeout(detectStep, 700);
  }).observe(document.body, {subtree: true, childList: true, characterData: true,
    attributes: true, attributeFilter: ['aria-current', 'data-automation-id', 'class', 'hidden']});
  // Full document navigation also resumes tabs authorized earlier in the session.
  if (previousStep && !previousStep.sign_in && previousStep.fields.length) {
    previousStep = null;
    stepTimer = setTimeout(detectStep, 700);
  }
  function validationErrors() {
    return [...new Set(Array.from(document.querySelectorAll('[role="alert"],[aria-invalid="true"],[data-automation-id*="error" i]'))
      .filter(visible).map(node => text(node) || label(node)).filter(Boolean))];
  }
  function navigationButton(final = false) {
    if(final && (signInPage() || globalThis.autoApplyCredentials?.incomplete()))return;
    const step=workdayState()?.step || '';
    const candidates=Array.from(document.querySelectorAll('button,input[type="button"],input[type="submit"],[role="button"]')).filter(node=>{
      const caption=clean(node.getAttribute('aria-label') || clean(node.getAttribute('aria-labelledby')).split(' ').map(id=>text(native.byId(node,id))).join(' ') || node.textContent || node.value);
      if(!interactiveVisible(node))return false;
      if(final)return /^(submit|submit application|send application|apply(?: now| application)?)$/i.test(caption) &&
        (/review/i.test(step) || node.getAttribute('data-automation-id')==='pageFooterNextButton' ||
         applicationWritingScope(node.form || writingScope(node)));
      // Workday reuses pageFooterNextButton for both Continue and Submit.
      // Inspect action words, without treating stepper text such as Application
      // Questions or generic jobApplication identifiers as final actions.
      const finalAction=value=>/\b(?:submit|apply|final[ -]?submit)\b/i.test(clean(value)
        .replace(/([a-z])([A-Z])/g,'$1 $2').replace(/[_-]/g,' '));
      const labelledBy=clean(node.getAttribute('aria-labelledby')).split(' ').map(id=>text(native.byId(node,id))).join(' ');
      const identity=[caption,node.getAttribute('aria-label'),labelledBy,node.title,
        node.getAttribute('data-automation-id'),node.getAttribute('data-automation-label'),node.id,node.name];
      return /^(next|save and continue|continue)$/i.test(caption) && !identity.some(finalAction);
    });
    return final?candidates.length===1?candidates[0]:undefined:candidates[0];
  }
  function navigationState() {
    const final=Boolean(navigationButton(true));
    const entry=!final && !navigationButton() && applicationEntry();
    return {kind:final?'submit':navigationButton()?'next':entry?'entry':'none',...(entry?{label:clean(entry.getAttribute('aria-label') || entry.textContent)}:{}),url:location.href,step:workdayState()?.step || ''};
  }
  async function manualSubmit(expected, nativeIntent=false) {
    await flushEdits();
    const state=navigationState(), errors=validationErrors();
    if(!expected || expected.url!==state.url || expected.step!==state.step || state.kind!=='submit' ||
       signInPage())
      return {clicked:false,errors,message:'Application changed or needs review. Use the employer page.'};
    const button=navigationButton(true),fresh=navigationState();
    if(!button || fresh.url!==state.url || fresh.step!==state.step || fresh.kind!=='submit')return {clicked:false,errors:[],message:'Submit is unavailable. Use the employer page.'};
    if(nativeIntent)announceSubmission();else armSubmission();
    button.click();
    detectConfirmation();
    return {clicked:true,errors:validationErrors()};
  }
  function fieldState(key, expected) {
    const record=records.get(key);
    if(!record || !boundRecord(record) || expected?.url!==location.href ||
       expected.control_identity!==record.field.control_identity || expected.label!==record.field.label)
      throw Error('Question changed. Run Autofill again.');
    return {field:record.field,value:valueOf(record),valid:!record.members.some(node=>node.getAttribute('aria-invalid')==='true' || node.validity && !node.validity.valid)};
  }
  function revealField(key, expected) {
    fieldState(key,expected);
    const record=records.get(key),node=record.element;
    node.scrollIntoView({block:'center',behavior:'smooth'});
    const animation=node.animate?.([{outline:'2px solid #b8b0df'},{outline:'2px solid transparent'}],{duration:1200});
    return {ok:true};
  }
  function protectField(key,expected){
    fieldState(key,expected);
    for(const member of records.get(key).members)repairEdited.add(member);
    repairRevision++;return {ok:true};
  }
  function restoreField(key,expected){
    fieldState(key,expected);
    for(const member of records.get(key).members)repairEdited.delete(member);
    return {ok:true};
  }
  let enterSubmitting=false;
  document.addEventListener('keydown',event=>{
    if(!event.isTrusted || event.key!=='Enter' || event.isComposing || event.keyCode===229 || event.repeat ||
       event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || enterSubmitting)return;
    const target=native.eventTarget(event),button=navigationButton(true);
    if(!button || native.closest(target,'textarea,input,select,[contenteditable]:not([contenteditable="false"]),#autoapply-sidebar,.autoapply-ask-ai') ||
       native.query('[role="listbox"],[role="menu"],[aria-expanded="true"]').some(visible) ||
       !(target===button || target===document.body || target===document.documentElement))return;
    event.preventDefault();enterSubmitting=true;
    chrome.runtime.sendMessage({type:'native-submit',expected:navigationState()}).then(value=>{
      if(value?.error)chrome.runtime.sendMessage({type:'user-answer-error',error:value.error}).catch(()=>{});
    }).catch(error=>chrome.runtime.sendMessage({type:'user-answer-error',error:error.message}).catch(()=>{})).finally(()=>{enterSubmitting=false;});
  },true);
  async function nextPage(requireComplete = false) {
    const errors = validationErrors();
    if (requireComplete && errors.length) return {clicked: false, errors};
    if (signInPage()) return {clicked: false, errors: ['Sign in before continuing.']};
    if (requireComplete && (await scan()).fields.some(field => field.required && !field.answered)) return {clicked: false, errors: []};
    if(navigationState().kind==='submit')return {clicked:false,errors:[],message:'Final submit is yours'};
    if(navigationState().kind==='entry')return {clicked:enterApplication(),errors:[]};
    const button = navigationButton();
    if (!button) {
      const final = Array.from(document.querySelectorAll('button,input[type="submit"],[role="button"]')).some(node =>
        visible(node) && /submit|apply/i.test([node.textContent, node.value, node.getAttribute('aria-label')].join(' ')));
      return {clicked: false, errors: [], message: final ? 'Final submit is yours' : 'No Next page button found.'};
    }
    button.click();
    await pause(1000);
    return {clicked: true, errors: validationErrors()};
  }
  const writingButtons = new Map();
  const writingSelector = 'textarea,[contenteditable="true"],input[type="text"],input:not([type])';
  const writingScope = field => field.closest('form,[role="form"]') || field.closest('main') || field.closest('section,article');
  function applicationWritingScope(root) {
    if (!root || !visible(root)) return false;
    // Inspect structure and captions, never answers, menus, or paired state.
    const nodes = selector => Array.from(root.querySelectorAll(selector)).filter(node => writingScope(node) === root);
    const identity = node => clean([node.id,node.getAttribute('name'),node.getAttribute('aria-label'),
      ...Array.from(node.labels || [], questionText)].join(' '));
    const controls = nodes('input,select,textarea,[contenteditable="true"]');
    const resume = controls.some(node => node.type === 'file' && /resume|\bcv\b/i.test(identity(node)));
    const contact = controls.filter(visible).some(node => /email|phone|full.?name|first.?name|last.?name/i.test(identity(node)));
    const headingSelector = 'h1,h2,h3,legend,[role="heading"]';
    const applicationHeading = node => visible(node) && /\b(?:job application|application (?:form|questions)|apply (?:for|to) (?:this|the|our)|your application)\b/i.test(text(node));
    const heading = nodes(headingSelector).some(applicationHeading);
    const finalApply = nodes('button,input[type="submit"],[role="button"]').filter(visible)
      .some(node => /^(?:submit application|send application|apply(?: now)?)$/i.test(clean(node.getAttribute('aria-label') || node.textContent || node.value)));
    if (heading && (contact || resume) || resume && contact && finalApply) return true;
    // Generic employer forms often put their heading immediately before the form.
    // Keep that association local and require strong evidence within this form.
    const preceding = root.previousElementSibling;
    if (root.matches('form,[role="form"]') && preceding?.matches(headingSelector) &&
        applicationHeading(preceding) && resume && contact &&
        nodes('button,input[type="submit"],[role="button"]').filter(visible)
          .some(node => /^submit$/i.test(clean(node.textContent || node.value)))) return true;
    const doc = root.ownerDocument, url = new URL(doc.URL);
    const host = url.hostname, path = url.pathname;
    const atsRoute = /(^|\.)(?:myworkdayjobs\.com|myworkdaysite\.com)$/.test(host) && /\/apply(?:\/|$)/i.test(path) ||
      /(^|\.)(?:greenhouse\.io|greenhouse\.com)$/.test(host) && /\/(?:jobs|embed\/job_app)\b/i.test(path) ||
      /(^|\.)pinpointhq\.com$/.test(host) && /\/postings\/[^/]+\/applications(?:\/new)?\/?$/i.test(path) ||
      /(^|\.)(?:applytojob\.com|jazz\.co)$/.test(host) && /\/apply\/[^/]+/i.test(path);
    const applicationControls = controls.some(node => /^(?:application_form_application_|resumator-)/.test(node.id)) ||
      Boolean(doc.querySelector('[data-automation-id="progressBarActiveStep"]') && controls.some(node => node.hasAttribute('data-automation-id')));
    return Boolean(atsRoute && (resume || applicationControls || heading && contact));
  }
  function writingApplication(field, scopes) {
    if(robotOnly(field))return false;
    if (!field.isConnected || !field.matches(writingSelector) || !visible(field) || field.closest('#autoapply-sidebar,.autoapply-ask-ai,aside,nav,[role="search"]')) return false;
    if (field.tagName === 'INPUT' && !(field.size >= 50 || field.getBoundingClientRect().height >= 60)) return false;
    // A chat/comment composer embedded alongside an application is still unrelated.
    if (/\b(?:chat|comment|newsletter|search|user.?prompt|support)\b/i.test([field.id,field.name,field.getAttribute('aria-label')].join(' ').replace(/[_-]/g,' '))) return false;
    const root = writingScope(field);
    if (!root) return false;
    if (!scopes.has(root)) scopes.set(root, applicationWritingScope(root));
    if (scopes.get(root)) return true;
    // Some employers split written questions into a same-origin application frame.
    // Require an explicitly named application frame inside a qualifying form.
    try {
      const frame = window.frameElement;
      return Boolean(frame && visible(frame) && /application|applicant/i.test(frame.title) &&
        applicationWritingScope(writingScope(frame)));
    } catch { return false; }
  }
  function syncWritingButtons() {
    const scopes = new Map();
    for (const [field, host] of writingButtons) if (!host.isConnected || !writingApplication(field,scopes)) { host.remove(); writingButtons.delete(field); }
    for (const field of document.querySelectorAll(writingSelector)) {
      if (writingButtons.has(field) || !writingApplication(field,scopes)) continue;
      const host = document.createElement('span'); host.className='autoapply-ask-ai';
      host.style.cssText='display:block; margin:4px 0 8px;';
      const shadow=host.attachShadow({mode:'open'}), style=document.createElement('style'), button=document.createElement('button');
      style.textContent='button{font:11px system-ui;padding:3px 8px;border:1px solid #555;border-radius:5px;background:#191919;color:#eee;cursor:pointer}button:focus-visible{outline:2px solid currentColor;outline-offset:2px}@media(prefers-color-scheme:light){button{background:#fff;color:#333;border-color:#aaa}}';
      button.textContent='Ask AI'; button.type='button';
      button.onclick=async () => {
        if (!writingApplication(field,new Map())) { syncWritingButtons(); return; }
        chatExplicit=field;chatFocus=field;
        if(extensionFilling)return;
        await scan();
        const record=Array.from(records.values()).find(record=>record.element===field);
        if (record) chrome.runtime.sendMessage({type:'ask-ai',key:record.field.key}).catch(()=>{});
      };
      shadow.append(style,button); field.after(host); writingButtons.set(field,host);
    }
    for (const frame of Array.from(document.querySelectorAll('iframe')).slice(0,20)) {
      try { frame.contentWindow.dispatchEvent(new Event('autoapply-writing-context')); } catch { /* Cross-origin frames gate themselves. */ }
    }
  }
  let writingButtonTimer;
  function scheduleWritingButtons() { clearTimeout(writingButtonTimer); writingButtonTimer=setTimeout(syncWritingButtons,80); }
  new MutationObserver(scheduleWritingButtons)
    .observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,
      attributeFilter:['hidden','style','class','aria-hidden','disabled','readonly','aria-disabled','aria-readonly',
        'id','name','title','type','size','aria-label','aria-labelledby','role','contenteditable']});
  for (const event of ['popstate','hashchange','autoapply-writing-context']) window.addEventListener(event,scheduleWritingButtons);
  setTimeout(syncWritingButtons,80);

  // Passive reads only: diagnostics must never replace the scanner's live handles.
  function snapshotIdentity(...args){return native.read(()=>snapshotIdentityRead(...args));}
  function snapshotIdentityRead() {
    const shape=JSON.stringify([location.href, workdayState()?.step, ...Array.from(native.query('form,fieldset,legend,h1,h2,h3,label,input,select,textarea,[role="combobox"],[role="radiogroup"]'))
      .filter(element => !diagnosticExcluded(element))
      .slice(0,10000).map(element => {if(!repairNodes.has(element))repairNodes.set(element,++repairNodeId);return [repairNodes.get(element),element.tagName, element.id.slice(0,2000), element.getAttribute('name')?.slice(0,2000), element.getAttribute('type'), element.getAttribute('aria-label')?.slice(0,2000),element.disabled,element.readOnly,element.getAttribute('aria-hidden'),
        Boolean(element.getClientRects().length), element.matches('legend,h1,h2,h3,label') ? text(element).slice(0,2000) : ''];})]);
    // This passive change detector is compact in session results and works on HTTP
    // fixtures too. The dedupe key is separately SHA-256 hashed by the worker.
    let first=2166136261,second=5381;
    for(let i=0;i<shape.length;i++){first=Math.imul(first^shape.charCodeAt(i),16777619);second=Math.imul(second,33)^shape.charCodeAt(i);}
    return [shape.length,first>>>0,second>>>0].join(':');
  }
  function unresolvedStructure(identity, fields) {
    if (identity !== snapshotIdentity() || signInPage() || extensionFilling) return null;
    if (!fields.every(field => {
      const record=records.get(field.key);
      return record && writableRecord(record) && record.element.isConnected && record.field.label===field.label &&
        (record.field.kind==='file' || interactiveVisible(record.element)) && !occupied(record);
    })) return null;
    const structure=pageStructure();
    return {...structure,diagnostic:inspectPage({passive:true}).diagnostic};
  }
  const diagnosticExcluded=element=>robotOnly(element) || globalThis.autoApplyCredentials?.sensitive(element) || Boolean(element.closest('#autoapply-sidebar,.autoapply-ask-ai')) || Boolean(element.parentElement?.closest('[contenteditable]:not([contenteditable="false"])')) ||
    element.matches('input[type="hidden"],input[type="password"]') || /captcha|honeypot|honey.?pot|fax|website_confirm|password|secret|token|api.?key/i.test([element.id,element.name,element.getAttribute('autocomplete')].join(' '));
  function structureOf(element) {
    const style=getComputedStyle(element),rect=element.getBoundingClientRect();
    const row={tag:element.tagName.toLowerCase(),visible:Boolean(rect.width && rect.height && style.display!=='none' && style.visibility!=='hidden'),
      disabled:Boolean(element.disabled || element.matches(':disabled')),readonly:Boolean(element.readOnly),
      display:style.display,visibility:style.visibility,opacity:Number(style.opacity),
      rect:[rect.x,rect.y,rect.width,rect.height].map(n=>Math.round(Math.max(-100000,Math.min(100000,n))*100)/100),
      transformed:Boolean(style.transform && style.transform!=='none'),transform:(style.transform || 'none').slice(0,200)};
    for(const attr of ['role','type','id','name','class','data-automation-id','aria-label','aria-labelledby','aria-controls','aria-owns','aria-haspopup','aria-expanded','aria-disabled','aria-readonly','aria-hidden','aria-checked','aria-pressed','inputmode','aria-autocomplete']) {
      const value=element.getAttribute(attr);if(value)row[attr]=value.slice(0,300);
    }
    return row;
  }
  // No scan, focus, clicks, keys, or menu opening: retain all live filling handles.
  function inspectPage(...args){return native.read(()=>inspectPageRead(...args));}
  function inspectPageRead({repair = false, passive = false} = {}) {
    if(globalThis.autoApplyCredentials?.active() || globalThis.autoApplyCredentials?.incomplete())return {identity:snapshotIdentity(),fields:[],diagnostic:{fields:[],responsive:true,inspection_ms:0},inspection:{truncated:Boolean(globalThis.autoApplyCredentials?.incomplete()),message:globalThis.autoApplyCredentials?.active()?"Account fields are private.":"Application scan incomplete; fields withheld for privacy."}};
    const started=performance.now();
    const roots=inspectionRoots(),rootIds=new Map(roots.map((r,i)=>[r.root,i]));
    const identity=snapshotIdentity(),detected=new Set(scannerControls()),fields=[],grouped=new Set();let truncated=Boolean(roots.truncated);
    for(const element of inputInventory(roots)) {
      if(diagnosticExcluded(element) || ['submit','reset','image','button'].includes(element.type) &&
         !element.matches('[aria-haspopup="listbox"],[role="combobox"]') &&
         !(supportedControl(element) && ['checkbox','radio'].includes(choiceKind(element))) || grouped.has(element))continue;
      if(element.parentElement?.closest('[contenteditable]:not([contenteditable="false"])') || element.closest('[role="combobox"]') && !element.matches('[role="combobox"]'))continue;
      const kind=element.tagName==='SELECT'?'select':element.matches('[role="combobox"],[aria-haspopup="listbox"],button[id$="--degree"]') || isPrompt(element)?'combobox':element.tagName==='TEXTAREA'||element.isContentEditable?'textarea':choiceKind(element) || 'text';
      const supported=supportedControl(element);
      const container=['radio','checkbox'].includes(kind)?choiceContainer(element):null;
      let members=['radio','checkbox'].includes(kind)?inputInventory(roots).filter(other=>choiceKind(other)===kind && sameChoiceScope(element,other) && (kind==='radio' && element.name?other.name===element.name:choiceContainer(other)===container && (container || other===element))):[element];
      if(!members.length)members=[element];
      members.forEach(member=>grouped.add(member));
      const fieldLabel=kind==='file'?fileLabel(element):['radio','checkbox'].includes(kind)?recoveredChoiceLabel(element,container,label(element)):label(element);
      const shape=node=>({...structureOf(node),root_id:rootIds.get(node.getRootNode())});
      const ancestors=[];for(let parent=native.parent(element);parent && !parent.matches('body,html') && ancestors.length<4;parent=native.parent(parent))ancestors.push(shape(parent));
      const exposed=ancestors.some(parent=>parent.visible && parent.opacity>0 && parent.visibility==='visible');
      // Include hidden native fallbacks only when related to an exposed field.
      if(!interactiveVisible(element) && !supported && !exposed)continue;
      if(!interactiveVisible(element) && !['checkbox','radio','file'].includes(kind))continue;
      const optionNodes=kind==='select'?Array.from(element.options).filter(o=>!o.disabled && o.value!==''):kind==='combobox'?comboOptions(element):[];
      const observed=repair?repairSession?.menus.get(element):null;
      const options=['radio','checkbox'].includes(kind)?members.map(label):observed?.options || optionNodes.map(questionText);
      const menuContainers=kind==='combobox'?[...new Set([...(element.getAttribute('aria-controls') || element.getAttribute('aria-owns') || '').split(/\s+/).map(id=>native.byId(element,id)),...comboContainer(element).querySelectorAll('[role="listbox"],.react-select__menu')].filter(Boolean))].slice(0,2):[];
      const field={label:fieldLabel.slice(0,300),kind:supported?kind:'text',required:members.some(e=>e.required || e.getAttribute('aria-required')==='true') || /\*/.test(fieldLabel),
        multiple:Boolean(kind==='checkbox' && members.length>1 || element.multiple || element.getAttribute('aria-multiselectable')==='true'),
        detected:supported && detected.has(element) && !(['radio','checkbox'].includes(kind) && !native.closest(element,'form,fieldset,[data-automation-id^="formField"],[role="group"],[role="radiogroup"]')),
        answered:element.matches('[role="checkbox"],[role="radio"],[role="switch"]') && !element.matches('input')?['true','mixed'].includes(element.getAttribute('aria-checked')):
          supported?Boolean(occupied({element,members,field:{kind}})):element.matches('[role="textbox"]')?Boolean(clean(element.textContent)):false,
        options:options.slice(0,20).map(s=>s.slice(0,200)),root_id:rootIds.get(element.getRootNode()),control:shape(element),ancestors,
        menu:{observed:options.length>0,count:Math.min(options.length,10000),roles:observed?.roles || [...new Set(optionNodes.map(o=>o.getAttribute('role') || 'none'))].slice(0,10),containers:menuContainers.map(structureOf),option_shapes:observed?.shapes || optionNodes.slice(0,3).map(structureOf)},
        reasons:[]};
      const outcome=repair?repairSession?.outcomes.get(element):null;
      if(outcome==='verification_failed')field.answered=false;
      // A known resume uploader owns this hidden native input. Keep its actual
      // shape, and assess the exposed UI through the same guard used for repair.
      if(kind==='file' && analyzeResume(element) && !interactiveVisible(element))field.file_proxy=shape(fileContainer(element));
      if(outcome)field.repair_status=outcome;
      if(repairSession?.reasons.has(element))field.reason_code=repairSession.reasons.get(element);
      if(!supported)field.reasons.push('unsupported_widget');
      if(element.readOnly || element.getAttribute('aria-readonly')==='true')field.reasons.push(selectOnlyControl(element) || ['radio','checkbox'].includes(kind) && interactiveVisible(element)?'readonly_proxy':'readonly');
      if(element.disabled || element.matches(':disabled') || element.closest('[aria-disabled="true"],[disabled]'))field.reasons.push('disabled');
      if(ancestors.some(p=>!p.visible || p.opacity===0 || p['aria-hidden']==='true') ||
         (field.file_proxy?native.closest(native.parent(element),'[hidden],[inert],[aria-hidden="true"]'):element.closest('[hidden],[inert],[aria-hidden="true"]')))field.reasons.push('hidden_parent');
      if(!field.control.visible)field.reasons.push('hidden_control');
      if(Number(getComputedStyle(element).opacity)===0)field.reasons.push('transparent_proxy');
      if(!field.detected)field.reasons.push('omitted_by_scanner');
      if(kind==='combobox' && !options.length)field.reasons.push('menu_not_exposed');
      if(fields.length>=50){truncated=true;break;}fields.push(field);
    }
    if(!passive)analysisIdentity=identity;
    return {identity,edit_revision:repairRevision,inspection:rootInspection(roots),diagnostic:{reason:'page_analysis',fields,truncated,busy:Boolean(extensionFilling || skillsState.busy),responsive:document.readyState!=='loading',inspection_ms:Math.min(30000,Math.round(performance.now()-started))}};
  }
  let analysisIdentity,analysisTimer;
  new MutationObserver(()=>{if(!analysisIdentity)return;clearTimeout(analysisTimer);analysisTimer=setTimeout(()=>{
    if(!repairExecuting && analysisIdentity!==snapshotIdentity()){analysisIdentity=undefined;chrome.runtime.sendMessage({type:'analysis-changed'}).catch(()=>{});}
  },150);}).observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['hidden','style','class','aria-label','aria-hidden','disabled','id']});
  let runtimeTimer,runtimeLast='',recognizedApplication=false;
  function initializingApplication(roots=native.discover()) {
    // The captured one-click shell is initialized but can have no inputs yet.
    return location.hostname==='jobs.smartrecruiters.com' &&
      /^\/oneclick-ui\/company\/[^/]+\/publication\/[a-f\d-]+\/?$/i.test(location.pathname) &&
      roots.some(r=>r.host==='spl-wrapper') && roots.some(r=>r.host==='spl-context-provider');
  }
  function inputReadiness(){return native.read(()=>{
    if(globalThis.autoApplyCredentials?.active() || globalThis.autoApplyCredentials?.incomplete())
      return {private:true,revision:repairRevision,url:location.href};
    const roots=native.discover(),inspection=rootInspection(roots),navigation=navigationState();
    const controls=inputInventory(roots).filter(el=>interactiveVisible(el) || el.type==='file' && exposedFile(el)).length;
    return {controls,navigation,revision:repairRevision,url:location.href,status:inspection.status,message:inspection.message,
      initializing:!controls && navigation.kind==='none' && inspection.status!=='access_blocked' &&
        (inspection.status==='loading' || initializingApplication(roots))};
  });}
  function runtimeState(...args){return native.read(()=>runtimeStateRead(...args));}
  function runtimeStateRead(){
    if(globalThis.autoApplyCredentials?.active())return {account:true,navigation:{kind:'none'}};
    const controls=inputInventory().filter(el=>interactiveVisible(el) || el.type==='file' && exposedFile(el));
    const navigation=navigationState();
    const initializing=initializingApplication();
    let application=initializing || navigation.kind==='submit' || controls.some(el=>applicationWritingScope(writingScope(el)));
    if(!application && recognizedApplication)application=controls.some(el=>{
      const root=writingScope(el);return root && Array.from(root.querySelectorAll('h1,h2,h3,legend')).some(h=>visible(h) && /job application|application (?:form|questions)|your application/i.test(text(h)));
    });
    if(application)recognizedApplication=true;
    const shape=JSON.stringify([location.href,workdayState()?.step,initializing,controls.map(el=>{
      if(!repairNodes.has(el))repairNodes.set(el,++repairNodeId);
      return [repairNodes.get(el),label(el).slice(0,2000),el.type,el.disabled,el.readOnly,el.required,el.getAttribute('aria-required'),
        el.matches('select')?Array.from(el.options,o=>[o.value,o.text,o.disabled]):el.matches('[role="combobox"]') && !occupied({element:el,members:[el],field:{kind:'combobox'}})?comboOptions(el).slice(0,100).map(text):el.getAttribute('aria-controls')];
    })]);
    return {application,shape,revision:repairRevision,navigation};
  }
  function signalRuntime(){
    clearTimeout(runtimeTimer);runtimeTimer=setTimeout(()=>{
      if(extensionFilling || repairExecuting || skillsState.busy){signalRuntime();return;}
      const state=runtimeState(),signature=JSON.stringify([state.account,state.shape,state.navigation]);
      if(signature===runtimeLast)return;runtimeLast=signature;
      chrome.runtime.sendMessage({type:'application-state-changed'}).catch(()=>{});
    },200);
  }
  new MutationObserver(changes=>{
    if(changes.every(change=>native.closest(change.target,'#autoapply-sidebar,.autoapply-ask-ai')))return;
    signalRuntime();
  }).observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,
    attributeFilter:['hidden','style','class','id','name','disabled','readonly','required','aria-hidden','aria-label','aria-labelledby','aria-describedby','aria-required','aria-controls','data-automation-id','role','type']});
  for(const event of ['popstate','hashchange'])addEventListener(event,signalRuntime);
  setTimeout(signalRuntime,0);
  function inspectionRoots() {return native.discover();}
  function actionTarget(raw) {
    try {
      const url=new URL(raw,location.href);
      if(!/^https?:$/.test(url.protocol))return {protocol:url.protocol};
      // Queries, fragments, credentials and arbitrary paths may contain secrets.
      const publicPath=url.hostname==='jobs.smartrecruiters.com' && /^\/(?:[^/]+\/\d+|oneclick-ui\/company\/[^/]+\/publication\/[a-f\d-]+)\/?$/i.test(url.pathname) ||
        url.hostname==='jobs.keysight.com' && /^\/external\/jobs\/\d+\/?$/.test(url.pathname);
      return {origin:url.origin,path:publicPath?url.pathname:'[redacted]'};
    } catch {return {invalid:true};}
  }
  const actionCaption=element=>{
    const caption=clean(element.getAttribute('aria-label') || questionText(element));
    return /^(?:apply(?: now| for this job| manually| with linkedin)?|i['\u2019]m interested|continue application|submit(?: application)?|next|sign in|log in)$/i.test(caption)?caption.slice(0,80):undefined;
  };
  function rootInspection(roots) {
    const rootInfo=roots.map(({root,host,parent_id,elements},id)=>({id,host,parent_id,kind:id?'open_shadow':'document',
      exposed_controls:elements.filter(e=>e.matches(controlSelector)).filter(e=>!excludedControl(e) && !diagnosticExcluded(e) &&
        (visible(e) || e.type==='file' && analyzeResume(e))).length}));
    const frames=roots.flatMap(({elements})=>elements.filter(e=>e.matches('iframe')))
      .filter(e=>!diagnosticExcluded(e)).slice(0,20).map(e=>({target:actionTarget(e.getAttribute('src') || 'about:blank'),visible:Boolean(e.getClientRects().length),
        accessible:Boolean(e.contentDocument),ready_state:e.contentDocument?.readyState || 'unavailable'}));
    const light=rootInfo[0].exposed_controls,shadow=rootInfo.slice(1).some(r=>r.exposed_controls);
    const verification=frames.some(f=>/captcha-delivery\.com$/.test(f.target.origin || '')) || /403 forbidden|access denied|verify you are human/i.test(document.title);
    const loading=document.readyState!=='complete' || native.query('[aria-busy="true"],[role="progressbar"]',roots).some(visible) ||
      !light && !shadow && initializingApplication(roots);
    const status=light || shadow?'controls_exposed':verification?'access_blocked':loading?'loading':frames.some(f=>f.visible)?'embedded_content_unavailable':'no_exposed_controls';
    const messages={access_blocked:'Public page access is blocked or requires verification. Open the page manually, then Analyze again.',
      loading:'The page is still loading. Wait for the application inputs, then Analyze again.',
      unsupported_shadow_controls:'Inputs are exposed inside open shadow roots but are not supported by this scanner. Root diagnostics were captured.',
      embedded_content_unavailable:'No inputs are exposed in this document; embedded content may be unavailable. Open the application frame and Analyze again.',
      no_exposed_controls:'No application inputs are exposed yet. Check the entry action and page loading, then Analyze again.'};
    return {status,message:messages[status],ready_state:document.readyState,roots:rootInfo,closed_shadow_roots:'not_observable',frames,truncated:Boolean(roots.truncated)};
  }
  function pageStructure(){return native.read(pageStructureRead);}
  function pageStructureRead() {
    if(globalThis.autoApplyCredentials?.active() || globalThis.autoApplyCredentials?.incomplete())return {host:location.hostname,nodes:[],truncated:Boolean(globalThis.autoApplyCredentials?.incomplete()),inspection:{truncated:Boolean(globalThis.autoApplyCredentials?.incomplete()),message:globalThis.autoApplyCredentials?.active()?"Account fields are private.":"Application scan incomplete; fields withheld for privacy."}};
    const nodes=[], encoder=new TextEncoder(); let size=0, truncated=false;
    const roots=inspectionRoots(),inspection=rootInspection(roots);
    const selector='form,fieldset,label,legend,h1,h2,h3,h4,h5,h6,input,textarea,select,option,button,a[href],iframe,[role],[data-automation-id],[contenteditable="true"]';
    for (const [rootId,{elements}] of roots.entries()) for (const element of elements.filter(e=>e.matches(selector))) {
      if (diagnosticExcluded(element)) continue;
      const row={tag:element.tagName.toLowerCase(),root_id:rootId, visible:Boolean(element.getClientRects().length && getComputedStyle(element).visibility!=='hidden')};
      if(element.matches('button,a[href],[role="button"],[role="link"]')) {
        const caption=actionCaption(element);
        if(caption){row.caption=caption;row.form_contained=Boolean(element.form || element.closest('form,[role="form"]'));if(element.hasAttribute('href'))row.target=actionTarget(element.getAttribute('href'));}
      }
      for (const attr of ['role','type','id','name','class','data-automation-id','aria-label','aria-checked','aria-pressed']) {
        const value=element.getAttribute(attr); if(value) row[attr]=value.slice(0,2000);
      }
      const labelledby=referencedLabel(element,'aria-labelledby'); if(labelledby)row.labelledby=labelledby.slice(0,2000);
      if (element.matches('input,textarea,select,[role="combobox"]')) row.label=label(element).slice(0,2000);
      if (element.matches('label,legend,option,[role="option"],[data-automation-id="formLabel"]')) {
        const copy=element.cloneNode(true);
        copy.querySelectorAll('input,textarea,select,[contenteditable],script,style,[data-automation-id="selectedItem"],[class*="single-value"],[class*="singleValue"]').forEach(node=>node.remove());
        if (!element.isContentEditable) row.text=text(copy).slice(0,2000);
      }
      if(element.matches('button') && element.closest('.ashby-application-form-input-yesno') && /^(yes|no)$/i.test(text(element)))row.text=text(element);
      const bytes=encoder.encode(JSON.stringify(row)).length;
      if (nodes.length >= 10000 || size+bytes>950000) {truncated=true;break;} size+=bytes;nodes.push(row);
    }
    return {host:location.hostname,nodes,truncated:truncated || inspection.truncated,inspection};
  }
  const chatDocument=crypto.randomUUID(),chatIdentities=new WeakMap();
  let chatEpoch=0;
  let chatFocus,chatExplicit;
  const writableChat=el=>el && !robotOnly(el) && !globalThis.autoApplyCredentials?.active() && !globalThis.autoApplyCredentials?.sensitive(el) && el && el.isConnected && interactiveVisible(el) &&
    !el.readOnly && !el.matches(':disabled,[readonly],[aria-readonly="true"]') &&
    (el.matches('textarea,input[type="text"],input:not([type]),input[type="email"],input[type="tel"],input[type="url"]') || el.isContentEditable) && !el.closest('#autoapply-sidebar') && !/captcha/i.test([el.id,el.name,label(el)].join(' '));
  document.addEventListener('focusin',event=>{if(!extensionFilling && event.isTrusted && writableChat(event.target))chatFocus=event.target;},true);
  document.addEventListener('pointerdown',event=>{if(event.isTrusted && writableChat(event.target)){chatExplicit=null;chatFocus=event.target;}},true);
  const coverFile=el=>el?.matches('input[type="file"]') && el.isConnected && !el.disabled && !el.multiple &&
    !globalThis.autoApplyCredentials?.sensitive(el) && exposedFile(el) && /cover.*letter/i.test(fileLabel(el)) &&
    !/resume|\bcv\b/i.test(fileLabel(el)) && (!el.accept || el.accept.split(',').some(v=>/^(?:\.pdf|application\/(?:pdf|\*))$/i.test(v.trim()))) &&
    Array.from(el.files||[]).every(f=>f.size<=6000000);
  const chatValue=el=>el.type==='file'?JSON.stringify({accept:el.accept,attached:attachedFile(el),files:Array.from(el.files||[],f=>[f.name,f.type,f.size,f.lastModified])}):String(el.isContentEditable?el.textContent:el.value);
  async function chatState(el){
    let value=chatValue(el);
    if(el.type==='file')for(const file of el.files||[])value+=':'+await chatFingerprint(new Uint8Array(await file.arrayBuffer()));
    return value;
  }
  async function chatFingerprint(value){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',value instanceof Uint8Array?value:new TextEncoder().encode(value))),b=>b.toString(16).padStart(2,'0')).join('');}
  async function chatContext({passive=false,request=''}={}){
    if(globalThis.autoApplyCredentials?.active() || globalThis.autoApplyCredentials?.incomplete())return {url:location.origin+location.pathname,page:pageInfo(),document:chatDocument,fields:[],current_key:null};
    if(!passive && !extensionFilling)await scan();
    const fields=Array.from(records.values()).filter(r=>writableChat(r.element)||coverFile(r.element)).slice(0,80).map(r=>{
      if(!chatIdentities.has(r.element))chatIdentities.set(r.element,crypto.randomUUID());
      return {key:r.field.key,label:r.field.label.slice(0,500),kind:r.field.kind,identity:chatIdentities.get(r.element),max_length:r.field.max_length,...(r.field.kind==='file'?{purpose:'cover_letter',accept:r.element.accept}:{})};
    });
    let named=fields.filter(f=>f.label.trim() && request.toLowerCase().includes(f.label.trim().toLowerCase()));
    if(!named.length && /cover letter/i.test(request))named=fields.filter(f=>/cover/i.test(f.label) && /letter/i.test(f.label));
    const selected=named.length===1?named[0]:fields.find(f=>records.get(f.key)?.element===(writableChat(chatExplicit)?chatExplicit:chatFocus));
    const current_answer=selected?{key:selected.key,text:records.get(selected.key).field.kind==='file'?'':chatValue(records.get(selected.key).element).slice(0,12000),fingerprint:await chatFingerprint(await chatState(records.get(selected.key).element))}:undefined;
    return {url:location.href,page:pageInfo(),document:chatDocument,epoch:chatEpoch,page_identity:snapshotIdentity(),fields,current_key:selected?.key || null,current_answer};
  }
  async function chatReplace(context,action){
    const field=context.fields?.find(f=>f.key===action.key),record=records.get(action.key);
    if(extensionFilling || skillsState.busy)throw Error('Application is busy. Wait for autofill or writing to finish, then retry.');
    if(context.url!==location.href || context.document!==chatDocument || context.epoch!==chatEpoch || context.page_identity!==snapshotIdentity() || !field || !record ||
       !(writableChat(record.element)||coverFile(record.element)) || chatIdentities.get(record.element)!==field.identity ||
       (record.field.kind==='file'?fileLabel(record.element):label(record.element))!==field.label || !['text','textarea','email','tel','url','file'].includes(record.field.kind) ||
       (record.field.kind==='file'?action.type!=='replace_cover_letter' || field.purpose!=='cover_letter' || field.accept!==record.element.accept:action.type!=='replace_text') || typeof action.text!=='string' || !action.text.trim() || action.text.length>12000 ||
       field.max_length!==null && action.text.length>field.max_length)throw Error('Field changed or is unavailable. Select the field and ask again.');
    const before=await chatState(record.element);
    if(context.current_answer?.key!==action.key || context.current_answer.fingerprint!==await chatFingerprint(before) ||
       await chatState(record.element)!==before || extensionFilling || !(writableChat(record.element)||coverFile(record.element)) || context.url!==location.href ||
       context.document!==chatDocument || context.epoch!==chatEpoch || context.page_identity!==snapshotIdentity() || records.get(action.key)!==record || (record.field.kind==='file'?fileLabel(record.element):label(record.element))!==field.label ||
       chatIdentities.get(record.element)!==field.identity)throw Error('Field changed while waiting. Select the field and retry; your edit is preserved.');
    const artifact=record.field.kind==='file';
    if(artifact && (!action.file || Object.keys(action.file).sort().join(',')!=='base64,filename,mime' ||
      action.file.filename!=='Cover Letter.pdf' || action.file.mime!=='application/pdf' || typeof action.file.base64!=='string' ||
      action.file.base64.length>8000000 || !/^JVBERi0[A-Za-z0-9+/=]+$/.test(action.file.base64)))throw Error('Invalid cover letter artifact');
    const summary=await fill([{key:action.key,status:artifact?'FILE':'RESOLVED',value:artifact?'cover_letter':action.text,
      force:true,replace_cover_letter:artifact,verify_settled:true,source:'AI'}],artifact?{cover_letter:action.file}:{});
    if(artifact){
      await new Promise(resolve=>setTimeout(resolve,250));
      const current=record.element.files?.[0];
      if(!record.element.isConnected || !current || current.name!==action.file.filename || current.type!=='application/pdf' ||
        await chatFingerprint(new Uint8Array(await current.arrayBuffer()))!==await chatFingerprint(Uint8Array.from(atob(action.file.base64),c=>c.charCodeAt(0))))throw Error('Cover letter attachment did not remain selected. Your draft is preserved.');
    }
    if(!summary.completed.some(f=>f.key===action.key && String(f.value)===(artifact?action.file.filename:action.text)))throw Error('Replacement did not remain in the field. Review the page; your draft is preserved.');
    return {ok:true};
  }
  globalThis.autoApplyContent = {inputReadiness,runtimeState,repairCheck:token=>{assertRepair(token);return true;},cancelRepair:()=>{repairRevision++;repairCancellation++;},repairStart,repairOptions,repairFill,repairFinish,repairContext:()=>({identity:snapshotIdentity(),revision:repairRevision}),inspectPage,analysisContext:()=>snapshotIdentity(),chatContext,chatReplace,beginTrace,endTrace,trace,skillsPage:pageInfo, skillsAvailable:()=>skillInputs().length > 0, autofillSkills, skillsStatus:()=>skillsState, cancelSkills:()=>{skillsCancelled=true;}, pageStructure, unresolvedStructure, scan, collectOptions, fill, prepareSections, enterApplication, sidebar, sidebarLayout, nextPage, navigationState, manualSubmit, confirmationEvidence, submissionBaseline:()=>submissionIntent?.before, validationErrors};
  if(globalThis.autoApplyCredentials){
    for(const key of ['inputReadiness','runtimeState','scan','inspectPage','pageStructure','unresolvedStructure','chatContext','fill','prepareSections','skillsPage','confirmationEvidence','submissionBaseline','validationErrors','navigationState','collectOptions','repairStart','repairOptions','repairFill','repairFinish','repairContext']){
      const original=globalThis.autoApplyContent[key];
      globalThis.autoApplyContent[key]=(...args)=>{const value=original(...args);return value?.then?value.then(globalThis.autoApplyCredentials.redact):globalThis.autoApplyCredentials.redact(value);};
    }
  }
  globalThis.autoApplyContent.syncWritingButtons=scheduleWritingButtons;
  Object.assign(globalThis.autoApplyContent,{fieldState,revealField,flushEdits,protectField,restoreField,
    cancelCoverUpload:token=>{if(cancelledCoverUploads.size>40)cancelledCoverUploads.clear();cancelledCoverUploads.add(token);}});
  globalThis[singleton]=globalThis.autoApplyContent;
  if(window===window.top) {
    chrome.runtime.sendMessage({type:'sidebar-ready'}).catch(()=>{});
    let lastPublicContext='';
    const capturePosting=()=>{
      const page=pageInfo();if(!page.context_source)return;
      const signature=JSON.stringify(page);if(signature===lastPublicContext)return;
      lastPublicContext=signature;
      chrome.runtime.sendMessage({type:'public-job-context',page}).catch(()=>{});
    };
    let postingTimer;
    new MutationObserver(()=>{clearTimeout(postingTimer);postingTimer=setTimeout(capturePosting,250);})
      .observe(document.body,{subtree:true,childList:true,characterData:true});
    capturePosting();
  }
})();
