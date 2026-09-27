import os
import re
import asyncio
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from .models import State
from .freshness import closed_status
from .security import SecurityDetector, classify_message, safe_url
from .retry import SiteError
from .cursor import get_cursor, CursorConfig


def classify_page_condition(security, snapshot):
    if security.blocking:
        return State.MANUAL_REVIEW, security.message
    text = snapshot.get('condition_text', snapshot['text'])
    patterns = [
        (State.ALREADY_APPLIED, r"you (?:have )?already applied|you (?:have )?previously (?:applied|submitted)|an application (?:already )?exists|duplicate application"),
    ]
    for state, pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return state, match[0]
    if snapshot.get('password') or re.search(r"/(?:login|signin|sign-in|authwall)(?:[/?#]|$)", snapshot['url'], re.I):
        return State.AUTH_REQUIRED, "Sign-in requires the configured account; restore the persistent browser session"
    if closed_status(text):
        return State.CLOSED, "LISTING_CLOSED: explicit closure text"
    return None, ""


async def page_condition(page):
    security, snapshot = await SecurityDetector().detect(page)
    return classify_page_condition(security, snapshot)


class PageLease:
    """Ownership of a page in AutoApply's dedicated context, including holds."""
    def __init__(self, browser, page):
        self.browser, self.page = browser, page
        self.preserved = False

    async def release(self, *, preserve=False):
        self.preserved = preserve
        if preserve or self.page.is_closed():
            return
        try:
            cursor = getattr(self.page, '_autoapply_cursor', None)
            if cursor:
                await cursor.cancel()
        finally:
            await self.page.close()


