import asyncio
import math
import random
from dataclasses import replace

import pytest

from autoapply.cursor import CursorConfig, CursorController, CursorError, CursorState, Point
from autoapply.cursor.backend import InputStateManager
from autoapply.cursor.planning import BezierPlanner, NoiseProfile, easing
from autoapply.cursor.target import TargetResolver


@pytest.mark.parametrize("end", [Point(1, 1), Point(10, 10), Point(700, 500), Point(1439, 999)])
@pytest.mark.parametrize("style", ["easeInOutCubic", "easeOutCubic", "smoothstep"])
def test_path_invariants(end, style):
    c = CursorConfig(easing=style)
    start = Point(1, 1)
    path = BezierPlanner(c).plan(start, end, (1440, 1000))
    assert (path[0].x, path[0].y) == (start.x, start.y)
    assert (path[-1].x, path[-1].y) == (end.x, end.y)
    assert 12 <= len(path) <= 60
    assert 250 <= path[-1].time_ms <= 1000
    assert all(math.isfinite(v) for p in path for v in (p.x, p.y, p.time_ms))
    assert all(0 <= p.x < 1440 and 0 <= p.y < 1000 for p in path)
    assert all(4 <= b.time_ms-a.time_ms <= 50 for a, b in zip(path, path[1:]))
    if end.x > 100:
        assert any(abs((p.x-start.x)*(end.y-start.y)-(p.y-start.y)*(end.x-start.x)) > 1 for p in path[1:-1])


def test_distance_timing_easing_and_invalid_endpoints():
    planner = BezierPlanner(CursorConfig())
    short = planner.plan(Point(10, 10), Point(50, 50), (1440, 1000))
    long = planner.plan(Point(10, 10), Point(1200, 800), (1440, 1000))
    assert len(short) < len(long) and short[-1].time_ms < long[-1].time_ms
    assert easing(.1, "easeInOutCubic") < .1
    assert easing(.9, "easeInOutCubic") > .9
    for point in (Point(-1, 0), Point(float("nan"), 1), Point(float("inf"), 1)):
        with pytest.raises(CursorError):
            planner.plan(Point(1, 1), point, (1440, 1000))


def test_seeded_variation_noise_and_safe_targets():
    c = CursorConfig(mode="owned_test", owned_origins=("http://localhost",),
                     target_strategy="GAUSSIAN_INTERIOR", noise_enabled=True,
                     overshoot_enabled=True, overshoot_probability=1,
                     curve_variation=True, timing_variation=True)
    def plan(seed):
        return BezierPlanner(c, random.Random(seed)).plan(Point(20, 20), Point(700, 500), (1440, 1000))
    assert plan(42) == plan(42) and plan(42) != plan(43)
    assert (plan(42)[-1].x, plan(42)[-1].y) == (700, 500)
    a, b = NoiseProfile(random.Random(42), 1.5), NoiseProfile(random.Random(42), 1.5)
    for i in range(101):
        p = a.offset(i/100)
        assert p == b.offset(i/100) and abs(p.x) <= 1.5 and abs(p.y) <= 1.5
    assert a.offset(0).distance(Point(0, 0)) == 0
    assert a.offset(1).distance(Point(0, 0)) < 1e-12
    box = dict(x=10, y=20, width=100, height=50)
    for strategy in ("GAUSSIAN_INTERIOR", "UNIFORM_INTERIOR", "SAFE_CENTER"):
        first = TargetResolver(replace(c, target_strategy=strategy), random.Random(42))
        second = TargetResolver(replace(c, target_strategy=strategy), random.Random(42))
        for i in range(100):
            p = first.candidate(box, i)
            assert p == second.candidate(box, i)
            assert 30 <= p.x <= 90 and 30 <= p.y <= 60


