(function initialize() {
  const singleton=Symbol.for('autoapply.content');
  if(globalThis[singleton]){globalThis.autoApplyContent=globalThis[singleton];return;}
  if (globalThis.autoApplyContent) return;
  if(!document.body){
    const pending=Symbol.for('autoapply.pending');
    if(!globalThis[pending]){globalThis[pending]=true;document.addEventListener('DOMContentLoaded',()=>{globalThis[pending]=false;initialize();},{once:true});}
    return;
  }
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
    if (!traces.length) return;
    const field = element ? {
      label: label(element), element_id: element.id,
      automation_id: element.getAttribute('data-automation-id') ||
        element.closest('[data-automation-id^="formField"]')?.getAttribute('data-automation-id')
    } : {};
    const entry = JSON.parse(JSON.stringify({timestamp: new Date().toISOString(), step, action, ...field, ...details},
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
  let sidebarHost;
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
    sidebarHost.style.setProperty('display',state==='closed'?'none':'block','important');
    sidebarHost.style.setProperty('width',state==='minimized'?'0':'min(400px,100vw)','important');
    sidebarHost.shadowRoot.querySelector('.panel').hidden=state!=='open';
    sidebarHost.shadowRoot.querySelector('.collapsed').hidden=state!=='minimized';
    return state;
  }
  function valueOf({element,members,field,savedDate}) {
    if (savedDate) return savedDate;
    if (field.kind==='file') return Array.from(element.files || [],file=>file.name).join(', ') || attachedFile(element);
    if (['radio','checkbox'].includes(field.kind)) return field.kind==='checkbox'&&members.length===1 ? element.checked : members.filter(el=>el.checked).map(label)[field.kind==='radio'?'shift':'slice']();
    if (field.kind==='select') return element.multiple ? Array.from(element.selectedOptions,o=>clean(o.text)) : clean(element.selectedOptions[0]?.text);
    if (field.kind==='combobox') return comboSelection(element) || clean(comboInput(element)?.value);
    return element.isContentEditable ? element.textContent : element.value;
  }
  const editTimers=new Map(), observedValues=new Map();
  function learn(event) {
    if(extensionFilling || !event.isTrusted) return;
    const record=Array.from(records.values()).find(r=>r.members.includes(event.target)||r.element.contains(event.target));
    if(!record || ['file','password','hidden'].includes(record.field.kind)||!interactiveVisible(record.element)) return;
    const value=valueOf(record), encoded=JSON.stringify(value);
    if(observedValues.get(record.field.key)===encoded) return;
    clearTimeout(editTimers.get(record.field.key));
    editTimers.set(record.field.key,setTimeout(()=>{
      observedValues.set(record.field.key,encoded);
      chrome.runtime.sendMessage({type:'user-answer',key:record.field.key,value}).catch(()=>{});
    },400));
  }
  document.addEventListener('change',learn,true); document.addEventListener('blur',learn,true);
  document.addEventListener('click',event=>{
    const button=event.target.closest('button,input[type="submit"],[role="button"]');
    if(event.isTrusted && button && visible(button) && (/^(submit(?: application)?|apply(?: now| application)?)$/i.test(clean(button.textContent||button.value)))) {
      armSubmission();
      chrome.runtime.sendMessage({type:'submit-intent',url:location.href}).catch(()=>{});
    }
  },true);
  let confirmationTimer, submissionIntent;
  function confirmationEvidence() {
    const pattern=/thank you for applying|application (?:has been |was )?submitted|we have received your application|application received/i;
    return Array.from(document.querySelectorAll('h1,h2,h3,#confirmation,[role="dialog"],[role="status"],[data-automation-id="congratulations"],main p'))
      .filter(el=>visible(el)&&!el.closest("#autoapply-sidebar"))
      .map(el=>clean(el.innerText)).find(value=>pattern.test(value)) || "";
  }
  function armSubmission() {submissionIntent={url:location.href,at:Date.now(),before:confirmationEvidence()};}

  function detectConfirmation() {
    const url=location.href;
    const strong=/greenhouse\.io|lever\.co/.test(location.hostname)&&/\/(confirmation|thanks)(?:[/?#]|$)/i.test(location.pathname)||/ashbyhq\.com/.test(location.hostname)&&/\/application\/.+\/submitted(?:[/?#]|$)/i.test(location.pathname);
    const evidence=confirmationEvidence();
    if(!strong && (!submissionIntent || Date.now()-submissionIntent.at>120000 || evidence===submissionIntent.before))return;
    const invalid=Array.from(document.querySelectorAll('[role="alert"],[aria-invalid="true"]')).some(el=>visible(el)&&(/error|required|invalid|missing|correct/i.test(text(el))||el.getAttribute('aria-invalid')==='true'));
    if(!invalid&&(strong||evidence)) chrome.runtime.sendMessage({type:'submission-confirmation',url,evidence:evidence||'Confirmation URL',strong:Boolean(strong)}).catch(()=>{});
  }
  new MutationObserver(()=>{clearTimeout(confirmationTimer);confirmationTimer=setTimeout(detectConfirmation,300);}).observe(document.body,{childList:true,subtree:true,characterData:true});
  setTimeout(detectConfirmation,300);
  let records = new Map();
  const clean = value => String(value || "").replace(/\s+/g, " ").trim();
  const text = element => clean(element?.textContent);
  const normal = value => clean(value).toLowerCase();

  function visible(element) {
    if (element.disabled || element.readOnly || element.matches(":disabled") || element.getAttribute("aria-disabled") === "true" || element.getAttribute("aria-readonly") === "true" || element.closest("[hidden], [inert], [aria-hidden='true'], [aria-disabled='true'], [disabled]")) return false;
    const rect = element.getBoundingClientRect();
    if (!rect.width || !rect.height || rect.right + scrollX <= 0 || rect.bottom + scrollY <= 0) return false;
    for (let node = element; node instanceof Element; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
    }
    return true;
  }

  function interactiveVisible(element) {
    if (selectOnlyControl(element)) return true;
    if (visible(element)) return true;
    if (!element.matches('input[type="checkbox"],input[type="radio"]') ||
        !element.isConnected || element.disabled || element.readOnly || element.matches(':disabled') ||
        element.closest('[hidden],[inert],[aria-hidden="true"],[aria-disabled="true"]') ||
        element.getAttribute('aria-readonly')==='true') return false;
    const rect=element.getBoundingClientRect(), style=getComputedStyle(element);
    if(rect.width<=1 || rect.height<=1 || style.display==='none' || style.visibility==='hidden' ||
        !visible(element.parentElement)) return false;
    // Only the native input may be transparent. A visible associated visual is required.
    return [...Array.from(element.labels || []), element.closest('label'),
      element.parentElement.querySelector('[data-automation-id="checkbox"],[role="checkbox"]')]
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
  function choiceContainer(element) {
    if (['current','fluent'].includes(sectionField(element))) return element;
    const jazz=element.closest('.form-group');
    if(jazz?.querySelector('label[id^="resumator-questionnaire-"]'))return jazz;
    return element.closest('[data-automation-id$="-CheckboxGroup"],[role="radiogroup"],fieldset,[data-automation-id^="formField"],[role="group"]');
  }
  function questionContainer(container) {
    if(!container) return container;
    for(let node=container;node && !node.matches('form,body');node=node.parentElement) {
      if (Array.from(node.children).some(child=>child.matches('legend,label,[data-automation-id="formLabel"],[data-automation-id="richText"]') && usableLabel(questionText(child))) ||
          usableLabel(referencedLabel(node,'aria-labelledby')) || usableLabel(node.getAttribute('aria-label'))) return node;
    }
    return container;
  }
  function currentControl(element) {
    return element.isConnected ? element : element.id ? document.getElementById(element.id) || element :
      document.querySelector('[data-autoapply-key="'+CSS.escape(element.dataset.autoapplyKey || '')+'"]') || element;
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
    copy.querySelectorAll('input,textarea,select,[contenteditable],script,style,[data-automation-id="selectedItem"]').forEach(node=>node.remove());
    return text(copy);
  }
  function label(element) {
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
    let question=(element.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>questionText(document.getElementById(id)))
      .filter(value=>value && !options.has(normal(value))).join(' ');
    // Pinpoint puts the question and option in one hidden aria label.
    if(option && question.toLowerCase().endsWith(' '+option.toLowerCase()))question=question.slice(0,-option.length).trim();
    return question;
  }
  function groupLabel(container, fallback) {
    container=questionContainer(container);
    return [...Array.from(container?.querySelectorAll('legend,[data-automation-id="formLabel"],[data-automation-id="richText"],label') || []).filter(node=>!node.querySelector('input,select,textarea') && (node.tagName!=='LABEL' || !node.htmlFor || document.getElementById(node.htmlFor)?.type==='hidden')).map(questionText), container?.getAttribute('aria-label'),
      container ? referencedLabel(container, 'aria-labelledby') : '',
      text(container?.querySelector('h1,h2,h3,h4,[role="heading"]')), fallback].map(usableLabel).find(Boolean) || 'Unlabelled field';
  }

  function commonContainer(members) {
    let container = members[0].parentElement;
    while (container && !members.every(member => container.contains(member))) container = container.parentElement;
    return container;
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
    return element.parentElement;
  }
  const openedCombos=new WeakSet();
  function comboOptions(element) {
    const ids = clean(element.getAttribute("aria-controls") || element.getAttribute("aria-owns") || element.getAttribute("list")).split(" ");
    const lists = ids.map(id => document.getElementById(id)).filter(Boolean);
    if (!lists.length) {
      lists.push(comboContainer(element));
      if (!selectOnlyControl(element) && (openedCombos.has(element) || element.getAttribute("aria-expanded") === "true" || isPrompt(element) && comboInput(element)===document.activeElement)) lists.push(...document.querySelectorAll('[data-automation-id="activeListContainer"],[data-automation-id="responsiveMonikerPrompt"],[role="listbox"]:not([data-automation-id="selectedItemList"])'));
    }
    const selector = '[role="option"],option,[data-automation-id="menuItem"],[data-automation-id="promptOption"]' +
      (selectOnlyControl(element) ? ',.react-select__menu .react-select__option' : '');
    return [...new Set(lists.flatMap(list => Array.from(list.querySelectorAll(selector)).map(option=>option.closest('[data-automation-id="menuItem"]') || option)))]
      .filter(option => !option.closest('[data-automation-id="selectedItemList"]') && !option.disabled && option.getAttribute("aria-disabled") !== "true" &&
        (text(option) || option.getAttribute('data-automation-label')) && !/^(loading\b|no (?:options|results|items)\b)/i.test(text(option)) &&
        (option.tagName === "OPTION" || visible(option)));
  }
  async function settledComboOptions(element, timeout = 6000) {
    const end = Date.now() + timeout;
    let previous = "", stableSince = Date.now();
    do {
      const options = comboOptions(element);
      const signature = JSON.stringify(options.map(text));
      if (!options.length || signature !== previous) stableSince = Date.now();
      else if (Date.now() - stableSince >= 250){trace("dropdown","options seen",element,{texts:options.slice(0,30).map(text),total:options.length});return options;}
      previous = signature;
      await pause(50);
    } while (Date.now() < end);
    return comboOptions(element);
  }
  async function openCombo(element) {
    trace("dropdown","opened",element);
    openedCombos.add(element);
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
      element.click();
      if (await waitFor(() => comboOptions(element).length, 120) || element.getAttribute('aria-expanded') === 'true') return;
      const control = element.closest('[class*="-control"],[class*="__control"]') || comboContainer(element) || element;
      const toggle = control.querySelector('button[type="button"][aria-label*="Toggle"],button[type="button"][aria-label*="Open"],[class*="dropdown-indicator"],[class*="indicatorContainer"]');
      const target = toggle && visible(toggle) ? toggle : control;
      target.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, button: 0}));
      target.click();
    }
  }
  function closeCombo(element) { keypress(element, "Escape"); element.blur(); openedCombos.delete(element); }
  function comboInput(element) {
    return element.matches("input,textarea") ? element : element.querySelector("input,textarea") ||
      (element.isContentEditable ? element :
        comboContainer(element).querySelector('input[data-automation-id="searchBox"],input[role="searchbox"]'));
  }
  function searchCombo(element, value) {
    const input = comboInput(element);
    if (!input || input.readOnly || input.disabled || input.getAttribute('aria-readonly') === 'true') return false;
    input.focus();
    if (input instanceof HTMLInputElement) {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, value);
    } else if ("value" in input) nativeSet(input, "value", value);
    else input.textContent = value;
    // No Enter key or change event: searching must never submit the form.
    input.dispatchEvent(new InputEvent("input", {bubbles: true,
      inputType: value ? "insertText" : "deleteContentBackward", data: value || null}));
    trace("search","typed",element,{value});
    return true;
  }
  function comboSelection(element) {
    const container = comboContainer(element);
    const hidden=clean(container.querySelector('input[type="hidden"]')?.value);
    return text(container.querySelector('[class*="singleValue"],[class*="single-value"]')) ||
      (hidden && !/^(select|choose|search)(?:\s*one)?(?:\.{3}|…)?$/i.test(hidden) ? hidden : '') ||
      text(container.querySelector('[data-automation-id="selectedItem"],[data-automation-id="selectedItemLabel"]')) ||
      (element.matches('button:is([aria-haspopup="listbox"],[id$="--degree"])') && !/^(select|choose)(\s*one)?(?:\.{3}|…)?$/i.test(text(element)) ? text(element) : "");
  }
  function referencedLabel(element, attribute) {
    return (element.getAttribute(attribute) || "").split(/\s+/)
      .map(id => questionText(document.getElementById(id))).filter(Boolean).join(" ");
  }
  function usableFileLabel(value) {
    value = usableLabel(value);
    // Upload buttons describe an action, not the field's purpose.
    if (!/\b(resume|cv|cover)\b/i.test(value) && /^(attach|upload|drop|choose|browse|select|drag)\b/i.test(value)) return "";
    return value;
  }
  function uploadGroupLabel(container, element) {
    const accessible = usableFileLabel(container.getAttribute("aria-label")) ||
      usableFileLabel(referencedLabel(container, "aria-labelledby")) ||
      usableFileLabel(referencedLabel(container, "aria-describedby"));
    if (accessible) return accessible;
    if (container.querySelectorAll('input[type="file"]').length > 1) return "";
    const labels = Array.from(container.querySelectorAll('label,legend,[role="heading"],h1,h2,h3,h4,[class*="label"],[id^="upload-label-"]'))
      .filter(node => node.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING)
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
  function exposedFile(element) {
    if(visible(element) || resumeAffordance(element) || attachedFile(element))return true;
    const container=fileContainer(element);
    if(!visible(container) || !/\b(resume|cv|cover)\b/i.test(fileLabel(element)))return false;
    // Workday keeps this input hidden even in the active, visible resume section.
    if(element.getAttribute('data-automation-id')==='file-upload-input-ref')return true;
    return Array.from(container.querySelectorAll('button,[role="button"],label')).some(node=>visible(node) &&
      /^(attach|upload|browse|choose|select)\b/i.test(text(node))) || Array.from(element.labels||[]).some(visible);
  }
  function fileContainer(element) {
    let container = element.parentElement;
    while (container && !container.matches("form,body")) {
      if (uploadGroupLabel(container, element)) return container;
      container = container.parentElement;
    }
    return element.parentElement;
  }
  function fileLabel(element) {
    const direct = [Array.from(element.labels || []).map(text).filter(Boolean).join(" "),
      element.getAttribute("aria-label"), referencedLabel(element, "aria-labelledby"),
      referencedLabel(element, "aria-describedby")].map(usableFileLabel).find(Boolean);
    const container = fileContainer(element);
    const group = uploadGroupLabel(container, element);
    const identity = normal([element.id, element.name, element.accept, element.getAttribute("aria-label"),
      element.getAttribute("aria-labelledby"), element.getAttribute("aria-describedby"), direct, group].join(" ")).replace(/[_-]/g, " ");
    const purpose = /\bcover\b/.test(identity) ? "Cover Letter" : /\b(resume|cv)\b/.test(identity) ? "Resume/CV" : "";
    const fieldLabel = direct || group;
    return purpose && !/\b(resume|cv|cover)\b/i.test(fieldLabel || "") ? purpose : fieldLabel || purpose ||
      (element.getAttribute('data-automation-id') === 'file-upload-input-ref' ? 'Resume/CV' : 'File attachment');
  }
  function attachedFile(element) {
    const container = element.closest('[data-automation-id^="formField"],[data-automation-id="fileUpload"],[class*="upload"],[class*="Upload"]') || fileContainer(element);
    const shown = Array.from(container.querySelectorAll('*')).filter(visible).map(text);
    const filename = shown.map(value => value.match(/[^\s<>]+\.(?:pdf|docx?|rtf|txt|odt)\b/i)?.[0]).find(Boolean);
    return filename || (shown.some(value => /successfully uploaded/i.test(value)) ? 'Already attached' : '');
  }
  function filenameShown(element, filename) {
    const container = fileContainer(element);
    return [container, ...container.querySelectorAll("*")].some(node => visible(node) &&
      Array.from(node.childNodes).some(child => child.nodeType === Node.TEXT_NODE && clean(child.textContent).includes(filename)));
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
    let posting;
    function find(value) {
      if (!value || typeof value !== "object") return;
      if ([value["@type"]].flat().includes("JobPosting")) posting ||= value;
      for (const child of Object.values(value)) if (typeof child === "object") {
        for (const entry of Array.isArray(child) ? child : [child]) find(entry);
      }
    }
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      try { find(JSON.parse(script.textContent)); } catch { /* Malformed metadata is common. */ }
    }
    // Template contents stay inert, including images embedded in job metadata.
    const description = document.createElement("template");
    description.innerHTML = typeof posting?.description === "string" ? posting.description : "";
    return {
      url: location.href,
      title: clean(posting?.title) || document.title,
      company: clean(document.querySelector('meta[property="og:site_name"]')?.content) || clean(posting?.hiringOrganization?.name) || document.title.split(/\s[|–-]\s/).pop(),
      description: (text(description.content) || text(document.querySelector('[data-automation-id="jobPostingDescription"],.job-description,#job-description,[itemprop="description"]')) || text(document.querySelector("main,article"))).slice(0, 6000)
    };
  }

  function signInPage() {
    return Boolean(document.querySelector('[data-automation-id="signInContent"],[data-automation-id="createAccountLink"]') ||
      (document.querySelector('input[type="password"]') &&
        /sign[\s-]*in|log[\s-]*in|create\s+(?:an?\s+)?account/i.test(document.body.innerText)));
  }

  function manualEntry() {
    return Array.from(document.querySelectorAll('[data-automation-id="applyManually"]')).find(visible);
  }

  function enterApplication() {
    if (signInPage() || /review/i.test(workdayState()?.step || '')) return false;
    const manual = manualEntry();
    const entry = manual || Array.from(document.querySelectorAll('button,a[href],[role="button"]')).find(element =>
      visible(element) && /^(apply|apply for this job)$/i.test((element.getAttribute('aria-label') || element.textContent).trim()));
    if (!entry || entry.type === "submit" || entry.form || entry.closest("form")) return false;
    // Exact entry labels only: never Next, Save and Continue, or Submit.
    if (entry.tagName === 'A') {
      const url = new URL(entry.href);
      if (!/^https?:$/.test(url.protocol)) return false;
      location.assign(url.href);
    } else entry.click();
    return true;
  }

  async function scan() {
    const started = performance.now();
    const previousRecords = records;
    records = new Map();
    const fields = [];
    const controls = Array.from(document.querySelectorAll('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="combobox"],button:is([aria-haspopup="listbox"],[id$="--degree"])'))
      .filter(element => (element.type === "file" ? !element.disabled && !element.matches(":disabled") && exposedFile(element) : interactiveVisible(element)) && (!["hidden", "submit", "reset", "image", "password"].includes(element.type) && (element.type !== "button" || element.getAttribute("role") === "combobox" || element.matches('button:is([aria-haspopup="listbox"],[id$="--degree"])'))) &&
        !(element.closest('[role="combobox"]') !== element && element.closest('[role="combobox"]')) &&
        !(element.parentElement?.closest('[contenteditable]:not([contenteditable="false"])')) &&
        !(!element.closest("form") && (element.type === "search" || element.closest('[role="search"]') || /\bsearch\b/i.test(label(element)))));
    const grouped = new Set();
    const repeated = new Set();
    const keys = new Set();
    for (const element of controls) {
      if (["checkbox","radio"].includes(element.type) && !element.closest('form,fieldset,[data-automation-id^="formField"],[role="group"],[role="radiogroup"]')) continue;
      if (grouped.has(element) || /captcha|honeypot|honey.?pot|fax|website_confirm/i.test([element.name,element.id,label(element)].join(" "))) continue;
      let kind = element.tagName === "SELECT" ? "select" : element.getAttribute("role") === "combobox" || element.matches('button:is([aria-haspopup="listbox"],[id$="--degree"])') || element.getAttribute("data-automation-id") === "multiSelectInput" || isPrompt(element) ? "combobox" :
        element.tagName === "TEXTAREA" || element.isContentEditable ? "textarea" : element.type || "text";
      if (!["text", "textarea", "select", "radio", "checkbox", "combobox", "file", "date", "number", "email", "tel", "url"].includes(kind)) kind = "text";
      let members = [element];
      let fieldLabel = kind === "file" ? fileLabel(element) : label(element);
      const section = sectionHint(element);
      const datePart = element.getAttribute('data-automation-id');
      if (/^dateSection(Month|Year)(?:-input)?$/.test(datePart || '')) {
        const heading = dateHeading(element);
        fieldLabel = heading.replace(/\s*\*$/, '').replace(/^(from|to)$/i, '$1 date') + ' ' + (datePart.includes('Month') ? 'month' : 'year');
      }
      let container;
      if (["radio", "checkbox"].includes(kind)) {
        container = choiceContainer(element);
        members = controls.filter(other => other.type === kind && other.form === element.form &&
          (kind === "checkbox" ? choiceContainer(other)===container : element.name ? other.name===element.name : choiceContainer(other)===container));
        if (!members.length) members = [element];
        if (!container && members.length > 1) container = commonContainer(members);
        if (members.length > 1 || kind === "radio") fieldLabel = choiceQuestion(element) || groupLabel(container, fieldLabel);
        members.forEach(member => grouped.add(member));
      }
      if (!element.dataset.autoapplyKey || keys.has(element.dataset.autoapplyKey)) element.dataset.autoapplyKey = `aa-${++nextKey}`;
      const key = element.dataset.autoapplyKey;
      keys.add(key);
      let options = kind === "select" ? Array.from(element.options).filter(option => !option.disabled && option.value !== "").map(option => clean(option.text)) :
        ["radio", "checkbox"].includes(kind) ? members.map(label) : [];
      container=questionContainer(container);
      fields.push({key, label: fieldLabel, kind, required: members.some(member => member.required || member.getAttribute("aria-required") === "true") || container?.getAttribute("aria-required") === "true" || /\*|\brequired\b/i.test([element.getAttribute('aria-label'), text(container?.querySelector('legend,label.control-label,[data-automation-id="formLabel"]')), text(element.closest('[data-automation-id^="formField"]')?.querySelector('label,legend,[data-automation-id="formLabel"]')), Array.from(element.labels || []).map(text).join(' ')].join(' ')),
        options, multiple: kind==='checkbox' && members.length>1 && !options.every(o=>/^(yes|no)$/i.test(o)) || isPrompt(element) || element.getAttribute("data-automation-id") === "multiSelectInput" ||
          element.getAttribute("aria-multiselectable") === "true", ...(kind === "combobox" ? {options_partial: Boolean(!selectOnlyControl(element) && comboInput(element) && comboInput(element).getAttribute("aria-autocomplete") !== "none")} : {}),
        section, placeholder: element.placeholder || '', max_length: element.maxLength >= 0 ? element.maxLength : null});
      records.set(key, {element, members, field: fields[fields.length - 1]});
      fields[fields.length - 1].answered = occupied(records.get(key));
      if (kind === 'file' && fields[fields.length - 1].answered) fields[fields.length - 1].attached_file = valueOf(records.get(key));
      observedValues.set(key,JSON.stringify(valueOf(records.get(key))));
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
      if (record.field.kind !== 'combobox' || !selectOnlyControl(record.element) || occupied(record)) continue;
      try {
        await openCombo(record.element);
        record.field.options = (await settledComboOptions(record.element, 1000)).map(text);
      } finally { closeCombo(record.element); }
    }
    const retained = Array.from(records.values(), record => ({...record.field, answered:occupied(record)}));
    previousStep = workdayState();
    return {page: pageInfo(), fields: retained, page_identity: snapshotIdentity(), next_action: navigationState(), workday_step: previousStep?.step, skills_available: skillInputs().length > 0, sign_in: signInPage(), manual_entry: Boolean(manualEntry()), scan_ms: Math.round(performance.now() - started)};
  }

  async function collectOptions(keys) {
    const started = performance.now(), fields = [];
    for (const key of keys) {
      const record = records.get(key);
      if (!record || record.field.kind !== "combobox" || occupied(record)) continue;
      try {
        await openCombo(record.element);
        record.field.options = (await settledComboOptions(record.element, 3000)).map(text);
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
    if (["checkbox", "radio"].includes(field.kind)) return members.some(member => member.checked);
    if (field.kind === "file") return Boolean(element.files.length || attachedFile(element));
    if (field.kind === "combobox") return Boolean(comboSelection(element) || phoneCountrySelection(element) || displayedChoice(element) || !isPrompt(element) && clean(comboInput(element)?.value) && !/^(select|choose|search)(?:\s*one)?(?:\.{3}|…)?$/i.test(clean(comboInput(element)?.value)) || !comboInput(element) && !/^(select|choose|search)(?:\s*one)?(?:\.{3}|…)?$/i.test(text(element)) && text(element));
    if (field.kind === "select") return Array.from(element.selectedOptions).some(option =>
      !option.disabled && option.value !== "" && !/^(choose|select)(?:\s*one)?(?:\.{3}|…)?$/i.test(clean(option.text)));
    return Boolean(clean(element.isContentEditable || !("value" in element) ? element.textContent : element.value));
  }

  function nativeSet(element, property, value) {
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
    for (const type of ["input", "change", "blur"]) element.dispatchEvent(new Event(type, {bubbles: true}));
  }
  function best(options, value, getText = item => item) {
    if (value === 'highest') {
      const numeric = options.filter(item => /^\s*\d+/.test(getText(item)));
      if (numeric.length) return numeric.reduce((a,b) => parseInt(getText(b),10)>parseInt(getText(a),10)?b:a);
      const rank = item => {const name=normal(getText(item));return /native/.test(name)?5:/fluent|expert/.test(name)?4:/advanced/.test(name)?3:/intermediate/.test(name)?2:/basic|beginner/.test(name)?1:0;};
      return options.filter(item=>rank(item)>0).sort((a,b)=>rank(b)-rank(a))[0];
    }
    const degree = name => /^(bachelor'?s?( degree)?|bachelor of science|b\.?s\.?)$/.test(normal(name));
    if (degree(value)) {
      const choices=options.filter(item=>degree(getText(item)));
      const preferred=choices.find(item=>normal(getText(item))==="bachelor's degree");
      if (preferred || choices.length===1) return preferred || choices[0];
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
    const months = monthValues(element, value)?.map(normal);
    if (months) return options.find(option => months.includes(normal(getText(option))) ||
      element.tagName === 'SELECT' && months.includes(normal(option.value)));
    return best(options, value, getText) || (normal(value) === 'mobile' && /^phone (?:device )?type$/i.test(label(element)) ?
      options.find(option => /^(cell|mobile phone)$/i.test(clean(getText(option)))) : undefined);
  }
  function mark(record, color) {
    for (const element of record.members) {
      const target = record.field.kind === "file" && !visible(element) ? fileContainer(element) : element;
      target.style.outline = `2px solid ${color}`;
      target.style.outlineOffset = "2px";
    }
  }

  async function fillCombo(element, value) {
    if (isPrompt(element) && isSkillsField(element)) return (await fillSkills(element, [value])).skipped === 0;
    const initial=element;
    const reacquire=()=>element=currentControl(initial);
    let committed = false;
    try {
      reacquire();
      await openCombo(element);
      if(isPrompt(element)) {searchCombo(element,value);promptEnter(comboInput(element));}
      const words = clean(value).split(" ");
      const prefix = words.slice(0, words.length > 3 ? 3 : 2).join(" ");
      const searches = [...new Set(monthValues(element, value) || [clean(value), prefix])];
      const options = await settledComboOptions(element, isPrompt(element) ? 1000 : 350);
      let option = isPrompt(element) ? await promptOption(element,value) : fieldChoice(element, options, value);
      for (const search of option ? [] : searches) {
        if (!searchCombo(element, search)) break;
        // Keep waiting for a match even when an unrelated first page stays rendered.
        option = await waitFor(() => isPrompt(element) ? comboOptions(element).find(o=>skillNormal(optionName(o))===skillNormal(value)) : fieldChoice(element, comboOptions(element), value), 2500);
        if (option) break;
      }
      if (!option){trace("dropdown","skipped",element,{reason:"no matching option",value});return false;}
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
          const clicked=pointerClick(target,()=>!isPrompt(element) || comboOptions(element).find(row=>skillNormal(optionName(row))===skillNormal(value))===option);
          trace('dropdown',clicked?'clicked':'activation interrupted',element,{target:selected,element_id:option.id});
          if (clicked) break; // A pending commit must never be activated twice.
          await settledComboOptions(element,1000);
        }
        let stableSince=0;
        committed = Boolean(await waitFor(() => {
          reacquire();
          const selection = normal(comboSelection(element));
          const matchesSelected = Boolean(selection && normal(selected).includes(selection));
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
            const hidden = container.querySelector('input[type="hidden"]');
            if (!closed || normal(singleValue) !== normal(selected) || hidden &&
                (!clean(hidden.value) || submittedValue != null && hidden.value !== submittedValue)) {stableSince=0;return false;}
            if (!stableSince) stableSince=Date.now();
            return Date.now()-stableSince>=300;
          }
          return matchesSelected || option.getAttribute("aria-selected") === "true" ||
            closed && Boolean(singleValue) && !container.querySelector('[class*="placeholder"]') ||
            closed && normal(element.value) === normal(selected);
        }));
      }
      trace("dropdown","verified",element,{verified:committed,value:selected,observed:committed?selected:"uncommitted"});
      return committed;
    } finally {
      if (!committed) {
        searchCombo(element, "");
      }
      closeCombo(element);
    }
  }

  async function phoneValue(element, value, field) {
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
  async function fill(answers, files = {}) {return traced("fill",()=>fillFields(answers,files));}
  async function fillFields(answers, files = {}) {
    const started = performance.now();
    extensionFilling++;
    try {
    const summary = {completed: [], filled: 0, needs_input: [], needs_writing: [], pending: [], writing: [], errors: [], skipped: 0};
    for (const answer of answers) {
      const record = refreshRecord(records.get(answer.key));
      if (!record || !record.members.every(member => record.field.kind === "file" ? member.isConnected && !member.disabled && !member.matches(":disabled") : interactiveVisible(member)) || occupied(record) && !answer.force) {trace("field","skipped",record?.element,{reason:!record?"missing":"occupied or unavailable"});summary.skipped++;continue;}
      let {element, members, field} = record;
      trace("field","found",element);
      if (isSkillsField(element)) { summary.skipped++; continue; }
      if (/\b(?:address(?: line)?\s*2|line\s*2|apt|apartment|suite|unit)\b/i.test(field.label)) {
        if (field.required) {summary.needs_input.push(field.label);summary.pending.push({...field});}
        else summary.skipped++;
        continue;
      }
      if (field.kind === "file" && occupied(record)) {summary.skipped++;continue;}
      if (["combobox","select","radio"].includes(field.kind) && !field.options.length && occupied(record) && !answer.force) { summary.skipped++; continue; }
      const unresolved = writing => {
        trace("field","verified",element,{verified:false,reason:"not committed or unresolved"});
        summary[writing ? "needs_writing" : "needs_input"].push(field.label);
        summary[writing ? "writing" : "pending"].push({...field});
        if (field.required) mark(record, "#999999");
      };
      if (answer.status === "NEEDS_WRITING") { unresolved(true); continue; }
      if (answer.status === "NEEDS_INPUT") { unresolved(false); continue; }
      if (answer.status === "OPTIONAL_SKIP") {
        // A select-only widget with no matching saved choice stays available in Fill.
        if (selectOnlyControl(element)) unresolved(false);
        else summary.skipped++;
        continue;
      }
      if (!["RESOLVED", "FILE"].includes(answer.status)) { unresolved(false); continue; }
      try {
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
          const region = container.parentElement || container;
          nativeSet(element, "files", transfer.files);
          events(element);
          const outcome = await waitFor(() => {
            const error = Array.from(region.querySelectorAll('[role="alert"],[class*="error"],[class*="Error"]'))
              .find(node => visible(node) && /upload|file|attach/i.test(text(node)));
            if (error) return {error: text(error)};
            const shown = Array.from(region.querySelectorAll('*')).some(node => visible(node) &&
              (text(node).includes(file.filename) || node.matches('button,[role="button"],a') && /remove/i.test(label(node))));
            if (shown || element.isConnected && visible(element) && element.files[0]?.name === file.filename) return {ok: true};
          }, 10000);
          if (!outcome?.ok) {
            summary.errors.push(outcome?.error ? `Upload failed: ${outcome.error}` : `Upload not yet confirmed: ${file.filename}. Check the page before retrying.`);
            unresolved(false); continue;
          }
        } else if (field.kind === "combobox") {
          let ok = true;
          for (const value of Array.isArray(answer.value) ? answer.value : [answer.value]) {
            if (!await fillCombo(element, value)) { ok = false; break; }
          }
          if (!ok) { unresolved(false); continue; }
        } else if (field.kind === "select") {
          const option = fieldChoice(element, Array.from(element.options).filter(option => !option.disabled && option.value !== ""), answer.value, option => option.text);
          if (!option) { unresolved(false); continue; }
          nativeSet(element, "value", option.value);
          events(element);
        } else if (["radio", "checkbox"].includes(field.kind)) {
          const values = Array.isArray(answer.value) ? answer.value : [answer.value];
          const matches = values.map(value => best(members, value, label));
          if (field.kind === "checkbox" && members.length === 1 && typeof answer.value === "boolean") {
            if (!await setCheckbox(element,answer.value)) {unresolved(false);continue;}
          } else {
            if (matches.some(member => !member) || field.kind === "radio" && matches.length !== 1) { unresolved(false); continue; }
            let verified=true;
            if(answer.force && field.kind==="checkbox") for(const member of members) if(member.checked && !matches.includes(member)) verified = await setCheckbox(currentControl(member),false) && verified;
            for (const member of matches) {
              const current=currentControl(member);
              if(field.kind==='checkbox') verified=await setCheckbox(current,true) && verified;
              else {if(!current.checked) current.click();verified=Boolean(await waitFor(()=>currentControl(member).checked,500)) && verified;}
            }
            refreshRecord(record);
            if(!verified || field.kind==='checkbox' && record.members.some(member=>Boolean(member.checked)!==values.includes(label(member)))) {unresolved(false);continue;}
          }
        } else {
          const value = String(await phoneValue(element, answer.value ?? "", field));
          if (field.max_length !== null && value.length > field.max_length) { unresolved(false); continue; }
          if (element.isContentEditable || !("value" in element)){trace("field","typed",element,{value});element.textContent = value;}
          else {
            element.focus();
            if (/_date$/.test(sectionField(element))) await typeDate(element,value);
            else nativeSet(element, "value", value);
          }
          events(element);
          element.blur?.();
          if((field.kind==='tel'||/phone|mobile/i.test(field.label)) && !await settledText(record,value,true)){unresolved(false);continue;}
          if(answer.verify_settled && !await settledText(record,value)){unresolved(false);continue;}
          if (/_date$/.test(sectionField(element)) && element.value !== value &&
              (!/^\d+$/.test(value) || Number(element.value) !== Number(value))) { unresolved(false); continue; }
        }
        trace("field","verified",element,{verified:true,...(field.kind!=="file"?{value:answer.value,observed:valueOf(record)}:{})});
        mark(record, "#888888");
        summary.filled++;
        const value=valueOf(record); observedValues.set(field.key,JSON.stringify(value));
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
  function dateHeading(element) {
    const field=element.closest('[data-automation-id^="formField"]');
    const wrapper=element.closest('[data-automation-id="dateInputWrapper"]') || field;
    const heading=text(field?.querySelector('label')) || text(wrapper?.querySelector('label')) || wrapper?.getAttribute('aria-label');
    if(heading) return heading;
    const id=field?.getAttribute('data-automation-id') || '';
    return /startDate|firstYearAttended/.test(id)?'From':/endDate|lastYearAttended/.test(id)?'To':label(element);
  }
  function sectionField(element) {
    let value = sectionNormal(label(element)).replace(/ star$/, '');
    const part = element.getAttribute('data-automation-id') || '';
    if (/dateSection(Month|Year)/.test(part)) {
      value = sectionNormal(dateHeading(element)) + ' ' + (part.includes('Month') ? 'month' : 'year');
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
    for (const type of ['pointerover','pointerdown','mousedown','pointerup','mouseup']) {
      const EventType=type.startsWith('pointer')?PointerEvent:MouseEvent;
      if (!element.isConnected || !current()) return false;
      element.dispatchEvent(new EventType(type,{bubbles:true,button:0,pointerId:1,pointerType:'mouse'}));
    }
    if (!element.isConnected || !current()) return false;
    element.click();
    return true;
  }
  let skillsCancelled=false, skillsState={busy:false,progress:''};
  const optionName = option => text(option.querySelector('[data-automation-id="promptOption"]')) ||
    clean(option.getAttribute('data-automation-label')) || text(option);
  const skillRequest = async (type, payload={}) => {
    try {const response=await chrome.runtime.sendMessage({type,...payload});if(response?.error)trace(type,'error',null,{error:response.error});return response;}catch(error){trace(type,'error',null,{error:error.message,stack:error.stack});return {}; }
  };
  function splitSkill(skill) {
    const expanded = {js:'JavaScript',ts:'TypeScript'};
    return [...new Set(skill.split(/\s*(?:\/|&|,|\band\b)\s*/i).filter(Boolean)
      .map(part=>expanded[normal(part)] || part.trim()))];
  }
  function skillResults(element, previousLists) {
    const lists=Array.from(document.querySelectorAll('[data-automation-id="activeListContainer"],[role="listbox"]'))
      .filter(list=>!list.matches('[data-automation-id="selectedItemList"]') && visible(list) &&
        (list.matches('[data-automation-id="activeListContainer"]') || !previousLists.has(list) ||
         comboContainer(element).contains(list) ||
         clean(element.getAttribute('aria-controls') || element.getAttribute('aria-owns')).split(' ').includes(list.id)));
    const rows=[...new Set(lists.flatMap(list=>Array.from(list.querySelectorAll('[role="option"],[data-automation-id="menuItem"],[data-automation-id="promptOption"],option')).map(row=>row.closest('[data-automation-id="menuItem"][role="option"]') || row)))]
      .filter(row=>visible(row) && row.getAttribute('aria-disabled')!=='true' && optionName(row) &&
        !/^(loading\b|no (?:items|options|results)\b)/i.test(optionName(row)));
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
          comboInput(element)?.focus(); searchCombo(element,''); searchCombo(element,skill);
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
    input=currentControl(input);
    if(Boolean(checked(input))===value) return true;
    input.click();
    if(await waitFor(()=>Boolean(checked(currentControl(input)))===value,150)) return true;
    const wrapper=input.closest('[data-automation-id^="formField"]') || input.parentElement;
    const targets=[...Array.from(input.labels||[]),input.closest('label'),wrapper.querySelector('[role="checkbox"]'),input.nextElementSibling].filter(el=>el && visible(el));
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
      if(/^date$/.test(heading) || /^date$/.test(normal(dateHeading(el)).replace(/\s*\*$/,''))) {
        managed.add(el);
        const part=el.getAttribute('data-automation-id')||'';
        const value=/Month/.test(part)?month:/Day/.test(part)?day:/Year/.test(part)?year:el.type==='date'?year+'-'+month+'-'+day:month+'/'+day+'/'+year;
        if(el.value!==value) {await typeDate(el,value);events(el);el.blur();}
        if(el.value!==value && Number(el.value)!==Number(value)) errors.push('Self Identify date did not commit');
      }
    }
    const choices=Array.from(document.querySelectorAll('input[type="checkbox"],input[type="radio"]')).filter(el=>/^(yes, i have a disability|no, i do not have a disability|i do not want to answer)/i.test(label(el)));
    for(const el of choices) managed.add(el);
    const preference=normal(profile.application_preferences?.disability || profile.demographics?.disability);
    const prefix=/^yes/.test(preference)?'yes,':/^no/.test(preference)?'no,':/decline|do not|prefer not/.test(preference)?'i do not want to answer':'';
    const target=choices.find(el=>normal(label(el)).startsWith(prefix));
    if(prefix && target) {
      for(const el of choices.filter(el=>el!==target && checked(el))) await setCheckbox(el,false);
      await setCheckbox(target,true);
      if(!checked(target) || choices.some(el=>el!==target && checked(el))) errors.push('Disability choice did not commit');
    } else if(choices.length) errors.push('Choose a disability response in your profile');
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
            const degreeReplace=kind==='education' && sectionField(el)==='degree' && Boolean(el.closest('[data-automation-id="formField-degree"]')) && !best([comboSelection(el) || el.value],entry.degree);
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
    const step=workdayState()?.step || '';
    return Array.from(document.querySelectorAll('button,input[type="button"],input[type="submit"],[role="button"]')).find(node=>{
      const caption=clean(node.textContent || node.value);
      if(!interactiveVisible(node))return false;
      if(final)return /^(submit|submit application)$/i.test(caption) &&
        (/review/i.test(step) || node.getAttribute('data-automation-id')==='pageFooterNextButton');
      // Workday reuses pageFooterNextButton for both Continue and Submit.
      // Inspect action words, without treating stepper text such as Application
      // Questions or generic jobApplication identifiers as final actions.
      const finalAction=value=>/\b(?:submit|apply|final[ -]?submit)\b/i.test(clean(value)
        .replace(/([a-z])([A-Z])/g,'$1 $2').replace(/[_-]/g,' '));
      const labelledBy=clean(node.getAttribute('aria-labelledby')).split(' ').map(id=>text(document.getElementById(id))).join(' ');
      const identity=[caption,node.getAttribute('aria-label'),labelledBy,node.title,
        node.getAttribute('data-automation-id'),node.getAttribute('data-automation-label'),node.id,node.name];
      return /^(next|save and continue|continue)$/i.test(caption) && !identity.some(finalAction);
    });
  }
  function navigationState() {
    const final=Boolean(navigationButton(true));
    return {kind:final?'submit':navigationButton()?'next':'none',url:location.href,step:workdayState()?.step || ''};
  }
  async function manualSubmit(expected) {
    const state=navigationState(), errors=validationErrors();
    if(!expected || expected.url!==state.url || expected.step!==state.step || state.kind!=='submit' ||
       errors.length || signInPage() || (await scan()).fields.some(f=>f.required&&!f.answered))
      return {clicked:false,errors,message:'Application changed or needs review. Use the employer page.'};
    const button=navigationButton(true);
    if(!button)return {clicked:false,errors:[],message:'Submit is unavailable. Use the employer page.'};
    armSubmission(); button.click();
    return {clicked:true,errors:validationErrors()};
  }
  async function nextPage(requireComplete = false) {
    const errors = validationErrors();
    if (requireComplete && errors.length) return {clicked: false, errors};
    if (signInPage()) return {clicked: false, errors: ['Sign in before continuing.']};
    if (requireComplete && (await scan()).fields.some(field => field.required && !field.answered)) return {clicked: false, errors: []};
    if(navigationState().kind==='submit')return {clicked:false,errors:[],message:'Final submit is yours'};
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
  function syncWritingButtons() {
    for (const [field, host] of writingButtons) if (!field.isConnected || !visible(field)) { host.remove(); writingButtons.delete(field); }
    for (const field of document.querySelectorAll('textarea,[contenteditable="true"],input[type="text"],input:not([type])')) {
      if (!visible(field) || writingButtons.has(field)) continue;
      if (field.tagName === 'INPUT' && !(field.size >= 50 || field.getBoundingClientRect().height >= 60)) continue;
      const host = document.createElement('span'); host.className='autoapply-ask-ai';
      host.style.cssText='display:block; margin:4px 0 8px;';
      const shadow=host.attachShadow({mode:'open'}), style=document.createElement('style'), button=document.createElement('button');
      style.textContent='button{font:11px system-ui;padding:3px 8px;border:1px solid #555;border-radius:5px;background:#191919;color:#eee;cursor:pointer}button:focus-visible{outline:2px solid currentColor;outline-offset:2px}@media(prefers-color-scheme:light){button{background:#fff;color:#333;border-color:#aaa}}';
      button.textContent='Ask AI'; button.type='button';
      button.onclick=async () => {
        chatExplicit=field;chatFocus=field;
        if(extensionFilling)return;
        await scan();
        const record=Array.from(records.values()).find(record=>record.element===field);
        if (record) chrome.runtime.sendMessage({type:'ask-ai',key:record.field.key}).catch(()=>{});
      };
      shadow.append(style,button); field.after(host); writingButtons.set(field,host);
    }
  }
  let writingButtonTimer;
  new MutationObserver(() => { clearTimeout(writingButtonTimer); writingButtonTimer=setTimeout(syncWritingButtons,80); })
    .observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['hidden','style','class']});
  setTimeout(syncWritingButtons,80);

  // Passive reads only: diagnostics must never replace the scanner's live handles.
  function snapshotIdentity() {
    const shape=JSON.stringify([location.href, workdayState()?.step, ...Array.from(document.querySelectorAll('form,fieldset,legend,h1,h2,h3,label,input,select,textarea,[role="combobox"],[role="radiogroup"]'))
      .filter(element => !element.closest('#autoapply-sidebar,.autoapply-ask-ai'))
      .slice(0,10000).map(element => [element.tagName, element.id.slice(0,2000), element.getAttribute('name')?.slice(0,2000), element.getAttribute('type'), element.getAttribute('aria-label')?.slice(0,2000),
        Boolean(element.getClientRects().length), element.matches('legend,h1,h2,h3,label') ? text(element).slice(0,2000) : ''])]);
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
      return record && record.element.isConnected && record.field.label===field.label &&
        (record.field.kind==='file' || interactiveVisible(record.element)) && !occupied(record);
    })) return null;
    return pageStructure();
  }
  function pageStructure() {
    const nodes=[], encoder=new TextEncoder(); let size=0, truncated=false;
    const selector='form,fieldset,label,legend,h1,h2,h3,h4,h5,h6,input,textarea,select,option,button,[role],[data-automation-id],[contenteditable="true"]';
    for (const element of document.querySelectorAll(selector)) {
      if (element.closest('#autoapply-sidebar,.autoapply-ask-ai')) continue;
      const row={tag:element.tagName.toLowerCase(), visible:Boolean(element.getClientRects().length && getComputedStyle(element).visibility!=='hidden')};
      for (const attr of ['role','type','id','name','data-automation-id','aria-label']) {
        const value=element.getAttribute(attr); if(value) row[attr]=value.slice(0,2000);
      }
      const labelledby=referencedLabel(element,'aria-labelledby'); if(labelledby)row.labelledby=labelledby.slice(0,2000);
      if (element.matches('input,textarea,select,button,[role="combobox"]')) row.label=label(element).slice(0,2000);
      if (element.matches('label,legend,h1,h2,h3,h4,h5,h6,option,button,[role="heading"],[role="option"],[data-automation-id="richText"],[data-automation-id="formLabel"]')) {
        const copy=element.cloneNode(true);
        copy.querySelectorAll('input,textarea,[contenteditable],script,style,[data-automation-id="selectedItem"]').forEach(node=>node.remove());
        if (!element.isContentEditable) row.text=text(copy).slice(0,2000);
      }
      const bytes=encoder.encode(JSON.stringify(row)).length;
      if (nodes.length >= 10000 || size+bytes>950000) {truncated=true;break;} size+=bytes;nodes.push(row);
    }
    return {host:location.hostname,nodes,truncated};
  }
  const chatDocument=crypto.randomUUID(),chatIdentities=new WeakMap();
  let chatFocus,chatExplicit;
  const writableChat=el=>el && el.isConnected && interactiveVisible(el) &&
    !el.readOnly && !el.matches(':disabled,[readonly],[aria-readonly="true"]') &&
    (el.matches('textarea,input[type="text"],input:not([type]),input[type="email"],input[type="tel"],input[type="url"]') || el.isContentEditable) && !el.closest('#autoapply-sidebar') && !/captcha/i.test([el.id,el.name,label(el)].join(' '));
  document.addEventListener('focusin',event=>{if(!extensionFilling && event.isTrusted && writableChat(event.target))chatFocus=event.target;},true);
  document.addEventListener('pointerdown',event=>{if(event.isTrusted && writableChat(event.target)){chatExplicit=null;chatFocus=event.target;}},true);
  const chatValue=el=>String(el.isContentEditable?el.textContent:el.value);
  async function chatFingerprint(value){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value))),b=>b.toString(16).padStart(2,'0')).join('');}
  async function chatContext({passive=false,request=''}={}){
    if(!passive && !extensionFilling)await scan();
    const fields=Array.from(records.values()).filter(r=>writableChat(r.element)).slice(0,80).map(r=>{
      if(!chatIdentities.has(r.element))chatIdentities.set(r.element,crypto.randomUUID());
      return {key:r.field.key,label:r.field.label.slice(0,500),kind:r.field.kind,identity:chatIdentities.get(r.element),max_length:r.field.max_length};
    });
    let named=fields.filter(f=>f.label.trim() && request.toLowerCase().includes(f.label.trim().toLowerCase()));
    if(!named.length && /cover letter/i.test(request))named=fields.filter(f=>/cover/i.test(f.label) && /letter/i.test(f.label));
    const selected=named.length===1?named[0]:fields.find(f=>records.get(f.key)?.element===(writableChat(chatExplicit)?chatExplicit:chatFocus));
    const current_answer=selected?{key:selected.key,text:chatValue(records.get(selected.key).element).slice(0,12000),fingerprint:await chatFingerprint(chatValue(records.get(selected.key).element))}:undefined;
    return {url:location.href,page:pageInfo(),document:chatDocument,fields,current_key:selected?.key || null,current_answer};
  }
  async function chatReplace(context,action){
    const field=context.fields?.find(f=>f.key===action.key),record=records.get(action.key);
    if(extensionFilling || skillsState.busy)throw Error('Application is busy. Wait for autofill or writing to finish, then retry.');
    if(context.url!==location.href || context.document!==chatDocument || !field || !record ||
       !writableChat(record.element) || chatIdentities.get(record.element)!==field.identity ||
       label(record.element)!==field.label || !['text','textarea','email','tel','url'].includes(record.field.kind) ||
       action.type!=='replace_text' || typeof action.text!=='string' || !action.text.trim() || action.text.length>12000 ||
       field.max_length!==null && action.text.length>field.max_length)throw Error('Field changed or is unavailable. Select the field and ask again.');
    const before=chatValue(record.element);
    if(context.current_answer?.key!==action.key || context.current_answer.fingerprint!==await chatFingerprint(before) ||
       chatValue(record.element)!==before || extensionFilling || !writableChat(record.element) || context.url!==location.href ||
       context.document!==chatDocument || records.get(action.key)!==record || label(record.element)!==field.label ||
       chatIdentities.get(record.element)!==field.identity)throw Error('Field changed while waiting. Select the field and retry; your edit is preserved.');
    const summary=await fill([{key:action.key,status:'RESOLVED',value:action.text,force:true,verify_settled:true,source:'AI'}]);
    if(!summary.completed.some(f=>f.key===action.key && String(f.value)===action.text))throw Error('Replacement did not remain in the field. Review the page; your draft is preserved.');
    return {ok:true};
  }
  globalThis.autoApplyContent = {chatContext,chatReplace,beginTrace,endTrace,trace,skillsPage:pageInfo, skillsAvailable:()=>skillInputs().length > 0, autofillSkills, skillsStatus:()=>skillsState, cancelSkills:()=>{skillsCancelled=true;}, pageStructure, unresolvedStructure, scan, collectOptions, fill, prepareSections, enterApplication, sidebar, nextPage, navigationState, manualSubmit, confirmationEvidence, validationErrors};
  globalThis[singleton]=globalThis.autoApplyContent;
  if(window===window.top)chrome.runtime.sendMessage({type:'sidebar-ready'}).catch(()=>{});
})();
