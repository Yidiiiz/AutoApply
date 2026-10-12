// Persist click counts over local fixture link navigation. No input is supplied.
window.audit = JSON.parse(sessionStorage.getItem('entry-audit') ||
  '{"accept":0,"submit":0,"input":0,"clicks":[]}');
document.addEventListener('click', event => {
  const control = event.target.closest('button,a,input[type=submit]');
  if (!control) return;
  const label = (control.getAttribute('aria-label') || control.textContent || control.value).trim();
  audit.clicks.push(label);
  if (/^accept/i.test(label)) audit.accept++;
  if (/submit/i.test(label) || control.type === 'submit') audit.submit++;
  sessionStorage.setItem('entry-audit', JSON.stringify(audit));
}, true);
document.addEventListener('input', () => { audit.input++; }, true);
document.addEventListener('submit', event => { event.preventDefault(); }, true);
function enterForm() {
  document.querySelector('#shell')?.remove();
  document.querySelector('#chooser')?.remove();
  document.querySelector('#application').hidden = false;
}
