
# run the script just run with the command uv run python executor.py
from __future__ import annotations
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# Guarantee ``action_classes`` imports regardless of the current directory.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from action_classes import ActionLevel0, ActionLevel1  # noqa: E402rones

# --------------------------------------------------------------------------- 
# Run configuration -- edit these, then just `python executor.py`
# ------------------------------------------------------------3figh--m-------------
# ACTIONS_FILE = _HERE: / "samples" / "timeline_new_case.json"

ACTIONS_FILE = Path(r"C:\Users\majid\Desktop\Executor\sample\Daraz_Payment.json")
DELAY_AFTER = 2.0           # seconds to wait after every action 
CONTINUE_ON_ERROR = False    # True: keep going past a failing action
DRY_RUN = False              # True: log actions only, no mouse/keyboard
ENABLE_FAILSAFE = True       # PyAutoGUI top-left-corner abort
MINIMIZE_WINDOWS_FIRST = False  # press Win+D to show the desktop before step 1

# Timeline-recording options (ignored for the flat-list input shape)
TARGET_RESOLUTION = None     # (width, height) to scale recorded coords onto;
                             # None = auto-detect this screen via PyAutoGUI
SCROLL_UNITS_PER_NOTCH = 120  # PyAutoGUI wheel units per recorded scroll notch.
                              # Windows passes this straight to mouse_event as
                              # dwData, where one wheel notch is WHEEL_DELTA=120.
COALESCE_SCROLLS = False     # merge a run of identical wheel notches into one
TYPING_INTERVAL = 0.02       # seconds between characters while typing
FREE_TIME_MAX_WAIT = 10.0    # cap (seconds) on a replayed FreeTime gap;
                             # 0 = drop FreeTime items entirely


# ---------------------------------------------------------------------------
# Timeline-recording adapter
#
# Turns one `{"items": [...]}` recording into the flat list of primitive
# actions that NaiveExecutor already knows how to run. Only `click`, `scroll`
# and `FreeTime` items appear in the two reference recordings; anything else
# is skipped with a warning rather than aborting a long replay.
# ---------------------------------------------------------------------------

_BUTTON_BY_VALUE = {
    "mouse_left": "left",
    "mouse_right": "right",
    "mouse_middle": "middle",
}


def _click_button(value: str, event: dict[str, Any]) -> str | None:
    """Resolve a click item's mouse button from its value, then its detail."""
    button = _BUTTON_BY_VALUE.get(value)
    if button is not None:
        return button
    detail = str(event.get("detail") or "").strip().casefold()
    if detail in {"left", "right", "middle"}:
        return detail
    return None


def _freetime_seconds(item: dict[str, Any]) -> float | None:
    """Duration of a FreeTime gap, from `totaltime` or the start/end stamps."""
    total = str(item.get("totaltime") or "").strip().rstrip("s")
    try:
        return float(total)
    except ValueError:
        pass
    start, end = item.get("starttime"), item.get("endtime")
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        return (
            datetime.fromisoformat(end) - datetime.fromisoformat(start)
        ).total_seconds()
    except ValueError:
        return None


def _parse_resolution(text: Any) -> tuple[int, int] | None:
    if not isinstance(text, str) or "x" not in text.casefold():
        return None
    left, _, right = text.casefold().partition("x")
    try:
        return int(left.strip()), int(right.strip())
    except ValueError:
        return None


def _recorded_resolution(item: dict[str, Any]) -> tuple[int, int] | None:
    event = (item.get("events") or [{}])[0]
    from_event = _parse_resolution(event.get("screen_resolution"))
    if from_event:
        return from_event
    size = (item.get("omniparser") or {}).get("image_size")
    if isinstance(size, (list, tuple)) and len(size) == 2:
        try:
            return int(size[0]), int(size[1])
        except (TypeError, ValueError):
            return None
    return None


def _scale_point(
    x: float,
    y: float,
    recorded: tuple[int, int] | None,
    target: tuple[int, int] | None,
) -> tuple[int, int]:
    if not recorded or not target or recorded == target:
        return round(x), round(y)
    return (
        round(x * target[0] / recorded[0]),
        round(y * target[1] / recorded[1]),
    )


