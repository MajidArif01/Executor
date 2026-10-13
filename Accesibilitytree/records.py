"""
records.py
----------
One dataclass per modality for the "accessibility" block that
accessibility_tree.py stores on each timeline item.

Fields created with optional() are left out of the JSON when they are None;
every other field is always written, even when its value is None.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass


def optional():
    return field(default=None, metadata={"optional": True})


def to_json(value):
    """Dataclass -> plain dict/list for json.dump, dropping unset optional fields."""
    if is_dataclass(value):
        result = {}
        for f in fields(value):
            item = getattr(value, f.name)
            if item is None and f.metadata.get("optional"):
                continue
            result[f.name] = to_json(item)
        return result
    if isinstance(value, list):
        return [to_json(item) for item in value]
    return value


@dataclass(kw_only=True)
class ClickPoint:
    x: int
    y: int
    normalized: list | None = optional()


@dataclass(kw_only=True)
class Miss:
    """A click, typing, or drag that could not be resolved."""
    matched: bool = False
    reason: str
    ax_source: str | None = optional()
    click: ClickPoint | None = optional()


@dataclass(kw_only=True)
class ClickRecord:
    ax_source: str
    matched: bool = True
    click: ClickPoint
    hit_layer: str | None
    window_context: dict
    target: dict
    ancestor_chain: list[dict]


@dataclass(kw_only=True)
class ScrollDelta:
    dx: float
    dy: float


@dataclass(kw_only=True)
class ScrollMiss:
    scroll: ScrollDelta
    screen: int | None
    matched: bool = False
    reason: str
    ax_source: str | None = optional()


@dataclass(kw_only=True)
class ScrollRecord:
    scroll: ScrollDelta
    screen: int | None
    window_context: dict
    target: dict
    ancestor_chain: list[dict]
    hit_layer: str | None
    hovered_element: dict
    scroll_container: dict | None
    status: str
    point: dict
    matched: bool = True
    ax_source: str


@dataclass(kw_only=True)
class DragPoint:
    x: int
    y: int


@dataclass(kw_only=True)
class DragSide:
    ax_source: str
    point: DragPoint
    status: str | None
    element: dict | None
    candidates: list[dict] | None = optional()


@dataclass(kw_only=True)
class DragRecord:
    matched: bool
    pick: DragSide
    drop: DragSide


@dataclass(kw_only=True)
class TypedText:
    value: str | None
    last_char_time: str | None


@dataclass(kw_only=True)
class TypingRecord:
    ax_source: str
    matched: bool
    typed: TypedText
    window_context: dict | None = None
    target: dict | None = None
    ancestor_chain: list[dict] = field(default_factory=list)
