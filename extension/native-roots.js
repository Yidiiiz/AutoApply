// Shared isolated-world traversal. Closed roots remain unobservable.
(() => {
  if (globalThis.autoApplyNativeRoots) return;
  const own = '#autoapply-sidebar,.autoapply-ask-ai';
  const parent = node => node?.parentElement || node?.getRootNode?.().host || null;
  function closest(node, selector) {
    for (; node instanceof Element; node = parent(node)) if (node.matches(selector)) return node;
    return null;
  }
  function discover() {
    const roots = [{root:document,host:'document',parent_id:null,depth:0,elements:[]}];
    let visited = 0;
    for (let i=0;i<roots.length;i++) {
      const walker=document.createTreeWalker(roots[i].root,NodeFilter.SHOW_ELEMENT,{
        acceptNode:node=>node.matches(own)?NodeFilter.FILTER_REJECT:NodeFilter.FILTER_ACCEPT
      });
      for (let node=walker.nextNode();node;node=walker.nextNode()) {
        if (++visited>10000) {roots.truncated=true;return roots;}
        roots[i].elements.push(node);
        if (node.shadowRoot) {
          if (roots.length>=128 || roots[i].depth>=32) {roots.truncated=true;continue;}
          roots.push({root:node.shadowRoot,host:node.localName.slice(0,80),parent_id:i,depth:roots[i].depth+1,elements:[]});
        }
      }
    }
    return roots;
  }
  const query = (selector, roots=discover()) => roots.flatMap(r=>r.elements.filter(e=>e.matches(selector)));
  // IDREFs stay in their tree; duplicate IDs in other roots are unrelated.
  const byId = (element,id) => id ? element.getRootNode().getElementById?.(id) || null : null;
  const eventTarget = event => event.composedPath?.()[0] || event.target;
  globalThis.autoApplyNativeRoots={discover,query,parent,closest,byId,eventTarget};
})();
