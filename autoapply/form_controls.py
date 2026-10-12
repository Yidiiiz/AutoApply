"""Shared offline DOM traversal and conservative application-frame boundaries."""
import re
from urllib.parse import urlsplit


CONTROL_SELECTOR = ('input,textarea,select,[role="combobox"],[role="checkbox"],'
                    '[role="radio"],[role="switch"],button[aria-haspopup="listbox"],'
                    '[contenteditable="true"],[contenteditable=""]')
READY_SELECTOR = CONTROL_SELECTOR.replace('input,', 'input:not([type="hidden"]):not([type="password"]):not([type="submit"]):not([type="button"]):not([type="reset"]),')


def application_frame(page, frame):
    from .security import PROVIDERS
    if frame == page.main_frame:
        return True
    if any(re.search(pattern, frame.url, re.I) for _, pattern, _ in PROVIDERS):
        return False
    if frame.url in {'about:blank', 'about:srcdoc'}:
        return frame.parent_frame is not None and application_frame(page, frame.parent_frame)
    left, right = urlsplit(page.url), urlsplit(frame.url)
    same_origin = bool(left.netloc and (left.scheme, left.netloc) == (right.scheme, right.netloc))
    return same_origin or bool(re.search(r'greenhouse|lever|ashby|smartrecruiters|application', frame.url, re.I))


DOM_VISIBILITY_JS = r'''
 const visible = e => {
   if (!e.getClientRects().length || composedClosest(e, '[hidden],[inert],[aria-hidden="true"]')) return false;
   for (let n=e; n; n=n.parentElement || n.getRootNode().host) {
     const s=getComputedStyle(n);
     if (s.display==='none' || s.visibility==='hidden' || s.visibility==='collapse') return false;
   }
   return true;
 };
'''

DOM_JS = r'''
 const deepQuery = (root, selector) => {
   const found = [...root.querySelectorAll(selector)];
   for (const host of root.querySelectorAll('*'))
     if (host.shadowRoot) found.push(...deepQuery(host.shadowRoot, selector));
   return found;
 };
 const composedClosest = (e, selector) => {
   for (let n=e; n; n=n.parentElement || n.getRootNode().host)
     if (n.matches?.(selector)) return n;
   return null;
 };
 __VISIBLE__
 const checked = e => !!e.checked || e.getAttribute('aria-checked') === 'true';
'''.replace('__VISIBLE__', DOM_VISIBILITY_JS)
