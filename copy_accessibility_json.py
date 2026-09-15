"""Copy exact event accessibility snapshots into the annotated JSON folders.

For every OmniParser-annotated mouse, typing or scroll timeline item, this
script finds the accessibility JSON with the same timestamp and screen, then
copies that original JSON unchanged into::

    AnotatedData/AnotatedJson/mouse/
    AnotatedData/AnotatedJson/keyboard/
    AnotatedData/AnotatedJson/scroll/

It does not modify the timeline, alter accessibility JSON content, generate an
index, or substitute a stale/nearest snapshot when the exact one is missing.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
from typing import Any


ANNOTATED_ROOT = "AnotatedData"
ANNOTATED_JSON_DIR = "AnotatedJson"
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
        # copied snapshots don't create spurious duplicate matches on re-runs.
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


def copy_matching_files(
    timeline: dict,
    snapshots: list[Snapshot],
    annotated_json_root: Path,
) -> tuple[int, int]:
    copied = 0
    missing = 0

    for item in timeline.get("items") or []:
        if not isinstance(item, dict):
            continue
        kind = item_kind(item)
        if kind is None:
            continue

        timestamp = item_timestamp(item)
        screen = item_screen(item)
        if timestamp is None:
            print(
                f"warning: node {item.get('index')}: no event timestamp; skipped",
                file=sys.stderr,
            )
            missing += 1
            continue

        snapshot = exact_snapshot(snapshots, timestamp, screen)
        if snapshot is None:
            print(
                f"warning: node {item.get('index')}: no exact accessibility JSON; skipped",
                file=sys.stderr,
            )
            missing += 1
            continue

        destination_dir = annotated_json_root / kind
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / snapshot.path.name
        if snapshot.path != destination.resolve():
            shutil.copy2(snapshot.path, destination)
        copied += 1
        print(f"copied: {snapshot.path} -> {destination.resolve()}")

    return copied, missing


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
        copied, missing = copy_matching_files(
            timeline, snapshots, destination_root
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"copied={copied}, missing={missing}")
    return 0 if missing == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
