"""Normalize explicit checkbox choice groups without weakening other validation."""
CHECKBOX_GROUP_JS = r'''e => {
 const root=e.closest('fieldset,[role=group],[data-field],.application-question');
 if(!root) return null;
 const controls=[...root.querySelectorAll('input[type=checkbox]')].filter(n=>!n.disabled);
 if(controls.length<2) return null;
 const heading=root.querySelector('legend,[data-question],.label')?.textContent || root.getAttribute('aria-label') || '';
 const bound=heading.match(/(?:select|choose)\s+(?:up to|at most)\s+(\d+)/i);
 if(!bound && !/(?:select|choose)\s+(?:all that apply|one or more)/i.test(heading)) return null;
 const min=root.getAttribute('aria-required')==='true'||controls.some(n=>n.required)?1:0;
 return {root,controls,min,max:bound?Number(bound[1]):controls.length,heading};
}'''

VALIDATION_JS = r'''() => {
 const groupFor=__GROUP__;
 const ignored=new Set(), groups=new Set(), issues=[];
 for(const e of document.querySelectorAll('input[type=checkbox]')) {
  const group=groupFor(e);
  if(!group || groups.has(group.root)) continue;
  groups.add(group.root);
  const count=group.controls.filter(n=>n.checked).length;
  if(count<group.min || count>group.max) issues.push('Checkbox group selection count outside allowed range: '+group.heading);
  for(const control of group.controls) if(control.validity.valueMissing && !control.validity.customError && control.getAttribute('aria-invalid')!=='true') ignored.add(control);
 }
 for(const e of document.querySelectorAll('input:invalid,select:invalid,textarea:invalid,[aria-invalid="true"]')) {
  if(!ignored.has(e)) issues.push('Invalid required form control');
 }
 return issues;
}'''.replace('__GROUP__', CHECKBOX_GROUP_JS)
