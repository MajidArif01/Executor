"""Resolve the clicked/focused accessibility element for each timeline event.

For every OmniParser-annotated mouse, scroll or typing timeline item, this
script finds the accessibility JSON with the same timestamp and screen, then:

* mouse / scroll: takes the event's ``(x, y)`` coordinate and hit-tests it
  against the UI-automation ``tree``, returning the deepest (smallest-area)
  element whose ``bounding_rectangle`` contains the point.
* keyboard: returns the element that has keyboard focus (where typing lands).

The resolved element (plus its ancestor chain and the click point) is written
as a small JSON per node into::

    AnotatedData/AccessibilityAnotatedJson/mouse/
    AnotatedData/AccessibilityAnotatedJson/keyboard/
    AnotatedData/AccessibilityAnotatedJson/scroll/

It does not modify the timeline, alter the source accessibility JSON, or
substitute a stale/nearest snapshot when the exact one is missing.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


ANNOTATED_ROOT = "AnotatedData"
ANNOTATED_JSON_DIR = "AccessibilityAnotatedJson"
SUPPORTED_KINDS = frozenset({"mouse", "keyboard", "scroll"})


@dataclass(frozen=True)
class Snapshot:
    path: Path
    timestamp: datetime
    screen: int | None


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return payload


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.utcoffset() is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def screen_number(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def load_snapshots(
    trees_dir: Path,
    exclude_dir: Path | None = None,
) -> list[Snapshot]:
    if not trees_dir.is_dir():
        raise FileNotFoundError(f"accessibility directory not found: {trees_dir}")

    exclude_resolved = exclude_dir.resolve() if exclude_dir else None

    snapshots: list[Snapshot] = []
    for path in sorted(trees_dir.rglob("*.json")):
        resolved = path.resolve()
        # Skip anything already inside the destination folder so previously
        # written outputs don't create spurious duplicate matches on re-runs.
        if exclude_resolved is not None and (
            resolved == exclude_resolved
            or exclude_resolved in resolved.parents
        ):
            continue
        try:
            payload = read_json(path)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        timestamp = parse_timestamp(payload.get("timestamp"))
        is_accessibility = "tree" in payload or "ax_tree_available" in payload
        if timestamp is None or not is_accessibility:
            continue
        snapshots.append(
            Snapshot(
                path=path.resolve(),
                timestamp=timestamp,
                screen=screen_number(payload.get("screen")),
            )
        )
    return snapshots


def item_screen(item: dict) -> int | None:
    omni = item.get("omniparser") or {}
    screen = screen_number(omni.get("screen"))
    if screen is not None:
        return screen
    for event in item.get("events") or []:
        if isinstance(event, dict):
            screen = screen_number(event.get("screen"))
            if screen is not None:
                return screen
    for image in item.get("images") or []:
        if isinstance(image, dict):
            screen = screen_number(image.get("screen"))
            if screen is not None:
                return screen
    return None


def selected_image(item: dict) -> dict | None:
    images = [image for image in item.get("images") or [] if isinstance(image, dict)]
    if not images:
        return None

    omni = item.get("omniparser") or {}
    wanted = omni.get("image_name")
    if wanted:
        wanted_name = Path(str(wanted)).name.casefold()
        for image in images:
            raw = image.get("value") or image.get("path") or ""
            name = Path(str(raw).replace("\\", "/")).name.casefold()
            if name == wanted_name:
                return image

    node_type = str(item.get("type") or "").strip().casefold()
    if node_type == "typing":
        dated = [
            (parse_timestamp(image.get("timestamp")), image)
            for image in images
        ]
        dated = [(timestamp, image) for timestamp, image in dated if timestamp]
        return max(dated, key=lambda pair: pair[0])[1] if dated else images[-1]

    screen = item_screen(item)
    if screen is not None:
        for image in images:
            if screen_number(image.get("screen")) == screen:
                return image
    return images[0]


def item_timestamp(item: dict) -> datetime | None:
    image = selected_image(item)
    candidates: list[Any] = [image.get("timestamp") if image else None]
    candidates.extend(
        event.get("timestamp")
        for event in item.get("events") or []
        if isinstance(event, dict)
    )
    candidates.extend(
        (item.get("time"), item.get("endtime"), item.get("starttime"))
    )
    for value in candidates:
        timestamp = parse_timestamp(value)
        if timestamp is not None:
            return timestamp
    return None


def item_kind(item: dict) -> str | None:
    omni = item.get("omniparser")
    if not isinstance(omni, dict):
        return None

    kind = str(omni.get("kind") or "").strip().casefold()
    if kind in SUPPORTED_KINDS:
        return kind

    node_type = str(item.get("type") or "").strip().casefold()
    return {
        "click": "mouse",
        "typing": "keyboard",
        "scroll": "scroll",
    }.get(node_type)


def exact_snapshot(
    snapshots: list[Snapshot],
    timestamp: datetime,
    screen: int | None,
) -> Snapshot | None:
    same_time = [entry for entry in snapshots if entry.timestamp == timestamp]
    if screen is None:
        return same_time[0] if len(same_time) == 1 else None

    same_screen = [entry for entry in same_time if entry.screen == screen]
    if len(same_screen) == 1:
        return same_screen[0]

    unspecified_screen = [entry for entry in same_time if entry.screen is None]
    return unspecified_screen[0] if len(unspecified_screen) == 1 else None


def default_annotated_json_root(timeline_path: Path) -> Path:
    timeline_dir = timeline_path.parent
    if timeline_dir.name.casefold() == "processdata":
        episode_dir = timeline_dir.parent
    else:
        episode_dir = timeline_dir
    return episode_dir / ANNOTATED_ROOT / ANNOTATED_JSON_DIR


def click_point(item: dict) -> tuple[int, int] | None:
    """Return the (x, y) screen-pixel coordinate of a mouse/scroll event."""
    for event in item.get("events") or []:
        if isinstance(event, dict):
            x = screen_number(event.get("x"))
            y = screen_number(event.get("y"))
            if x is not None and y is not None:
                return x, y
    omni = item.get("omniparser") or {}
    query = omni.get("match_query") or {}
    x = screen_number(query.get("x"))
    y = screen_number(query.get("y"))
    return (x, y) if x is not None and y is not None else None


def rect_contains(rect: Any, x: int, y: int) -> bool:
    if not isinstance(rect, dict):
        return False
    try:
        return (
            rect["left"] <= x <= rect["right"]
            and rect["top"] <= y <= rect["bottom"]
        )
    except (KeyError, TypeError):
        return False


def rect_area(rect: Any) -> int:
    if not isinstance(rect, dict):
        return 0
    try:
        return max(0, rect["right"] - rect["left"]) * max(
            0, rect["bottom"] - rect["top"]
        )
    except (KeyError, TypeError):
        return 0


def find_element_at_point(
    node: dict,
    x: int,
    y: int,
    path: list[dict] | None = None,
) -> tuple[dict, list[dict], int] | None:
    """Deepest, smallest-area visible node whose rectangle contains (x, y)."""
    path = (path or []) + [node]
    best: tuple[dict, list[dict], int] | None = None

    rect = node.get("bounding_rectangle")
    if rect_contains(rect, x, y) and not node.get("is_offscreen", False):
        best = (node, path, rect_area(rect))

    for child in node.get("children") or []:
        if isinstance(child, dict):
            found = find_element_at_point(child, x, y, path)
            if found is not None and (best is None or found[2] <= best[2]):
                best = found

    return best


def find_focused_element(
    node: dict,
    path: list[dict] | None = None,
) -> tuple[dict, list[dict]] | None:
    """Deepest node with keyboard focus (where typed text is delivered)."""
    path = (path or []) + [node]
    result: tuple[dict, list[dict]] | None = None
    if node.get("has_keyboard_focus") is True:
        result = (node, path)
    for child in node.get("children") or []:
        if isinstance(child, dict):
            found = find_focused_element(child, path)
            if found is not None:
                result = found  # prefer the deepest focused node
    return result


def describe_element(node: dict) -> dict:
    return {
        "name": node.get("name"),
        "control_type": node.get("control_type"),
        "localized_control_type": node.get("localized_control_type"),
        "automation_id": node.get("automation_id"),
        "class_name": node.get("class_name"),
        "bounding_rectangle": node.get("bounding_rectangle"),
        "clickable_point": node.get("clickable_point"),
        "is_enabled": node.get("is_enabled"),
        "is_offscreen": node.get("is_offscreen"),
    }


def output_filename(item: dict, snapshot: Snapshot, kind: str) -> str:
    """Prefer the OmniParser node_X_ name; else build one from the snapshot."""
    omni = item.get("omniparser") or {}
    annotated = omni.get("annotated_json_path")
    if annotated:
        return Path(str(annotated).replace("\\", "/")).name
    index = item.get("index")
    stem = snapshot.path.stem.replace("_accessibility", "")
    return f"node_{index}_{stem}.json"


def resolve_elements(
    timeline: dict,
    snapshots: list[Snapshot],
    annotated_json_root: Path,
) -> tuple[int, int]:
    resolved = 0
    missing = 0

    for item in timeline.get("items") or []:
        if not isinstance(item, dict):
            continue
        kind = item_kind(item)
        if kind is None:
            continue

        node_index = item.get("index")
        timestamp = item_timestamp(item)
        screen = item_screen(item)
        if timestamp is None:
            print(
                f"warning: node {node_index}: no event timestamp; skipped",
                file=sys.stderr,
            )
            missing += 1
            continue

        snapshot = exact_snapshot(snapshots, timestamp, screen)
        if snapshot is None:
            print(
                f"warning: node {node_index}: no exact accessibility JSON; skipped",
                file=sys.stderr,
            )
            missing += 1
            continue

        try:
            tree = read_json(snapshot.path).get("tree")
        except (OSError, json.JSONDecodeError, ValueError):
            tree = None
        if not isinstance(tree, dict):
            print(
                f"warning: node {node_index}: accessibility JSON has no tree; skipped",
                file=sys.stderr,
            )
            missing += 1
            continue

        point = click_point(item) if kind in {"mouse", "scroll"} else None
        matched: dict | None = None
        ancestors: list[str | None] = []
        strategy = "none"

        if kind in {"mouse", "scroll"}:
            if point is None:
                print(
                    f"warning: node {node_index}: no click coordinate; skipped",
                    file=sys.stderr,
                )
                missing += 1
                continue
            hit = find_element_at_point(tree, point[0], point[1])
            if hit is not None:
                matched = describe_element(hit[0])
                ancestors = [n.get("name") for n in hit[1][:-1]]
                strategy = "point_in_bbox"
        else:  # keyboard
            focus = find_focused_element(tree)
            if focus is not None:
                matched = describe_element(focus[0])
                ancestors = [n.get("name") for n in focus[1][:-1]]
                strategy = "keyboard_focus"

        result = {
            "node_index": node_index,
            "node_type": item.get("type"),
            "kind": kind,
            "value": item.get("value"),
            "screen": screen,
            "time": timestamp.isoformat(),
            "click": {"x": point[0], "y": point[1]} if point else None,
            "accessibility_source": snapshot.path.name,
            "match_strategy": strategy if matched else "none",
            "matched_element": matched,
            "ancestors": ancestors,
        }

        destination_dir = annotated_json_root / kind
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / output_filename(item, snapshot, kind)
        with destination.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)

        resolved += 1
        label = matched["name"] if matched else "<no element>"
        print(f"node {node_index} [{kind}] -> {label!r} :: {destination}")

    return resolved, missing


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument(
        "--trees-dir",
        type=Path,
        required=True,
        help="Directory containing the original accessibility JSON snapshots",
    )
    parser.add_argument(
        "--annotated-json-root",
        type=Path,
        help=(
            "Destination directory; defaults to "
            f"<episode>/{ANNOTATED_ROOT}/{ANNOTATED_JSON_DIR}"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    timeline_path = args.timeline.resolve()
    destination_root = (
        args.annotated_json_root.resolve()
        if args.annotated_json_root
        else default_annotated_json_root(timeline_path).resolve()
    )

    try:
        timeline = read_json(timeline_path)
        if not isinstance(timeline.get("items"), list):
            raise ValueError(f"timeline has no items list: {timeline_path}")
        snapshots = load_snapshots(
            args.trees_dir.resolve(), exclude_dir=destination_root
        )
        if not snapshots:
            raise ValueError("no accessibility JSON snapshots were found")
        resolved, missing = resolve_elements(
            timeline, snapshots, destination_root
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"resolved={resolved}, missing={missing}")
    return 0 if missing == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
