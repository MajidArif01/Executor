"""Level-0 and Level-1 action primitives.

Copied verbatim from ``AT_DOM_Executor_v2/workflow_executor/actions/action_classes.py``
with only two trims for this standalone, coordinate-only executor:

* ``ActionLevel2`` / ``ActionLevel3`` and the ``execution_backend_adapter``
  import are removed (no UIA/DOM, no observation).
* ``ActionLevel1`` no longer takes an inert ``backend_adapter`` argument.

Keep this file diff-able against the source so upstream fixes to Level 0/1 can
be re-copied.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Any


class ActionLevel0:
    """Atomic mouse, keyboard, scrolling, movement, and timing actions.

    Level 0 accepts physical coordinates and raw keys only. It deliberately
    knows nothing about workflow target names, LLM element IDs, DOM nodes,
    accessibility controls, or screen-understanding output. Higher levels or
    Action Execution must resolve semantic targets to coordinates first.
    """

    _SUPPORTED_BUTTONS = {"left", "middle", "right"}

    def __init__(
        self,
        pyautogui_backend: Any | None = None,
        sleep_backend: Callable[[float], None] | None = None,
        *,
        enable_failsafe: bool = True,
    ) -> None:
        self._pyautogui = pyautogui_backend
        self._sleep = sleep_backend or time.sleep
        self.enable_failsafe = bool(enable_failsafe)

    def _backend(self) -> Any:
        """Load PyAutoGUI lazily so importing the package has no GUI side effect."""
        if self._pyautogui is None:
            try:
                import pyautogui
            except ImportError as exc:
                raise RuntimeError(
                    "PyAutoGUI is required for Level-0 action execution."
                ) from exc
            self._pyautogui = pyautogui

        self._pyautogui.FAILSAFE = self.enable_failsafe
        return self._pyautogui

    @staticmethod
    def _number(value: Any, name: str) -> int | float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"{name} must be a finite number.")
        return value

    @classmethod
    def _duration(cls, value: Any) -> int | float:
        duration = cls._number(value, "duration")
        if duration < 0:
            raise ValueError("duration cannot be negative.")
        return duration

    @classmethod
    def _button(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("button must be left, middle, or right.")
        button = value.strip().casefold()
        if button not in cls._SUPPORTED_BUTTONS:
            raise ValueError("button must be left, middle, or right.")
        return button

    @staticmethod
    def _key(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("key must be a non-empty string.")
        return value.strip()

    def click(
        self,
        x: int | float,
        y: int | float,
        button: str = "left",
        *,
        clicks: int = 1,
        interval: int | float = 0,
    ) -> None:
        """Perform one or more mouse clicks at physical coordinates."""
        if isinstance(clicks, bool) or not isinstance(clicks, int) or clicks < 1:
            raise ValueError("clicks must be a positive integer.")
        delay = self._duration(interval)
        backend = self._backend()
        validated_x = self._number(x, "x")
        validated_y = self._number(y, "y")
        validated_button = self._button(button)

        self.move(validated_x, validated_y, duration=0.2)

        if clicks == 1 and not delay:
            backend.click(validated_x, validated_y, button=validated_button)
            return
        backend.click(
            validated_x,
            validated_y,
            button=validated_button,
            clicks=clicks,
            interval=delay,
        )

    def mouse_down(self, button: str = "left") -> None:
        """Press and hold a mouse button."""
        self._backend().mouseDown(button=self._button(button))

    def mouse_up(self, button: str = "left") -> None:
        """Release a held mouse button."""
        self._backend().mouseUp(button=self._button(button))

    def keypress(self, key: str) -> None:
        """Press and release one keyboard key."""
        self._backend().press(self._key(key))

    def key_down(self, key: str) -> None:
        """Press and hold one keyboard key or modifier."""
        self._backend().keyDown(self._key(key))

    def key_up(self, key: str) -> None:
        """Release a held keyboard key or modifier."""
        self._backend().keyUp(self._key(key))

    def move(
        self,
        x: int | float,
        y: int | float,
        duration: int | float = 0,
    ) -> None:
        """Move the pointer to physical screen coordinates without clicking."""
        self._backend().moveTo(
            self._number(x, "x"),
            self._number(y, "y"),
            duration=self._duration(duration),
        )

    def scroll(
        self,
        dx: int | float = 0,
        dy: int | float = 0,
    ) -> None:
        """Scroll by horizontal and/or vertical amounts.

        Amount signs follow PyAutoGUI semantics. A zero axis is skipped.
        """
        horizontal = self._number(dx, "dx")
        vertical = self._number(dy, "dy")
        backend = self._backend()
        if horizontal:
            horizontal_scroll = getattr(backend, "hscroll", None)
            if not callable(horizontal_scroll):
                raise RuntimeError(
                    "The configured PyAutoGUI backend does not support "
                    "horizontal scrolling."
                )
            horizontal_scroll(horizontal)
        if vertical:
            backend.scroll(vertical)

    def wait(self, duration: int | float) -> None:
        """Pause execution for a fixed duration in seconds."""
        self._sleep(self._duration(duration))


class ActionLevel1:
    """Deterministic macros composed from Level-0 atomic actions.

    Targets are still physical screen coordinates. Level 1 deliberately
    performs no observation or state-based decision.
    """

    _TYPE_MODES = {"append", "replace"}

    def __init__(self, level_0: ActionLevel0) -> None:
        if not isinstance(level_0, ActionLevel0):
            raise TypeError("level_0 must be an ActionLevel0 instance.")
        self.level_0 = level_0

    @staticmethod
    def _text(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("text must be a string.")
        return value

    @classmethod
    def _type_mode(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("mode must be append or replace.")
        mode = value.strip().casefold()
        if mode not in cls._TYPE_MODES:
            raise ValueError("mode must be append or replace.")
        return mode

    @staticmethod
    def _keys(values: Iterable[str]) -> tuple[str, ...]:
        if isinstance(values, (str, bytes)):
            raise ValueError("keys must be an iterable of key names.")
        try:
            keys = tuple(ActionLevel0._key(value) for value in values)
        except TypeError as exc:
            raise ValueError("keys must be an iterable of key names.") from exc
        if not keys:
            raise ValueError("keys must contain at least one key.")
        return keys

    @staticmethod
    def _point(value: Any, position: int) -> tuple[int | float, int | float]:
        if (
            isinstance(value, (str, bytes))
            or not isinstance(value, Sequence)
            or len(value) != 2
        ):
            raise ValueError(
                f"path point {position} must contain exactly x and y."
            )
        return (
            ActionLevel0._number(value[0], f"path[{position}].x"),
            ActionLevel0._number(value[1], f"path[{position}].y"),
        )

    @classmethod
    def _path(
        cls,
        values: Iterable[Sequence[int | float]],
    ) -> tuple[tuple[int | float, int | float], ...]:
        if isinstance(values, (str, bytes)):
            raise ValueError("path must be an iterable of coordinate pairs.")
        try:
            path = tuple(
                cls._point(point, position)
                for position, point in enumerate(values)
            )
        except TypeError as exc:
            raise ValueError(
                "path must be an iterable of coordinate pairs."
            ) from exc
        if len(path) < 2:
            raise ValueError(
                "path must contain at least a start and destination point."
            )
        return path

    @staticmethod
    def _text_key(character: str) -> str:
        """Translate control whitespace to PyAutoGUI key names."""
        return {
            " ": "space",
            "\n": "enter",
            "\r": "enter",
            "\t": "tab",
        }.get(character, character)

    def _enter_text(self, text: str, interval: int | float) -> None:
        delay = ActionLevel0._duration(interval)
        for position, character in enumerate(text):
            if character.isascii() and character.isalpha() and character.isupper():
                self.combo_key(("shift", character.casefold()))
            else:
                self.level_0.keypress(self._text_key(character))
            if delay and position < len(text) - 1:
                self.level_0.wait(delay)

    def type(
        self,
        x: int | float,
        y: int | float,
        text: str,
        mode: str = "replace",
        *,
        interval: int | float = 0,
        button: str = "left",
    ) -> None:
        """Focus a physical target and replace or append its text."""
        content = self._text(text)
        selected_mode = self._type_mode(mode)
        delay = ActionLevel0._duration(interval)

        self.level_0.click(x, y, button=button)
        if selected_mode == "replace":
            self.combo_key(("ctrl", "a"))
        self._enter_text(content, delay)

    def type_text(self, text: str, *, interval: int | float = 0) -> None:
        """Type text into whatever already holds keyboard focus."""
        self._enter_text(self._text(text), ActionLevel0._duration(interval))

    def combo_key(self, keys: Iterable[str]) -> None:
        """Press keys as one chord and release them in reverse order."""
        chord = self._keys(keys)
        pressed: list[str] = []
        try:
            for key in chord:
                self.level_0.key_down(key)
                pressed.append(key)
        finally:
            for key in reversed(pressed):
                self.level_0.key_up(key)

    def drag(
        self,
        path: Iterable[Sequence[int | float]],
        *,
        button: str = "left",
        move_duration: int | float = 0,
    ) -> None:
        """Drag from the first point through every remaining path point."""
        points = self._path(path)
        duration = ActionLevel0._duration(move_duration)
        validated_button = ActionLevel0._button(button)

        start_x, start_y = points[0]
        self.level_0.move(start_x, start_y)
        self.level_0.mouse_down(validated_button)
        try:
            for x, y in points[1:]:
                self.level_0.move(x, y, duration=duration)
        finally:
            self.level_0.mouse_up(validated_button)

    def hover(
        self,
        x: int | float,
        y: int | float,
        duration: int | float | None = None,
        *,
        move_duration: int | float = 0,
    ) -> None:
        """Move onto a target and optionally remain there for a duration."""
        self.level_0.move(x, y, duration=move_duration)
        if duration is not None:
            self.level_0.wait(duration)

    def scroll_step(
        self,
        *,
        dx: int | float = 0,
        dy: int | float = 0,
        steps: int = 1,
        interval: int | float = 0,
    ) -> None:
        """Repeat a known scroll operation a fixed number of times."""
        if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
            raise ValueError("steps must be a positive integer.")
        horizontal = ActionLevel0._number(dx, "dx")
        vertical = ActionLevel0._number(dy, "dy")
        if not horizontal and not vertical:
            raise ValueError("dx and dy cannot both be zero.")
        delay = ActionLevel0._duration(interval)

        for position in range(steps):
            self.level_0.scroll(dx=horizontal, dy=vertical)
            if delay and position < steps - 1:
                self.level_0.wait(delay)
