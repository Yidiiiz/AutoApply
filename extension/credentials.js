// Isolated-world only. Never send values through runtime messages or storage.
(() => {
  if(globalThis.autoApplyCredentials)return;
  const marked=new WeakSet(),secrets=new Set();
  const visible=el=>Boolean(el?.isConnected && el.getClientRects().length && getComputedStyle(el).visibility!=='hidden' && getComputedStyle(el).display!=='none');
  const editable=el=>visible(el) && !el.disabled && !el.readOnly && !el.matches(':disabled,[aria-readonly="true"]');
  const hint=el=>[el.name,el.id,el.getAttribute('autocomplete'),el.getAttribute('aria-label'),...Array.from(el.labels||[],l=>l.textContent)].join(' ');
  const passwords=()=>Array.from(document.querySelectorAll('input[type="password"]')).filter(visible);
  const active=()=>passwords().length>0;
  const sensitive=el=>Boolean(el && (marked.has(el) || el.type==='password' ||
    /password|username|user.?name|account.?email|login.?email/i.test(hint(el)) ||
    el.matches('input,textarea,[contenteditable]') && Array.from(secrets).some(secret=>secret && String(el.value || el.textContent || '').includes(secret)) ||
    active() && el.matches('input,textarea,select,[contenteditable]')));
  function redact(value){
    if(typeof value==='string'){for(const secret of secrets)if(secret)value=value.split(secret).join('[private account value]');return value;}
    if(Array.isArray(value))return value.map(redact);
    if(value && typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>[redact(k),redact(v)]));
    return value;
  }
  function form(){
    const pw=passwords();
    if(!pw.length || pw.some(el=>!editable(el)) || pw.length>2)return null;
    const root=pw[0].form || pw[0].closest('[role="form"],main,[role="dialog"]');
    if(!root || pw.some(el=>!root.contains(el)))return null;
    if(/reset.?password|password.?reset|change.?password|password.?change|forgot.?password/i.test(location.pathname))return null;
    if(root.tagName==='FORM'){
      try{const target=new URL(root.getAttribute('action')||location.href,location.href);if(target.protocol!=='https:' || target.origin!==location.origin)return null;}catch{return null;}
    }
    const headings=Array.from(root.querySelectorAll('h1,h2,h3,legend,button,input[type="submit"],[role="button"]')).filter(visible).map(el=>el.textContent||el.value||'').join(' ');
    const context=headings+' '+(root.getAttribute('aria-label')||'')+' '+Array.from(document.querySelectorAll('h1,h2')).filter(visible).map(el=>el.textContent).join(' ');
    if(/reset\s+(?:your\s+)?password|change\s+(?:your\s+)?password|forgot\s+password|current\s+password|one.?time|verification\s+code|security\s+code/i.test(context) ||
       pw.some(el=>/current[\s_-]*password|old.?password|otp|one.?time/i.test([el.name,el.id,el.getAttribute('aria-label'),...Array.from(el.labels||[],l=>l.textContent)].join(' '))))return null;
    const signup=/create\s+(?:an?\s+)?account|sign\s*up|register/i.test(context);
    if(!signup && !/sign[\s-]*in|log[\s-]*in/i.test(context))return null;
    const emails=Array.from(root.querySelectorAll('input')).filter(el=>editable(el) &&
      ['email','text',''].includes(el.type) && (el.type==='email' || /email|username|user.?name/i.test(hint(el))));
    if(emails.length!==1 || !signup && pw.length!==1)return null;
    if(signup && pw.length===2 && (!/confirm|repeat|re.?enter/i.test(hint(pw[1])) || /confirm|repeat|re.?enter/i.test(hint(pw[0]))))return null;
    // CAPTCHA/OTP controls are never touched; wait for the user on such forms.
    if(Array.from(root.querySelectorAll('input,iframe,[data-sitekey]')).some(el=>visible(el) && (el.hasAttribute('data-sitekey') || /captcha|one-time-code|otp|verification.?code|security.?code/i.test(hint(el)+' '+(el.getAttribute('src')||'')))))return null;
    for(const el of [...emails,...pw]){marked.add(el);if(el.value)secrets.add(el.value);}
    return {mode:signup?'signup':'login',email:emails[0],password:pw[0],confirm:pw[1],root};
  }
  const describe=()=>{const f=form();return {active:active(),supported:Boolean(f) && location.protocol==='https:',mode:f?.mode,url:location.origin+location.pathname};};
  function fill(value,expected){
    const f=form();
    if(!f || location.protocol!=='https:' || location.origin+location.pathname!==expected.url || f.mode!==expected.mode ||
       !value?.configured || typeof value.email!=='string' || typeof value.password!=='string')return {status:'unavailable',filled:0};
    secrets.add(value.email);secrets.add(value.password);
    let filled=0;
    const set=(el,text)=>{
      if(!el || !editable(el) || el.value || !el.isConnected)return;
      marked.add(el);
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,text);
      el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));
      if(el.value===text)filled++;
    };
    try{
      set(f.email,value.email);
      // Do not pair an occupied, different username with the stored password.
      if(f.email.value===value.email){
        set(f.password,value.password);
        if(f.mode==='signup' && f.password.value===value.password)set(f.confirm,value.password);
      }
      return {status:filled?'filled':'preserved',filled};
    }catch{return {status:'unavailable',filled:0};}
  }
  globalThis.autoApplyCredentials={active,sensitive,redact,describe,fill};
})();
