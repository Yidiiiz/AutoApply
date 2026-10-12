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
    const control = element.closest('[class*="-control"],[class*="__control"]');
    return control?.parentElement || element.parentElement;
  }
  function comboOptions(element) {
    const ids = clean(element.getAttribute("aria-controls") || element.getAttribute("aria-owns") || element.getAttribute("list")).split(" ");
    const lists = ids.map(id => document.getElementById(id)).filter(Boolean);
    if (!lists.length) {
      lists.push(comboContainer(element));
      if (element.getAttribute("aria-expanded") === "true") lists.push(...document.querySelectorAll('[role="listbox"]'));
    }
    return [...new Set(lists.flatMap(list => Array.from(list.querySelectorAll('[role="option"],option'))))]
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
    keypress(element, "ArrowDown");
    if (element.getAttribute("aria-expanded") !== "true" && !element.getAttribute("list")) {
      const control = element.closest('[class*="-control"],[class*="__control"]') || element;
      control.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, button: 0}));
    }
  }
  function closeCombo(element) { keypress(element, "Escape"); element.blur(); }
  function comboSelection(element) {
    const container = comboContainer(element);
    return text(container.querySelector('[class*="singleValue"],[class*="single-value"]')) ||
      clean(container.querySelector('input[type="hidden"]')?.value);
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
    return purpose && !/\b(resume|cv|cover)\b/i.test(fieldLabel || "") ? purpose : fieldLabel || purpose || "File attachment";
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

  async function scan() {
    records = new Map();
    const fields = [];
    const controls = Array.from(document.querySelectorAll('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="combobox"]'))
      .filter(element => (element.type === "file" ? !element.disabled && !element.matches(":disabled") : visible(element)) && !["hidden", "submit", "button", "reset", "image", "password"].includes(element.type) &&
        !(element.closest('[role="combobox"]') !== element && element.closest('[role="combobox"]')) &&
        !(element.parentElement?.closest('[contenteditable]:not([contenteditable="false"])')) &&
        !(!element.closest("form") && (element.type === "search" || element.closest('[role="search"]') || /\bsearch\b/i.test(label(element)))));
    const grouped = new Set();
    const keys = new Set();
    for (const element of controls) {
      if (grouped.has(element)) continue;
      let kind = element.tagName === "SELECT" ? "select" : element.getAttribute("role") === "combobox" ? "combobox" :
        element.tagName === "TEXTAREA" || element.isContentEditable ? "textarea" : element.type || "text";
      if (!["text", "textarea", "select", "radio", "checkbox", "combobox", "file", "date", "number", "email", "tel", "url"].includes(kind)) kind = "text";
      let members = [element];
      let fieldLabel = kind === "file" ? fileLabel(element) : label(element);
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
      if (kind === "combobox") {
        try {
          openCombo(element);
          await waitFor(() => comboOptions(element).length);
          options = comboOptions(element).map(text).filter(Boolean);
        } finally { closeCombo(element); }
      }
      fields.push({key, label: fieldLabel, kind, required: members.some(member => member.required || member.getAttribute("aria-required") === "true") || container?.getAttribute("aria-required") === "true" || fieldLabel.includes("*"),
        options, section: sectionHint(element), max_length: element.maxLength >= 0 ? element.maxLength : null});
      records.set(key, {element, members, field: fields[fields.length - 1]});
    }
    return {page: pageInfo(), fields};
  }

  function occupied({element, members, field}) {
    if (["checkbox", "radio"].includes(field.kind)) return members.some(member => member.checked);
    if (field.kind === "file") return element.files.length > 0;
    if (field.kind === "combobox" && comboSelection(element)) return true;
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
    return exact || folded || (clean(value) ? options.find(item => normal(getText(item)).includes(normal(value))) : undefined);
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
      const searches = [...new Set([String(value), words.slice(0, 3).join(" "), words.slice(0, 2).join(" ")])];
      let option;
      for (const search of searches) {
        if ("value" in element) nativeSet(element, "value", search);
        else if (element.isContentEditable) element.textContent = search;
        element.dispatchEvent(new Event("input", {bubbles: true}));
        // Async search may replace the menu; match the full answer after options settle.
        await pause(100);
        option = best(await settledComboOptions(element), value, text);
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
          return normal(selection) === normal(selected) || selection && !clean(element.value) ||
            element.getAttribute("aria-expanded") === "false" && normal(element.value) === normal(selected);
        }));
      }
      return committed;
    } finally {
      if (!committed) {
        if ("value" in element) nativeSet(element, "value", "");
        else if (element.isContentEditable) element.textContent = "";
        element.dispatchEvent(new Event("input", {bubbles: true}));
      }
      closeCombo(element);
    }
  }

  async function fill(answers, files = {}) {
    const summary = {filled: 0, needs_input: [], needs_writing: [], skipped: 0};
    for (const answer of answers) {
      const record = records.get(answer.key);
      if (!record || !record.members.every(member => record.field.kind === "file" ? member.isConnected && !member.disabled && !member.matches(":disabled") : visible(member)) || occupied(record)) { summary.skipped++; continue; }
      const {element, members, field} = record;
      const unresolved = writing => {
        summary[writing ? "needs_writing" : "needs_input"].push(field.label);
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
          nativeSet(element, "files", transfer.files);
          events(element);
          const attached = element.files[0]?.name === file.filename;
          const shown = visible(element) || await waitFor(() => filenameShown(element, file.filename), 6000);
          if (!attached || !shown) { unresolved(false); continue; }
        } else if (field.kind === "combobox") {
          if (!await fillCombo(element, answer.value)) { unresolved(false); continue; }
        } else if (field.kind === "select") {
          const option = best(Array.from(element.options).filter(option => !option.disabled && option.value !== ""), answer.value, option => option.text);
          if (!option) { unresolved(false); continue; }
          nativeSet(element, "value", option.value);
          events(element);
        } else if (["radio", "checkbox"].includes(field.kind)) {
          const values = Array.isArray(answer.value) ? answer.value : [answer.value];
          const matches = values.map(value => best(members, value, label));
          if (field.kind === "checkbox" && members.length === 1 && typeof answer.value === "boolean") {
            nativeSet(element, "checked", answer.value); events(element);
          } else {
            if (matches.some(member => !member) || field.kind === "radio" && matches.length !== 1) { unresolved(false); continue; }
            for (const member of matches) { nativeSet(member, "checked", true); events(member); }
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
    return summary;
  }
  globalThis.autoApplyContent = {scan, fill};
})();