@pytest.mark.parametrize("kwargs", [dict(noise_enabled=True), dict(target_strategy="GAUSSIAN_INTERIOR"),
    dict(overshoot_enabled=True), dict(timing_variation=True), dict(curve_variation=True),
    dict(mode="owned_test"), dict(min_steps=1), dict(max_steps=2.5), dict(press_duration_ms=1001),
    dict(min_frame_delay_ms=1), dict(max_replans=11), dict(noise_px=float("nan")),
    dict(owned_origins=["https://example.test/path"]), dict(noise_enabled="false")])
def test_invalid_policy_and_configuration(kwargs):
    with pytest.raises(ValueError):
        CursorConfig(**kwargs)


def test_owned_origin_scope():
    c = CursorConfig(mode="owned_test", owned_origins=("https://example.test",))
    c.authorize("https://example.test/path")
    for url in ("https://example.test.evil/path", "http://example.test", "https://example.test:444", "about:blank"):
        with pytest.raises(CursorError):
            c.authorize(url)


class FailingBackend:
    def __init__(self):
        self.events = []

    async def key_down(self, key):
        self.events.append(("key_down", key))

    async def key_up(self, key):
        self.events.append(("key_up", key))

    async def down(self, point, button, state):
        self.events.append(("down", button))
        raise RuntimeError("Ambiguous input failure")

    async def up(self, point, button, state):
        self.events.append(("up", button))


async def test_failed_dispatch_retains_ownership_until_idempotent_cleanup():
    backend = FailingBackend()
    inputs = InputStateManager(backend)
    await inputs.modifiers(["Shift"])
    with pytest.raises(RuntimeError):
        await inputs.down(Point(2, 3), "left")
    assert inputs.state.buttons == {"left"}
    await inputs.reset(Point(2, 3))
    await inputs.reset(Point(2, 3))
    assert backend.events == [("key_down", "Shift"), ("down", "left"), ("up", "left"), ("key_up", "Shift")]
    assert not inputs.state.buttons and not inputs.state.modifiers


@pytest.fixture
async def cursor_page(config, monkeypatch):
    from autoapply.browser import Browser
    from autoapply.config import ROOT
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "data/private/playwright"))
    browser = Browser(config)
    page = await browser.new_page()
    await page.route("**/*", lambda route: route.fulfill(body="<html><body></body></html>", content_type="text/html"))
    await page.goto("http://cursor.test/")
    yield page
    await browser.close()


HTML = """<style>button {position:absolute;left:850px;top:550px;width:120px;height:60px}</style>
<button id=target><span>Test</span></button><script>
window.events=[];
for (const type of ['mousemove','mousedown','mouseup','click','dblclick','contextmenu','auxclick'])
 document.addEventListener(type,e=>{events.push({type,x:e.clientX,y:e.clientY,button:e.button,
 buttons:e.buttons,detail:e.detail,shift:e.shiftKey,target:e.target.id});
 if(type==='contextmenu')e.preventDefault();});
</script>"""


async def prepared(page, **kwargs):
    await page.set_content(HTML)
    cursor = CursorController(page, CursorConfig(**kwargs))
    page._autoapply_cursor = cursor
    await cursor.synchronize(Point(10, 10))
    await page.evaluate("events=[]")
    return cursor, page.locator("#target")


@pytest.mark.browser
@pytest.mark.parametrize("backend", ["playwright", "cdp"])
async def test_real_input_order_persistence_and_single_click(cursor_page, backend):
    page = cursor_page
    cursor, target = await prepared(page, backend=backend)
    await cursor.click_element(target)
    events = await page.evaluate("events")
    types = [e["type"] for e in events]
    assert types.count("mousedown") == types.count("mouseup") == types.count("click") == 1
    assert types.index("mousedown") > max(i for i, t in enumerate(types) if t == "mousemove")
    assert types.index("mousedown") < types.index("mouseup") < types.index("click")
    assert (events[0]["x"], events[0]["y"]) == (10, 10)
    down = events[types.index("mousedown")]
    assert (down["x"], down["y"]) == (910, 580)
    assert cursor.current_position == Point(910, 580)
    await page.evaluate("target.style.left='150px';events=[]")
    await cursor.click_element(target)
    first = (await page.evaluate("events"))[0]
    assert (first["x"], first["y"]) == (910, 580)
    assert not cursor.input_state.buttons and cursor.state == CursorState.IDLE


