"""Live UI executor — find elements by description in the target window's
accessibility tree, then act on them.

Instead of relying on fragile screen coordinates, this executor takes a
target element specification (name, control_type, automation_id, class_name,
or any combination) and searches the live accessibility tree of the
foreground window (or a named window) to locate it.

Requirements:
    pip install uiautomation

Usage (CLI):
    # Find and click a button named "Submit" in the foreground window
    python executor.py click --name "Submit" --control_type ButtonControl

    # Type into the Chrome address bar
    python executor.py type --name "Address and search bar" --control_type EditControl --text "https://google.com"

    # Invoke a link by automation_id
    python executor.py invoke --automation_id "save-btn"

    # Find element in a specific window (not just foreground)
    python executor.py click --name "Submit" --window "Untitled - Notepad"

    # Inspect: find element and print its full info
    python executor.py inspect --name "Submit" --control_type ButtonControl

    # List all matching elements (when name is ambiguous)
    python executor.py find --name "Close"

    # Find by index when multiple matches exist (0-based)
    python executor.py click --name "Close" --index 2

    # Interactive REPL
    python executor.py repl

Usage (as a library):
    from executor import Executor, ElementQuery

    exe = Executor()

    # Find and click
    query = ElementQuery(name="Submit", control_type="ButtonControl")
    result = exe.click(query)

    # Find in a specific window and type
    query = ElementQuery(name="Address and search bar", control_type="EditControl")
    result = exe.type_text(query, "hello world", window_name="Google Chrome")
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

try:
    import uiautomation as auto
except ImportError:
    print(
        "error: 'uiautomation' package is required.\n"
        "       Install it with:  pip install uiautomation",
        file=sys.stderr,
    )
    sys.exit(1)


# ── Control type name → uiautomation class mapping ──────────────────────
_CONTROL_TYPE_MAP: dict[str, type] = {
    "ButtonControl": auto.ButtonControl,
    "EditControl": auto.EditControl,
    "TextControl": auto.TextControl,
    "CheckBoxControl": auto.CheckBoxControl,
    "RadioButtonControl": auto.RadioButtonControl,
    "ComboBoxControl": auto.ComboBoxControl,
    "ListControl": auto.ListControl,
    "ListItemControl": auto.ListItemControl,
    "MenuControl": auto.MenuControl,
    "MenuItemControl": auto.MenuItemControl,
    "TabControl": auto.TabControl,
    "TabItemControl": auto.TabItemControl,
    "TreeControl": auto.TreeControl,
    "TreeItemControl": auto.TreeItemControl,
    "HyperlinkControl": auto.HyperlinkControl,
    "ImageControl": auto.ImageControl,
    "DocumentControl": auto.DocumentControl,
    "GroupControl": auto.GroupControl,
    "PaneControl": auto.PaneControl,
    "ScrollBarControl": auto.ScrollBarControl,
    "SliderControl": auto.SliderControl,
    "SpinnerControl": auto.SpinnerControl,
    "ToolBarControl": auto.ToolBarControl,
    "ToolTipControl": auto.ToolTipControl,
    "WindowControl": auto.WindowControl,
    "DataGridControl": auto.DataGridControl,
    "DataItemControl": auto.DataItemControl,
    "HeaderControl": auto.HeaderControl,
    "HeaderItemControl": auto.HeaderItemControl,
    "CustomControl": auto.CustomControl,
}


# ═══════════════════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ElementQuery:
    """Describes *what* to find. Any combination of fields narrows the search;
    fields left as None are not used as filters."""
    name: str | None = None
    control_type: str | None = None       # e.g. "ButtonControl", "EditControl"
    automation_id: str | None = None
    class_name: str | None = None
    index: int = 0                         # pick the Nth match (0-based)
    search_depth: int = 20                 # max tree depth to search

    def describe(self) -> str:
        parts = []
        if self.name is not None:
            parts.append(f"name={self.name!r}")
        if self.control_type is not None:
            parts.append(f"control_type={self.control_type}")
        if self.automation_id is not None:
            parts.append(f"automation_id={self.automation_id!r}")
        if self.class_name is not None:
            parts.append(f"class_name={self.class_name!r}")
        if self.index:
            parts.append(f"index={self.index}")
        return ", ".join(parts) or "(empty query)"


@dataclass
class BoundingRect:
    left: int
    top: int
    right: int
    bottom: int

    @classmethod
    def from_control(cls, ctrl: auto.Control) -> BoundingRect | None:
        r = ctrl.BoundingRectangle
        if r.width() == 0 and r.height() == 0:
            return None
        return cls(left=r.left, top=r.top, right=r.right, bottom=r.bottom)


@dataclass
class ElementInfo:
    name: str
    control_type: str
    localized_control_type: str
    automation_id: str
    class_name: str
    bounding_rectangle: dict | None
    is_enabled: bool
    is_offscreen: bool
    patterns: list[str]
    value: str | None = None
    clickable_point: dict | None = None

    @classmethod
    def from_control(cls, ctrl: auto.Control) -> ElementInfo:
        rect = BoundingRect.from_control(ctrl)
        patterns = _get_supported_patterns(ctrl)
        value = _try_get_value(ctrl)
        cp = None
        try:
            cp_raw = ctrl.GetClickablePoint()
            if cp_raw:
                cp = {"x": cp_raw[0], "y": cp_raw[1]}
        except Exception:
            pass

        return cls(
            name=ctrl.Name,
            control_type=ctrl.ControlTypeName,
            localized_control_type=ctrl.LocalizedControlTypeName,
            automation_id=ctrl.AutomationId,
            class_name=ctrl.ClassName,
            bounding_rectangle=asdict(rect) if rect else None,
            is_enabled=ctrl.IsEnabled,
            is_offscreen=ctrl.IsOffscreen,
            patterns=patterns,
            value=value,
            clickable_point=cp,
        )


@dataclass
class WindowInfo:
    name: str
    control_type: str
    class_name: str
    automation_id: str
    pid: int
    bounding_rectangle: dict | None

    @classmethod
    def from_control(cls, ctrl: auto.Control) -> WindowInfo:
        rect = BoundingRect.from_control(ctrl)
        return cls(
            name=ctrl.Name,
            control_type=ctrl.ControlTypeName,
            class_name=ctrl.ClassName,
            automation_id=ctrl.AutomationId,
            pid=ctrl.ProcessId,
            bounding_rectangle=asdict(rect) if rect else None,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _get_supported_patterns(ctrl: auto.Control) -> list[str]:
    patterns = []
    checks = [
        ("InvokePattern", auto.PatternId.InvokePatternId),
        ("ValuePattern", auto.PatternId.ValuePatternId),
        ("TogglePattern", auto.PatternId.TogglePatternId),
        ("SelectionItemPattern", auto.PatternId.SelectionItemPatternId),
        ("ExpandCollapsePattern", auto.PatternId.ExpandCollapsePatternId),
        ("ScrollPattern", auto.PatternId.ScrollPatternId),
        ("ScrollItemPattern", auto.PatternId.ScrollItemPatternId),
    ]
    for name, pid in checks:
        try:
            if ctrl.GetPattern(pid):
                patterns.append(name)
        except Exception:
            pass
    return patterns


def _try_get_value(ctrl: auto.Control) -> str | None:
    try:
        pat = ctrl.GetPattern(auto.PatternId.ValuePatternId)
        if pat:
            return ctrl.GetPropertyValue(auto.PropertyId.ValueValuePropertyId)
    except Exception:
        pass
    return None


def _timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="microseconds")


def _build_ancestor_chain(ctrl: auto.Control) -> list[str]:
    ancestors: list[str] = []
    desktop_hwnd = auto.GetRootControl().NativeWindowHandle
    current = ctrl.GetParentControl()
    while current:
        if current.NativeWindowHandle == desktop_hwnd:
            break
        ancestors.append(current.Name or "")
        current = current.GetParentControl()
    ancestors.reverse()
    return ancestors


def _center_of_rect(rect: dict) -> tuple[int, int]:
    x = (rect["left"] + rect["right"]) // 2
    y = (rect["top"] + rect["bottom"]) // 2
    return x, y


# ═══════════════════════════════════════════════════════════════════════════
# Core Executor
# ═══════════════════════════════════════════════════════════════════════════

class ElementNotFoundError(Exception):
    pass


class Executor:
    """Live Windows UI Automation executor — finds elements by description,
    not coordinates."""

    # ── Window targeting ──────────────────────────────────────────────

    @staticmethod
    def get_foreground_window() -> auto.WindowControl:
        """The current foreground window."""
        return auto.GetForegroundControl()

    @staticmethod
    def find_window(name: str | None = None, class_name: str | None = None) -> auto.WindowControl:
        """Find a top-level window by name and/or class_name.
        Returns foreground window if both are None."""
        if name is None and class_name is None:
            return auto.GetForegroundControl()

        conditions = {}
        if name is not None:
            conditions["SubName"] = name   # substring match on title
        if class_name is not None:
            conditions["ClassName"] = class_name

        root = auto.GetRootControl()
        window = root.WindowControl(searchDepth=1, **conditions)
        if not window.Exists(maxSearchSeconds=3):
            raise ElementNotFoundError(
                f"Window not found: name={name!r}, class_name={class_name!r}"
            )
        return window

    # ── Element search ────────────────────────────────────────────────

    def _build_search_conditions(self, query: ElementQuery) -> dict:
        conds: dict[str, Any] = {}
        if query.name is not None:
            conds["Name"] = query.name
        if query.automation_id is not None:
            conds["AutomationId"] = query.automation_id
        if query.class_name is not None:
            conds["ClassName"] = query.class_name
        return conds

    def find_element(
        self,
        query: ElementQuery,
        window_name: str | None = None,
        window_class: str | None = None,
    ) -> auto.Control:
        """Find a single matching element in the target window's tree."""
        window = self.find_window(name=window_name, class_name=window_class)
        conds = self._build_search_conditions(query)

        # Pick the typed search method if control_type is specified
        ctrl_cls_name = "Control"
        if query.control_type and query.control_type in _CONTROL_TYPE_MAP:
            ctrl_cls_name = query.control_type

        if query.index == 0:
            search_fn = getattr(window, ctrl_cls_name, window.Control)
            element = search_fn(searchDepth=query.search_depth, **conds)

            if not element.Exists(maxSearchSeconds=5):
                raise ElementNotFoundError(
                    f"Element not found: {query.describe()} "
                    f"in window {window.Name!r}"
                )
            return element

        # index > 0: collect matches and pick Nth
        matches = self.find_all_elements(query, window_name, window_class)
        if query.index >= len(matches):
            raise ElementNotFoundError(
                f"Element index {query.index} out of range — "
                f"only {len(matches)} match(es) for: {query.describe()}"
            )
        return matches[query.index]

    def find_all_elements(
        self,
        query: ElementQuery,
        window_name: str | None = None,
        window_class: str | None = None,
    ) -> list[auto.Control]:
        """Return ALL matching elements (useful for disambiguation)."""
        window = self.find_window(name=window_name, class_name=window_class)
        conds = self._build_search_conditions(query)

        ctrl_cls_name = "Control"
        if query.control_type and query.control_type in _CONTROL_TYPE_MAP:
            ctrl_cls_name = query.control_type

        search_fn = getattr(window, ctrl_cls_name, window.Control)
        results = []
        idx = 1
        while True:
            el = search_fn(
                searchDepth=query.search_depth,
                foundIndex=idx,
                **conds,
            )
            if not el.Exists(maxSearchSeconds=1):
                break
            results.append(el)
            idx += 1
        return results

    # ── Inspect ───────────────────────────────────────────────────────

    def inspect(
        self,
        query: ElementQuery,
        window_name: str | None = None,
        window_class: str | None = None,
    ) -> dict:
        ctrl = self.find_element(query, window_name, window_class)
        window = self.find_window(name=window_name, class_name=window_class)
        info = ElementInfo.from_control(ctrl)
        win_info = WindowInfo.from_control(window)
        ancestors = _build_ancestor_chain(ctrl)

        return {
            "query": query.describe(),
            "timestamp": _timestamp(),
            "active_window": asdict(win_info),
            "element": asdict(info),
            "ancestors": ancestors,
        }

    def find(
        self,
        query: ElementQuery,
        window_name: str | None = None,
        window_class: str | None = None,
    ) -> list[dict]:
        controls = self.find_all_elements(query, window_name, window_class)
        return [{"index": i, **asdict(ElementInfo.from_control(c))} for i, c in enumerate(controls)]

    # ── Actions ───────────────────────────────────────────────────────

    def _get_click_point(self, ctrl: auto.Control) -> tuple[int, int]:
        try:
            pt = ctrl.GetClickablePoint()
            if pt:
                return int(pt[0]), int(pt[1])
        except Exception:
            pass
        rect = BoundingRect.from_control(ctrl)
        if rect:
            return _center_of_rect(asdict(rect))
        raise ElementNotFoundError("Element has no clickable point or bounding rect")

    def click(
        self, query: ElementQuery,
        window_name: str | None = None, window_class: str | None = None,
    ) -> dict:
        ctrl = self.find_element(query, window_name, window_class)
        info = ElementInfo.from_control(ctrl)

        if "InvokePattern" in info.patterns:
            ctrl.GetInvokePattern().Invoke()
            method = "InvokePattern"
        else:
            x, y = self._get_click_point(ctrl)
            auto.Click(x, y)
            method = f"mouse_click({x},{y})"

        return {
            "action": "click", "query": query.describe(),
            "element_name": info.name, "control_type": info.control_type,
            "method": method,
        }

    def double_click(
        self, query: ElementQuery,
        window_name: str | None = None, window_class: str | None = None,
    ) -> dict:
        ctrl = self.find_element(query, window_name, window_class)
        info = ElementInfo.from_control(ctrl)
        x, y = self._get_click_point(ctrl)
        auto.Click(x, y, clickTimes=2)
        return {"action": "double_click", "query": query.describe(),
                "element_name": info.name, "point": {"x": x, "y": y}}

    def right_click(
        self, query: ElementQuery,
        window_name: str | None = None, window_class: str | None = None,
    ) -> dict:
        ctrl = self.find_element(query, window_name, window_class)
        info = ElementInfo.from_control(ctrl)
        x, y = self._get_click_point(ctrl)
        auto.RightClick(x, y)
        return {"action": "right_click", "query": query.describe(),
                "element_name": info.name, "point": {"x": x, "y": y}}

    def type_text(
        self, query: ElementQuery, text: str, *, clear_first: bool = False,
        window_name: str | None = None, window_class: str | None = None,
    ) -> dict:
        ctrl = self.find_element(query, window_name, window_class)
        info = ElementInfo.from_control(ctrl)

        if "ValuePattern" in info.patterns:
            vp = ctrl.GetValuePattern()
            if clear_first:
                vp.SetValue("")
            vp.SetValue(text)
            method = "ValuePattern.SetValue"
        else:
            x, y = self._get_click_point(ctrl)
            auto.Click(x, y)
            time.sleep(0.1)
            if clear_first:
                auto.SendKeys("{Ctrl}a{Delete}", waitTime=0.05)
            auto.SendKeys(text, interval=0.02, waitTime=0.05)
            method = "click_and_SendKeys"

        return {
            "action": "type_text", "query": query.describe(),
            "text": text, "element_name": info.name,
            "control_type": info.control_type, "method": method,
        }

    def invoke(
        self, query: ElementQuery,
        window_name: str | None = None, window_class: str | None = None,
    ) -> dict:
        ctrl = self.find_element(query, window_name, window_class)
        info = ElementInfo.from_control(ctrl)
        if "InvokePattern" in info.patterns:
            ctrl.GetInvokePattern().Invoke()
        else:
            x, y = self._get_click_point(ctrl)
            auto.Click(x, y)
        return {"action": "invoke", "query": query.describe(),
                "element_name": info.name, "control_type": info.control_type}

    def toggle(
        self, query: ElementQuery,
        window_name: str | None = None, window_class: str | None = None,
    ) -> dict:
        ctrl = self.find_element(query, window_name, window_class)
        info = ElementInfo.from_control(ctrl)
        if "TogglePattern" in info.patterns:
            tp = ctrl.GetTogglePattern()
            old = tp.ToggleState
            tp.Toggle()
            return {"action": "toggle", "query": query.describe(),
                    "element_name": info.name, "old_state": old, "new_state": tp.ToggleState}
        else:
            x, y = self._get_click_point(ctrl)
            auto.Click(x, y)
            return {"action": "toggle", "query": query.describe(),
                    "element_name": info.name, "method": "click_fallback"}

    def expand_collapse(
        self, query: ElementQuery,
        window_name: str | None = None, window_class: str | None = None,
    ) -> dict:
        ctrl = self.find_element(query, window_name, window_class)
        info = ElementInfo.from_control(ctrl)
        if "ExpandCollapsePattern" in info.patterns:
            ecp = ctrl.GetExpandCollapsePattern()
            if ecp.ExpandCollapseState == auto.ExpandCollapseState.Collapsed:
                ecp.Expand(); action = "expanded"
            else:
                ecp.Collapse(); action = "collapsed"
            return {"action": action, "query": query.describe(), "element_name": info.name}
        else:
            x, y = self._get_click_point(ctrl)
            auto.Click(x, y)
            return {"action": "expand_collapse", "query": query.describe(),
                    "element_name": info.name, "method": "click_fallback"}

    def set_focus(
        self, query: ElementQuery,
        window_name: str | None = None, window_class: str | None = None,
    ) -> dict:
        ctrl = self.find_element(query, window_name, window_class)
        info = ElementInfo.from_control(ctrl)
        ctrl.SetFocus()
        return {"action": "set_focus", "query": query.describe(), "element_name": info.name}

    def send_keys(self, keys: str) -> dict:
        auto.SendKeys(keys, interval=0.02, waitTime=0.05)
        return {"action": "send_keys", "keys": keys}


