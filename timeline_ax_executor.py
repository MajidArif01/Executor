"""Replay a recorded timeline.json using its saved accessibility data.

For each click / keyboard step, the element recorded in the item's
`accessibility` block (written by Accesibilitytree/accessibility_tree.py) is
found live with UI Automation and acted on, so the step still lands when the
window has moved. Scrolls resolve the live scrollable ancestor. Steps with no usable block (drags, unmatched items,
or an element that is not on screen now) fall back to the recorded
coordinates, scaled to this screen.

    uv run python timeline_ax_executor.py <timeline.json | episode_dir> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from accessibility_tree_executor import ElementQuery, auto
from action_classes import ActionLevel0, ActionLevel1
from Accesibilitytree.accessibility_tree import is_mouse_drag, parse_drag_points
from executor import (
    _recorded_resolution,
    _scale_point,
    _scroll_direction_fallback,
    _scroll_notches,
)

TIMELINE_RELPATH = Path("processdata") / "timeline.json"
SCROLL_UNITS_PER_NOTCH = 1  # PyAutoGUI takes notches, not WHEEL_DELTA
TYPING_INTERVAL = 0.02
SEARCH_DEPTH = 40             # Chrome page content sits deep in the tree
DOUBLE_CLICK_SECONDS = 0.5    # Windows default double-click time

Step = tuple[str, str]  # (ax | fallback | skipped, detail)


def resolve_timeline(path: Path) -> Path:
    """Accept a timeline.json or an episode folder that contains one."""
    return path / TIMELINE_RELPATH if path.is_dir() else path


def target_query(block: dict[str, Any]) -> ElementQuery | None:
    """Query for the recorded element, or None if it cannot be found by
    description (a control type alone would match the wrong element)."""
    target = block.get("target") or {}
    name = target.get("name") or None
    automation_id = target.get("automation_id") or None
    if name is None and automation_id is None:
        return None
    return ElementQuery(
        name=name,
        automation_id=automation_id,
        control_type=target.get("control_type") or None,
        class_name=target.get("class_name") or None,
        search_depth=SEARCH_DEPTH,
    )


def live_point(ctrl: Any, recorded: tuple[int, int] | None) -> tuple[int, int] | None:
    """Where to click the live element: the recorded point if it is still
    inside it (big targets like the desktop list), else its clickable point,
    else its centre. None when the element has no on-screen area."""
    rect = ctrl.BoundingRectangle
    if rect.width() <= 0 or rect.height() <= 0:
        return None
    if recorded and rect.left <= recorded[0] < rect.right and rect.top <= recorded[1] < rect.bottom:
        return recorded
    try:
        x, y, ok = ctrl.GetClickablePoint()
        if ok:
            return int(x), int(y)
    except Exception:
        pass
    return rect.xcenter(), rect.ycenter()


class TimelineAxExecutor:
    def __init__(self, *, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.level_0 = ActionLevel0()
        self.level_1 = ActionLevel1(self.level_0)
        width, height = auto.GetScreenSize()
        self.screen = (int(width), int(height))

    # -- lookup --------------------------------------------------------------

    @staticmethod
    def top_levels(block: dict[str, Any]) -> Iterator[Any]:
        """Where to search: the recorded top-level element (by name and class,
        then class alone, since titles change per page), then the foreground
        window. Any control type: the desktop is a Pane, not a Window, which
        Executor.find_window would miss."""
        root_node = (block.get("ancestor_chain") or [{}])[0]
        name, class_name = root_node.get("name"), root_node.get("class_name")
        desktop = auto.GetRootControl()
        for conds in ({"Name": name, "ClassName": class_name}, {"ClassName": class_name}):
            conds = {k: v for k, v in conds.items() if v}
            if conds:
                top = desktop.Control(searchDepth=1, **conds)
                if top.Exists(maxSearchSeconds=1):
                    yield top
        yield auto.GetForegroundControl()

    def find_live(self, item: dict[str, Any]) -> tuple[Any | None, str]:
        """Find the item's recorded element on screen now."""
        block = item.get("accessibility") or {}
        if not block.get("matched"):
            return None, block.get("reason") or "no accessibility block"
        query = target_query(block)
        if query is None:
            return None, "recorded element has no name or automation_id"
        conds = {k: v for k, v in (("Name", query.name), ("AutomationId", query.automation_id),
                                    ("ClassName", query.class_name)) if v}
        try:
            for top in self.top_levels(block):
                search = getattr(top, query.control_type or "Control", top.Control)
                ctrl = search(searchDepth=query.search_depth, **conds)
                if ctrl.Exists(maxSearchSeconds=2):
                    return ctrl, query.describe()
        except Exception as exc:  # UIA/COM errors: treat as not found
            return None, f"lookup error {type(exc).__name__}: {query.describe()}"
        return None, f"not found live: {query.describe()}"

    def point(self, item: dict[str, Any], x: float, y: float) -> tuple[int, int]:
        return _scale_point(x, y, _recorded_resolution(item), self.screen)

    # -- steps ---------------------------------------------------------------

    def run_item(self, item: dict[str, Any], clicks: int = 1) -> Step:
        """Execute one timeline item and say how it was done."""
        kind = item.get("type")
        value = str(item.get("value") or "")
        event = (item.get("events") or [{}])[0] or {}

        if is_mouse_drag(item):
            return self.drag(item, event)
        if kind == "click":
            button = "right" if value == "mouse_right" else "left"
            return self.click(item, event, button, clicks)
        if kind == "scroll":
            return self.scroll(item, event, value)
        if kind == "Typing":
            method, info = self.focus(item)
            if not self.dry_run:
                self.level_1.type_text(value, interval=TYPING_INTERVAL)
            return method, f"typed {value!r} into {info}"
        if kind in ("special", "KeyPress"):
            keys = [k.strip() for k in value.split(",") if k.strip()]
            if not keys:
                return "skipped", "no key recorded"
            method, info = self.focus(item)
            if not self.dry_run:
                for key in keys:
                    self.level_0.keypress(key)
            return method, f"pressed {', '.join(keys)} into {info}"
        return "skipped", ""

    def click(self, item: dict[str, Any], event: dict[str, Any], button: str,
              clicks: int = 1) -> Step:
        """A real mouse click (so double-clicks and focus behave as recorded).
        If the recorded point is still inside the live element, click exactly
        there, which matters for big targets like the desktop list; otherwise
        the element moved, so click its live clickable point."""
        action = f"{button}-{'double-' if clicks == 2 else ''}click"
        recorded = self.point(item, event["x"], event["y"]) if "x" in event else None
        ctrl, info = self.find_live(item)
        live = live_point(ctrl, recorded) if ctrl is not None else None
        if live is not None:
            (x, y), method = live, "ax"
        elif recorded:
            if ctrl is not None:
                info = f"no on-screen point: {info}"
            (x, y), method, info = recorded, "fallback", f"[{info}]"
        else:
            raise ValueError(f"click has no coordinates ({info})")
        if not self.dry_run:
            self.level_0.click(x, y, button, clicks=clicks)
        return method, f"{action} ({x}, {y}) {info}"

    def focus(self, item: dict[str, Any]) -> Step:
        """Put focus on the recorded field before keys go in. A Chrome page
        document is never focused directly: that would blur the field inside."""
        ctrl, info = self.find_live(item)
        if ctrl is None:
            return "fallback", f"current focus [{info}]"
        if ctrl.ControlTypeName != "DocumentControl" and not ctrl.HasKeyboardFocus:
            if not self.dry_run:
                ctrl.SetFocus()
        return "ax", info

    def scroll(self, item: dict[str, Any], event: dict[str, Any], value: str) -> Step:
        if "x" not in event:
            raise ValueError("scroll has no anchor")
        events = item.get("events") or []
        if len(events) > 1 and any((ev.get("x"), ev.get("y"), ev.get("screen")) !=
                                  (event.get("x"), event.get("y"), event.get("screen"))
                                  for ev in events):
            # Resolve each anchor separately when the cursor changed surfaces.
            details = []
            for ev in events:
                single = {**item, "events": [ev]}
                single.pop("scroll_merge", None)
                single.pop("accessibility", None)
                single.pop("omniparser", None)
                method, detail = self.scroll(single, ev, value)
                details.append(detail)
            return "fallback", "; ".join(details)
        dx, dy = _scroll_notches(item)
        if dx == 0 and dy == 0:
            dx, dy = _scroll_direction_fallback(value)
        recorded = self.point(item, event["x"], event["y"])
        ctrl, info = self.find_live(item)
        # ScrollPattern metadata is often absent from saved trees; inspect
        # the hovered live control and then its parents when available.
        if ctrl is None and not self.dry_run:
            try:
                ctrl = auto.ControlFromPoint(*recorded)
            except Exception:
                pass
        hovered_point = live_point(ctrl, recorded) if ctrl is not None else None
        container = None
        before = None
        visited = set()
        while ctrl is not None and id(ctrl) not in visited and len(visited) < SEARCH_DEPTH:
            visited.add(id(ctrl))
            try:
                pattern = ctrl.GetScrollPattern()
                if pattern and ((dy and pattern.VerticallyScrollable) or
                                (dx and pattern.HorizontallyScrollable)):
                    container = ctrl
                    before = (pattern.HorizontalScrollPercent, pattern.VerticalScrollPercent)
                    break
                ctrl = ctrl.GetParentControl()
            except Exception:
                break
        point = live_point(container, recorded) if container is not None else None
        x, y = point or hovered_point or recorded
        method = "ax" if point or hovered_point else "fallback"
        movement = "movement unverified"
        if not self.dry_run:
            self.level_0.move(x, y)
            self.level_0.scroll(dx * SCROLL_UNITS_PER_NOTCH, dy * SCROLL_UNITS_PER_NOTCH)
            if before is not None:
                time.sleep(0.15)
                try:
                    after = (pattern.HorizontalScrollPercent, pattern.VerticalScrollPercent)
                    movement = "content moved" if after != before else "no movement (possibly at boundary)"
                except Exception:
                    pass
        return method, f"scroll dx={dx} dy={dy} at ({x}, {y}); {movement} [{info}]"

    def drag(self, item: dict[str, Any], event: dict[str, Any]) -> Step:
        parsed = parse_drag_points(event)
        if parsed is None:
            raise ValueError(f"drag has no start/end point: {event.get('detail')!r}")
        start_pt, end_pt = parsed
        start = self.point(item, start_pt[0], start_pt[1])
        end = self.point(item, end_pt[0], end_pt[1])
        if not self.dry_run:
            self.level_1.drag([start, end], move_duration=0.5)
        return "fallback", f"drag {start} -> {end}"