@pytest.mark.browser
@pytest.mark.parametrize("backend", ["playwright", "cdp"])
@pytest.mark.parametrize("button,count,expected", [("left", 2, "dblclick"), ("right", 1, "contextmenu"), ("middle", 1, "auxclick")])
async def test_buttons_modifiers_and_click_count(cursor_page, backend, button, count, expected):
    cursor, target = await prepared(cursor_page, backend=backend)
    await cursor.click_element(target, button=button, click_count=count, modifiers=["Shift"])
    events = await cursor_page.evaluate("events")
    assert len([e for e in events if e["type"] == "mousedown"]) == count
    assert len([e for e in events if e["type"] == expected]) == 1
    assert all(e["shift"] for e in events if e["type"] in {"mousedown", "mouseup"})
    assert not cursor.input_state.buttons and not cursor.input_state.modifiers


@pytest.mark.browser
@pytest.mark.parametrize("condition", ["target.disabled=true", "target.style.display='none'", "target.remove()",
    "document.body.insertAdjacentHTML('beforeend', '<div style=\"position:fixed;inset:0;background:white;z-index:100\"></div>')"])
async def test_unavailable_target_never_presses(cursor_page, condition):
    cursor, target = await prepared(cursor_page, actionability_timeout_ms=250)
    await cursor_page.evaluate(condition)
    with pytest.raises(Exception):
        await cursor.click_element(target)
    assert not [e for e in await cursor_page.evaluate("events") if e["type"] == "mousedown"]


async def wait_moving(cursor):
    for _ in range(300):
        if cursor.state == CursorState.MOVING:
            return
        await asyncio.sleep(.01)
    raise AssertionError("Cursor did not start moving")


@pytest.mark.browser
async def test_manual_cancels_active_and_queued_and_requires_resync(cursor_page):
    cursor, target = await prepared(cursor_page)
    active = asyncio.create_task(cursor.click_element(target))
    await wait_moving(cursor)
    queued = asyncio.create_task(cursor.click_element(target))
    await asyncio.sleep(0)
    await cursor.enter_manual_mode()
    results = await asyncio.gather(active, queued, return_exceptions=True)
    assert all(isinstance(result, CursorError) for result in results)
    assert cursor.state == CursorState.MANUAL_REQUIRED and not cursor.position_known
    assert not [e for e in await cursor_page.evaluate("events") if e["type"] == "mousedown"]
    with pytest.raises(CursorError):
        await cursor.exit_manual_mode()
    await cursor.exit_manual_mode(Point(20, 20))
    await cursor.click_element(target)
    assert len([e for e in await cursor_page.evaluate("events") if e["type"] == "click"]) == 1


@pytest.mark.browser
@pytest.mark.parametrize("backend", ["playwright", "cdp"])
async def test_drag_pointer_capture_and_manual_cleanup(cursor_page, backend):
    cursor, target = await prepared(cursor_page, backend=backend)
    await cursor_page.evaluate("() => {target.onpointerdown=e=>target.setPointerCapture(e.pointerId)}")
    await cursor.drag(target, Point(250, 200), modifiers=["Shift"])
    events = await cursor_page.evaluate("events")
    down = next(i for i, e in enumerate(events) if e["type"] == "mousedown")
    moves = [e for e in events[down+1:] if e["type"] == "mousemove"]
    assert moves and all(e["buttons"] == 1 and e["shift"] and e["target"] == "target" for e in moves)
    assert len([e for e in events if e["type"] == "mouseup"]) == 1
    await cursor.press(modifiers=["Shift"])
    await cursor.enter_manual_mode()
    assert not cursor.input_state.buttons and not cursor.input_state.modifiers and not cursor.position_known
    await cursor.reset_input_state()
    assert cursor.state == CursorState.MANUAL_REQUIRED


