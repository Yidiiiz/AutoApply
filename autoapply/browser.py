import os
import re
import asyncio
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

from .models import State
from .freshness import closed_status
from .security import SecurityDetector, classify_message, safe_url
from .retry import SiteError
from .cursor import get_cursor, CursorConfig
from playwright._impl._errors import TargetClosedError
from .browser_resources import DedicatedResources, BrowserOwnershipError

MAX_BROWSER_STARTS = 3
STARTUP_ATTEMPT_TIMEOUT = 45


async def resource_call(operation, *args):
    # OS inspection runs off-loop. Cancellation must wait for this bounded call
    # before cleanup releases a profile lock or reads its ownership inventory.
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class BrowserStartFailed(RuntimeError):
    """Affirmative pre-application startup failure, never delivery uncertainty."""


def recoverable_start_error(exc):
    return isinstance(exc, (TargetClosedError, TimeoutError, subprocess.TimeoutExpired)) or any(
        text in str(exc).lower() for text in (
            'target.createtarget', 'failed to open a new tab', 'target page, context or browser has been closed',
            'browser closed', 'browser has been closed', 'context closed', 'context has been closed'))


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
        if self.page.is_closed():
            self.browser.leases.pop(self.page, None)
            return
        if preserve:
            return
        try:
            cursor = getattr(self.page, '_autoapply_cursor', None)
            if cursor:
                try:
                    await cursor.cancel()
                except TargetClosedError:
                    pass
        finally:
            closed = False
            try:
                await self.page.close()
                closed = True
            except TargetClosedError:
                closed = True  # A concurrent context/browser close released it.
            finally:
                if closed or self.page.is_closed():
                    self.browser.leases.pop(self.page, None)


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
        self.fill_invariant = None
        self._fill_route_installed = False
        self.unexpected_popup_violations = 0
        self.resources = None
        self.health_pages = set()
        self.startup_pages = set()
        self.health_ready = False
        self.startup_telemetry = dict(browser_start_count=0, browser_restart_count=0,
                                      browser_health_probe_failures=0)
        self.startup_diagnostics = []
        self.startup_guard = None

    @property
    def application_pages(self):
        diagnostic = self.startup_pages | self.health_pages
        return [p for p in self.context.pages if not any(p is d for d in diagnostic)] if self.context else []

    def check_tab_limit(self, *, opening=False):
        count = len(self.application_pages)
        self.max_tabs_observed = max(self.max_tabs_observed, count)
        if self.max_active_application_tabs is not None and (
                self.tab_limit_violated or count + int(opening) > self.max_active_application_tabs):
            self.tab_limit_violated = True
            raise RuntimeError('BATCH_TAB_LIMIT_VIOLATION')

    async def enforce_application_limit(self, limit):
        self.max_active_application_tabs = limit
        if self.context:
            if self.fill_invariant and not self._fill_route_installed:
                await self.fill_invariant.install(self.context)
                self._fill_route_installed = True
            for page in list(self.context.pages):
                if page.url == 'about:blank' and page not in self.leases and page not in self.startup_pages:
                    await page.close()  # Only the dedicated context's unowned startup page.
        self.check_tab_limit()

    def page_created(self, page):
        self.lease(page)
        self.application_tabs_opened += 1
        try:
            self.check_tab_limit()
        except RuntimeError:
            # A popup must not become another application session.
            self.unexpected_popup_violations += 1
            task = asyncio.create_task(self.release_page(page))
            self.pending_closes.add(task)
            def finished(task):
                self.pending_closes.discard(task)
                if not task.cancelled():
                    task.exception()  # The limit remains violated even if close failed.
            task.add_done_callback(finished)

    async def start(self):
        async with self.start_lock:
            return await self._startup_recovery(self._start)

    def startup_authorized(self):
        # A later fresh application reservation can grant a new startup scope.
        # Without that explicit Engine guard, once any application page has existed
        # this browser must never infer that closed pages mean untouched delivery.
        return self.startup_guard() if self.startup_guard is not None else self.application_tabs_opened == 0

    def startup_untouched(self, opened):
        return (self.application_tabs_opened == opened and not self.leases
                and not self.observations and not self.tab_limit_violated
                and not self.application_pages
                and self.startup_authorized())

    async def _startup_recovery(self, operation):
        # This scope ends as soon as an application page exists. Neither navigation
        # nor any applicant action is ever replayed by this loop.
        opened = self.application_tabs_opened
        baseline = self.startup_telemetry['browser_start_count']
        for launch in range(MAX_BROWSER_STARTS):
            try:
                async with asyncio.timeout(STARTUP_ATTEMPT_TIMEOUT):
                    return await operation()
            except BaseException as exc:
                if self.tab_limit_violated:
                    raise
                untouched = self.startup_untouched(opened)
                # Startup diagnostic pages are owned separately, and can be disposed
                # even if a probe or initial target failed partway through startup.
                if (self.resources and not self.health_ready
                        and self.application_tabs_opened == opened and not self.leases):
                    untouched = self.startup_authorized()
                if not untouched:
                    if not self.health_ready and self.application_tabs_opened == opened and not self.leases:
                        await asyncio.shield(self.close())
                    raise
                diagnostic = dict(launch=launch + 1, error_type=type(exc).__name__,
                                  target_creation_failure='target.createtarget' in str(exc).lower(),
                                  profile=dict(self.resources.diagnostics) if self.resources else {})
                self.startup_diagnostics.append(diagnostic)
                try:
                    await asyncio.shield(self.close())
                except Exception as cleanup:
                    diagnostic['cleanup_error'] = type(cleanup).__name__
                    raise BrowserStartFailed('Browser cleanup could not be verified; restart disabled') from cleanup
                if not isinstance(exc, Exception):
                    raise
                if (not recoverable_start_error(exc) or isinstance(exc, BrowserOwnershipError)
                        or launch == MAX_BROWSER_STARTS - 1
                        or self.startup_telemetry['browser_start_count'] - baseline >= MAX_BROWSER_STARTS):
                    raise BrowserStartFailed('Dedicated browser startup failed before application work') from exc
                self.startup_telemetry['browser_restart_count'] += 1
                # Two four-second settling intervals preserve the default eight-
                # second grace even when three target failures return immediately.
                await asyncio.sleep(4)

    async def _start(self):
        if self.context:
            return
        if self.resources:
            raise BrowserOwnershipError('Previous dedicated cleanup remains unresolved')
        from playwright.async_api import async_playwright
        local_browsers = self.config.root / "data/private/playwright"
        if local_browsers.exists():
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(local_browsers))
        self.resources = DedicatedResources(self.config.private)
        self.resources.inventory_timeout_seconds = self.config['browser'].get('process_inventory_timeout_seconds', 30)
        await resource_call(self.resources.acquire)
        self.startup_telemetry['browser_start_count'] += 1
        self.reset_profile_zoom()
        self.playwright = await async_playwright().start()
        await resource_call(self.resources.capture, self.driver_pid())
        geometry = {"viewport":{"width":1440,"height":900}, "device_scale_factor":1} if self.config['browser']['headless'] else {"no_viewport":True}
        self.context = await self.playwright.chromium.launch_persistent_context(
            str(self.config.private / "browser_profile"), headless=self.config["browser"]["headless"],
            channel=self.config["browser"]["channel"], accept_downloads=False,
            service_workers='block' if self.network_policy or self.fill_invariant else 'allow',
            timeout=30000, **geometry, args=["--window-size=1460,1000", self.resources.marker])
        self.context.set_default_timeout(self.config["browser"]["timeout_ms"])
        if self.network_policy:
            await self.context.route('**/*', self.network_policy.route)
        if self.fill_invariant:
            await self.fill_invariant.install(self.context)
            self._fill_route_installed = True
        if self.max_active_application_tabs is not None and self.context.browser:
            session = await self.context.browser.new_browser_cdp_session()
            try:
                processes = await session.send('SystemInfo.getProcessInfo')
                self.browser_pid = next((p['id'] for p in processes['processInfo'] if p['type'] == 'browser'), None)
            finally:
                await session.detach()
        await resource_call(self.resources.capture, self.driver_pid())
        await self.health_probe()
        self.context.on('page', self.page_created)

    def driver_pid(self):
        # Playwright has no public driver PID API. Absence is fail-safe: only the
        # token/profile-proven Chromium tree can then be reaped by the OS fallback.
        try:
            return self.playwright._impl_obj._connection._transport._proc.pid
        except AttributeError:
            return None

    async def health_probe(self):
        try:
            async with asyncio.timeout(10):
                if self.leases or any(p.url != 'about:blank' for p in self.context.pages):
                    raise BrowserOwnershipError('Unexpected restored target; startup recovery disabled')
                # Prove target creation before disposing Chromium's initial blank.
                initial = list(self.context.pages)
                # On headed Chromium closing the final window can leave a live
                # context that rejects Target.createTarget. Keep its original
                # owned blank only until the actual application page exists.
                self.startup_pages = set(initial[:1]) if not self.config['browser']['headless'] else set()
                page = await self.context.new_page()
                self.health_pages.add(page)
                if await page.evaluate('() => location.href') != 'about:blank':
                    raise BrowserOwnershipError('Health target is not blank')
                for blank in initial:
                    if blank not in self.startup_pages:
                        await blank.close()
                await page.close()
                self.health_pages.discard(page)
                if self.application_pages or self.leases or set(self.context.pages) != self.startup_pages:
                    raise BrowserOwnershipError('Startup targets or leases remain after probe')
                self.health_ready = True
        except BaseException:
            self.startup_telemetry['browser_health_probe_failures'] += 1
            raise

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
            async with self.start_lock:
                return await self._startup_recovery(self._new_page)

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
        await self._start()
        self.check_tab_limit(opening=True)
        prior_leases = set(self.leases)
        try:
            page = await self.context.new_page()
        except BaseException:
            # A CDP target may be observed before the creation/detach call fails.
            # Its page event already established ownership; never leak that page.
            for created in set(self.leases) - prior_leases:
                await asyncio.shield(self.leases[created].release())
            raise
        from .patience import PatiencePolicy
        page._autoapply_patience_policy = PatiencePolicy(**self.config.data.get('browser', {}).get('patience', {}))
        lease = self.lease(page)
        try:
            for startup in list(self.startup_pages):
                await startup.close()
                self.startup_pages.discard(startup)
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
        from .patience import page_policy
        timeout = max(self.config['browser']['timeout_ms'], page_policy(page).timeout * 1000)
        response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
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
        resource_failure = failure = None
        had_resources = self.resources is not None
        if self.resources:
            try:
                await resource_call(self.resources.capture, self.driver_pid())
            except Exception as exc:
                resource_failure = exc
        try:
            if self.pending_closes:
                results = await asyncio.wait_for(
                    asyncio.gather(*self.pending_closes, return_exceptions=True), 10)
                self.pending_closes.clear()
                failure = next((r for r in results if isinstance(r, BaseException) and not isinstance(r, TargetClosedError)), None)
            if self.context:
                try:
                    for page in list(self.context.pages):
                        cursor = getattr(page, "_autoapply_cursor", None)
                        if cursor:
                            try:
                                await asyncio.wait_for(cursor.cancel(), 10)
                            except TargetClosedError:
                                pass  # Closed targets have no remaining input state.
                            except Exception as exc:
                                failure = failure or exc
                finally:
                    try:
                        await asyncio.wait_for(self.context.close(), 10)
                    except TargetClosedError:
                        pass  # Normal teardown may race an already closed target.
        except Exception as exc:
            failure = failure or exc
        finally:
            try:
                if self.playwright:
                    try:
                        await asyncio.wait_for(self.playwright.stop(), 10)
                    except TargetClosedError:
                        pass
                    except Exception as exc:
                        failure = failure or exc
            finally:
                self._fill_route_installed = False
                self.context = self.playwright = None
                self.leases.clear()
                self.observations.clear()
                self.health_pages.clear()
                self.startup_pages.clear()
                self.health_ready = False
                self.browser_pid = None
                if self.resources:
                    try:
                        await resource_call(self.resources.reap)
                        if resource_failure:
                            raise resource_failure
                    finally:
                        self.resources.release()
                    self.resources = None
        if failure:
            if not had_resources:
                raise failure
            # Context/driver close failed, but the OS fallback just verified all
            # captured owned processes exited. This is a clean recovery boundary.
            self.startup_diagnostics.append({'cleanup_recovered': type(failure).__name__})