def _scroll_notches(item: dict[str, Any]) -> tuple[int, int]:
    """Return one item's wheel delta as (dx_notches, dy_notches)."""
    omni = item.get("omniparser") or {}
    if omni.get("scroll_dx") is not None or omni.get("scroll_dy") is not None:
        return int(omni.get("scroll_dx") or 0), int(omni.get("scroll_dy") or 0)

    detail = (item.get("events") or [{}])[0].get("detail") or ""
    dx = dy = 0
    for part in detail.split(","):
        key, _, value = part.strip().partition("=")
        try:
            if key.strip() == "dx":
                dx = int(float(value))
            elif key.strip() == "dy":
                dy = int(float(value))
        except ValueError:
            continue
    return dx, dy


def _scroll_direction_fallback(value: str) -> tuple[int, int]:
    """Wheel delta implied by the item's `value` when `detail` carries none."""
    if value == "scroll_down":
        return 0, -1
    if value == "scroll_up":
        return 0, 1
    if value == "scroll_left":
        return -1, 0
    if value == "scroll_right":
        return 1, 0
    return 0, 0


def normalize_timeline(
    raw: dict[str, Any],
    target_resolution: tuple[int, int] | None,
    *,
    scroll_units_per_notch: int,
    coalesce: bool,
    typing_interval: float = 0.02,
    free_time_max: float = 0.0,
    log: Callable[[str], None],
) -> list[dict[str, Any]]:
    items = raw.get("items")
    if not isinstance(items, list):
        raise ValueError("timeline recording must contain an 'items' list.")

    actions: list[dict[str, Any]] = []
    position = 0
    count = len(items)
    while position < count:
        item = items[position]
        item = item if isinstance(item, dict) else {}
        src = item.get("index", position)
        item_type = str(item.get("type") or "").strip()
        value = str(item.get("value") or "").strip().casefold()
        event = (item.get("events") or [None])[0]

        if item_type == "FreeTime":
            seconds = _freetime_seconds(item) if free_time_max > 0 else None
            if seconds is None or seconds <= 0:
                log(f"  (item {src}) skip FreeTime")
            else:
                seconds = min(seconds, free_time_max)
                actions.append(
                    {"type": "wait", "seconds": round(seconds, 3), "src": src}
                )
            position += 1
            continue

        if item_type == "click":
            if not isinstance(event, dict) or "x" not in event:
                log(f"  WARNING item {src}: click has no coordinates, skipped")
                position += 1
                continue
            button = _click_button(value, event)
            if button is None:
                log(
                    f"  WARNING item {src}: unknown click value "
                    f"{item.get('value')!r}, skipped"
                )
                position += 1
                continue
            x, y = _scale_point(
                event["x"], event["y"],
                _recorded_resolution(item), target_resolution,
            )
            actions.append(
                {"type": "click", "x": x, "y": y, "button": button, "src": src}
            )
            position += 1
            continue

        if item_type == "scroll":
            if not isinstance(event, dict) or "x" not in event:
                log(f"  WARNING item {src}: scroll has no anchor, skipped")
                position += 1
                continue
            anchor = (event["x"], event["y"])
            dx_notches, dy_notches = _scroll_notches(item)
            if dx_notches == 0 and dy_notches == 0:
                dx_notches, dy_notches = _scroll_direction_fallback(value)

            end = position + 1
            if coalesce:
                while end < count:
                    nxt = items[end] if isinstance(items[end], dict) else {}
                    nxt_event = (nxt.get("events") or [None])[0]
                    same = (
                        str(nxt.get("type") or "").strip() == "scroll"
                        and str(nxt.get("value") or "").strip().casefold()
                        == value
                        and isinstance(nxt_event, dict)
                        and (nxt_event.get("x"), nxt_event.get("y")) == anchor
                    )
                    if not same:
                        break
                    add_dx, add_dy = _scroll_notches(nxt)
                    if add_dx == 0 and add_dy == 0:
                        add_dx, add_dy = _scroll_direction_fallback(value)
                    dx_notches += add_dx
                    dy_notches += add_dy
                    end += 1

            x, y = _scale_point(
                anchor[0], anchor[1],
                _recorded_resolution(item), target_resolution,
            )
            span = (
                str(src)
                if end == position + 1
                else f"{src}..{items[end - 1].get('index', end - 1)}"
            )
            actions.append(
                {
                    "type": "scroll_at",
                    "x": x,
                    "y": y,
                    "dx": dx_notches * scroll_units_per_notch,
                    "dy": dy_notches * scroll_units_per_notch,
                    "notches": abs(dy_notches) or abs(dx_notches),
                    "src": span,
                }
            )
            position = end
            continue

        if item_type == "Typing":
            # A Typing node's `matched_element` is where OmniParser *found* the
            # typed text afterwards (a suggestion row, an OCR'd label), not the
            # field that had focus. Clicking it would move focus off the field
            # the preceding click opened, so type into the current focus.
            text = str(item.get("value") or "")
            if not text:
                log(f"  WARNING item {src}: Typing has no value, skipped")
            else:
                actions.append(
                    {
                        "type": "type_text",
                        "text": text,
                        "interval": typing_interval,
                        "src": src,
                    }
                )
            position += 1
            continue

        if item_type == "special":
            tokens = [
                token.strip().casefold()
                for token in str(item.get("value") or "").split(",")
                if token.strip()
            ]
            if not tokens:
                log(f"  WARNING item {src}: special has no key, skipped")
            else:
                if len(set(tokens)) > 1:
                    log(
                        f"  WARNING item {src}: special {item.get('value')!r} "
                        f"has mixed keys; pressing {tokens[0]!r} x{len(tokens)} "
                        "(key chords are not handled)"
                    )
                actions.append(
                    {
                        "type": "key_press",
                        "key": tokens[0],
                        "repeat": len(tokens),
                        "src": src,
                    }
                )
            position += 1
            continue

        if item_type == "KeyPress":
            key = str(item.get("value") or "").strip()
            if not key:
                log(f"  WARNING item {src}: KeyPress has no key, skipped")
            else:
                actions.append(
                    {"type": "key_press", "key": key, "src": src}
                )
            position += 1
            continue

        log(f"  WARNING item {src}: unsupported type {item_type!r}, skipped")
        position += 1

    return actions