@pytest.mark.browser
async def test_hover_mutation_replans_from_actual_position(cursor_page):
    cursor, target = await prepared(cursor_page)
    await cursor_page.evaluate("document.addEventListener('mousemove', e=>{if(e.clientX>300 && !window.changed){window.changed=true;target.style.left='500px'};})")
    await cursor.click_element(target)
    paths = [e for e in cursor.events if e["event"] == "path"]
    assert len(paths) >= 2
    assert paths[1]["start"] != paths[0]["start"] and paths[1]["start"] != paths[0]["end"]
    downs = [e for e in await cursor_page.evaluate("events") if e["type"] == "mousedown"]
    assert len(downs) == 1 and downs[0]["x"] == 560


@pytest.mark.browser
async def test_security_hold_during_movement_preserves_page(cursor_page):
    cursor, target = await prepared(cursor_page)
    blocked = False
    async def guard():
        return blocked
    cursor.security_guard = guard
    task = asyncio.create_task(cursor.click_element(target))
    await wait_moving(cursor)
    blocked = True
    with pytest.raises(CursorError):
        await task
    assert cursor.state == CursorState.MANUAL_REQUIRED and not cursor.position_known
    assert not cursor_page.is_closed()
    assert not [e for e in await cursor_page.evaluate("events") if e["type"] == "mousedown"]
    with pytest.raises(CursorError):
        await cursor.exit_manual_mode(Point(1, 1))


@pytest.mark.browser
async def test_navigation_cancels_movement(cursor_page):
    cursor, target = await prepared(cursor_page)
    task = asyncio.create_task(cursor.click_element(target))
    await wait_moving(cursor)
    await cursor_page.goto("http://cursor.test/next")
    with pytest.raises(CursorError):
        await task
    assert cursor.state == CursorState.CANCELLED and not cursor.input_state.buttons


@pytest.mark.browser
async def test_resize_replans_before_press(cursor_page):
    cursor, target = await prepared(cursor_page)
    task = asyncio.create_task(cursor.click_element(target))
    await wait_moving(cursor)
    await cursor_page.set_viewport_size(dict(width=1200, height=900))
    await task
    assert any(e["event"] == "replan" for e in cursor.events)
    assert len([e for e in await cursor_page.evaluate("events") if e["type"] == "click"]) == 1


@pytest.mark.browser
async def test_nested_scroll_and_iframe_coordinates(cursor_page):
    page = cursor_page
    cursor, _ = await prepared(page)
    await page.set_content('<div style="height:1800px"></div><div style="height:180px;overflow:auto"><div style="height:600px"></div><iframe style="margin-left:100px;width:400px;height:160px" src="http://child.test/"></iframe></div>')
    frame = page.frame_locator("iframe")
    await frame.locator("body").wait_for()
    child = next(f for f in page.frames if f != page.main_frame)
    await child.set_content('<button id="inside" style="margin:30px;width:100px;height:50px" onclick="window.clicked=(window.clicked||0)+1">Inside</button>')
    target = frame.locator("#inside")
    await cursor.click_element(target)
    assert await child.evaluate("window.clicked") == 1
    box = await target.bounding_box()
    assert abs(cursor.current_position.x-(box["x"]+box["width"]/2)) < 1
    assert await page.evaluate("scrollY") > 0
    await page.evaluate("document.body.insertAdjacentHTML('beforeend','<div style=\"position:fixed;inset:0;z-index:100;background:white\"></div>')")
    assert not await cursor.resolver.hit_test.valid(target, cursor.current_position)


@pytest.mark.browser
async def test_framework_control_uses_one_mechanism(cursor_page):
    cursor, _ = await prepared(cursor_page)
    await cursor_page.evaluate("document.body.insertAdjacentHTML('beforeend','<select id=choice><option>A</option><option>B</option></select>')")
    await cursor.native_control(lambda: cursor_page.locator("#choice").select_option(label="B"), "native select")
    assert await cursor_page.locator("#choice").input_value() == "B"
    assert not [e for e in await cursor_page.evaluate("events") if e["type"] == "mousedown"]
    assert any(e["event"] == "framework_fallback" for e in cursor.events)


