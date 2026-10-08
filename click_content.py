
"""Crop every click and typing node's screenshot, as base64 PNGs.

For each click node this script takes the matched element's bbox (or, with no
element, a box around the click point); for each typing node it takes the
element showing the typed text (or, with no element, a box around the click
that focused the field). The box grows outward by 10% per side, is cropped out
of the node's screenshot and stored base64-encoded in ``base64.json`` beside the
timeline, ready to hand to a VLM.

Clicks use the screenshot of the display they landed on; typing nodes use their
last screenshot, the frame after the whole string is in.

The file is rebuilt on every run, one record per node in index order; a node
that could not be cropped stays in with ``status: skipped`` and its reason::

    {
      "nodes": [
        {
          "index": 7, "type": "Typing",
          "status": "cropped",            # or "skipped"
          "skip_reason": null,
          "match_status": "match",        # the mapper's verdict
          "content": "settrin| ",         # matched element's text, or null
          "target": {"value": "setrin", "field_value": "setrin", "anchor": [x, y]},
                                          # a click: {"click": [x, y]}
          "screenshot": {"name": "....png", "size": [2560, 1600]},
          "crop": {"source": "bbox_normalized", "source_bbox": [...],
                   "box": [...], "size": [w, h]},
          "image_base64": "iVBORw0..."
        }
      ]
    }

    python click_content.py --episode-dir <EPISODE_ROOT>
    python click_content.py --episode-dir <EPISODE_ROOT> --kind typing --dry-run
    python click_content.py --timeline sample/Calculator.json --episode-dir . --dry-run
"""

from __future__ import annotations

import argparse
import base64
import collections
import io
import json
import sys
from pathlib import Path

from PIL import Image

from Omniparser_Runner.config import CLICK_TYPE, TIMELINE_RELPATH, TYPING_TYPE
from Omniparser_Runner.timeline_reader import (
    load_timeline,
    pick_event_image,
    pick_last_image,
    resolve_image,
)
from mousemapper import NEAREST_RADIUS_PX
from node import field_context

# Grow the source bbox outward on every side by this fraction of its own
# width/height before cropping (0.10 => each edge moves out 10%).
DEFAULT_PAD_FRAC = 0.10

OUTPUT_NAME = "base64.json"

# Status we record for a click the mapper never touched, so an un-enriched
# episode is distinguishable from one the mapper looked at and gave up on.
NO_OMNIPARSER = "no_omniparser"

# Which timeline node types each --kind selects.
KINDS = {
    "click": frozenset({CLICK_TYPE}),
    "typing": frozenset({TYPING_TYPE}),
    "all": frozenset({CLICK_TYPE, TYPING_TYPE}),
}


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #

def has_no_content(item: dict) -> bool:
    """True if this node's matched element names nothing usable.

    Covers both shapes seen in the wild: an element matched but with an empty
    or absent ``content``, and ``match_status: not_found`` with no element.
    """
    matched = (item.get("omniparser") or {}).get("matched_element")
    content = (matched or {}).get("content")
    return not (isinstance(content, str) and content.strip())


def match_status(item: dict) -> str:
    """Why this click has no content, in one word.

    A node with no ``omniparser`` block at all was never run through the mapper
    — a whole un-enriched episode looks like this — which is a different problem
    from a node the mapper looked at and failed to name.
    """
    block = item.get("omniparser")
    if not isinstance(block, dict) or not block:
        return NO_OMNIPARSER
    return block.get("match_status") or NO_OMNIPARSER


def click_point(item: dict) -> tuple[int, int] | None:
    """Where the click landed: the matcher's query, else the raw event."""
    query = (item.get("omniparser") or {}).get("match_query") or {}
    x, y = query.get("x"), query.get("y")
    if x is None or y is None:
        events = item.get("events") or []
        first = events[0] if events else {}
        x, y = first.get("x"), first.get("y")
    if x is None or y is None:
        return None
    return int(x), int(y)


def typing_anchor(
    item: dict,
    context_anchor: tuple[float, float] | None,
) -> tuple[float, float] | None:
    """The normalized click that focused a typing node's field.

    The matcher records it in ``match_query.anchor``; a node it never touched
    falls back to the anchor rebuilt from the timeline by ``field_context``.
    """
    anchor = ((item.get("omniparser") or {}).get("match_query") or {}).get("anchor")
    if isinstance(anchor, (list, tuple)) and len(anchor) == 2:
        return float(anchor[0]), float(anchor[1])
    return context_anchor


