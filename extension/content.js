(() => {
  if (globalThis.autoApplyContent) return;
  let nextKey = 0, extensionFilling = 0;
  let sidebarHost;
  function sidebar(mode = 'toggle', tabId) {
    if (window !== window.top) return;
    if (!sidebarHost) {
      sidebarHost = document.createElement('div');
      sidebarHost.id = 'autoapply-sidebar';
      sidebarHost.style.cssText = 'all:initial!important;position:fixed!important;inset:0 0 0 auto!important;width:min(380px,100vw)!important;height:100dvh!important;z-index:2147483647!important;display:block!important;';
      const shadow = sidebarHost.attachShadow({mode:'open'});
      const style = document.createElement('style');
      style.textContent = ':host{color-scheme:light dark} .panel{height:100%;background:Canvas;box-shadow:-8px 0 32px #0002;animation:slide .18s ease-out}iframe{border:0;width:100%;height:100%;display:block} .restore{position:fixed;right:0;top:40%;border:1px solid #71717a;border-radius:8px 0 0 8px;padding:12px 8px;background:Canvas;color:CanvasText;cursor:pointer;font:13px ui-sans-serif,system-ui} [hidden]{display:none!important}@keyframes slide{from{transform:translateX(100%)}to{transform:translateX(0)}}@media(prefers-reduced-motion:reduce){.panel{animation:none}}';
      const panel = document.createElement('div'); panel.className = 'panel';
      const frame = document.createElement('iframe'); frame.src = chrome.runtime.getURL('sidepanel.html')+(tabId?'?tabId='+tabId:''); frame.title = 'AutoApply application assistant';
      panel.append(frame);
      const restore = document.createElement('button'); restore.className='restore'; restore.textContent='AutoApply'; restore.setAttribute('aria-label','Restore AutoApply sidebar');
      const positionKey = 'flagPosition:' + location.hostname;
      const clamp = top => Math.max(0, Math.min(innerHeight - restore.offsetHeight, top));
      let drag, dragged = false;
      chrome.storage.local.get(positionKey).then(saved => { if (Number.isFinite(saved[positionKey])) restore.style.top = clamp(saved[positionKey]) + 'px'; });
      restore.style.touchAction = 'none';
      restore.onpointerdown = event => { drag = {y:event.clientY, top:restore.getBoundingClientRect().top}; dragged=false; restore.setPointerCapture(event.pointerId); };
      restore.onpointermove = event => { if (!drag) return; if (Math.abs(event.clientY-drag.y)>3) dragged=true; if (dragged) restore.style.top=clamp(drag.top+event.clientY-drag.y)+'px'; };
      restore.onpointerup = () => { if (dragged) chrome.storage.local.set({[positionKey]:clamp(restore.getBoundingClientRect().top)}); drag=null; };
      restore.onpointercancel = () => { drag=null; };
      window.addEventListener('resize', () => { if (restore.style.top) restore.style.top=clamp(parseFloat(restore.style.top))+'px'; });
      restore.onclick=()=>{if(dragged){dragged=false;return;}sidebar('open');chrome.runtime.sendMessage({type:'sidebar-state',state:'open'}).catch(()=>{});};
      shadow.append(style,panel,restore); document.documentElement.append(sidebarHost);
      sidebarHost.dataset.state='closed';
    }
    const state = mode === 'toggle' ? sidebarHost.dataset.state === 'open' ? 'closed' : 'open' : mode;
    sidebarHost.dataset.state=state;
    sidebarHost.style.setProperty('display',state==='closed'?'none':'block','important');
    sidebarHost.style.setProperty('width',state==='minimized'?'0':'min(380px,100vw)','important');
    sidebarHost.shadowRoot.querySelector('.panel').hidden=state!=='open';
    sidebarHost.shadowRoot.querySelector('.restore').hidden=state!=='minimized';
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
    if(!record || ['file','password','hidden'].includes(record.field.kind)||!visible(record.element)) return;
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
    if(event.isTrusted && button && visible(button) && (button.type==='submit'||/^(submit(?: application)?|apply(?: now| application)?)$/i.test(clean(button.textContent||button.value)))) {
      chrome.runtime.sendMessage({type:'submit-intent'}).catch(()=>{});
    }
  },true);
  let confirmationTimer;
  function detectConfirmation() {
    const url=location.href;
    const strong=/greenhouse\.io|lever\.co/.test(location.hostname)&&/\/(confirmation|thanks)(?:[/?#]|$)/i.test(location.pathname)||/ashbyhq\.com/.test(location.hostname)&&/\/application\/.+\/submitted(?:[/?#]|$)/i.test(location.pathname);
    const evidence=(document.body.innerText||'').match(/thank you for applying|application (?:has been |was )?submitted|we have received your application|application received/i)?.[0] || (document.querySelector('[data-automation-id="congratulations"]') ? 'Workday congratulations' : '');
    const invalid=Array.from(document.querySelectorAll('[role="alert"],[aria-invalid="true"]')).some(el=>visible(el)&&(/error|required|invalid|missing|correct/i.test(text(el))||el.getAttribute('aria-invalid')==='true'));
    if(!invalid&&(strong||evidence)) chrome.runtime.sendMessage({type:'submission-confirmation',url,evidence:evidence||'Confirmation URL',strong:Boolean(strong)}).catch(()=>{});
  }
  new MutationObserver(()=>{clearTimeout(confirmationTimer);confirmationTimer=setTimeout(detectConfirmation,300);}).observe(document.body,{childList:true,subtree:true,characterData:true});
  setTimeout(detectConfirmation,300);
  if(window===window.top)chrome.runtime.sendMessage({type:'sidebar-ready'}).catch(()=>{});
  let records = new Map();
  const clean = value => String(value || "").replace(/\s+/g, " ").trim();
  const text = element => clean(element?.textContent);
  const normal = value => clean(value).toLowerCase();

  function visible(element) {
    if (element.disabled || element.readOnly || element.matches(":disabled") || element.getAttribute("aria-disabled") === "true" || element.getAttribute("aria-readonly") === "true" || element.closest("[hidden], [inert], [aria-hidden='true']")) return false;
    const rect = element.getBoundingClientRect();
    if (!rect.width || !rect.height || rect.right + scrollX <= 0 || rect.bottom + scrollY <= 0) return false;
    for (let node = element; node instanceof Element; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
    }
    return true;
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
    return sources.map(usableLabel).find(Boolean) || 'Unlabelled field';
  }

  function groupLabel(container, fallback) {
    return [...Array.from(container?.querySelectorAll('legend,[data-automation-id="formLabel"],[data-automation-id="richText"]') || []).map(questionText), container?.getAttribute('aria-label'),
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
  function comboOptions(element) {
    const ids = clean(element.getAttribute("aria-controls") || element.getAttribute("aria-owns") || element.getAttribute("list")).split(" ");
    const lists = ids.map(id => document.getElementById(id)).filter(Boolean);
    if (!lists.length) {
      lists.push(comboContainer(element));
      if (element.getAttribute("aria-expanded") === "true") lists.push(...document.querySelectorAll('[role="listbox"]'));
    }
    return [...new Set(lists.flatMap(list => Array.from(list.querySelectorAll('[role="option"],option,[data-automation-id="promptOption"]'))))]
      .filter(option => !option.disabled && option.getAttribute("aria-disabled") !== "true" &&
        text(option) && !/^(loading\b|no options\b|no results\b)/i.test(text(option)) &&
        (option.tagName === "OPTION" || visible(option)));
  }
  async function settledComboOptions(element, timeout = 6000) {
    const end = Date.now() + timeout;
    let previous = "", stableSince = Date.now();
    do {
      const options = comboOptions(element);
      const signature = JSON.stringify(options.map(text));
      if (!options.length || signature !== previous) stableSince = Date.now();
      else if (Date.now() - stableSince >= 250) return options;
      previous = signature;
      await pause(50);
    } while (Date.now() < end);
    return comboOptions(element);
  }
  async function openCombo(element) {
    element.focus();
    if (element.matches('button[aria-haspopup="listbox"]')) { element.click(); return; }
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
  function closeCombo(element) { keypress(element, "Escape"); element.blur(); }
  function comboInput(element) {
    return element.matches("input,textarea") ? element : element.querySelector("input,textarea") ||
      (element.isContentEditable ? element :
        comboContainer(element).querySelector('input[data-automation-id="searchBox"],input[role="searchbox"]'));
  }
  function searchCombo(element, value) {
    const input = comboInput(element);
    if (!input || input.readOnly || input.disabled) return false;
    input.focus();
    if (input instanceof HTMLInputElement) {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, value);
    } else if ("value" in input) nativeSet(input, "value", value);
    else input.textContent = value;
    // No Enter key or change event: searching must never submit the form.
    input.dispatchEvent(new InputEvent("input", {bubbles: true,
      inputType: value ? "insertText" : "deleteContentBackward", data: value || null}));
    return true;
  }
  function comboSelection(element) {
    const container = comboContainer(element);
    return text(container.querySelector('[class*="singleValue"],[class*="single-value"]')) ||
      clean(container.querySelector('input[type="hidden"]')?.value) ||
      text(container.querySelector('[data-automation-id="selectedItem"],[data-automation-id="selectedItemLabel"]')) ||
      (element.matches('button[aria-haspopup="listbox"]') && !/^(select|choose)( one)?(?:\.{3})?$/i.test(text(element)) ? text(element) : "");
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
  function sectionHint(element) {
    for (let container = element.parentElement; container && container !== document.body; container = container.parentElement) {
      const headings = Array.from(container.querySelectorAll('legend,h1,h2,h3,h4,[role="heading"]'))
        .filter(heading => heading.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING);
      if (headings.length) return text(headings[headings.length - 1]);
    }
    return "";
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
    const controls = Array.from(document.querySelectorAll('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="combobox"],button[aria-haspopup="listbox"]'))
      .filter(element => (element.type === "file" ? !element.disabled && !element.matches(":disabled") : visible(element)) && (!["hidden", "submit", "reset", "image", "password"].includes(element.type) && (element.type !== "button" || element.getAttribute("role") === "combobox" || element.matches('button[aria-haspopup="listbox"]'))) &&
        !(element.closest('[role="combobox"]') !== element && element.closest('[role="combobox"]')) &&
        !(element.parentElement?.closest('[contenteditable]:not([contenteditable="false"])')) &&
        !(!element.closest("form") && (element.type === "search" || element.closest('[role="search"]') || /\bsearch\b/i.test(label(element)))));
    const grouped = new Set();
    const repeated = new Set();
    const keys = new Set();
    for (const element of controls) {
      if (grouped.has(element) || /honeypot|honey.?pot|fax|website_confirm/i.test([element.name,element.id,label(element)].join(" "))) continue;
      let kind = element.tagName === "SELECT" ? "select" : element.getAttribute("role") === "combobox" || element.matches('button[aria-haspopup="listbox"]') || element.getAttribute("data-automation-id") === "multiSelectInput" ? "combobox" :
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
        container = element.closest(kind === "radio" ? '[role="radiogroup"],fieldset' : 'fieldset,[role="group"]');
        members = controls.filter(other => (!['current','fluent'].includes(sectionField(element)) || other===element) && other.type === kind && other.form === element.form &&
          (kind === "checkbox" && container ? container.contains(other) : element.name ? other.name === element.name : container && container.contains(other)));
        if (!members.length) members = [element];
        if (!container && members.length > 1) container = commonContainer(members);
        if (members.length > 1 || kind === "radio") fieldLabel = groupLabel(container, fieldLabel);
        members.forEach(member => grouped.add(member));
      }
      if (!element.dataset.autoapplyKey || keys.has(element.dataset.autoapplyKey)) element.dataset.autoapplyKey = `aa-${++nextKey}`;
      const key = element.dataset.autoapplyKey;
      keys.add(key);
      let options = kind === "select" ? Array.from(element.options).filter(option => !option.disabled && option.value !== "").map(option => clean(option.text)) :
        ["radio", "checkbox"].includes(kind) ? members.map(label) : [];
      fields.push({key, label: fieldLabel, kind, required: members.some(member => member.required || member.getAttribute("aria-required") === "true") || container?.getAttribute("aria-required") === "true" || /\*|\brequired\b/i.test([element.getAttribute('aria-label'), text(container?.querySelector('legend,[data-automation-id="formLabel"]')), text(element.closest('[data-automation-id^="formField"]')?.querySelector('label,legend,[data-automation-id="formLabel"]')), Array.from(element.labels || []).map(text).join(' ')].join(' ')),
        options, multiple: element.getAttribute("data-automation-id") === "multiSelectInput" ||
          element.getAttribute("aria-multiselectable") === "true", ...(kind === "combobox" ? {options_partial: Boolean(comboInput(element) && comboInput(element).getAttribute("aria-autocomplete") !== "none")} : {}),
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
    const retained = Array.from(records.values(), record => ({...record.field, answered:occupied(record)}));
    previousStep = workdayState();
    return {page: pageInfo(), fields: retained, workday_step: previousStep?.step, sign_in: signInPage(), manual_entry: Boolean(manualEntry()), scan_ms: Math.round(performance.now() - started)};
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
    if (field.kind === "combobox") return Boolean(comboSelection(element) || phoneCountrySelection(element) || displayedChoice(element) || clean(comboInput(element)?.value) || !comboInput(element) && !/^(select|choose|search)\b/i.test(text(element)) && text(element));
    if (field.kind === "select") return Array.from(element.selectedOptions).some(option =>
      !option.disabled && option.value !== "" && !/^(choose|select)\b/i.test(clean(option.text)));
    return Boolean(clean(element.isContentEditable || !("value" in element) ? element.textContent : element.value));
  }

  function nativeSet(element, property, value) {
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
  function fieldChoice(element, options, value, getText = text) {
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
    let committed = false;
    try {
      await openCombo(element);
      const words = clean(value).split(" ");
      const prefix = words.slice(0, words.length > 3 ? 3 : 2).join(" ");
      const searches = [...new Set([clean(value), prefix])];
      let option = fieldChoice(element, await settledComboOptions(element, 350), value);
      for (const search of option ? [] : searches) {
        if (!searchCombo(element, search)) break;
        // Keep waiting for a match even when an unrelated first page stays rendered.
        option = await waitFor(() => fieldChoice(element, comboOptions(element), value), 2500);
        if (option) break;
      }
      if (!option) return false;
      const selected = text(option);
      const choices = comboOptions(element).map(text);
      if (option.tagName === "OPTION" && element.getAttribute("list")) {
        nativeSet(element, "value", selected);
        events(element);
        committed = element.value === selected;
      } else {
        option.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, button: 0}));
        option.click();
        committed = Boolean(await waitFor(() => {
          const selection = normal(comboSelection(element));
          const matchesSelected = Boolean(selection && normal(selected).includes(selection));
          // A different displayed option overrides other commit signals.
          if (selection && choices.some(choice => normal(choice) !== normal(selected) &&
            (normal(choice) === selection || !matchesSelected && normal(choice).includes(selection)))) return false;
          const container = comboContainer(element);
          const singleValue = text(container.querySelector('[class*="singleValue"],[class*="single-value"]'));
          const closed = element.getAttribute("aria-expanded") === "false";
          return matchesSelected || option.getAttribute("aria-selected") === "true" ||
            closed && Boolean(singleValue) && !container.querySelector('[class*="placeholder"]') ||
            closed && normal(element.value) === normal(selected);
        }));
      }
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
    for(let i=0;i<2&&group?.parentElement&&!group.matches('form,body');i++) {if(group.querySelector('[data-automation-id="countryPhoneCode"],select,[role="combobox"],.iti__flag-container'))break;group=group.parentElement;}
    if(group?.matches("form,body"))group=null;
    const country=Array.from(group?.querySelectorAll('select,[role="combobox"],button[aria-haspopup="listbox"],[data-automation-id="countryPhoneCode"]')||[]).find(el=>el!==element&&/country|dial.code|phone.code/i.test(label(el)+' '+el.getAttribute('data-automation-id')));
    const picker=group?.querySelector('.iti__flag-container,.iti__country-container,.selected-flag,[class*="flag-dropdown"]');
    if(country && !occupied({element:country,members:[country],field:{kind:country.tagName==='SELECT'?'select':'combobox'}})) {
      if(country.tagName==='SELECT') {const option=Array.from(country.options).find(o=>/united states|\+1(?:\D|$)/i.test(o.text));if(option){nativeSet(country,'value',option.value);events(country);}}
      else await fillCombo(country,'United States (+1)');
    }
    const hint=element.placeholder||'',pattern=element.getAttribute('pattern')||'';
    const workday=location.hostname.endsWith('.myworkdayjobs.com')||element.closest('[data-automation-id^="formField"]')&&/^phone number/i.test(field.label);
    if(!workday&&!country&&!picker&&(hint.includes('+')||pattern.includes('\\+')||/international/i.test(field.label)&&!hint&&!pattern&&!(element.maxLength>0&&element.maxLength<=12)&&element.inputMode!=='numeric'))return value;
    const national=digits.slice(1);
    if(workday)return national;
    if(/\(\d{3}\)\s*\d{3}[- ]\d{4}/.test(hint))return '('+national.slice(0,3)+') '+national.slice(3,6)+'-'+national.slice(6);
    if(/\d{3}-\d{3}-\d{4}/.test(hint))return national.slice(0,3)+'-'+national.slice(3,6)+'-'+national.slice(6);
    return national;
  }
  async function fill(answers, files = {}) {
    const started = performance.now();
    extensionFilling++;
    try {
    const summary = {completed: [], filled: 0, needs_input: [], needs_writing: [], pending: [], writing: [], errors: [], skipped: 0};
    for (const answer of answers) {
      const record = records.get(answer.key);
      if (!record || !record.members.every(member => record.field.kind === "file" ? member.isConnected && !member.disabled && !member.matches(":disabled") : visible(member)) || occupied(record) && !answer.force) { summary.skipped++; continue; }
      const {element, members, field} = record;
      if (/\b(?:address(?: line)?\s*2|line\s*2|apt|apartment|suite|unit)\b/i.test(field.label)) {
        if (field.required) {summary.needs_input.push(field.label);summary.pending.push({...field});}
        else summary.skipped++;
        continue;
      }
      if (field.kind === "file" && occupied(record)) {summary.skipped++;continue;}
      if (["combobox","select","radio"].includes(field.kind) && !field.options.length && occupied(record)) { summary.skipped++; continue; }
      const unresolved = writing => {
        summary[writing ? "needs_writing" : "needs_input"].push(field.label);
        summary[writing ? "writing" : "pending"].push({...field});
        if (field.required) mark(record, "#d99022");
      };
      if (answer.status === "NEEDS_WRITING") { unresolved(true); continue; }
      if (answer.status === "NEEDS_INPUT") { unresolved(false); continue; }
      if (answer.status === "OPTIONAL_SKIP") { summary.skipped++; continue; }
      if (!["RESOLVED", "FILE"].includes(answer.status)) { unresolved(false); continue; }
      try {
        if (record.dateElements) {
          if (!/^\d{4}-(0[1-9]|1[0-2])(?:-01)?$/.test(String(answer.value))) { unresolved(false); continue; }
          record.savedDate=String(answer.value).slice(0,7);
          for (const date of record.dateElements) {
            if (occupied({element:date,members:[date],field:{kind:date.tagName==='SELECT'?'select':date.type||'text'}})) continue;
            const value=dateValue(date,String(answer.value));
            if(date.tagName==='SELECT') {const option=best(Array.from(date.options),value,o=>o.text);if(!option)continue;nativeSet(date,'value',option.value);}
            else {date.focus();nativeSet(date,'value',value);}
            events(date);date.blur?.();
            if (Number(date.value)!==Number(value) && date.value!==value) throw Error('Date did not commit');
          }
        } else if (field.kind === "file") {
          const file = files[answer.value];
          if (!file) { unresolved(answer.value === "cover_letter"); continue; }
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
            if (element.checked !== answer.value) element.click(); events(element);
          } else {
            if (matches.some(member => !member) || field.kind === "radio" && matches.length !== 1) { unresolved(false); continue; }
            if(answer.force && field.kind==="checkbox") for(const member of members) if(member.checked && !matches.includes(member)) member.click();
            for (const member of matches) { if (!member.checked) member.click(); events(member); }
          }
        } else {
          const value = String(await phoneValue(element, answer.value ?? "", field));
          if (field.max_length !== null && value.length > field.max_length) { unresolved(false); continue; }
          if (element.isContentEditable || !("value" in element)) element.textContent = value;
          else {
            element.focus();
            nativeSet(element, "value", value);
          }
          events(element);
          element.blur?.();
          if (/_date$/.test(sectionField(element)) && element.value !== value &&
              (!/^\d+$/.test(value) || Number(element.value) !== Number(value))) { unresolved(false); continue; }
        }
        mark(record, "#68a879");
        summary.filled++;
        const value=valueOf(record); observedValues.set(field.key,JSON.stringify(value));
        summary.completed.push({...field,value,source:answer.source||"AI"});
      } catch { unresolved(false); }
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
    value = sectionNormal(value).replace(/ section$/, '');
    return Object.keys(sectionNames).find(kind => sectionNames[kind].includes(value));
  }
  function resumeSections() {
    const found = new Map();
    for (const node of document.querySelectorAll('legend,h1,h2,h3,h4,[role="heading"],[aria-label],[data-automation-id]')) {
      const kind = sectionKind(node.getAttribute('aria-label') || node.getAttribute('data-automation-id') || text(node));
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
    return /startDate/.test(id)?'From':/endDate/.test(id)?'To':label(element);
  }
  function sectionField(element) {
    let value = sectionNormal(label(element)).replace(/ star$/, '');
    const part = element.getAttribute('data-automation-id') || '';
    if (/dateSection(Month|Year)/.test(part)) {
      value = sectionNormal(dateHeading(element)) + ' ' + (part.includes('Month') ? 'month' : 'year');
    }
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
    const anchors = Array.from(container.querySelectorAll('input,textarea,select,[role="combobox"],button[aria-haspopup="listbox"]'))
      .filter(el => sectionField(el) === (kind==='education'?'school':kind==='languages'?'language':kind==='links'?'url':'title'));
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
      const name = new Date(Number(year),Number(month)-1,1).toLocaleString('en-US',{month:'long'});
      if (element.tagName==='SELECT') return Array.from(element.options).find(o=>[name.toLowerCase(),name.slice(0,3).toLowerCase(),month,String(Number(month))].includes(normal(o.text)))?.text || name;
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
  function selectedSkills(element) {
    const container = comboContainer(element);
    return Array.from(container.querySelectorAll('[data-automation-id="selectedItem"],[data-automation-id="selectedItemLabel"],[class*="multiValueLabel"],[class*="multi-value__label"],[class*="token"],[data-skill]')).map(text).map(normal);
  }
  async function fillSkills(element, skills) {
    if(clean(comboInput(element)?.value)) return {added:0,skipped:0};
    let added=0, skipped=0;
    for (const skill of skills) {
      if (selectedSkills(element).includes(normal(skill))) continue;
      try {
        await openCombo(element); searchCombo(element,skill);
        const option = await waitFor(()=>comboOptions(element).find(o=>normal(text(o))===normal(skill)),450);
        if (option) { option.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,button:0})); option.click(); }
        else if (element.getAttribute('data-allow-create')==='true' || /token/.test(comboContainer(element).className) || /creatable/i.test(comboContainer(element).className) || comboOptions(element).some(o=>/^(create|add) /i.test(text(o)) && normal(text(o)).includes(normal(skill)))) {
          const input=comboInput(element);
          // Keep Enter confined to the token widget, never the containing form.
          const stop=event=>{if(event.key==='Enter')event.preventDefault();};
          input.addEventListener('keydown',stop); keypress(input,'Enter'); input.removeEventListener('keydown',stop);
        }
        if (await waitFor(()=>selectedSkills(element).includes(normal(skill)),250)) added++; else skipped++;
      } finally { searchCombo(element,''); closeCombo(element); }
    }
    return {added,skipped};
  }
  async function prepareSections(profile) {
    const sections=[], supplied=new Map(), managed=new Set(), completed=[];
    const containers=resumeSections();
    extensionFilling++;
    try {
      for (const [container,kind] of containers) {
        const entries = kind==='links'?Object.entries(profile.links||{}).filter(([,url])=>url).map(([title,url])=>({title,url})):profile[kind] || [];
        if (kind==='skills') continue;
        const cap = {experience:6,projects:6,education:3,languages:6,links:6}[kind];
        let groups=entryGroups(container,kind);
        const add = Array.from(container.querySelectorAll('button,a,[role="button"],input[type="button"]')).find(el=>visible(el) &&
          el.type!=='submit' && /^(add(?: another)?(?: (?:work experience|experience|employment|project|education|language|website|link))?|\+)$/.test(sectionNormal(el.getAttribute('aria-label') || text(el) || el.value) || text(el)) &&
          (el.tagName!=='A' || !el.getAttribute('href') || el.getAttribute('href')==='#'));
        let added=0;
        for (let index=0; index<Math.min(entries.length,cap); index++) {
          if (index>=groups.length) {
            if (!add) break;
            const before=groups.length; add.click();
            if (!await waitFor(()=>entryGroups(container,kind).length>before,1500)) break;
            groups=entryGroups(container,kind);
          }
          const group=groups[index], entry=entries[index];
          const elements=Array.from(group.querySelectorAll('input,textarea,select,[role="combobox"],button[aria-haspopup="listbox"]')).filter(visible);
          // Preserve an entire entry already containing user facts.
          const occupiedGroup=elements.some(el=>!['hidden','button'].includes(el.type) && sectionField(el)!=='current' && occupied({element:el,members:[el],field:{kind:el.tagName==='SELECT'?'select':el.getAttribute('role')==='combobox'?'combobox':el.type||'text'}}));
          for (const el of elements) {
            managed.add(el);
            if(occupiedGroup) continue;
            const key=sectionField(el); if(!key && kind!=='languages') continue;
            let value=entry[key];
            if(kind==='languages' && key!=='language' && key!=='fluent' && (el.tagName==='SELECT' || el.getAttribute('role')==='combobox' || el.matches('button[aria-haspopup="listbox"]'))) value=entry.proficiency;
            if(kind==='education' && key==='degree' && (el.tagName==='SELECT' || el.getAttribute('role')==='combobox' || el.matches('button[aria-haspopup="listbox"]'))) {
              const options=el.tagName==='SELECT'?Array.from(el.options).filter(o=>o.value && !o.disabled):await (openCombo(el),settledComboOptions(el,350));
              if(!best(options,value,el.tagName==='SELECT'?o=>o.text:text) && best(options,entry.major,el.tagName==='SELECT'?o=>o.text:text)) value=entry.major;
              if(el.tagName!=='SELECT') closeCombo(el);
            }
            if(key==='company') value=entry.company || entry.organization;
            if(kind==='links' && key==='url') {const named=Object.keys(profile.links||{}).find(name=>sectionNormal(label(el)).includes(sectionNormal(name)));if(named)value=profile.links[named];}
            if(key==='title' && /^(role|position)$/.test(sectionNormal(label(el))) && kind==='projects') value=entry.organization || entry.title;
            if(key==='technologies') value=(entry.technologies||[]).join(', ');
            if(key==='current') value=Boolean(entry.current);
            if(key==='fluent') value=Boolean(entry.fluent);
            if(key==='end_date' && entry.current) value='';
            else if(/_date$/.test(key)) value=dateValue(el,value);
            if(value!==undefined && value!=='') supplied.set(el,{value:trimSection(value,el.maxLength>=0?el.maxLength:null),boolean:key==='current'||key==='fluent'});
          }
          if(!occupiedGroup) {
            // Commit this entry before clicking Add for the next one.
            await scan();
            const entryAnswers=[];
            for(const record of records.values()) {
              const chosen=supplied.get(record.element);
              if(group.contains(record.element) && chosen) entryAnswers.push({key:record.field.key,status:'RESOLVED',value:chosen.boolean?chosen.value==='true':chosen.value,source:'profile:sections'});
            }
            entryAnswers.sort((a,b)=>Number(records.get(b.key)?.field.kind==='checkbox')-Number(records.get(a.key)?.field.kind==='checkbox'));
            const filled=await fill(entryAnswers);
            completed.push(...filled.completed);
            added++;
          }
        }
        sections.push({name:kind,added,skipped:0});
      }
      await scan();
      for(const [container,kind] of containers) {
        if(kind==='skills' && profile.skills?.length) for(const record of records.values()) {
          if(!container.contains(record.element) || !/skills/i.test(record.field.label)) continue;
          managed.add(record.element);
          if(record.field.kind==='combobox') {
            const counts=await fillSkills(record.element,profile.skills||[]); sections.push({name:kind,...counts}); record.field.answered=true;
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
      const answers=[];
      for(const record of records.values()) {
        const chosen=supplied.get(record.element);
        if(managed.has(record.element) && !record.field.section_missing_date) {
          record.field.section_managed=true;
          answers.push({key:record.field.key,status:chosen?'RESOLVED':'OPTIONAL_SKIP',value:chosen?.boolean?chosen.value==='true':chosen?.value,source:'profile:sections'});
        }
      }
      return {fields:Array.from(records.values(),r=>({...r.field,answered:occupied(r)})),answers,sections,completed};
    } finally { extensionFilling--; }
  }

  function workdayState() {
    if (!location.hostname.endsWith('.myworkdayjobs.com') &&
        !document.querySelector('[data-automation-id="progressBar"],[data-automation-id="progressBarActiveStep"],[data-automation-id="signInContent"]')) return null;
    const active = document.querySelector('[data-automation-id="progressBarActiveStep"]') ||
      document.querySelector('[data-automation-id="progressBar"] [aria-current="step"]');
    const step = text(active) || text(document.querySelector('main h1,main h2,form h1,form h2,[data-automation-id="heading"]'));
    const fields = Array.from(document.querySelectorAll('input,textarea,select,button[aria-haspopup="listbox"]'))
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
  async function nextPage(requireComplete = false) {
    const errors = validationErrors();
    if (requireComplete && errors.length) return {clicked: false, errors};
    if (signInPage()) return {clicked: false, errors: ['Sign in before continuing.']};
    if (requireComplete && (await scan()).fields.some(field => field.required && !field.answered)) return {clicked: false, errors: []};
    const button = Array.from(document.querySelectorAll('button,input[type="button"],input[type="submit"],[role="button"]'))
      .find(node => {
        const caption = clean(node.textContent || node.value || node.getAttribute('aria-label'));
        const identity = [caption, node.getAttribute('aria-label'), node.getAttribute('data-automation-id')].join(' ');
        return visible(node) && !/submit|apply/i.test(identity) && /^(next|save and continue|continue)$/i.test(caption);
      });
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
      style.textContent='button{font:11px system-ui;padding:3px 8px;border:1px solid #aaa;border-radius:5px;background:#fff;color:#444;cursor:pointer}';
      button.textContent='Ask AI'; button.type='button';
      button.onclick=async () => {
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
  globalThis.autoApplyContent = {pageStructure, scan, collectOptions, fill, prepareSections, enterApplication, sidebar, nextPage, validationErrors};
})();