@pytest.mark.browser
async def test_concurrent_clicks_are_serialized(cursor_page):
    cursor, target = await prepared(cursor_page)
    await asyncio.gather(cursor.click_element(target), cursor.click_element(target))
    events = [e["type"] for e in await cursor_page.evaluate("events") if e["type"] in {"mousedown", "mouseup", "click"}]
    assert events == ["mousedown", "mouseup", "click"] * 2
    assert [e["event"] for e in cursor.events if e["event"] in {"start", "end"}] == ["start", "end"] * 2


@pytest.mark.browser
@pytest.mark.parametrize("stop", ["cancel", "task_cancel"])
async def test_cancel_during_press_releases_only_once(cursor_page, stop):
    cursor, target = await prepared(cursor_page)
    task = asyncio.create_task(cursor.click_element(target, press_duration_ms=900, modifiers=["Shift"]))
    for _ in range(300):
        if cursor.input_state.buttons:
            break
        await asyncio.sleep(.01)
    assert cursor.input_state.buttons
    if stop == "cancel":
        await cursor.cancel()
    else:
        task.cancel()
    result = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(result[0], (CursorError, asyncio.CancelledError))
    events = await cursor_page.evaluate("events")
    assert len([e for e in events if e["type"] == "mousedown"]) == 1
    assert len([e for e in events if e["type"] == "mouseup"]) == 1
    assert not cursor.input_state.buttons and not cursor.input_state.modifiers
    await cursor.reset_input_state()
    assert len([e for e in await cursor_page.evaluate("events") if e["type"] == "mouseup"]) == 1


@pytest.mark.browser
async def test_page_teardown_discards_input_state(cursor_page):
    cursor, _ = await prepared(cursor_page)
    await cursor.press(modifiers=["Control"])
    await cursor_page.close()
    assert not cursor.position_known and not cursor.input_state.buttons and not cursor.input_state.modifiers
    with pytest.raises(CursorError):
        await cursor.synchronize(Point(1, 1))


@pytest.mark.browser
async def test_detachment_during_movement_cannot_press(cursor_page):
    cursor, target = await prepared(cursor_page)
    await cursor_page.evaluate("document.addEventListener('mousemove',e=>{if(e.clientX>300)document.querySelector('#target')?.remove()})")
    with pytest.raises(Exception):
        await cursor.click_element(target)
    assert not [e for e in await cursor_page.evaluate("events") if e["type"] == "mousedown"]


@pytest.mark.browser
async def test_irregular_target_uses_safe_alternative(cursor_page):
    cursor, target = await prepared(cursor_page)
    # Pointer-transparent center: rectangular geometry alone would hit the cover.
    await cursor_page.evaluate("document.body.insertAdjacentHTML('beforeend','<div style=\"position:absolute;left:900px;top:570px;width:20px;height:20px;z-index:10\"></div>')")
    await cursor.click_element(target)
    assert cursor.current_position != Point(910, 580)
    assert len([e for e in await cursor_page.evaluate("events") if e["type"] == "click"]) == 1


@pytest.mark.browser
async def test_owned_mode_rejects_third_party_child_frame(cursor_page):
    cursor, _ = await prepared(cursor_page, mode="owned_test", owned_origins=("http://cursor.test",), noise_enabled=True)
    await cursor_page.set_content('<iframe src="http://child.test/"></iframe>')
    frame = cursor_page.frame_locator("iframe")
    await frame.locator("body").wait_for()
    child = next(f for f in cursor_page.frames if f != cursor_page.main_frame)
    await child.set_content("<button>Child</button>")
    with pytest.raises(CursorError, match="denied"):
        await cursor.click_element(frame.locator("button"))