class Browser:
    def __init__(self, config):
        self.config, self.context, self.playwright = config, None, None
        self.start_lock = asyncio.Lock()
        self.page_lock = asyncio.Lock()
        self.leases = {}
        self.pending_closes = set()
        self.observations = {}
        self.max_active_application_tabs = None
        self.tab_limit_violated = False
        self.max_tabs_observed = 0
        self.application_tabs_opened = 0
        self.browser_pid = None
        self.network_policy = None

    def check_tab_limit(self, *, opening=False):
        count = len(self.context.pages) if self.context else 0
        self.max_tabs_observed = max(self.max_tabs_observed, count)
        if self.max_active_application_tabs is not None and (
                self.tab_limit_violated or count + int(opening) > self.max_active_application_tabs):
            self.tab_limit_violated = True
            raise RuntimeError('BATCH_TAB_LIMIT_VIOLATION')

    async def enforce_application_limit(self, limit):
        self.max_active_application_tabs = limit
        if self.context:
            for page in list(self.context.pages):
                if page.url == 'about:blank' and page not in self.leases:
                    await page.close()  # Only the dedicated context's unowned startup page.
        self.check_tab_limit()

    def page_created(self, page):
        self.lease(page)
        self.application_tabs_opened += 1
        try:
            self.check_tab_limit()
        except RuntimeError:
            # A popup must not become another application session.
            task = asyncio.create_task(self.release_page(page))
            self.pending_closes.add(task)
            def finished(task):
                self.pending_closes.discard(task)
                if not task.cancelled():
                    task.exception()  # The limit remains violated even if close failed.
            task.add_done_callback(finished)

    async def start(self):
        async with self.start_lock:
            try:
                await self._start()
            except BaseException:
                await asyncio.shield(self.close())
                raise

    async def _start(self):
        if self.context:
            return
        from playwright.async_api import async_playwright
        local_browsers = self.config.root / "data/private/playwright"
        if local_browsers.exists():
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(local_browsers))
        self.reset_profile_zoom()
        self.playwright = await async_playwright().start()
        geometry = {"viewport":{"width":1440,"height":900}, "device_scale_factor":1} if self.config['browser']['headless'] else {"no_viewport":True}
        self.context = await self.playwright.chromium.launch_persistent_context(
            str(self.config.private / "browser_profile"), headless=self.config["browser"]["headless"],
            channel=self.config["browser"]["channel"], accept_downloads=False,
            service_workers='block' if self.network_policy else 'allow',
            **geometry, args=["--window-size=1460,1000"])
        self.context.set_default_timeout(self.config["browser"]["timeout_ms"])
        if self.network_policy:
            await self.context.route('**/*', self.network_policy.route)
        if self.max_active_application_tabs is not None and self.context.browser:
            session = await self.context.browser.new_browser_cdp_session()
            try:
                processes = await session.send('SystemInfo.getProcessInfo')
                self.browser_pid = next((p['id'] for p in processes['processInfo'] if p['type'] == 'browser'), None)
            finally:
                await session.detach()
        if self.max_active_application_tabs is not None:
            # Persistent contexts create a blank startup page. Never close restored forms.
            for page in list(self.context.pages):
                if page.url == 'about:blank':
                    await page.close()
            self.check_tab_limit()
        self.context.on('page', self.page_created)

    def reset_profile_zoom(self):
        """Reset only zoom preferences in AutoApply's own, not-yet-open profile."""
        path = self.config.private / 'browser_profile/Default/Preferences'
        if not path.exists():
            return
        preferences = json.loads(path.read_text(encoding='utf-8'))
        partition = preferences.setdefault('partition', {})
        prior = {key:partition.get(key) for key in ('per_host_zoom_levels','default_zoom_level')}
        if prior['per_host_zoom_levels'] or prior['default_zoom_level']:
            backup = self.config.private / 'browser-zoom-before.json'
            backup.write_text(json.dumps(prior, indent=2), encoding='utf-8')
            partition.update(per_host_zoom_levels={}, default_zoom_level=0)
            temporary = path.with_suffix('.zoom-tmp')
            temporary.write_text(json.dumps(preferences), encoding='utf-8')
            temporary.replace(path)

    async def new_page(self):
        async with self.page_lock:
            return await self._new_page()

    def lease(self, page):
        if page.context is not self.context:
            raise ValueError('Cannot own a page outside the AutoApply context')
        if page not in self.leases:
            self.leases[page] = PageLease(self, page)
            page.on('close', lambda: self.leases.pop(page, None))
        return self.leases[page]

    async def release_page(self, page, *, preserve=False):
        await self.lease(page).release(preserve=preserve)

    async def _new_page(self):
        await self.start()
        self.check_tab_limit(opening=True)
        page = await self.context.new_page()
        lease = self.lease(page)
        try:
            self.check_tab_limit()
            await self.ensure_desktop(page)
            self.observe(page)
            async def security_guard():
                self.check_tab_limit()
                result, _ = await SecurityDetector().detect(page, self.observation(page))
                return result.blocking
            get_cursor(page, CursorConfig(**self.config.data.get("cursor", {})), security_guard)
            return page
        except BaseException:
            await asyncio.shield(lease.release())
            raise

    async def ensure_desktop(self, page):
        before = await page.evaluate("() => ({width:innerWidth,height:innerHeight,outerWidth,outerHeight,scale:devicePixelRatio})")
        available = await page.evaluate("() => ({width:screen.availWidth,height:screen.availHeight})")
        if not self.config["browser"]["headless"]:
            width, height = min(1460, available['width']), min(1000, available['height'])
            session = await self.context.new_cdp_session(page)
            try:
                window = await session.send("Browser.getWindowForTarget")
                await session.send("Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"windowState": "normal"}})
                await session.send("Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"width":width,"height":height}})
                await page.set_viewport_size({"width": max(800,width-20), "height":max(500,height-100)})
            finally:
                await session.detach()
        else:
            await page.set_viewport_size({"width": 1440, "height": 900})
        after = await page.evaluate("() => ({width:innerWidth,height:innerHeight,outerWidth,outerHeight,scale:devicePixelRatio})")
        self.viewport_diagnostics = {"before":before,"after":after}
        return self.viewport_diagnostics

    def observe(self, page):
        if page in self.observations:
            return
        data = self.observations[page] = {"status": None, "dialogs": [], "messages": [], "dialog_open": False, "network_error": False, "pending_uploads":{}, "upload_failed":False, "http_failures": []}
        page._autoapply_observation = data
        from .uploads import UploadTracker
        tracker = page._autoapply_uploads = UploadTracker()
        def request_started(request):
            tracker.started(request)
            if request in tracker.requests:
                data['pending_uploads'][id(request)] = True
        def request_finished(request):
            tracker.finished(request)
            data['pending_uploads'].pop(id(request), None)
        async def hold_cursor():
            cursor = getattr(page, "_autoapply_cursor", None)
            if cursor:
                try:
                    await cursor.enter_manual_mode()
                except Exception:
                    # The hold remains active even when browser input cleanup fails.
                    cursor._event("cleanup_failed")
        async def response(response):
            request = response.request
            if response.status >= 400:
                try:
                    frame_url = safe_url(request.frame.url)
                    main_frame = request.frame == page.main_frame
                except Exception:
                    frame_url, main_frame = "", False
                data['http_failures'].append(dict(url=safe_url(response.url),
                    method=request.method, resource_type=request.resource_type,
                    status=response.status, frame_url=frame_url, main_frame=main_frame))
                data['http_failures'] = data['http_failures'][-50:]
            await tracker.response(response)
            if id(request) in data['pending_uploads'] and response.status >= 400:
                data['upload_failed'] = True
            relevant = request.resource_type in {"document", "xhr", "fetch"}
            # A read-only background authentication probe is diagnostic evidence,
            # not proof that the application document or submission was rejected.
            # Keep DOM/error-message detection and document/write failures blocking.
            background_auth_probe = (response.status == 401 and request.method == 'GET'
                                     and request.resource_type in {'xhr', 'fetch'})
            if relevant and not background_auth_probe and (response.status >= 400 or request.resource_type == "document" and request.frame == page.main_frame):
                data["status"] = response.status
            # Inspect only bounded, first-party JSON error messages. Never store bodies,
            # headers, request payloads, credentials or challenge-response fields.
            if request.resource_type not in {"xhr", "fetch"} or urlsplit(response.url).hostname != urlsplit(page.url).hostname:
                return
            try:
                headers = response.headers
                length = int(headers.get("content-length", "0"))
                if "application/json" not in headers.get("content-type", "") or not 0 < length <= 65536:
                    return
                payload = await response.json()
                if isinstance(payload, dict):
                    for key in ("error", "message", "error_description"):
                        value = payload.get(key)
                        if isinstance(value, str):
                            detected = classify_message(value)
                            if detected.blocking:
                                data["messages"].append(detected.message)
                                data["messages"] = data["messages"][-10:]
                                await hold_cursor()
            except (ValueError, TypeError):
                pass
            except Exception:
                # Navigation may dispose a response; DOM/HTTP detection still applies.
                pass
        def failed(request):
            tracker.failed(request)
            if request.failure == 'net::ERR_NETWORK_ACCESS_DENIED':
                data['execution_blocked'] = True
            if id(request) in data['pending_uploads']:
                data['upload_failed'] = True
            data['pending_uploads'].pop(id(request), None)
            if request.resource_type in {"document", "xhr", "fetch"}:
                data["network_error"] = True
        async def dialog(dialog):
            # Keep the dialog available for the user; persist only classified security text.
            result = classify_message(dialog.message, prominent=True)
            data["dialogs"].append(result.message or "Unresolved browser dialog")
            data["dialog_open"] = True
            await hold_cursor()
        page.on("response", response)
        page.on('request',request_started)
        page.on('requestfinished',request_finished)
        page.on("requestfailed", failed)
        page.on("dialog", dialog)
        page.on("close", lambda: self.observations.pop(page, None))

    def observation(self, page):
        return self.observations.get(page, {})

    def reset_observation(self, page):
        data = self.observation(page)
        data.update(status=None, dialogs=[], messages=[], dialog_open=False, network_error=False)

    async def uploads_ready(self, page, controls, timeout_seconds=180):
        from .uploads import wait_for_uploads
        return await wait_for_uploads(self, page, controls, timeout_seconds)

    async def change_marker(self, page):
        return await page.evaluate("""() => {
          if (!window.__autoapplyChanges) {
            window.__autoapplyChanges = {count:0};
            new MutationObserver(() => window.__autoapplyChanges.count++).observe(document.documentElement,
              {subtree:true,childList:true,characterData:true,attributes:true});
          }
          return window.__autoapplyChanges.count;
        }""")

    async def wait_for_change(self, page, marker, timeout_ms):
        from playwright.async_api import TimeoutError as PlaywrightTimeout
        try:
            await page.wait_for_function("n => !window.__autoapplyChanges || window.__autoapplyChanges.count !== n",
                                         arg=marker, timeout=max(1, timeout_ms))
        except PlaywrightTimeout:
            pass

    async def navigate(self, page, url):
        from .jobs import canonical_url
        canonical_url(url)
        response = await page.goto(url, wait_until="domcontentloaded")
        await self.normalize_zoom(page)
        if response and response.status in {404, 410}:
            condition, evidence = await page_condition(page)
            if condition in {State.MANUAL_REVIEW, State.AUTH_REQUIRED}:
                return condition, evidence
            return State.CLOSED, f"HTTP {response.status}"
        if response and response.status in {401, 403, 429}:
            return State.MANUAL_REVIEW, f"HTTP {response.status}; authentication, security or rate limit"
        if response and response.status >= 500:
            raise SiteError(f"Transient HTTP {response.status}")
        return await page_condition(page)

    async def normalize_zoom(self, page):
        # Persistent Chromium profiles retain per-site browser zoom. A fixed
        # viewport alone cannot correct a site saved at 33% zoom.
        before = await page.evaluate("() => ({width:innerWidth,height:innerHeight,scale:devicePixelRatio})")
        after = dict(before)
        self.zoom_diagnostics = {"before":before,"after":after}
        if page.viewport_size and abs(after['width']-page.viewport_size['width']) > 3:
            from .scrolling import NavigationError
            raise NavigationError('Browser zoom changed during the session; restore 100% zoom before continuing')
        return self.zoom_diagnostics

    async def screenshot(self, page, folder, name):
        Path(folder).mkdir(parents=True, exist_ok=True)
        masks = [frame.locator('input,textarea,select,[contenteditable],video,canvas,iframe,img') for frame in page.frames]
        await page.screenshot(path=str(Path(folder) / (name + ".png")), full_page=True, mask=masks, timeout=5000)

    async def close(self):
        try:
            if self.pending_closes:
                await asyncio.gather(*self.pending_closes, return_exceptions=True)
            if self.context:
                try:
                    for page in list(self.context.pages):
                        cursor = getattr(page, "_autoapply_cursor", None)
                        if cursor:
                            try:
                                await cursor.cancel()
                            except Exception:
                                pass  # Context close also destroys its input state.
                finally:
                    await self.context.close()
        finally:
            try:
                if self.playwright:
                    await self.playwright.stop()
            finally:
                self.context = self.playwright = None
                self.leases.clear()
                self.observations.clear()
