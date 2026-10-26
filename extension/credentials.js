// Isolated-world only. Never send values through runtime messages or storage.
(() => {
  if(globalThis.autoApplyCredentials)return;
  const native=globalThis.autoApplyNativeRoots;
  const marked=new WeakSet(),secrets=new Set(),identities=new WeakMap();
  const identity=el=>{if(el&&!identities.has(el))identities.set(el,crypto.randomUUID());return el?identities.get(el):null;};
  const visible=el=>{
    if(!el?.isConnected || !el.getClientRects().length)return false;
    for(let node=el;node instanceof Element;node=node.assignedSlot || native.parent(node)){
      const style=getComputedStyle(node);
      if(node.hidden || node.inert || node.getAttribute('aria-hidden')==='true' ||
         style.display==='none' || ['hidden','collapse'].includes(style.visibility) || Number(style.opacity)===0)return false;
    }
    return true;
  };
  const editable=el=>visible(el) && !el.disabled && !el.readOnly && !el.matches(':disabled,[aria-readonly="true"]');
  const hint=el=>[el.name,el.id,el.getAttribute('autocomplete'),el.getAttribute('aria-label'),...Array.from(el.labels||[],l=>l.textContent)].join(' ');
  const passwords=()=>native.query('input[type="password"]').filter(visible);
  const contains=(root,el)=>{for(;el;el=native.parent(el))if(el===root)return true;return false;};
  const scope=pw=>native.closest(pw,'form,[role="form"],[role="dialog"],main');
  const optional=(pw,roots)=>{
    const root=scope(pw);
    const headings=native.query('h1,h2,h3,legend',roots).filter(el=>root&&contains(root,el)&&visible(el)).map(el=>el.textContent).join(' ');
    return /create[\s_-]*account[\s_-]*optional|(?:create\s+(?:an?\s+)?account|password)\s*(?:\(\s*)?optional|optional\s+(?:account|password)/i.test(hint(pw)+' '+headings);
  };
  function authContext(pw,roots){
    const root=scope(pw);
    if(!root)return null;
    const nodes=native.query('h1,h2,h3,legend,button,input[type="submit"],[role="button"]',roots).filter(el=>contains(root,el)&&visible(el));
    const context=[root.getAttribute('aria-label'),...nodes.map(el=>el.textContent||el.value||'')].join(' ');
    if(/create[\s_-]*account[\s_-]*optional|(?:create\s+(?:an?\s+)?account|password)\s*(?:\(\s*)?optional|optional\s+(?:account|password)/i.test(context+' '+hint(pw)))return null;
    return /sign[\s-]*in|log[\s-]*in|create\s+(?:an?\s+)?account|sign\s*up|register/i.test(context)?root:null;
  }
  const active=()=>{
    const roots=native.discover(),pw=native.query('input[type="password"]',roots);
    for(const el of pw){marked.add(el);if(el.value)secrets.add(el.value);}
    let found=false;
    for(const password of pw.filter(visible)){
      const auth=authContext(password,roots),root=auth || scope(password);
      if(!root || optional(password,roots))continue;
      found ||= Boolean(auth);
      for(const el of native.query('input,textarea',roots))if(contains(root,el) &&
        (el.type==='password'||el.type==='email'||/username|user.?name|login|email/i.test(hint(el)))){
        marked.add(el);if(el.value)secrets.add(el.value);
      }
    }
    return found;
  };
  // An incomplete scan is a privacy barrier, never evidence of an account.
  const incomplete=()=>Boolean(native.discover().truncated);
  const sensitive=el=>{
    if(!el)return false;
    const authentication=active();
    return Boolean(marked.has(el) || el.type==='password' ||
      /password|username|user.?name|account.?email|login.?email/i.test(hint(el)) ||
      el.matches('input,textarea,[contenteditable]') && Array.from(secrets).some(secret=>secret && String(el.value || el.textContent || '').includes(secret)) ||
      (authentication || incomplete()) && el.matches('input,textarea,select,[contenteditable]'));
  };

  function redact(value){
    if(typeof value==='string'){for(const secret of secrets)if(secret)value=value.split(secret).join('[private account value]');return value;}
    if(Array.isArray(value))return value.map(redact);
    if(value && typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>[redact(k),redact(v)]));
    return value;
  }
  function form(){
    if(native.discover().truncated)return null;
    const pw=passwords();
    if(!pw.length || pw.some(el=>!editable(el)) || pw.length>2)return null;
    const roots=native.discover(),root=authContext(pw[0],roots);
    if(!root || pw.some(el=>!contains(root,el)))return null;
    if(/reset.?password|password.?reset|change.?password|password.?change|forgot.?password/i.test(location.pathname))return null;
    if(root.tagName==='FORM'){
      try{const target=new URL(root.getAttribute('action')||location.href,location.href);if(target.protocol!=='https:' || target.origin!==location.origin)return null;}catch{return null;}
    }
    const headings=native.query('h1,h2,h3,legend,button,input[type="submit"],[role="button"]',roots).filter(el=>contains(root,el)).filter(visible).map(el=>el.textContent||el.value||'').join(' ');
    const context=headings+' '+(root.getAttribute('aria-label')||'')+' '+Array.from(document.querySelectorAll('h1,h2')).filter(visible).map(el=>el.textContent).join(' ');
    if(/reset\s+(?:your\s+)?password|change\s+(?:your\s+)?password|forgot\s+password|current\s+password|one.?time|verification\s+code|security\s+code/i.test(context) ||
       pw.some(el=>/current[\s_-]*password|old.?password|otp|one.?time/i.test([el.name,el.id,el.getAttribute('aria-label'),...Array.from(el.labels||[],l=>l.textContent)].join(' '))))return null;
    const signup=/create\s+(?:an?\s+)?account|sign\s*up|register/i.test(context);
    if(!signup && !/sign[\s-]*in|log[\s-]*in/i.test(context))return null;
    const emails=native.query('input',roots).filter(el=>contains(root,el)).filter(el=>editable(el) &&
      ['email','text',''].includes(el.type) && (el.type==='email' || /email|username|user.?name/i.test(hint(el))));
    if(emails.length!==1 || !signup && pw.length!==1 || [...emails,...pw].some(el=>el.form && el.form!==root))return null;
    if(signup && pw.length===2 && (!/confirm|repeat|re.?enter/i.test(hint(pw[1])) || /confirm|repeat|re.?enter/i.test(hint(pw[0]))))return null;
    // CAPTCHA/OTP controls are never touched; wait for the user on such forms.
    if(native.query('input,iframe,[data-sitekey]',roots).filter(el=>contains(root,el)).some(el=>visible(el) && (el.hasAttribute('data-sitekey') || /captcha|one-time-code|otp|verification.?code|security.?code/i.test(hint(el)+' '+(el.getAttribute('src')||'')))))return null;
    for(const el of [...emails,...pw]){marked.add(el);if(el.value)secrets.add(el.value);}
    return {mode:signup?'signup':'login',email:emails[0],password:pw[0],confirm:pw[1],root,identity:[identity(root),identity(emails[0]),...pw.map(identity)].join(':')};
  }
  const describe=()=>{const f=form();return {active:active(),supported:Boolean(f) && location.protocol==='https:',mode:f?.mode,identity:f?.identity,url:location.origin+location.pathname};};
  function fill(value,expected){
    const f=form();
    if(!f || location.protocol!=='https:' || location.origin+location.pathname!==expected.url || f.mode!==expected.mode || f.identity!==expected.identity ||
       !value?.configured || typeof value.email!=='string' || typeof value.password!=='string')return {status:'unavailable',filled:0};
    secrets.add(value.email);secrets.add(value.password);
    let filled=0;
    const set=(el,text)=>{
      if(!el || !editable(el) || el.value || !el.isConnected)return;
      marked.add(el);
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,text);
      el.dispatchEvent(new Event('input',{bubbles:true,composed:true}));el.dispatchEvent(new Event('change',{bubbles:true,composed:true}));
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
  globalThis.autoApplyCredentials={active,incomplete,sensitive,redact,describe,fill};
})();
