"""Read timeline.json and reduce typing / mouse nodes to one screenshot each.

Which screenshot each node contributes:
  * ``Typing``  -> the LAST image of the node (the screen after the full string
                   has been typed).
  * ``click``   -> the image whose ``screen`` matches the click event's screen
                   (the display the user actually clicked on).
  * ``scroll``  -> the image whose ``screen`` matches the scroll event's screen
                   (the display the wheel event landed on).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from Omniparser_Runner.config import (
    CLICK_TYPE,
    KEYBOARD,
    KEYBOARD_JSON_DIR,
    MOUSE,
    MOUSE_IMAGE_DIR,
    MOUSE_JSON_DIR,
    SCROLL,
    SCROLL_IMAGE_DIR,
    SCROLL_JSON_DIR,
    SCROLL_TYPE,
    TYPING_IMAGE_DIR,
    TYPING_TYPE,
)

# Where each node kind's output lands under AnotatedData/.
IMAGE_SUBDIRS = {
    KEYBOARD: TYPING_IMAGE_DIR,
    MOUSE: MOUSE_IMAGE_DIR,
    SCROLL: SCROLL_IMAGE_DIR,
}
JSON_SUBDIRS = {
    KEYBOARD: KEYBOARD_JSON_DIR,
    MOUSE: MOUSE_JSON_DIR,
    SCROLL: SCROLL_JSON_DIR,
}


@dataclass
class ExtractedNode:
    """One timeline node reduced to the single screenshot worth parsing."""

    index: int | None
    node_type: str          # "Typing", "click" or "scroll"
    kind: str               # KEYBOARD, MOUSE or SCROLL
    value: str
    image_path: Path
    image_name: str
    screen: int | None
    screen_matched: bool = True
    times: dict = field(default_factory=dict)
    click: dict | None = None
    scroll: dict | None = None

    @property
    def image_subdir(self) -> str:
        return IMAGE_SUBDIRS[self.kind]

    @property
    def json_subdir(self) -> str:
        return JSON_SUBDIRS[self.kind]


def load_timeline(timeline_path: Path) -> dict:
    if not timeline_path.is_file():
        raise FileNotFoundError(f"timeline.json not found: {timeline_path}")
    with open(timeline_path, encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict) or "items" not in payload:
        raise ValueError(f"Unrecognized timeline format (no 'items'): {timeline_path}")
    return payload


def resolve_image(episode_dir: Path, image: dict) -> Path:
    """Timeline image paths are relative to the episode root."""
    raw = image.get("path") or image.get("value") or ""
    candidate = Path(str(raw).replace("\\", "/"))
    if candidate.is_absolute():
        return candidate
    return (episode_dir / candidate).resolve()


def pick_last_image(node: dict) -> dict | None:
    """Last screenshot of a typing node — the state after the full string."""
    images = node.get("images") or []
    if not images:
        return None
    with_ts = [img for img in images if img.get("timestamp")]
    if with_ts:
        return max(with_ts, key=lambda img: img["timestamp"])
    return images[-1]


def event_screen(node: dict) -> int | None:
    """The display the node's first located event happened on."""
    for event in node.get("events") or []:
        if event.get("screen") is not None:
            return event["screen"]
    return None


def pick_event_image(node: dict) -> tuple[dict | None, bool]:
    """Screenshot of the display a pointer event landed on.

    Used for both ``click`` and ``scroll`` nodes — they carry the same
    ``events[*].screen`` / ``images[*].screen`` shape. Returns (image, matched).
    """
    images = node.get("images") or []
    if not images:
        return None, False

    target = event_screen(node)
    if target is not None:
        for img in images:
            if img.get("screen") == target:
                return img, True

    # The event's display was not captured; fall back to the node's own shot.
    return images[0], target is None


def click_details(node: dict) -> dict | None:
    events = node.get("events") or []
    if not events:
        return None
    first = events[0]
    return {
        "x": first.get("x"),
        "y": first.get("y"),
        "screen": first.get("screen"),
        "detail": first.get("detail"),
    }


def parse_scroll_delta(detail: object) -> tuple[int | None, int | None]:
    """Parse a scroll event's ``detail`` (``"dx=0,dy=-1"``) into (dx, dy).

    Anything unparseable — a missing detail, a click's ``"left"``, a malformed
    pair — yields ``(None, None)`` rather than raising, so a single odd capture
    never breaks a whole episode.
    """
    if not isinstance(detail, str):
        return None, None

    axes: dict[str, int] = {}
    for part in detail.split(","):
        key, sep, raw = part.partition("=")
        key = key.strip().lower()
        if not sep or key not in ("dx", "dy"):
            continue
        try:
            axes[key] = int(float(raw.strip()))
        except ValueError:
            continue

    return axes.get("dx"), axes.get("dy")


def scroll_details(node: dict) -> dict | None:
    """Where the wheel event happened and how far it scrolled."""
    events = node.get("events") or []
    if not events:
        return None
    first = events[0]
    detail = first.get("detail")
    dx, dy = parse_scroll_delta(detail)
    return {
        "x": first.get("x"),
        "y": first.get("y"),
        "screen": first.get("screen"),
        "detail": detail,
        "scroll_dx": dx,
        "scroll_dy": dy,
    }


def extract_nodes(timeline: dict, episode_dir: Path) -> list[ExtractedNode]:
    """Keep typing, mouse-click and mouse-scroll nodes, one screenshot each."""
    nodes: list[ExtractedNode] = []

    for item in timeline.get("items", []):
        node_type = item.get("type")
        click = scroll = None

        if node_type == TYPING_TYPE:
            image, screen_matched = pick_last_image(item), True
            kind = KEYBOARD
        elif node_type == CLICK_TYPE:
            image, screen_matched = pick_event_image(item)
            kind, click = MOUSE, click_details(item)
        elif node_type == SCROLL_TYPE:
            image, screen_matched = pick_event_image(item)
            kind, scroll = SCROLL, scroll_details(item)
        else:
            continue

        if image is None:
            print(
                f"warning: node {item.get('index')} ({node_type}) has no images, "
                f"skipping.",
                file=sys.stderr,
            )
            continue

        img_path = resolve_image(episode_dir, image)
        times = {
            key: item[key]
            for key in ("time", "starttime", "endtime", "totaltime")
            if item.get(key)
        }

        nodes.append(
            ExtractedNode(
                index=item.get("index"),
                node_type=node_type,
                kind=kind,
                value=item.get("value", ""),
                image_path=img_path,
                image_name=image.get("value") or img_path.name,
                screen=image.get("screen"),
                screen_matched=screen_matched,
                times=times,
                click=click,
                scroll=scroll,
            )
        )

    return nodes
