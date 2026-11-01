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
  const editable=el=>visible(el) && !el.disabled && !el.readOnly && !el.matches(':disabled,[aria-readonly="true"]') && !native.closest(el,'[inert],[aria-disabled="true"]');
  const workday=root=>/^(?:.+\.)?myworkdayjobs\.com$/.test(location.hostname) && root?.matches('form[data-automation-id="signInFormo"]');
  // Workday renders recovery and alternate-mode navigation as native buttons,
  // sometimes with the default submit type. Their captions are not form state.
  const navigation=el=>el.matches('a,[role="link"]') || (workday(scope(el)) ||
    workday(native.closest(el,'[data-automation-id="signInContent"]')?.querySelector('form[data-automation-id="signInFormo"]'))) &&
    ['forgotPasswordLink','signInLink','createAccountExpandButton'].includes(el.getAttribute('data-automation-id'));
  let diagnosis={reason:'no_password',password_count:0,email_count:0,action_count:0};
  const reject=reason=>{diagnosis.reason=reason;return null;};
  let revision=0, ticket, synthetic=false, href=location.href, autoEdited=false, verified;
  const invalidate=()=>{revision++;ticket=undefined;};
  // History API changes need no DOM mutation or browser event. Keep the full
  // href private and expose only revision changes, never query/hash values.
  const refreshContext=()=>{if(href!==location.href){href=location.href;invalidate();}};
  for(const type of ['beforeinput','input','change','keydown'])document.addEventListener(type,e=>{if(e.isTrusted && !synthetic && !e.target.closest?.('#autoapply-sidebar')){autoEdited=true;invalidate();}},true);
  for(const type of ['pagehide','popstate','hashchange'])addEventListener(type,invalidate);
  const hint=el=>[el.name,el.id,el.getAttribute('title'),el.getAttribute('autocomplete'),el.getAttribute('aria-label'),...Array.from(el.labels||[],l=>l.textContent)].join(' ');
  const confirmation=el=>/confirm|repeat|re.?enter|\bverify[\s_-]*(?:new[\s_-]*)?password\b/i.test(hint(el)) ||
    workday(scope(el)) && el.getAttribute('data-automation-id')==='verifyPassword';
  const passwords=()=>native.query('input[type="password"]').filter(visible);
  const contains=(root,el)=>{for(;el;el=native.parent(el))if(el===root)return true;return false;};
  // Native ownership wins over nested ARIA presentation containers. A form=
  // reference alone cannot pull credentials from elsewhere in the document.
  const scope=pw=>pw.form?.ownerDocument===document && contains(pw.form,pw)?pw.form:native.closest(pw,'form,[role="form"],[role="dialog"],main');
  const inAccount=(f,el)=>contains(f.surface,el) || el.form===f.root;
  function accountSurface(root,roots){
    const forms=native.query('form',roots).filter(el=>visible(el) && (workday(el) || authContext(el,roots)===el ||
      native.query('input[type="password"]',roots).some(pw=>visible(pw) && (pw.form===el || contains(el,pw)))));
    diagnosis.account_form_count=Math.min(forms.length,1000);
    const surfaces=workday(root)?native.query('[data-automation-id="signInContent"]',roots).filter(el=>contains(el,root)&&visible(el)):[];
    diagnosis.known_surface_count=Math.min(surfaces.length,1000);
    return surfaces[0] || root;
  }
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
    return workday(root) || /sign[\s-]*in|log[\s-]*in|create\s+(?:an?\s+)?account|sign\s*up|register/i.test(context)?root:null;
  }
  const active=()=>{
    const roots=native.discover(),pw=native.query('input[type="password"]',roots);
    for(const el of pw){marked.add(el);if(el.value)secrets.add(el.value);}
    let found=native.query('form[data-automation-id="signInFormo"]',roots).some(root=>workday(root) && visible(root) && native.query('[data-automation-id="createAccountExpandButton"],[data-automation-id="createAccountSubmitButton"],[data-automation-id="signInSubmitButton"]',roots).some(el=>contains(root,el)&&visible(el)));
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
    refreshContext();
    diagnosis={reason:'supported',password_count:0,email_count:0,action_count:0,external_action_count:0,native_owner_mismatch_count:0,account_form_count:0,known_surface_count:0};
    if(native.discover().truncated)return reject('scan_incomplete');
    const pw=passwords();
    diagnosis.password_count=Math.min(pw.length,1000);
    if(!pw.length)return reject('no_password');
    if(pw.some(el=>!editable(el)))return reject('controls_not_editable');
    const roots=native.discover(),root=authContext(pw[0],roots);
    const surface=accountSurface(root,roots);
    if(pw.length>2 || !root || pw.some(el=>!contains(root,el)))return reject('scope_ambiguous');
    if(location.protocol!=='https:')return reject('insecure_origin');
    if(/reset.?password|password.?reset|change.?password|password.?change|forgot.?password/i.test(location.pathname))return reject('reset_password');
    if(root.tagName==='FORM'){
      try{const target=new URL(root.getAttribute('action')||location.href,location.href);if(target.protocol!=='https:' || target.origin!==location.origin)return reject('cross_origin');}catch{return reject('cross_origin');}
    }
    const headings=native.query('h1,h2,h3,legend,button,input[type="submit"],[role="button"]',roots).filter(el=>contains(surface,el) && !navigation(el)).filter(visible).map(el=>el.textContent||el.value||'').join(' ');
    const context=headings+' '+(root.getAttribute('aria-label')||'')+' '+Array.from(document.querySelectorAll('h1,h2')).filter(visible).map(el=>el.textContent).join(' ');
    if(/reset\s+(?:your\s+)?password|change\s+(?:your\s+)?password|forgot\s+password/i.test(context))return reject('reset_password');
    if(/current\s+password/i.test(context) || pw.some(el=>/current[\s_-]*password|old.?password/i.test([el.name,el.id,el.getAttribute('aria-label'),...Array.from(el.labels||[],l=>l.textContent)].join(' '))))return reject('current_password');
    if(/one.?time|verification\s+code|security\s+code/i.test(context) || pw.some(el=>/otp|one.?time/i.test(hint(el))))return reject('otp');
    // A unique known native Workday surface and recognized password pair are
    // sufficient for filling. The primary action may render later.
    const inferredSignup=workday(root) && diagnosis.account_form_count===1 && diagnosis.known_surface_count===1 &&
      pw.length===2 && confirmation(pw[1]) && !confirmation(pw[0]);
    const signup=workday(root)?inferredSignup || native.query('button[data-automation-id="createAccountSubmitButton"],input[type="submit"][data-automation-id="createAccountSubmitButton"]',roots).some(el=>inAccount({root,surface},el)&&visible(el)):/create\s+(?:an?\s+)?account|sign\s*up|register/i.test(context);
    if(!signup && !workday(root) && !/sign[\s-]*in|log[\s-]*in/i.test(context))return reject('scope_ambiguous');
    const emails=native.query('input',roots).filter(el=>contains(root,el)).filter(el=>editable(el) &&
      ['email','text',''].includes(el.type) && (el.type==='email' || /email|username|user.?name/i.test(hint(el))));
    diagnosis.email_count=Math.min(emails.length,1000);
    if(emails.length!==1)return reject('no_matching_email');
    diagnosis.native_owner_mismatch_count=[...emails,...pw].filter(el=>root.tagName==='FORM'?el.form!==root:el.form && el.form!==root).length;
    if(diagnosis.native_owner_mismatch_count)return reject('native_owner_mismatch');
    if(diagnosis.account_form_count>1 || diagnosis.known_surface_count>1 || surface!==root && diagnosis.account_form_count!==1)return reject('account_surface_ambiguous');
    if(!signup && pw.length!==1)return reject('signup_mode_unavailable');
    if(signup && pw.length===2 && (!confirmation(pw[1]) || confirmation(pw[0])))return reject('confirmation_unrecognized');
    // CAPTCHA/OTP controls are never touched; wait for the user on such forms.
    const challenges=native.query('input,iframe,[data-sitekey]',roots).filter(el=>inAccount({root,surface},el)&&visible(el));
    if(challenges.some(el=>el.hasAttribute('data-sitekey') || /captcha/i.test(hint(el)+' '+(el.getAttribute('src')||''))))return reject('captcha');
    if(challenges.some(el=>/one-time-code|otp|verification.?code|security.?code/i.test(hint(el)+' '+(el.getAttribute('src')||''))))return reject('otp');
    for(const el of [...emails,...pw]){marked.add(el);if(el.value)secrets.add(el.value);}
    return {mode:signup?'signup':'login',email:emails[0],password:pw[0],confirm:pw[1],root,surface,identity:[identity(root),identity(surface),identity(emails[0]),...pw.map(identity)].join(':')};
  }
  const passwordPolicy=f=>{
    if(f?.mode!=='signup')return 'standard';
    if(f.password.minLength>=12)return 'strict';
    const rules=workday(f.root)?native.query('[data-automation-id="passwordRulesList"]').filter(el=>contains(f.surface,el)&&visible(el)).slice(0,1).map(el=>(el.textContent||'').slice(0,2000)).join(' '):'';
    return /\b12\b/.test(rules) && /upper.?case/i.test(rules) && /lower.?case/i.test(rules) && /number|numeric|digit/i.test(rules) && /special|symbol/i.test(rules)?'strict':'standard';
  };
  const verifiedCount=f=>f && verified?.identity===f.identity && verified.revision===revision?
    verified.fields.filter(({el,value})=>el.isConnected && editable(el) && el.value===value).length:0;
  const describe=()=>{
    const f=form(),actionReady=Boolean(f && action(f));
    // Missing/unavailable actions permit filling; unsafe ownership, origins
    // and ambiguous actions still withhold credentials.
    const supported=Boolean(f && (actionReady || diagnosis.reason==='action_unavailable'));
    return {active:active(),supported,actionReady,diagnosis:{...diagnosis},mode:f?.mode,identity:f?.identity,formIdentity:f?identity(f.root):undefined,url:location.origin+location.pathname,revision,passwordPolicy:passwordPolicy(f),filled:verifiedCount(f),autoEligible:!autoEdited};
  };
  function fill(value,expected){
    const f=form();
    if(!f || location.protocol!=='https:' || location.origin+location.pathname!==expected.url || f.mode!==expected.mode || f.identity!==expected.identity || revision!==expected.revision ||
       passwordPolicy(f)!==expected.passwordPolicy || !value?.configured || typeof value.email!=='string' || typeof value.password!=='string')return {status:'unavailable',filled:0};
    secrets.add(value.email);secrets.add(value.password);
    if([f.email,f.password,f.confirm].some(el=>el?.value && el.value!==(el===f.email?value.email:value.password)))return {status:'preserved',filled:0};
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
      verified={identity:f.identity,revision,fields:[f.email,f.password,f.confirm].filter(Boolean).map(el=>({el,value:el===f.email?value.email:value.password}))};
      return {status:filled?'filled':'preserved',filled:verifiedCount(f)};
    }catch{return {status:'unavailable',filled:0};}
  }

  function action(f){
    const roots=native.discover();
    const matchesMode=el=>(f.mode==='signup'?/^(create\s+(?:an?\s+)?account|sign\s*up|register)$/i:/^(sign[\s-]*in|log[\s-]*in)$/i).test((el.textContent||el.value||'').trim());
    const buttons=native.query('button,input[type="submit"]',roots).filter(el=>inAccount(f,el) && !navigation(el));
    const candidates=buttons.filter(el=>visible(el) &&
      (matchesMode(el) || workday(f.root) && /^(create\s+(?:an?\s+)?account|sign\s*up|register|sign[\s-]*in|log[\s-]*in)$/i.test((el.textContent||el.value||'').trim())) &&
      !/application|sso|google|linkedin|reset|forgot/i.test(hint(el)+' '+el.textContent));
    diagnosis.action_count=Math.min(candidates.length,1000);
    diagnosis.external_action_count=Math.min(candidates.filter(el=>!contains(f.root,el)).length,1000);
    // An unavailable known native action must not hide an unsafe owner/target.
    const guarded=buttons.filter(el=>candidates.includes(el) || workday(f.root) &&
      ['createAccountSubmitButton','signInSubmitButton'].includes(el.getAttribute('data-automation-id')));
    diagnosis.native_owner_mismatch_count=Math.min([...guarded,...consentControls(f)].filter(el=>el.form && el.form!==f.root).length,1000);
    if(diagnosis.native_owner_mismatch_count)return reject('native_owner_mismatch');
    if(guarded.some(el=>{try{const target=new URL(el.getAttribute('formaction')||location.href,location.href);return target.origin!==location.origin || target.protocol!=='https:';}catch{return true;}}))return reject('cross_origin');
    if(candidates.length!==1)return reject(candidates.length?'action_ambiguous':'action_unavailable');
    const button=candidates[0];
    const trusted=!workday(f.root) || button.getAttribute('data-automation-id')===(f.mode==='signup'?'createAccountSubmitButton':'signInSubmitButton');
    return editable(button) && matchesMode(button) && trusted?button:reject('action_unavailable');
  }
  function consentEligible(el,f){
    if(!inAccount(f,el) || el.form && el.form!==f.root || el.disabled || el.readOnly || el.getAttribute('aria-readonly')==='true' || el.matches(':disabled') || native.closest(el,'[hidden],[inert],[aria-hidden="true"],[aria-disabled="true"]'))return false;
    if(visible(el))return true;
    // Only Workday's exact native checkbox can use this transparent proxy.
    if(!workday(f.root) || el.getAttribute('data-automation-id')!=='createAccountCheckbox')return false;
    const style=getComputedStyle(el),rect=el.getBoundingClientRect();
    return Number(style.opacity)===0 && style.display!=='none' && style.visibility==='visible' && rect.width>1 && rect.height>1 && visible(native.parent(el));
  }
  function consentControls(f){
    return native.query('input[type="checkbox"]').filter(el=>inAccount(f,el) &&
      !/marketing|newsletter|promotional|job alerts|keep me|updates|offers/i.test(hint(el)) &&
      (workday(f.root) && el.getAttribute('data-automation-id')==='createAccountCheckbox' ||
       /\b(?:agree|consent|accept)\b.*\b(?:terms|privacy|above|account)\b/i.test(hint(el))));
  }
  const matches=(f,expected)=>f && location.protocol==='https:' && location.origin+location.pathname===expected.url &&
    f.identity===expected.identity && f.mode===expected.mode && revision===expected.revision && passwordPolicy(f)===expected.passwordPolicy;
  const validState=f=>f.root.tagName==='FORM' && f.root.checkValidity() && consentControls(f).every(el=>el.checkValidity()) && !native.query('[aria-invalid="true"]').some(el=>inAccount(f,el)&&visible(el));
  async function prepare(value,expected,preview=false){
    ticket=undefined;
    const failure=reason=>{const details={...diagnosis,reason:reason || diagnosis.reason};return {status:reason==='consent_required'?'consent_required':'unavailable',filled:verifiedCount(form()),diagnosis:details};};
    const initial=form();
    if(!initial)return failure();
    if(!matches(initial,expected))return failure('context_changed');
    if(!action(initial) && diagnosis.reason!=='action_unavailable')return failure();
    const result=fill(value,expected),f=form();
    if(!f)return failure();
    if(result.status==='unavailable' || !matches(f,expected))return failure('context_changed');
    if([f.email,f.password,f.confirm].some(el=>el && el.value!==(el===f.email?value.email:value.password)))return failure('credentials_preserved');
    if(!action(f) && diagnosis.reason!=='action_unavailable')return failure();
    const consent=consentControls(f);
    const consentRequired=consent.some(el=>!consentEligible(el,f)) || native.query('input[type="checkbox"]').some(el=>inAccount(f,el) && el.required && !el.checked && !consent.includes(el));
    synthetic=true;
    try{for(const el of consent)if(consentEligible(el,f) && !el.checked){el.click();el.dispatchEvent(new Event('input',{bubbles:true,composed:true}));el.dispatchEvent(new Event('change',{bubbles:true,composed:true}));}}finally{synthetic=false;}
    await new Promise(resolve=>setTimeout(resolve,350));
    const settled=form();
    if(!settled)return failure();
    if(!matches(settled,expected))return failure('context_changed');
    if([settled.email,settled.password,settled.confirm].some(el=>el && el.value!==(el===settled.email?value.email:value.password)))return failure('credentials_preserved');
    if(!action(settled))return diagnosis.reason==='action_unavailable'?{status:'filled',filled:result.filled,diagnosis:{...diagnosis}}:failure();
    if(consentRequired)return failure('consent_required');
    if(!consent.every(el=>el.checked && el.getAttribute('aria-checked')!=='false' && consentEligible(el,settled)))return failure('consent_unsettled');
    if(!validState(settled))return failure('form_invalid');
    if(preview)return {status:'ready',filled:verifiedCount(settled),diagnosis:{...diagnosis}};
    ticket={id:crypto.randomUUID(),href:location.href,expected,button:action(settled),consent,values:[settled.email.value,settled.password.value,settled.confirm?.value]};
    return {status:'ready',filled:verifiedCount(settled),ticket:ticket.id,diagnosis:{...diagnosis}};
  }
  function activate(id){
    const pending=ticket;ticket=undefined;
    const f=form();
    if(!pending || location.href!==pending.href || id!==pending.id || !matches(f,pending.expected) || action(f)!==pending.button ||
       !pending.consent.every(el=>el.checked && el.getAttribute('aria-checked')!=='false' && consentEligible(el,f)) ||
       [f.email.value,f.password.value,f.confirm?.value].some((v,i)=>v!==pending.values[i]) || !validState(f))return {status:'unavailable'};
    // No asynchronous gap remains between the final document/edit guard and click.
    pending.button.click();
    return {status:'activated'};
  }
  globalThis.autoApplyCredentials={active,incomplete,sensitive,redact,describe,fill,prepare,activate,invalidate};
  // Bounded document initialization, including delayed native form rendering.
  // Signals contain no DOM text/values. Background re-probes before any release.
  if(location.protocol==='https:' && /(?:^|\.)(?:myworkdayjobs\.com|greenhouse\.io|lever\.co|smartrecruiters\.com|pinpointhq\.com|applytojob\.com|icims\.com|taleo\.net|successfactors\.com)$/.test(location.hostname)){
    let timer,last='',signals=0;
    const observer=new MutationObserver(records=>{
      if(records.every(record=>record.target.closest?.('#autoapply-sidebar')))return;
      schedule();
    });
    const schedule=()=>{clearTimeout(timer);timer=setTimeout(()=>{
      if(autoEdited || signals>=24)return;
      const state=describe(),signature=JSON.stringify([state.identity,state.revision,state.mode,state.supported,state.actionReady,state.passwordPolicy]);
      if(!state.active || signature===last)return;
      last=signature;signals++;
      chrome.runtime.sendMessage({type:'account-state-changed'}).catch(()=>{});
    },250);};
    observer.observe(document,{subtree:true,childList:true,attributes:true,characterData:true});
    setTimeout(()=>{observer.disconnect();clearTimeout(timer);},60000);
    schedule();
  }
})();
