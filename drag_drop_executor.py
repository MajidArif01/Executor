"""Replay one recorded Windows drag using LIVE accessibility elements.

Install: uv add uiautomation pynput
Preview: uv run python drag_drop_executor.py drag_node.json --dry-run
Execute: uv run python drag_drop_executor.py drag_node.json
Desktop: uv run python drag_drop_executor.py drag_node.json --show-desktop
Timeline: uv run python drag_drop_executor.py timeline.json --id 2

Recorded snapshot files are evidence, not live UI handles. No coordinate fallback
is used. A completed gesture does not prove the application accepted the drop.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import sys
import time
from pathlib import Path


def load_node(path, action_id=None):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(data, dict) and data.get("kind") == "mouse_drag":
        nodes = [data]
    elif isinstance(data, list):
        nodes = data
    elif isinstance(data, dict):
        nodes = data.get("actions", data.get("events", data.get("timeline", [])))
    else:
        nodes = []
    if not isinstance(nodes, list):
        raise ValueError("Expected a drag node, list, or actions/events/timeline list.")
    matches = [n for n in nodes if isinstance(n, dict)
               and n.get("kind") == "mouse_drag"
               and (action_id is None or n.get("id") == action_id)]
    if len(matches) != 1:
        raise ValueError("Select exactly one mouse_drag action; use --id for a timeline.")
    node = matches[0]
    if node.get("button", "left") != "left":
        raise ValueError("This executor supports left-button drags only.")
    if node.get("drop_kind") not in (None, "dropped_onto"):
        raise ValueError("This executor expects an element-to-element drop.")
    for side in ("from", "to"):
        spec = node.get(side, {}).get("element", {})
        if not all(spec.get(k) for k in ("name", "control_type", "window")):
            raise ValueError(f"{side}.element requires name, control_type and window.")
    if node["from"]["element"] == node["to"]["element"]:
        raise ValueError("Source and target identities are identical.")
    return node


def enable_dpi_awareness():
    # Must happen before UIAutomation/pynput are imported or UI handles created.
    user32 = ctypes.windll.user32
    try:
        fn = user32.SetProcessDpiAwarenessContext
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = ctypes.c_bool
        if fn(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    except AttributeError:
        pass
    result = ctypes.windll.shcore.SetProcessDpiAwareness(2)
    if result != 0:
        raise RuntimeError("Could not establish DPI awareness. Run in a fresh Python process.")


def walk(root, max_depth=12):
    stack = [(root, 0)]
    count = 0
    while stack:
        control, depth = stack.pop()
        count += 1
        if count > 10000:
            raise RuntimeError("Accessibility search exceeded 10,000 nodes.")
        yield control
        if depth < max_depth:
            stack.extend((child, depth + 1) for child in reversed(control.GetChildren()))


def identity_matches(control, spec):
    return (control.Name == spec["name"]
            and control.ControlTypeName == spec["control_type"]
            and (not spec.get("automation_id")
                 or control.AutomationId == spec["automation_id"]))


def resolve(auto, spec):
    windows = [w for w in auto.GetRootControl().GetChildren()
               if w.Name == spec["window"]]
    if len(windows) != 1:
        raise RuntimeError(f"Expected one live window named {spec['window']!r}; found {len(windows)}.")
    matches = [c for c in walk(windows[0]) if identity_matches(c, spec)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {spec['name']!r}; found {len(matches)}. No drag performed.")
    control = matches[0]
    if not control.IsEnabled or control.IsOffscreen:
        raise RuntimeError(f"{spec['name']!r} is disabled or off-screen.")
    return control


def center(control):
    rect = control.BoundingRectangle
    if rect.right <= rect.left or rect.bottom <= rect.top:
        raise RuntimeError(f"{control.Name!r} has empty bounds.")
    return (round((rect.left + rect.right) / 2),
            round((rect.top + rect.bottom) / 2))


def ensure_hit(auto, point, expected):
    """Reject occlusion: the actual hit or one of its ancestors must be expected."""
    expected_id = tuple(expected.GetRuntimeId() or ())
    if not expected_id:
        raise RuntimeError(f"{expected.Name!r} has no runtime ID; cannot verify the hit safely.")
    hit = auto.ControlFromPoint(*point)
    actual = f"{hit.Name!r} ({hit.ControlTypeName})" if hit is not None else "no element"
    for _ in range(30):
        if hit is None:
            break
        if tuple(hit.GetRuntimeId() or ()) == expected_id:
            return
        hit = hit.GetParentControl()
    raise RuntimeError(f"{expected.Name!r} is not exposed at {point}; actual hit: {actual}. "
                       "For desktop icons, rerun with --show-desktop.")


def execute_drag(node, *, dry_run=False, duration=None, countdown=3, show_desktop=False):
    if sys.platform != "win32":
        raise RuntimeError("Run this executor on Windows in your interactive desktop session.")
    enable_dpi_awareness()
    import uiautomation as auto
    from pynput.mouse import Button, Controller

    seconds = float(duration if duration is not None
                    else node.get("duration_ms", 1500) / 1000)
    if not math.isfinite(seconds) or seconds <= 0 or seconds > 60:
        raise ValueError("Drag duration must be between 0 and 60 seconds.")
    source_spec, target_spec = node["from"]["element"], node["to"]["element"]
    if show_desktop:
        if any(s["window"] != "Program Manager" for s in (source_spec, target_spec)):
            raise ValueError("--show-desktop is only for drags between desktop elements.")
        from comtypes.client import CreateObject
        print("Minimizing windows to expose desktop icons...", flush=True)
        CreateObject("Shell.Application", dynamic=True).MinimizeAll()
        time.sleep(1)
    source, target = resolve(auto, source_spec), resolve(auto, target_spec)
    start, end = center(source), center(target)
    plan = {"action_id": node.get("id"), "source": source_spec,
            "target": target_spec, "live_from": start, "live_to": end,
            "duration_seconds": seconds}
    print(json.dumps(plan, indent=2))
    if dry_run:
        ensure_hit(auto, start, source)
        ensure_hit(auto, end, target)
        return {**plan, "status": "dry_run", "drop_verified": False}
    print(f"Dragging in {countdown:g} seconds. Ctrl+C cancels.", flush=True)
    time.sleep(countdown)
    # Resolve again after countdown so stale positions are never used.
    source, target = resolve(auto, source_spec), resolve(auto, target_spec)
    start, end = center(source), center(target)
    ensure_hit(auto, start, source)
    ensure_hit(auto, end, target)
    mouse = Controller()
    mouse.position = start
    if tuple(mouse.position) != start:
        raise RuntimeError("Cursor did not reach the source point.")
    try:
        mouse.press(Button.left)
        time.sleep(0.15)
        steps = max(2, math.ceil(seconds * 60))
        began = time.monotonic()
        for step in range(1, steps + 1):
            ratio = step / steps
            mouse.position = (round(start[0] + (end[0] - start[0]) * ratio),
                              round(start[1] + (end[1] - start[1]) * ratio))
            time.sleep(max(0, began + seconds * ratio - time.monotonic()))
        mouse.position = end
        if tuple(mouse.position) != end:
            raise RuntimeError("Cursor did not reach the target point.")
        time.sleep(0.25)  # Let the destination process drag-over before release.
    finally:
        mouse.release(Button.left)  # Also release on Ctrl+C or an exception.
    return {**plan, "live_from": start, "live_to": end,
            "status": "gesture_completed", "drop_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path)
    parser.add_argument("--id", type=int, help="Select a drag action from a timeline")
    parser.add_argument("--dry-run", action="store_true", help="Resolve and check without moving mouse")
    parser.add_argument("--duration", type=float, help="Override drag duration in seconds")
    parser.add_argument("--countdown", type=float, default=3)
    parser.add_argument("--show-desktop", action="store_true",
                        help="Minimize open windows before resolving desktop icons (also in dry-run)")
    args = parser.parse_args()
    try:
        if not math.isfinite(args.countdown) or not 0 <= args.countdown <= 60:
            raise ValueError("Countdown must be between 0 and 60 seconds.")
        result = execute_drag(load_node(args.input, args.id), dry_run=args.dry_run,
                              duration=args.duration, countdown=args.countdown,
                              show_desktop=args.show_desktop)
        print(json.dumps(result, indent=2))
    except KeyboardInterrupt:
        print("Cancelled; mouse button released if a drag was started.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