def is_double_click(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Same button, same point, within the Windows double-click time."""
    if is_mouse_drag(first):
        return False
    if (second.get("type"), second.get("value")) != (first.get("type"), first.get("value")):
        return False
    a, b = (((i.get("events") or [{}])[0] or {}) for i in (first, second))
    if (a.get("x"), a.get("y")) != (b.get("x"), b.get("y")):
        return False
    try:
        gap = datetime.fromisoformat(second["time"]) - datetime.fromisoformat(first["time"])
    except (KeyError, TypeError, ValueError):
        return False
    return gap.total_seconds() <= DOUBLE_CLICK_SECONDS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("timeline", type=Path, help="timeline.json or episode folder")
    parser.add_argument("--dry-run", action="store_true",
                        help="find elements and print each step, but do not act")
    parser.add_argument("--delay", type=float, default=1.5, help="seconds after each step")
    parser.add_argument("--from", dest="start", type=int, default=0,
                        help="first item index to run")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()

    path = resolve_timeline(args.timeline)
    try:
        items = json.loads(path.read_text(encoding="utf-8-sig"))["items"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Error: cannot read timeline {path}: {exc}", file=sys.stderr)
        return 2

    runner = TimelineAxExecutor(dry_run=args.dry_run)
    print(f"Replaying {path} on {runner.screen[0]}x{runner.screen[1]}"
          + (" (dry run)" if args.dry_run else ""))
    counts = {"ax": 0, "fallback": 0, "skipped": 0, "failed": 0}
    position = 0
    while position < len(items):
        item = items[position]
        index = item.get("index", position)
        # Two quick clicks on one spot are a double-click: replay them as one.
        clicks = 2 if position + 1 < len(items) and is_double_click(item, items[position + 1]) else 1
        position += clicks
        if index < args.start:
            continue
        label = f"[{index}] {item.get('type')} {str(item.get('value'))[:30]!r}"
        try:
            method, detail = runner.run_item(item, clicks)
        except Exception as exc:  # report the step, then stop or go on
            counts["failed"] += 1
            print(f"{label}: FAILED {type(exc).__name__}: {exc}")
            if not args.continue_on_error:
                break
            continue
        counts[method] += 1
        print(f"{label}: {method} {detail}".rstrip())
        if method != "skipped" and not args.dry_run:
            time.sleep(args.delay)

    print("\nDone: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