# ═══════════════════════════════════════════════════════════════════════════
# REPL
# ═══════════════════════════════════════════════════════════════════════════

def _parse_query_from_parts(parts: list[str]) -> tuple[ElementQuery, str | None, str | None]:
    """Parse --flags from token list. Returns (query, window_name, text_value)."""
    import shlex
    q = ElementQuery()
    window_name = None
    text_val = None
    i = 0
    while i < len(parts):
        tok = parts[i]
        if tok == "--name" and i + 1 < len(parts):
            q.name = parts[i + 1]; i += 2
        elif tok == "--control_type" and i + 1 < len(parts):
            q.control_type = parts[i + 1]; i += 2
        elif tok == "--automation_id" and i + 1 < len(parts):
            q.automation_id = parts[i + 1]; i += 2
        elif tok == "--class_name" and i + 1 < len(parts):
            q.class_name = parts[i + 1]; i += 2
        elif tok == "--index" and i + 1 < len(parts):
            q.index = int(parts[i + 1]); i += 2
        elif tok == "--window" and i + 1 < len(parts):
            window_name = parts[i + 1]; i += 2
        elif tok == "--text" and i + 1 < len(parts):
            text_val = parts[i + 1]; i += 2
        else:
            i += 1
    return q, window_name, text_val


def _repl(exe: Executor) -> None:
    print("Executor REPL — type 'help' for commands, 'quit' to exit.\n")
    while True:
        try:
            line = input("executor> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); break
        if not line:
            continue

        import shlex
        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split()

        cmd = parts[0].lower()
        try:
            if cmd in ("quit", "exit", "q"):
                break
            elif cmd == "help":
                print(
                    "Flags:  --name X  --control_type X  --automation_id X\n"
                    "        --class_name X  --index N  --window X  --text X\n\n"
                    "Commands:\n"
                    "  inspect  ...flags         — full element info\n"
                    "  find     ...flags         — list all matches\n"
                    "  click    ...flags         — click element\n"
                    "  dblclick ...flags         — double-click\n"
                    "  rclick   ...flags         — right-click\n"
                    "  type     ...flags --text X — type text\n"
                    "  invoke   ...flags         — invoke (button/link)\n"
                    "  toggle   ...flags         — toggle checkbox\n"
                    "  expand   ...flags         — expand/collapse\n"
                    "  focus    ...flags         — set keyboard focus\n"
                    "  keys     <keystring>      — send raw keys\n"
                    "  window                    — foreground window info\n"
                    "  quit"
                )
            elif cmd == "window":
                fg = exe.get_foreground_window()
                print(json.dumps(asdict(WindowInfo.from_control(fg)), indent=2))
            elif cmd == "keys":
                print(json.dumps(exe.send_keys(" ".join(parts[1:])), indent=2))
            else:
                query, win, text_val = _parse_query_from_parts(parts[1:])
                dispatch = {
                    "inspect": lambda: exe.inspect(query, window_name=win),
                    "find": lambda: exe.find(query, window_name=win),
                    "click": lambda: exe.click(query, window_name=win),
                    "dblclick": lambda: exe.double_click(query, window_name=win),
                    "rclick": lambda: exe.right_click(query, window_name=win),
                    "invoke": lambda: exe.invoke(query, window_name=win),
                    "toggle": lambda: exe.toggle(query, window_name=win),
                    "expand": lambda: exe.expand_collapse(query, window_name=win),
                    "focus": lambda: exe.set_focus(query, window_name=win),
                }
                if cmd == "type":
                    if text_val is None:
                        print("error: --text required"); continue
                    res = exe.type_text(query, text_val, window_name=win)
                elif cmd in dispatch:
                    res = dispatch[cmd]()
                else:
                    print(f"Unknown: {cmd}"); continue
                print(json.dumps(res, indent=2, ensure_ascii=False))
        except ElementNotFoundError as exc:
            print(f"not found: {exc}")
        except Exception as exc:
            print(f"error: {exc}")


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def _add_query_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--name", default=None, help="Element Name")
    p.add_argument("--control_type", default=None, help="e.g. ButtonControl, EditControl")
    p.add_argument("--automation_id", default=None, help="AutomationId")
    p.add_argument("--class_name", default=None, help="ClassName")
    p.add_argument("--index", type=int, default=0, help="0-based index among matches")
    p.add_argument("--window", default=None, help="Target window name (default: foreground)")
    p.add_argument("--depth", type=int, default=20, help="Max search depth")


def _query_from_args(args: argparse.Namespace) -> ElementQuery:
    return ElementQuery(
        name=args.name, control_type=args.control_type,
        automation_id=args.automation_id, class_name=args.class_name,
        index=args.index, search_depth=args.depth,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Live UI executor — find by element description")
    sub = parser.add_subparsers(dest="command")

    for cmd in ("inspect", "find", "click", "dblclick", "rclick", "invoke", "toggle", "expand", "focus"):
        p = sub.add_parser(cmd)
        _add_query_args(p)

    p_type = sub.add_parser("type")
    _add_query_args(p_type)
    p_type.add_argument("--text", required=True)
    p_type.add_argument("--clear", action="store_true")

    p_keys = sub.add_parser("keys")
    p_keys.add_argument("keystring")

    sub.add_parser("window")
    sub.add_parser("repl")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help(); return 0

    exe = Executor()
    try:
        if args.command == "repl":
            _repl(exe); return 0
        if args.command == "window":
            fg = exe.get_foreground_window()
            print(json.dumps(asdict(WindowInfo.from_control(fg)), indent=2)); return 0
        if args.command == "keys":
            print(json.dumps(exe.send_keys(args.keystring), indent=2)); return 0

        query = _query_from_args(args)
        win = args.window
        dispatch = {
            "inspect": lambda: exe.inspect(query, window_name=win),
            "find": lambda: exe.find(query, window_name=win),
            "click": lambda: exe.click(query, window_name=win),
            "dblclick": lambda: exe.double_click(query, window_name=win),
            "rclick": lambda: exe.right_click(query, window_name=win),
            "invoke": lambda: exe.invoke(query, window_name=win),
            "toggle": lambda: exe.toggle(query, window_name=win),
            "expand": lambda: exe.expand_collapse(query, window_name=win),
            "focus": lambda: exe.set_focus(query, window_name=win),
        }
        if args.command == "type":
            res = exe.type_text(query, args.text, clear_first=args.clear, window_name=win)
        else:
            res = dispatch[args.command]()
        print(json.dumps(res, indent=2, ensure_ascii=False))
    except ElementNotFoundError as exc:
        print(f"not found: {exc}", file=sys.stderr); return 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())