def typing_point(
    item: dict,
    image_size: tuple[int, int],
    context_anchor: tuple[float, float] | None,
) -> tuple[int, int] | None:
    """Where a typing node's field is, in pixels: its anchor scaled by the image."""
    anchor = typing_anchor(item, context_anchor)
    if anchor is None:
        return None
    width, height = image_size
    return int(round(anchor[0] * width)), int(round(anchor[1] * height))


def typed_value(item: dict) -> str:
    """The whole field the matcher looked for, else this node's own fragment."""
    query = (item.get("omniparser") or {}).get("match_query") or {}
    return query.get("field_value") or item.get("value") or ""


def source_bbox(
    item: dict,
    image_size: tuple[int, int],
    base_radius: int,
    point: tuple[int, int] | None,
    point_origin: str,
) -> tuple[tuple[float, float, float, float], str] | None:
    """The box to grow, in pixels, plus where it came from.

    Prefers the matched element's own pixel bbox, falls back to its normalized
    bbox scaled by the image, and finally -- no element at all -- to a small box
    centred on ``point`` (the click, or the click that focused a typed field),
    labelled ``point_origin``.
    """
    matched = (item.get("omniparser") or {}).get("matched_element") or {}
    width, height = image_size

    pixels = matched.get("bbox_pixels")
    if isinstance(pixels, (list, tuple)) and len(pixels) == 4:
        x1, y1, x2, y2 = (float(v) for v in pixels)
        return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)), "bbox_pixels"

    normalized = matched.get("bbox")
    if isinstance(normalized, (list, tuple)) and len(normalized) == 4:
        x1, y1, x2, y2 = (float(v) for v in normalized)
        box = (
            min(x1, x2) * width,
            min(y1, y2) * height,
            max(x1, x2) * width,
            max(y1, y2) * height,
        )
        return box, "bbox_normalized"

    if point is None:
        return None
    x, y = point
    box = (x - base_radius, y - base_radius, x + base_radius, y + base_radius)
    return box, point_origin


def pad_about_center(
    box: tuple[float, float, float, float],
    pad_frac: float,
    image_size: tuple[int, int],
) -> tuple[int, int, int, int] | None:
    """Grow the box outward on each side by ``pad_frac`` of its own width/height,
    then clamp to the image.

    Returns None if nothing of the box survives inside the image.
    """
    x1, y1, x2, y2 = box
    # A degenerate box would pad to nothing; give it a pixel to grow from.
    width = max(x2 - x1, 1.0)
    height = max(y2 - y1, 1.0)
    px, py = width * pad_frac, height * pad_frac

    img_w, img_h = image_size
    left = max(0, int(round(x1 - px)))
    top = max(0, int(round(y1 - py)))
    right = min(img_w, int(round(x2 + px)))
    bottom = min(img_h, int(round(y2 + py)))

    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


# --------------------------------------------------------------------------- #
# Screenshot lookup
# --------------------------------------------------------------------------- #

def screenshot_for(item: dict, episode_dir: Path) -> Path | None:
    """The node's screenshot, resolved against this machine.

    A click uses the display it landed on; a typing node uses its last frame,
    the one the matcher parsed. Timelines captured elsewhere carry absolute
    paths from that machine, so the episode-relative path wins and the absolute
    one is only a fallback.
    """
    if item.get("type") == TYPING_TYPE:
        image = pick_last_image(item)
    else:
        image, _matched = pick_event_image(item)
    if image is not None:
        candidate = resolve_image(episode_dir, image)
        if candidate.is_file():
            return candidate

    absolute = (item.get("omniparser") or {}).get("image_path")
    if absolute:
        candidate = Path(str(absolute))
        if candidate.is_file():
            return candidate
    return None