class ActionError(RuntimeError):
    """One action failed while replaying and execution was aborted."""

    def __init__(self, index: int, action_type: str, cause: Exception) -> None:
        self.index = index
        self.action_type = action_type
        self.cause = cause
        super().__init__(
            f"action #{index} ({action_type or 'unknown'}) failed: "
            f"{type(cause).__name__}: {cause}"
        )


@dataclass
class ActionResult:
    """Outcome of a single replayed action."""

    index: int
    action_type: str
    ok: bool
    error: str | None = None


class NaiveExecutor:
    """Replay a flat JSON list of coordinate-based actions, one by one."""

    def __init__(
        self,
        *,
        delay_after: float = DELAY_AFTER,
        enable_failsafe: bool = True,
        continue_on_error: bool = False,
        dry_run: bool = False,
        target_resolution: tuple[int, int] | None = None,
        scroll_units_per_notch: int = SCROLL_UNITS_PER_NOTCH,
        coalesce_scrolls: bool = COALESCE_SCROLLS,
        typing_interval: float = TYPING_INTERVAL,
        free_time_max_wait: float = FREE_TIME_MAX_WAIT,
        minimize_first: bool = MINIMIZE_WINDOWS_FIRST,
        pyautogui_backend: Any | None = None,
        logger: Callable[[str], None] = print,
    ) -> None:
        if delay_after < 0:
            raise ValueError("delay_after cannot be negative.")
        self.delay_after = float(delay_after)
        self.continue_on_error = bool(continue_on_error)
        self.dry_run = bool(dry_run)
        self.minimize_first = bool(minimize_first)
        self._target_resolution = target_resolution
        self._scroll_units_per_notch = int(scroll_units_per_notch)
        self._coalesce_scrolls = bool(coalesce_scrolls)
        self._typing_interval = float(typing_interval)
        self._free_time_max_wait = max(float(free_time_max_wait), 0.0)
        self._log = logger

        self.level_0 = ActionLevel0(
            pyautogui_backend=pyautogui_backend,
            enable_failsafe=enable_failsafe,
        )
        self.level_1 = ActionLevel1(self.level_0)

        self._handlers: dict[str, Callable[[dict[str, Any]], None]] = {
            # Level 0
            "click": self._do_click,
            "move": self._do_move,
            "mouse_down": self._do_mouse_down,
            "mouse_up": self._do_mouse_up,
            "key_press": self._do_key_press,
            "keypress": self._do_key_press,  # alias for the Level-0 method name
            "key_down": self._do_key_down,
            "key_up": self._do_key_up,
            "scroll": self._do_scroll,
            "wait": self._do_wait,
            # Level 1
            "type": self._do_type,
            "type_text": self._do_type_text,
            "combo_key": self._do_combo_key,
            "drag": self._do_drag,
            "hover": self._do_hover,
            "scroll_step": self._do_scroll_step,
            # composed: move the pointer to an anchor, then turn the wheel
            "scroll_at": self._do_scroll_at,
        }

    # ------------------------------------------------------------------ run --

    def run_file(self, path: str | Path) -> list[ActionResult]:
        """Load a JSON file and replay its actions."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return self.run(self._load_actions(raw))

    def _load_actions(self, raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, dict) and isinstance(raw.get("items"), list):
            target = self._target_resolution or self._detect_resolution()
            if target is None:
                self._log(
                    "WARNING: screen resolution unknown; recorded coordinates "
                    "used as-is (no scaling)."
                )
            else:
                self._log(f"Scaling recorded coordinates onto {target[0]}x{target[1]}.")
            return normalize_timeline(
                raw,
                target,
                scroll_units_per_notch=self._scroll_units_per_notch,
                coalesce=self._coalesce_scrolls,
                typing_interval=self._typing_interval,
                free_time_max=self._free_time_max_wait,
                log=self._log,
            )
        return self._extract_actions(raw)

    @staticmethod
    def _detect_resolution() -> tuple[int, int] | None:
        try:
            import pyautogui

            size = pyautogui.size()
            return int(size[0]), int(size[1])
        except Exception:
            return None

    def run(self, actions: list[dict[str, Any]]) -> list[ActionResult]:
        """Replay an already-parsed list of action objects in order."""
        if not isinstance(actions, list):
            raise ValueError("actions must be a list.")

        if self.minimize_first:
            self._log("Minimizing all applications")
            if not self.dry_run:
                self.level_1.combo_key(("winleft", "d"))
                self.level_0.wait(1.0)  # let the show-desktop animation settle

        total = len(actions)
        results: list[ActionResult] = []
        for index, action in enumerate(actions, start=1):
            action_type = ""
            try:
                if not isinstance(action, dict):
                    raise ValueError("each action must be a JSON object.")
                action_type = str(action.get("type") or "").strip()
                handler = self._handlers.get(action_type)
                if handler is None:
                    raise ValueError(
                        f"unknown action type {action_type!r}; supported: "
                        + ", ".join(sorted(set(self._handlers)))
                    )
                label = action_type
                if "src" in action:
                    label = f"{action_type} (item {action['src']})"
                self._log(
                    f"[{index}/{total}] {label} "
                    f"{self._describe(action)}".rstrip()
                )
                if not self.dry_run:
                    handler(action)
                results.append(ActionResult(index, action_type, ok=True))
            except Exception as exc:  # naive executor: report every failure
                results.append(
                    ActionResult(index, action_type, ok=False, error=str(exc))
                )
                if not self.continue_on_error:
                    raise ActionError(index, action_type, exc) from exc
                self._log(f"[{index}/{total}] FAILED: {exc}")

            if self.delay_after and not self.dry_run:
                time.sleep(self.delay_after)

        return results

    # -------------------------------------------------------------- parsing --

    @staticmethod
    def _extract_actions(raw: Any) -> list[dict[str, Any]]:
        actions = raw.get("actions") if isinstance(raw, dict) else raw
        if not isinstance(actions, list):
            raise ValueError(
                "JSON must be a list of actions or an object with an "
                "'actions' list."
            )
        return actions

    @staticmethod
    def _require(action: dict[str, Any], key: str) -> Any:
        if key not in action:
            raise ValueError(f"missing required field {key!r}")
        return action[key]

    @staticmethod
    def _describe(action: dict[str, Any]) -> str:
        parts: list[str] = []
        for key in (
            "x", "y", "button", "key", "repeat", "keys", "text", "mode",
            "seconds", "dx", "dy", "notches", "path",
        ):
            if key not in action:
                continue
            value = action[key]
            if key == "text" and isinstance(value, str) and len(value) > 30:
                value = value[:27] + "..."
            parts.append(f"{key}={value!r}")
        return " ".join(parts)

    # ------------------------------------------------------------- handlers --

    def _do_click(self, a: dict[str, Any]) -> None:
        self.level_0.click(
            self._require(a, "x"),
            self._require(a, "y"),
            a.get("button", "left"),
            clicks=a.get("clicks", 1),
            interval=a.get("interval", 0),
        )

    def _do_move(self, a: dict[str, Any]) -> None:
        self.level_0.move(
            self._require(a, "x"),
            self._require(a, "y"),
            duration=a.get("duration", 0),
        )

    def _do_mouse_down(self, a: dict[str, Any]) -> None:
        self.level_0.mouse_down(a.get("button", "left"))

    def _do_mouse_up(self, a: dict[str, Any]) -> None:
        self.level_0.mouse_up(a.get("button", "left"))

    def _do_key_press(self, a: dict[str, Any]) -> None:
        key = self._require(a, "key")
        repeat = a.get("repeat", 1)
        repeat = int(repeat) if isinstance(repeat, int) else 1
        for _ in range(max(repeat, 1)):
            self.level_0.keypress(key)

    def _do_key_down(self, a: dict[str, Any]) -> None:
        self.level_0.key_down(self._require(a, "key"))

    def _do_key_up(self, a: dict[str, Any]) -> None:
        self.level_0.key_up(self._require(a, "key"))

    def _do_scroll(self, a: dict[str, Any]) -> None:
        self.level_0.scroll(a.get("dx", 0), a.get("dy", 0))

    def _do_wait(self, a: dict[str, Any]) -> None:
        self.level_0.wait(self._require(a, "seconds"))

    def _do_type(self, a: dict[str, Any]) -> None:
        self.level_1.type(
            self._require(a, "x"),
            self._require(a, "y"),
            self._require(a, "text"),
            mode=a.get("mode", "replace"),
            interval=a.get("interval", 0),
            button=a.get("button", "left"),
        )

    def _do_type_text(self, a: dict[str, Any]) -> None:
        self.level_1.type_text(
            self._require(a, "text"),
            interval=a.get("interval", 0),
        )

    def _do_combo_key(self, a: dict[str, Any]) -> None:
        self.level_1.combo_key(self._require(a, "keys"))

    def _do_drag(self, a: dict[str, Any]) -> None:
        self.level_1.drag(
            self._require(a, "path"),
            button=a.get("button", "left"),
            move_duration=a.get("move_duration", 0),
        )

    def _do_hover(self, a: dict[str, Any]) -> None:
        self.level_1.hover(
            self._require(a, "x"),
            self._require(a, "y"),
            a.get("duration"),
            move_duration=a.get("move_duration", 0),
        )

    def _do_scroll_step(self, a: dict[str, Any]) -> None:
        self.level_1.scroll_step(
            dx=a.get("dx", 0),
            dy=a.get("dy", 0),
            steps=a.get("steps", 1),
            interval=a.get("interval", 0),
        )

    def _do_scroll_at(self, a: dict[str, Any]) -> None:
        # Wheel scrolling acts on whatever is under the pointer, so put the
        # pointer on the recorded anchor first, then turn the wheel. The
        # underlying ActionLevel0.scroll is used unchanged.
        self.level_0.move(self._require(a, "x"), self._require(a, "y"))
        self.level_0.scroll(a.get("dx", 0), a.get("dy", 0))


# --------------------------------------------------------------------- main --


def main() -> int:
    executor = NaiveExecutor(
        delay_after=DELAY_AFTER,
        enable_failsafe=ENABLE_FAILSAFE,
        continue_on_error=CONTINUE_ON_ERROR,
        dry_run=DRY_RUN,
        target_resolution=TARGET_RESOLUTION,
        scroll_units_per_notch=SCROLL_UNITS_PER_NOTCH,
        coalesce_scrolls=COALESCE_SCROLLS,
        typing_interval=TYPING_INTERVAL,
        free_time_max_wait=FREE_TIME_MAX_WAIT,
        minimize_first=MINIMIZE_WINDOWS_FIRST,
    )
    print(f"Running {ACTIONS_FILE}")
    try:
        results = executor.run_file(ACTIONS_FILE)
    except ActionError as exc:
        print(f"\nAborted: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"\nCould not run actions file: {exc}", file=sys.stderr)
        return 2

    failed = [result for result in results if not result.ok]
    print(
        f"\nDone: {len(results) - len(failed)}/{len(results)} "
        "actions succeeded."
    )
    for result in failed:
        print(
            f"  #{result.index} {result.action_type}: {result.error}",
            file=sys.stderr,
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
