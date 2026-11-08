"""T069 focused actual MV3 Chrome checks, with no server or applicant imports.

Direct: .venv/Scripts/python.exe tests/test_extension_entry.py
All employer URLs below are fulfilled offline. Public observations are documented
separately; synthetic blocked/shadow/Keysight cases do not claim live form access.
"""
import json
import os
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/extension/t069"
SR = "https://jobs.smartrecruiters.com/Example/123456789"
ONECLICK = "https://jobs.smartrecruiters.com/oneclick-ui/company/Example/publication/00000000-0000-4000-8000-000000000001"
KEYSIGHT = "https://jobs.keysight.com/external/jobs/12345"
GREENHOUSE = "https://job-boards.greenhouse.io/example/jobs/12345"
WORKDAY = "https://example.myworkdayjobs.com/jobs/job/123"
OTHER = "https://example.test/contact"


def test_t069_offline_chrome(tmp_path):
    checks = []

    def check(value, name):
        assert value, name
        checks.append(name)
        print("PASS", name, flush=True)

    with sync_playwright() as playwright:
        # Same full Chromium installation as test_extension.py, never private data.
        chrome_root = Path(os.environ["LOCALAPPDATA"]) / "ms-playwright"
        executable = next(iter(sorted(chrome_root.glob("chromium-*/chrome-win64/chrome.exe"), reverse=True)))
        extension = Path(os.environ.get("T069_EXTENSION_DIR", ROOT / "extension"))
        context = playwright.chromium.launch_persistent_context(
            str(tmp_path / "chrome-profile"), executable_path=str(executable), headless=True,
            args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}",
                  "--disable-background-networking", "--disable-component-update",
                  "--host-resolver-rules=MAP * ~NOTFOUND"],
        )
        traffic = []
        fixture = {"html": "<main><h1>Job application</h1><form><label>First name<input id=name></label><button type=submit>Submit application</button></form></main>"}

        def route_request(route):
            request = route.request
            traffic.append((request.method, request.url))
            if request.method == "GET" and request.is_navigation_request():
                route.fulfill(status=200, content_type="text/html", body=fixture["html"])
            else:
                route.abort()

        context.route("**/*", route_request)
        try:
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
            worker.evaluate("""async()=>{
              for(let i=0; i<300 && !globalThis.autoApplySidebar; i++)await new Promise(r=>setTimeout(r,10));
              await chrome.storage.session.set({chromeRunPaused:true});
              await chrome.storage.local.set({server:'http://127.0.0.1:1',key:'offline-fixture'});
              globalThis.requests=[];globalThis.runItems=[];
              globalThis.fetch=async(url,options={})=>{
                const path=new URL(url).pathname;requests.push({path,method:options.method,payload:options.body?JSON.parse(options.body):null});
                let result={ok:true};
                if(path==='/ext/run/status')result={items:runItems,poll_age_seconds:0};
                if(path==='/ext/run/next')result={done:true};
                if(path==='/ext/ping')result={ok:true,auto_advance:false};
                if(path==='/ext/answers')result={answers:[]};
                if(path==='/ext/snapshot')result={ok:true,summary:'No form fields detected',findings:[]};
                return new Response(JSON.stringify(result),{status:200,headers:{'Content-Type':'application/json'}});
              };
            }""")
            page = context.new_page()
            extension_id = worker.url.split("/")[2]
            panel = context.new_page()
            panel.goto(f"chrome-extension://{extension_id}/sidepanel.html")

            def wait(predicate, name):
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    if predicate():
                        return
                    page.wait_for_timeout(30)
                raise AssertionError(name)

            def load(url, html):
                fixture["html"] = html
                page.goto(url)
                tab_id = worker.evaluate("async url=>(await chrome.tabs.query({})).find(t=>t.url===url).id", url)
                wait(lambda: worker.evaluate("""id=>chrome.scripting.executeScript({target:{tabId:id},func:()=>!!globalThis.autoApplyContent}).then(r=>r[0]?.result)""", tab_id), "content initialized")
                return tab_id

            def api(tab_id, method, *args):
                return worker.evaluate("""async({id,method,args})=>(await chrome.scripting.executeScript({target:{tabId:id},func:async(method,args)=>globalThis.autoApplyContent[method](...args),args:[method,args]}))[0].result""", dict(id=tab_id, method=method, args=args))

            def state():
                return page.locator("#autoapply-sidebar").get_attribute("data-state")

            def show(tab_id, mode):
                worker.evaluate("({id,mode})=>autoApplySidebar(id,mode)", dict(id=tab_id, mode=mode))

            tab_id = load(GREENHOUSE, fixture["html"])
            page.evaluate("document.documentElement.append(document.createElement('div'))")
            page.wait_for_timeout(80)
            check(page.locator('#autoapply-sidebar').count() == 0, "root mutations never create an initial sidebar")
            show(tab_id, "closed")
            # Hold the actual onUpdated/sidebar-ready restore's old storage read.
            worker.evaluate("""()=>{
              globalThis.originalGet=chrome.storage.session.get.bind(chrome.storage.session);
              globalThis.holdSidebar=true;globalThis.restoreHeld=false;
              chrome.storage.session.get=async keys=>{
                const snapshot=await originalGet(keys);
                if(holdSidebar && Array.isArray(keys) && keys.includes('sidebarTabs') && (keys.includes('chromeRun') || keys.includes('autofillTabs'))){
                  holdSidebar=false;restoreHeld=true;await new Promise(r=>globalThis.releaseRestore=r);
                }
                return snapshot;
              };
            }""")
            # Real content-to-worker ready message, not a mocked sidebar function.
            worker.evaluate("""id=>{chrome.scripting.executeScript({target:{tabId:id},func:()=>{chrome.runtime.sendMessage({type:'sidebar-ready'});}});return true;}""", tab_id)
            wait(lambda: worker.evaluate("restoreHeld"), "ready restore held")
            show(tab_id, "open")
            check(state() == "open", "explicit open before delayed ready restore")
            worker.evaluate("releaseRestore()")
            page.wait_for_timeout(200)
            check(state() == "open", "delayed closed restore cannot close explicit open")
            # Real navigation onUpdated restore with the same race.
            show(tab_id, "closed")
            worker.evaluate("holdSidebar=true;restoreHeld=false")
            page.reload()
            wait(lambda: worker.evaluate("restoreHeld"), "navigation restore held")
            show(tab_id, "open")
            worker.evaluate("releaseRestore()")
            page.wait_for_timeout(200)
            check(state() == "open", "navigation restore cannot undo open")
            worker.evaluate("()=>{chrome.storage.session.get=originalGet;}")
            # Ready may also arrive after open starts but before it persists.
            show(tab_id, "closed")
            worker.evaluate("""id=>{
              globalThis.originalExecute=chrome.scripting.executeScript.bind(chrome.scripting);
              globalThis.holdExecute=true;globalThis.executeHeld=false;
              chrome.scripting.executeScript=async args=>{
                if(holdExecute && args.func?.toString().includes('autoApplyContent?.sidebar')){
                  holdExecute=false;executeHeld=true;await new Promise(r=>globalThis.releaseExecute=r);
                }
                return originalExecute(args);
              };
              globalThis.pendingOpen=autoApplySidebar(id,'open');return true;
            }""", tab_id)
            wait(lambda: worker.evaluate("executeHeld"), "explicit injection held")
            worker.evaluate("""id=>{chrome.scripting.executeScript({target:{tabId:id},func:()=>{chrome.runtime.sendMessage({type:'sidebar-ready'});}});return true;}""", tab_id)
            page.wait_for_timeout(100)
            worker.evaluate("releaseExecute();pendingOpen")
            page.wait_for_timeout(150)
            check(state() == "open", "ready during open uses latest intent before storage persists")
            worker.evaluate("()=>{chrome.scripting.executeScript=originalExecute;}")
            for mode in ("minimized", "closed", "open"):
                show(tab_id, mode)
                page.reload()
                wait(lambda: page.locator("#autoapply-sidebar").count() and state() == mode, f"restore {mode}")
                check(page.locator("#autoapply-sidebar").count() == 1, f"one sidebar and persisted {mode}")
            # Older open/close commands and repeated injections cannot duplicate hosts.
            worker.evaluate("""async id=>{await Promise.all([autoApplySidebar(id,'closed'),autoApplySidebar(id,'minimized'),autoApplySidebar(id,'open')]);await chrome.scripting.executeScript({target:{tabId:id},files:['content.js']});}""", tab_id)
            check(state() == "open" and page.locator("#autoapply-sidebar").count() == 1, "newest explicit intent wins with one host")
            # Content-origin minimized dismiss/restore uses the same ordering path.
            show(tab_id, "minimized")
            page.get_by_role("button", name="Restore AutoApply sidebar").click()
            wait(lambda: state() == "open", "restore flag open")
            show(tab_id, "minimized")
            page.get_by_role("button", name="Dismiss minimized AutoApply").click()
            page.wait_for_timeout(120)
            page.reload()
            wait(lambda: page.locator("#autoapply-sidebar").count() and state() == "closed", "dismiss persisted")
            check(True, "content close and restore persist")

            hydration_html = (FIXTURES / "hydration.html").read_text(encoding="utf-8")
            tab_id = load(GREENHOUSE, hydration_html)
            show(tab_id, "open")
            page.evaluate("rememberSidebar()")
            for mode in ("open", "minimized"):
                show(tab_id, mode)
                page.evaluate("savedSidebar.remove()")
                wait(lambda: page.locator('#autoapply-sidebar').count() == 1, f"reattach {mode}")
                check(state() == mode and page.evaluate("sameSidebar()"), f"detached {mode} automatically reuses host frame and position")
                page.evaluate("hydrateRoot()")
                wait(lambda: page.locator('#autoapply-sidebar').count() == 1, f"hydrate {mode}")
                check(state() == mode and page.evaluate("sameSidebar()"), f"root hydration preserves {mode} host and shadow state")
            # The watcher must retarget after replacing the root, too.
            page.evaluate("savedSidebar.remove()")
            wait(lambda: page.locator('#autoapply-sidebar').count() == 1, "detach after root replacement")
            check(state() == "minimized" and page.evaluate("sameSidebar()"), "observer follows replacement document root")
            show(tab_id, "closed")
            page.evaluate("savedSidebar.remove();hydrateRoot()")
            page.wait_for_timeout(100)
            check(page.locator('#autoapply-sidebar').count() == 0, "closed detached host stays absent through hydration")
            show(tab_id, "open")
            check(page.evaluate("sameSidebar()") and state() == "open", "explicit reopen reuses detached closed host")
            # Drain the mutation callback so a recovery is queued, then close
            # before its timer fires. No storage/worker scheduling is involved.
            result = worker.evaluate("""async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
              const host=document.querySelector('#autoapply-sidebar');host.remove();await Promise.resolve();
              autoApplyContent.sidebar('closed');await new Promise(r=>setTimeout(r,40));
              return !host.isConnected && host.dataset.state==='closed' && !document.querySelector('#autoapply-sidebar');
            }}))[0].result""", tab_id)
            check(result, "new close cancels queued detached-host recovery")
            show(tab_id, "open")
            result = worker.evaluate("""async id=>(await chrome.scripting.executeScript({target:{tabId:id},func:async()=>{
              const host=document.querySelector('#autoapply-sidebar');host.remove();await Promise.resolve();
              autoApplyContent.sidebar('minimized');autoApplyContent.sidebar('closed');autoApplyContent.sidebar('open');
              await new Promise(r=>setTimeout(r,40));
              return host.isConnected && host.dataset.state==='open' && document.querySelectorAll('#autoapply-sidebar').length===1;
            }}))[0].result""", tab_id)
            check(result and page.evaluate("sameSidebar()"), "new open wins pending repair minimize close race with one original host")
            show(tab_id, "minimized")
            page.get_by_role("button", name="Dismiss minimized AutoApply").click()
            wait(lambda: state() == "closed", "dismiss before detach")
            page.evaluate("savedSidebar.remove();hydrateRoot()")
            page.wait_for_timeout(100)
            check(page.locator('#autoapply-sidebar').count() == 0, "user-dismissed sidebar never resurrects on removal")
            show(tab_id, "open")
            # Unrelated application churn must cause no sidebar moves/reloads.
            page.evaluate("""()=>{
              window.sidebarAdds=0;window.hostObserver=new MutationObserver(records=>{
                for(const r of records)for(const n of r.addedNodes)if(n.id==='autoapply-sidebar')sidebarAdds++;
              });hostObserver.observe(document.documentElement,{childList:true});
              for(let i=0;i<100;i++){const n=document.createElement('div');document.body.append(n);n.remove();}
            }""")
            page.wait_for_timeout(80)
            check(page.evaluate("sidebarAdds===0 && sameSidebar()"), "application subtree churn never moves or recreates sidebar")
            # A hostile renderer cannot cause an unbounded observer feedback loop.
            page.evaluate("""()=>{
              window.removals=0;hostObserver.disconnect();
              window.remover=new MutationObserver(()=>{const host=document.querySelector('#autoapply-sidebar');if(host){removals++;host.remove();}});
              remover.observe(document.documentElement,{childList:true});savedSidebar.remove();
            }""")
            page.wait_for_timeout(150)
            removals = page.evaluate("removals")
            page.wait_for_timeout(80)
            check(0 < removals <= 5 and page.evaluate("removals") == removals, "repeated renderer removals stop at bounded recovery without polling")
            page.evaluate("remover.disconnect()")
            show(tab_id, "open")
            page.evaluate("savedSidebar.remove()")
            wait(lambda: page.locator('#autoapply-sidebar').count() == 1, "rearm recovery")
            check(state() == "open" and page.evaluate("sameSidebar()"), "explicit reopen rearms bounded recovery using original host")

            sr_html = (FIXTURES / "smartrecruiters.html").read_text(encoding="utf-8")
            tab_id = load(SR, sr_html)
            structure = api(tab_id, "pageStructure")
            actions = [n for n in structure["nodes"] if n.get("caption") == "I'm interested"]
            canonical = [n for n in actions if n["target"].get("origin") == "https://jobs.smartrecruiters.com"]
            check(len(actions) == 5 and len(canonical) == 3 and len({json.dumps(n["target"]) for n in canonical}) == 1, "observed repeated Interested anchors captured with sanitized target")
            check(api(tab_id, "enterApplication"), "SmartRecruiters Interested enters exact OneClick URL")
            page.wait_for_url(ONECLICK + "?dcr_ci=Example")

            # Synthetic guards; Keysight public DOM was blocked, not captured.
            cases = [
                (KEYSIGHT, '<button type=button role=link onclick="clicks++">Apply now</button>', True),
                (KEYSIGHT, '<button type=button role=link onclick="clicks++">Apply</button>', True),
                (KEYSIGHT, '<button type=button onclick="clicks++">Apply for this job</button>', True),
                (KEYSIGHT, '<form><button onclick="clicks++">Apply now</button></form>', False),
                (KEYSIGHT, '<div role=form><button type=button onclick="clicks++">Apply</button></div>', False),
                (KEYSIGHT, '<button type=submit onclick="clicks++">Apply</button>', False),
                (KEYSIGHT, '<button type=button onclick="clicks++">Submit application</button>', False),
                (KEYSIGHT, '<input aria-label="First name"><button type=button onclick="clicks++">Apply</button>', False),
                (KEYSIGHT, '<button type=button onclick="clicks++">Apply</button><button type=button onclick="clicks++">Apply now</button>', False),
                (OTHER, '<button type=button onclick="clicks++">Apply now</button>', False),
                (OTHER, '<a href="/apply">I\'m interested</a>', False),
                (SR, '<a href="https://example.test/oauth">I\'m interested</a>', False),
                (SR, sr_html.replace('id="st-apply" href="' + ONECLICK, 'id="st-apply" href="' + ONECLICK[:-1] + '2'), False),
                (SR, '<a href="https://jobs.smartrecruiters.com/oneclick-ui/company/Other/publication/00000000-0000-4000-8000-000000000001">I\'m interested</a>', False),
                (SR, '<a href="https://person:secret@jobs.smartrecruiters.com/oneclick-ui/company/Example/publication/00000000-0000-4000-8000-000000000001">I\'m interested</a>', False),
                (WORKDAY, '<main data-automation-id=jobPostingPage><button type=button onclick="clicks++">Continue application</button></main>', True),
                (WORKDAY, '<main data-automation-id=jobPostingPage><button type=button onclick="clicks++">Continue</button></main>', False),
                (WORKDAY, '<div data-automation-id=progressBar><span data-automation-id=progressBarActiveStep>Review</span></div><button type=button onclick="clicks++">Continue application</button>', False),
                (WORKDAY, '<h1>Sign in</h1><input type=email><input type=password><button type=button onclick="clicks++">Apply</button>', False),
                (WORKDAY, '<button type=button data-automation-id=applyManually onclick="clicks++">Apply Manually</button>', True),
            ]
            for index, (url, html, expected) in enumerate(cases):
                tab_id = load(url, '<script>window.clicks=0;window.submissions=0;document.addEventListener("submit",e=>{e.preventDefault();submissions++})</script>' + html)
                result = api(tab_id, "enterApplication")
                check(result == expected and page.evaluate("clicks") == int(expected) and page.evaluate("submissions") == 0, f"entry guard {index}: {expected}")

            # Preserve the existing native scan/fill path and manual review.
            tab_id = load(GREENHOUSE, '<main><h1>Job application</h1><form onsubmit="event.preventDefault();window.submissions++"><label>First name<input id=name></label><button type=submit>Apply</button></form></main><script>window.submissions=0</script>')
            scanned = api(tab_id, "scan")
            field = scanned["fields"][0]
            filled = api(tab_id, "fill", [{"key": field["key"], "status": "RESOLVED", "value": "Offline Example"}], {})
            check(field["label"] == "First name" and filled["filled"] == 1 and page.locator("#name").input_value() == "Offline Example" and page.evaluate("submissions") == 0, "native scan labels and fill remain functional without submitting")
            page.locator("#name").fill("Manual edit")
            api(tab_id, "fill", [{"key": field["key"], "status": "RESOLVED", "value": "Replacement"}], {})
            check(page.locator("#name").input_value() == "Manual edit" and "Manual edit" not in json.dumps(api(tab_id, "pageStructure")), "manual answers preserved and absent from structure")

            # Unavailable OneClick, embedded verification and open shadow inputs.
            tab_id = load(ONECLICK, '<title>SmartRecruiters</title><iframe src="https://geo.captcha-delivery.com/captcha/?secret=SECRET"></iframe><button id=apply-with-linkedin type=button>Apply with LinkedIn</button>')
            structure = api(tab_id, "pageStructure")
            check(structure["inspection"]["status"] == "access_blocked" and "SECRET" not in json.dumps(structure), "verification frame diagnosed without target secrets")
            response = panel.evaluate("id=>chrome.runtime.sendMessage({type:'page-snapshot',tabId:id})", tab_id)
            check(response["ok"] and "blocked" in response["summary"], "actual Analyze replaces false no-form summary with access condition")
            snapshot = worker.evaluate("requests.filter(r=>r.path==='/ext/snapshot').at(-1).payload")
            check(snapshot["diagnostic"]["inspections"][0]["status"] == "access_blocked", "Analyze payload retains root and frame evidence")
            tab_id = load(ONECLICK, '<h1>Application</h1><example-form></example-form><script>document.querySelector("example-form").attachShadow({mode:"open"}).innerHTML=\'<label>First name<input value="PRIVATE ANSWER"></label><button type=button>Apply</button>\'</script>')
            structure = api(tab_id, "pageStructure")
            check(structure["inspection"]["status"] == "unsupported_shadow_controls" and any(n.get("root_id") == 1 and n["tag"] == "input" for n in structure["nodes"]), "open shadow inputs diagnosed specifically")
            check("PRIVATE ANSWER" not in json.dumps(structure) and not api(tab_id, "enterApplication"), "shadow answers excluded and active shadow form blocks entry")
            response = panel.evaluate("id=>chrome.runtime.sendMessage({type:'page-snapshot',tabId:id})", tab_id)
            check("shadow" in response["summary"], "Analyze reports unsupported shadow inputs truthfully")
            tab_id = load(SR, '<a href="https://person:SECRET@jobs.smartrecruiters.com/oneclick-ui/company/Example/publication/00000000-0000-4000-8000-000000000001?token=SECRET#SECRET">I\'m interested</a><a href="https://example.test/private/SECRET?token=SECRET">Apply</a>')
            structure = api(tab_id, "pageStructure")
            check("SECRET" not in json.dumps(structure) and "person" not in json.dumps(structure), "action diagnostics omit credentials query fragment and arbitrary paths")
            tab_id = load(ONECLICK, '<iframe src="https://example.test/embedded"></iframe>')
            check(api(tab_id, "pageStructure")["inspection"]["status"] == "embedded_content_unavailable", "unavailable embedded content is distinct from no form")
            tab_id = load(ONECLICK, '<main></main><script>for(let i=0;i<45;i++){const host=document.createElement("example-control");document.body.append(host);host.attachShadow({mode:"open"}).innerHTML="<input>"}</script>')
            structure = api(tab_id, "pageStructure")
            check(len(structure["inspection"]["roots"]) == 40 and structure["truncated"] and not api(tab_id, "enterApplication"), "root diagnostic bound is explicit and prevents unsafe entry")
            tab_id = load(ONECLICK, '<div aria-busy=true>Loading</div><button id=apply-with-linkedin type=button>Apply with LinkedIn</button>')
            check(api(tab_id, "pageStructure")["inspection"]["status"] == "loading" and not api(tab_id, "enterApplication"), "loading form and LinkedIn OAuth remain manual")

            # Existing run's current claim must hold during no-input entry/review.
            item = dict(application_id=1, run_id="offline-run", state="opened", url=ONECLICK)
            worker.evaluate("""async({id,item})=>{runItems=[item,{application_id:2,state:'pending',run_id:item.run_id}];await chrome.storage.session.set({chromeRunPaused:false,chromeRun:{active:true,current:item,tabId:id,started:true}});}""", dict(id=tab_id, item=item))
            for _ in range(3):
                worker.evaluate("autoApplyRunTick()")
            requests = worker.evaluate("requests")
            check(not any(r["path"] == "/ext/run/next" for r in requests) and worker.evaluate("runItems[1].state==='pending'"), "three real run ticks never advance an unconfirmed current claim")
            check(not any(r["path"] == "/ext/applied" or r["path"] == "/ext/run/result" and r["payload"].get("state") in {"skipped", "submitted"} for r in requests), "no entry or Analyze action records submission or releases run")
            check(all(method == "GET" for method, _ in traffic), "actual browser employer requests remain GET-only and offline")
        finally:
            context.close()
    print(f"COMPLETE {len(checks)} actual Chrome checks", flush=True)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="t069-") as directory:
        test_t069_offline_chrome(Path(directory))