def encode_png(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


# --------------------------------------------------------------------------- #
# Main pass
# --------------------------------------------------------------------------- #

def matched_content(item: dict) -> str | None:
    """The OCR / caption text of the element the node matched, if any."""
    matched = (item.get("omniparser") or {}).get("matched_element") or {}
    content = matched.get("content")
    return content if isinstance(content, str) and content.strip() else None


def node_record(item: dict, status: str) -> dict:
    """One node's entry with every key present, filled in as the crop proceeds.

    Click and typing nodes share this shape; only ``target`` differs, so a
    consumer can read every node the same way.
    """
    return {
        "index": item.get("index"),
        "type": item.get("type"),
        "status": "skipped",
        "skip_reason": None,
        "match_status": status,
        "content": matched_content(item),
        "target": {},
        "screenshot": None,
        "crop": None,
        "image_base64": None,
    }


def collect(
    timeline: dict,
    episode_dir: Path,
    pad_frac: float,
    base_radius: int,
    dry_run: bool,
    node_types: frozenset[str] = KINDS["all"],
) -> tuple[list[dict], dict]:
    """Crop every selected click / typing node. Returns (nodes, counters).

    Every selected node gets a record, in timeline order -- a node that could
    not be cropped is kept with ``status: skipped`` and the reason, rather than
    silently left out.
    """
    nodes: list[dict] = []
    counts = {"clicks": 0, "typing": 0, "no_content": 0, "cropped": 0, "skipped": 0}
    statuses: collections.Counter[str] = collections.Counter()

    items = timeline.get("items", [])
    # Rebuilds the focusing click for typing nodes the matcher never touched.
    context = field_context(items) if TYPING_TYPE in node_types else {}

    for item in items:
        node_type = item.get("type")
        if node_type not in node_types:
            continue
        is_typing = node_type == TYPING_TYPE
        counts["typing" if is_typing else "clicks"] += 1
        if has_no_content(item):
            counts["no_content"] += 1

        index = item.get("index")
        label = f"node {index} ({node_type})"
        omni = item.get("omniparser") or {}
        status = match_status(item)
        statuses[status] += 1

        record = node_record(item, status)
        nodes.append(record)
        anchor = None
        if is_typing:
            anchor = typing_anchor(item, (context.get(index) or {}).get("anchor"))
            record["target"] = {
                "value": item.get("value") or "",
                "field_value": typed_value(item),
                "anchor": list(anchor) if anchor else None,
            }
        else:
            record["target"] = {"click": list(click_point(item) or []) or None}

        def skip(reason: str) -> None:
            print(f"warning: {label}: {reason}, skipping.", file=sys.stderr)
            record["skip_reason"] = reason
            counts["skipped"] += 1

        path = screenshot_for(item, episode_dir)
        if path is None:
            skip("screenshot not found")
            continue

        try:
            with Image.open(path) as handle:
                image = handle.convert("RGB")
        except OSError as exc:
            skip(f"cannot read {path.name} ({exc})")
            continue

        # The recorded size is what OmniParser normalized against; trust the
        # real pixels when the two disagree.
        recorded = omni.get("image_size")
        size = image.size
        if isinstance(recorded, (list, tuple)) and len(recorded) == 2:
            if tuple(recorded) == image.size:
                size = tuple(recorded)
        record["screenshot"] = {"name": path.name, "size": list(size)}

        if is_typing:
            point = typing_point(item, size, anchor)
            point_origin = "typing_anchor"
        else:
            point = click_point(item)
            point_origin = "click_point"

        found = source_bbox(item, size, base_radius, point, point_origin)
        if found is None:
            skip(f"no bbox and no {point_origin}")
            continue
        box, origin = found

        crop_box = pad_about_center(box, pad_frac, size)
        if crop_box is None:
            skip("crop falls outside the image")
            continue

        record["crop"] = {
            "source": origin,
            "source_bbox": [int(round(v)) for v in box],
            "box": list(crop_box),
            "size": [crop_box[2] - crop_box[0], crop_box[3] - crop_box[1]],
        }
        record["status"] = "cropped"
        counts["cropped"] += 1

        if dry_run:
            crop = record["crop"]
            print(
                f"{label}: [{status}] {origin} {crop['source_bbox']} -> "
                f"crop {crop['box']} ({crop['size'][0]}x{crop['size'][1]}) "
                f"from {path.name}"
            )
        else:
            record["image_base64"] = encode_png(image.crop(crop_box))

    counts["statuses"] = dict(statuses)
    return nodes, counts


def sort_key(node: dict) -> tuple[int, int]:
    index = node.get("index")
    return (0, index) if isinstance(index, int) else (1, 0)


def write_output(
    out_path: Path,
    nodes: list[dict],
    node_types: frozenset[str],
) -> list[dict]:
    """Write the episode's crops, rebuilt from scratch, nodes in index order.

    The file always reflects one run, so stale entries from older runs or
    settings never linger. The one exception: a ``--kind`` run that covers only
    some node types keeps the other types' nodes from the existing file, so
    ``--kind typing`` does not wipe the clicks. Returns the nodes written.
    """
    kept: list[dict] = []
    if out_path.is_file():
        try:
            loaded = json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"warning: {out_path} is not valid JSON, overwriting.", file=sys.stderr)
            loaded = None
        # Only this layout carries a ``nodes`` list; anything older is rebuilt.
        if isinstance(loaded, dict) and isinstance(loaded.get("nodes"), list):
            kept = [
                node for node in loaded["nodes"]
                if isinstance(node, dict) and node.get("type") not in node_types
            ]

    merged = sorted(kept + nodes, key=sort_key)
    payload = {"nodes": merged}
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return merged


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #

def audit(episodes_root: Path, node_types: frozenset[str] = KINDS["all"]) -> int:
    """Report which episodes' timelines have nodes the mappers never touched.

    Answers the question this script exists to serve: before cropping anything,
    which timelines are simply un-enriched?
    """
    if not episodes_root.is_dir():
        print(f"error: not a directory: {episodes_root}", file=sys.stderr)
        return 1

    missing_timeline: list[str] = []
    rows: list[tuple[str, int, int, dict[str, int]]] = []

    for episode in sorted(p for p in episodes_root.iterdir() if p.is_dir()):
        timeline_path = episode / TIMELINE_RELPATH
        if not timeline_path.is_file():
            missing_timeline.append(episode.name)
            continue
        try:
            timeline = load_timeline(timeline_path)
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            print(f"warning: {episode.name}: {exc}", file=sys.stderr)
            continue

        statuses: collections.Counter[str] = collections.Counter()
        clicks = 0
        for item in timeline.get("items", []):
            if item.get("type") not in node_types:
                continue
            clicks += 1
            statuses[match_status(item)] += 1
        rows.append((episode.name, clicks, statuses[NO_OMNIPARSER], dict(statuses)))

    unenriched = [row for row in rows if row[2]]
    for name, clicks, bare, statuses in rows:
        flag = "  <-- no omniparser data" if bare else ""
        breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(statuses.items()))
        print(f"{name:28} {clicks:5} nodes  {breakdown}{flag}")

    if missing_timeline:
        print(f"\nno {TIMELINE_RELPATH} at all: {', '.join(missing_timeline)}")
    print(
        f"\n{len(unenriched)}/{len(rows)} timelines have nodes with no omniparser "
        f"block ({sum(row[2] for row in unenriched)} nodes total)."
    )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--episode-dir",
        type=Path,
        help="Episode root; screenshots resolve relative to it.",
    )
    parser.add_argument(
        "--audit",
        type=Path,
        metavar="EPISODES_ROOT",
        help=(
            "Report which episodes' timelines have clicks with no omniparser "
            "data, then exit. Nothing is cropped or written."
        ),
    )
    parser.add_argument(
        "--kind",
        choices=sorted(KINDS),
        default="all",
        help="Which nodes to crop: click, typing or all (default: all).",
    )
    parser.add_argument(
        "--timeline",
        type=Path,
        help=f"Timeline JSON (default: <episode-dir>/{TIMELINE_RELPATH}).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help=f"Output JSON (default: {OUTPUT_NAME} beside the timeline).",
    )
    parser.add_argument(
        "--pad-frac",
        type=float,
        default=DEFAULT_PAD_FRAC,
        help=f"Grow the bbox outward on each side by this fraction of its size "
        f"(default: {DEFAULT_PAD_FRAC}).",
    )
    parser.add_argument(
        "--base-radius",
        type=int,
        default=NEAREST_RADIUS_PX,
        help=(
            "Half-size of the box used when a click or typing node matched nothing "
            f"(default: {NEAREST_RADIUS_PX})."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be cropped without encoding or writing.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    node_types = KINDS[args.kind]

    if args.audit is not None:
        return audit(args.audit.resolve(), node_types)

    if args.episode_dir is None:
        print("error: --episode-dir is required (or use --audit).", file=sys.stderr)
        return 2

    episode_dir = args.episode_dir.resolve()
    timeline_path = (args.timeline or episode_dir / TIMELINE_RELPATH).resolve()
    out_path = (args.out or timeline_path.parent / OUTPUT_NAME).resolve()

    try:
        timeline = load_timeline(timeline_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    nodes, counts = collect(
        timeline, episode_dir, args.pad_frac, args.base_radius, args.dry_run,
        node_types,
    )

    written: list[dict] = []
    if not args.dry_run:
        written = write_output(out_path, nodes, node_types)

    print(
        f"{counts['clicks']} clicks and {counts['typing']} typing nodes scanned, "
        f"{counts['no_content']} without content, "
        f"{counts['cropped']} cropped, {counts['skipped']} skipped."
    )
    if counts["statuses"]:
        breakdown = ", ".join(
            f"{status}: {n}" for status, n in sorted(counts["statuses"].items())
        )
        print(f"  by status -- {breakdown}")
    if counts["statuses"].get(NO_OMNIPARSER):
        print(
            f"  note: {counts['statuses'][NO_OMNIPARSER]} nodes carry no omniparser "
            f"block at all -- run mousemapper.py / node.py over {timeline_path.name} "
            f"first if you wanted real bboxes rather than point boxes."
        )
    if not args.dry_run:
        print(f"wrote {len(written)} nodes to {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
