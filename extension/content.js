(() => {
  if (globalThis.autoApplyContent) return;
  let nextKey = 0;
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

  function label(element) {
    const explicit = Array.from(element.labels || []).map(text).filter(Boolean).join(" ");
    const labelled = (element.getAttribute("aria-labelledby") || "").split(/\s+/).map(id => text(document.getElementById(id))).filter(Boolean).join(" ");
    return explicit || text(element.closest("label")) || clean(element.getAttribute("aria-label")) || labelled ||
      clean(element.getAttribute("placeholder")) || text(element.previousElementSibling) ||
      text(element.closest('[data-automation-id^="formField"]')?.querySelector('label')) ||
      clean(element.getAttribute('data-automation-id')?.replace(/([a-z])([A-Z])/g, '$1 $2')) ||
      clean(element.name || element.id) || "Unlabelled field";
  }

  function groupLabel(container, fallback) {
    return text(container?.querySelector("legend")) || clean(container?.getAttribute("aria-label")) ||
      (container?.getAttribute("aria-labelledby") || "").split(/\s+/).map(id => text(document.getElementById(id))).filter(Boolean).join(" ") ||
      text(container?.querySelector("h1,h2,h3,h4,[role='heading']")) || fallback;
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
    return [];
  }
  function openCombo(element) {
    element.focus();
    if (element.matches('button[aria-haspopup="listbox"]')) { element.click(); return; }
    keypress(element, "ArrowDown");
    if (element.getAttribute("aria-expanded") !== "true" && !element.getAttribute("list")) {
      const control = element.closest('[class*="-control"],[class*="__control"]') || element;
      control.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, button: 0}));
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
      .map(id => text(document.getElementById(id))).filter(Boolean).join(" ");
  }
  function usableFileLabel(value) {
    value = clean(value);
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
      description: (text(description.content) || text(document.querySelector("main,article"))).slice(0, 20000)
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
    if (signInPage()) return false;
    const manual = manualEntry();
    const entry = manual || Array.from(document.querySelectorAll('button,a[href],[role="button"]')).find(element =>
      visible(element) && /^(apply|apply for this job)$/i.test((element.getAttribute('aria-label') || element.textContent).trim()));
    if (!entry) return false;
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
      if (grouped.has(element)) continue;
      let kind = element.tagName === "SELECT" ? "select" : element.getAttribute("role") === "combobox" || element.matches('button[aria-haspopup="listbox"]') || element.getAttribute("data-automation-id") === "multiSelectInput" ? "combobox" :
        element.tagName === "TEXTAREA" || element.isContentEditable ? "textarea" : element.type || "text";
      if (!["text", "textarea", "select", "radio", "checkbox", "combobox", "file", "date", "number", "email", "tel", "url"].includes(kind)) kind = "text";
      let members = [element];
      let fieldLabel = kind === "file" ? fileLabel(element) : label(element);
      const section = sectionHint(element);
      if (/^(work experience|employment|education)\b/i.test(section)) {
        const identity = section + ':' + fieldLabel;
        if (repeated.has(identity)) continue;
        repeated.add(identity);
      }
      const datePart = element.getAttribute('data-automation-id');
      if (/^dateSection(Month|Year)$/.test(datePart || '')) {
        const date = element.closest('[data-automation-id="dateInputWrapper"],[data-automation-id^="formField"]');
        const heading = text(date?.querySelector('label')) || clean(date?.getAttribute('aria-label')) || fieldLabel;
        fieldLabel = heading.replace(/\s*\*$/, '').replace(/^(from|to)$/i, '$1 date') + ' ' + (datePart.endsWith('Month') ? 'month' : 'year');
      }
      let container;
      if (["radio", "checkbox"].includes(kind)) {
        container = element.closest(kind === "radio" ? '[role="radiogroup"],fieldset' : 'fieldset,[role="group"]');
        members = controls.filter(other => other.type === kind && other.form === element.form &&
          (kind === "checkbox" && container ? container.contains(other) : element.name ? other.name === element.name : container && container.contains(other)));
        if (!members.length) members = [element];
        if (!container && members.length > 1) container = commonContainer(members);
        if (members.length > 1 || kind === "radio") fieldLabel = groupLabel(container, element.name || fieldLabel);
        members.forEach(member => grouped.add(member));
      }
      if (!element.dataset.autoapplyKey || keys.has(element.dataset.autoapplyKey)) element.dataset.autoapplyKey = `aa-${++nextKey}`;
      const key = element.dataset.autoapplyKey;
      keys.add(key);
      let options = kind === "select" ? Array.from(element.options).filter(option => !option.disabled && option.value !== "").map(option => clean(option.text)) :
        ["radio", "checkbox"].includes(kind) ? members.map(label) : [];
      fields.push({key, label: fieldLabel, kind, required: members.some(member => member.required || member.getAttribute("aria-required") === "true") || container?.getAttribute("aria-required") === "true" || fieldLabel.includes("*"),
        options, multiple: element.getAttribute("data-automation-id") === "multiSelectInput" ||
          element.getAttribute("aria-multiselectable") === "true", ...(kind === "combobox" ? {options_partial: Boolean(comboInput(element) && comboInput(element).getAttribute("aria-autocomplete") !== "none")} : {}),
        section, max_length: element.maxLength >= 0 ? element.maxLength : null});
      records.set(key, {element, members, field: fields[fields.length - 1]});
      fields[fields.length - 1].answered = occupied(records.get(key));
    }
    previousStep = workdayState();
    return {page: pageInfo(), fields, workday_step: previousStep?.step, sign_in: signInPage(), manual_entry: Boolean(manualEntry()), scan_ms: Math.round(performance.now() - started)};
  }

  async function collectOptions(keys) {
    const started = performance.now(), fields = [];
    for (const key of keys) {
      const record = records.get(key);
      if (!record || record.field.kind !== "combobox" || occupied(record)) continue;
      try {
        openCombo(record.element);
        record.field.options = (await settledComboOptions(record.element, 700)).map(text);
        fields.push({...record.field});
      } finally { closeCombo(record.element); }
    }
    return {fields, options_ms: Math.round(performance.now() - started)};
  }

  function phoneCountrySelection(element) {
    const container = comboContainer(element);
    const identity = [label(element), element.id, element.name, container.className].join(" ");
    const shown = text(container);
    // Country controls next to a phone input often render only a flag and dial code.
    const phone = /phone|dial|calling|country.code/i.test(identity) ||
      container.parentElement?.querySelector('input[type="tel"]');
    return phone && /\+\d{1,4}\b/.test(shown) ? shown : "";
  }

  function occupied({element, members, field}) {
    if (["checkbox", "radio"].includes(field.kind)) return members.some(member => member.checked);
    if (field.kind === "file") return element.files.length > 0;
    if (field.kind === "combobox") return Boolean(comboSelection(element) || phoneCountrySelection(element) || clean(comboInput(element)?.value));
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
    const exact = options.find(item => clean(getText(item)) === clean(value));
    const folded = options.find(item => normal(getText(item)) === normal(value));
    return exact || folded || (clean(value) ? options.find(item => normal(getText(item)).startsWith(normal(value))) ||
      options.find(item => normal(getText(item)).includes(normal(value))) : undefined);
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
      openCombo(element);
      const words = clean(value).split(" ");
      const prefix = words.slice(0, words.length > 3 ? 3 : 2).join(" ");
      const searches = [...new Set([clean(value), prefix])];
      let option = best(await settledComboOptions(element, 350), value, text);
      for (const search of option ? [] : searches) {
        if (!searchCombo(element, search)) break;
        // Keep waiting for a match even when an unrelated first page stays rendered.
        option = await waitFor(() => best(comboOptions(element), value, text), 2500);
        if (option) break;
      }
      if (!option) return false;
      const selected = text(option);
      if (option.tagName === "OPTION" && element.getAttribute("list")) {
        nativeSet(element, "value", selected);
        events(element);
        committed = element.value === selected;
      } else {
        option.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, button: 0}));
        option.click();
        committed = Boolean(await waitFor(() => {
          const selection = comboSelection(element);
          return normal(selection) === normal(selected) ||
            element.getAttribute("aria-expanded") === "false" && normal(element.value) === normal(selected);
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

  async function fill(answers, files = {}) {
    const started = performance.now();
    const summary = {filled: 0, needs_input: [], needs_writing: [], pending: [], writing: [], errors: [], skipped: 0};
    for (const answer of answers) {
      const record = records.get(answer.key);
      if (!record || !record.members.every(member => record.field.kind === "file" ? member.isConnected && !member.disabled && !member.matches(":disabled") : visible(member)) || occupied(record)) { summary.skipped++; continue; }
      const {element, members, field} = record;
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
        if (field.kind === "file") {
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
          const option = best(Array.from(element.options).filter(option => !option.disabled && option.value !== ""), answer.value, option => option.text);
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
            for (const member of matches) { if (!member.checked) member.click(); events(member); }
          }
        } else {
          const value = String(answer.value ?? "");
          if (field.max_length !== null && value.length > field.max_length) { unresolved(false); continue; }
          if (element.isContentEditable || !("value" in element)) element.textContent = value;
          else nativeSet(element, "value", value);
          events(element);
        }
        mark(record, "#68a879");
        summary.filled++;
      } catch { unresolved(false); }
    }
    summary.fill_ms = Math.round(performance.now() - started);
    return summary;
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
  globalThis.autoApplyContent = {scan, collectOptions, fill, enterApplication};
})();
