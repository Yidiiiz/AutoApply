"""Bounded directional scrolling with verified movement and nested containers."""
import asyncio
import logging

log = logging.getLogger("autoapply.scroll")


class NavigationError(RuntimeError):
    category = "ELEMENT_NOT_REACHABLE"


class ScrollFailed(NavigationError):
    category = "SCROLL_FAILED"


# Runs in the target's own frame. No answer values or text enter diagnostics.
CONTAINERS = """e => {
 const d=e.ownerDocument, w=d.defaultView, found=[];
 const scrollable=n => n && /auto|scroll/.test(w.getComputedStyle(n).overflowY) && n.scrollHeight>n.clientHeight+1;
 for(let n=e.parentElement;n;n=n.parentElement) if(scrollable(n)) found.push(n);
 if(!found.length) for(const n of d.querySelectorAll('form,main,[role=main],[role=dialog]'))
   if(n.contains(e) && scrollable(n)) found.push(n);
 if(d.scrollingElement && !found.includes(d.scrollingElement)) found.push(d.scrollingElement);
 return found;
}"""
METRICS = """e => {const r=e.getBoundingClientRect(),w=e.ownerDocument.defaultView;
 return {top:e.scrollTop,height:e.clientHeight,max:e.scrollHeight-e.clientHeight,
 x:Math.max(1,Math.min(w.innerWidth-2,r.left+Math.min(r.width/2,100))),
 y:Math.max(1,Math.min(w.innerHeight-2,r.top+Math.min(r.height/2,100))),
 container:e.tagName.toLowerCase()+(e.id?'#'+e.id:'')};}"""
VISIBILITY = """e => {
 const r=e.getBoundingClientRect(),w=e.ownerDocument.defaultView;
 let top=0,bottom=w.innerHeight,left=0,right=w.innerWidth;
 for(let n=e.parentElement;n;n=n.parentElement){const s=w.getComputedStyle(n),b=n.getBoundingClientRect();
   if(/auto|scroll|hidden|clip/.test(s.overflowY)){top=Math.max(top,b.top);bottom=Math.min(bottom,b.bottom);}
   if(/auto|scroll|hidden|clip/.test(s.overflowX)){left=Math.max(left,b.left);right=Math.min(right,b.right);}}
 const cy=r.top+r.height/2,cx=r.left+r.width/2;
 return {visible:r.width>0 && r.height>0 && cy>=top && cy<bottom && cx>=left && cx<right,
 direction:cy<top?'UP':'DOWN'};
}"""


class ScrollController:
    def __init__(self, page, guard=None, max_steps=24):
        self.page, self.guard, self.max_steps = page, guard, max_steps
        self.events = []

    async def _check(self):
        if self.guard:
            result = self.guard()
            if hasattr(result, "__await__"):
                result = await result
            if result:
                raise NavigationError("Navigation stopped by interaction guard")

    async def scroll_down(self, target=None):
        return await self.scroll("DOWN", target)

    async def scroll_up(self, target=None):
        return await self.scroll("UP", target)

    async def scroll(self, direction, target=None):
        if direction not in {"UP", "DOWN"}:
            raise ValueError("Use UP or DOWN")
        await self._check()
        target = target if target is not None else self.page.locator('form,main,[role=main],body').first
        if hasattr(target, "count") and await target.count() != 1:
            raise NavigationError("Scroll target missing or ambiguous")
        array = await target.evaluate_handle(CONTAINERS)
        try:
            containers = await array.get_properties()
            for handle in containers.values():
                for strategy in ("wheel", "container", "pagedown"):
                    await self._check()
                    before = await handle.evaluate(METRICS)
                    delta = max(80, min(before["height"], 900)*.75) * (1 if direction == "DOWN" else -1)
                    if strategy == "wheel":
                        box = await handle.as_element().bounding_box()
                        if box:
                            viewport = await self.page.evaluate("() => [innerWidth,innerHeight]")
                            x = max(1,min(viewport[0]-2,box['x']+min(box['width']/2,100)))
                            y = max(1,min(viewport[1]-2,box['y']+min(box['height']/2,100)))
                            await self.page.mouse.move(x,y)
                            cursor = getattr(self.page, "_autoapply_cursor", None)
                            if cursor:
                                from .cursor.types import Point
                                cursor.current_position, cursor.position_known = Point(x,y), True
                        await self.page.mouse.wheel(0, delta)
                    elif strategy == "container":
                        await handle.evaluate("(e,d) => e.scrollBy({top:d,behavior:'instant'})", delta)
                    else:
                        await handle.evaluate("e => {if(!e.hasAttribute('tabindex')) {e.setAttribute('tabindex','-1'); e.dataset.autoapplyScrollFocus='1'} e.focus({preventScroll:true})}")
                        await self.page.keyboard.press("PageDown" if direction == "DOWN" else "PageUp")
                        await handle.evaluate("e => {if(e.dataset.autoapplyScrollFocus){e.removeAttribute('tabindex');delete e.dataset.autoapplyScrollFocus}}")
                    # Wheel/PageDown delivery is asynchronous. Finish when the
                    # owned container moves, with the former 80ms bound.
                    await handle.evaluate('''(e, before) => new Promise(resolve => {
                      const end=performance.now()+80;
                      setTimeout(resolve,80);
                      const sample=()=>Math.abs(e.scrollTop-before)>.5||performance.now()>=end
                        ? resolve() : requestAnimationFrame(sample);
                      sample();
                    })''', before['top'])
                    await self._check()
                    after = await handle.evaluate(METRICS)
                    event = dict(container=before['container'], direction=direction, strategy=strategy,
                                 before=before['top'], after=after['top'], changed=abs(after['top']-before['top'])>.5)
                    self.events.append(event)
                    self.events = self.events[-200:]
                    log.info("[SCROLL] %s", event)
                    if event['changed']:
                        return True
            return False
        finally:
            for handle in locals().get('containers', {}).values():
                await handle.dispose()
            await array.dispose()

    async def ensure_visible(self, target):
        if hasattr(target, "count") and await target.count() != 1:
            raise NavigationError("Target missing or ambiguous; bounded search stopped")
        for _ in range(self.max_steps):
            await self._check()
            visible = await target.evaluate(VISIBILITY)
            if visible['visible']:
                return
            if not await self.scroll(visible['direction'], target):
                raise ScrollFailed("No movement after alternate scroll strategies")
        raise NavigationError("Target unreachable within bounded scroll search")