@pytest.mark.browser
async def test_unresolved_dialog_enters_manual_without_dismissal(cursor_page):
    cursor, _ = await prepared(cursor_page)
    dialog_task = asyncio.create_task(cursor_page.evaluate("alert('Manual inspection required')"))
    for _ in range(200):
        if cursor.state == CursorState.MANUAL_REQUIRED:
            break
        await asyncio.sleep(.01)
    assert cursor.state == CursorState.MANUAL_REQUIRED and not cursor.position_known
    assert not dialog_task.done()
    await cursor_page.close()
    await asyncio.gather(dialog_task, return_exceptions=True)


@pytest.mark.browser
async def test_navigation_cleans_low_level_held_input(cursor_page):
    cursor, _ = await prepared(cursor_page)
    await cursor.press(modifiers=["Shift"])
    await cursor_page.goto("http://cursor.test/new-document")
    for _ in range(100):
        if not cursor.input_state.buttons and not cursor.input_state.modifiers:
            break
        await asyncio.sleep(.01)
    assert cursor.state == CursorState.CANCELLED
    assert not cursor.input_state.buttons and not cursor.input_state.modifiers


@pytest.mark.browser
async def test_recovery_invalidates_preexisting_queue(cursor_page):
    cursor, target = await prepared(cursor_page)
    async with cursor._lock:
        # Both await the same lock; recovery is deliberately queued first.
        recovery = asyncio.create_task(cursor.synchronize(Point(30, 30)))
        await asyncio.sleep(0)
        stale = asyncio.create_task(cursor.click_element(target))
        await asyncio.sleep(0)
    await recovery
    with pytest.raises(CursorError):
        await stale
    assert not [e for e in await cursor_page.evaluate("events") if e["type"] == "mousedown"]


async def test_backend_timeout_preserves_uncertain_ownership():
    class HungBackend(FailingBackend):
        async def down(self, point, button, state):
            await asyncio.Event().wait()
    backend = HungBackend()
    inputs = InputStateManager(backend, timeout_ms=20)
    with pytest.raises(TimeoutError):
        await inputs.down(Point(1, 1), "left")
    assert inputs.state.buttons == {"left"}
    await inputs.reset(Point(1, 1))
    assert not inputs.state.buttons and backend.events == [("up", "left")]


@pytest.mark.browser
@pytest.mark.parametrize("backend", ["playwright", "cdp"])
async def test_device_scale_does_not_scale_mouse_coordinates(cursor_page, backend):
    context = await cursor_page.context.browser.new_context(
        viewport=dict(width=1440, height=1000), device_scale_factor=2)
    try:
        page = await context.new_page()
        await page.route("**/*", lambda route: route.fulfill(body="<body></body>"))
        await page.goto("http://cursor.test/")
        cursor, target = await prepared(page, backend=backend)
        assert await page.evaluate("devicePixelRatio") == 2
        await cursor.click_element(target)
        events = [e for e in await page.evaluate("events") if e["type"] == "mousedown"]
        assert len(events) == 1 and (events[0]["x"], events[0]["y"]) == (910, 580)
    finally:
        await context.close()


@pytest.mark.browser
async def test_scaled_iframe_hit_test_and_rotated_frame_rejection(cursor_page):
    cursor, _ = await prepared(cursor_page)
    await cursor_page.set_content('<iframe style="margin:80px;transform:scale(.75);transform-origin:0 0" src="http://child.test/"></iframe>')
    frame = cursor_page.frame_locator("iframe")
    await frame.locator("body").wait_for()
    child = next(f for f in cursor_page.frames if f != cursor_page.main_frame)
    await child.set_content('<button style="margin:20px;width:120px;height:60px" onclick="window.clicked=true">Child</button>')
    await cursor.click_element(frame.locator("button"))
    assert await child.evaluate("window.clicked") is True
    await cursor_page.locator("iframe").evaluate("e=>e.style.transform='rotate(10deg)'")
    box = await frame.locator("button").bounding_box()
    assert not await cursor.resolver.hit_test.valid(frame.locator("button"), Point(box["x"]+box["width"]/2, box["y"]+box["height"]/2))
