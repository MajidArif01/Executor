"""Crop the clicks OmniParser couldn't name, as base64 PNGs.

A click node whose ``omniparser.matched_element`` carries no ``content`` -- or
that matched nothing at all -- tells us nothing about what the user pressed. For
each of those this script takes the element's bbox (or, with no element, a box
around the click point), grows it 10x about its center, crops that region out of
the node's screenshot and stores it base64-encoded in a single ``base64.json``
keyed by node index, ready to hand to a VLM.

    python click_content.py --episode-dir <EPISODE_ROOT>
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

from Omniparser_Runner.config import CLICK_TYPE, TIMELINE_RELPATH
from Omniparser_Runner.timeline_reader import (
    load_timeline,
    pick_event_image,
    resolve_image,
)
from mousemapper import NEAREST_RADIUS_PX

# Grow the source bbox outward on every side by this fraction of its own
# width/height before cropping (0.10 => each edge moves out 10%).
DEFAULT_PAD_FRAC = 0.10

OUTPUT_NAME = "base64.json"

# Status we record for a click the mapper never touched, so an un-enriched
# episode is distinguishable from one the mapper looked at and gave up on.
NO_OMNIPARSER = "no_omniparser"


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



def source_bbox(
    item: dict,
    image_size: tuple[int, int],
    base_radius: int,
) -> tuple[tuple[float, float, float, float], str] | None:
    """The box to grow, in pixels, plus where it came from.

    Prefers the matched element's own pixel bbox, falls back to its normalized
    bbox scaled by the image, and finally -- no element at all -- to a small box
    centred on the click point.
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

    point = click_point(item)
    if point is None:
        return None
    x, y = point
    box = (x - base_radius, y - base_radius, x + base_radius, y + base_radius)
    return box, "click_point"


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
    """The screenshot the click landed on, resolved against this machine.

    Timelines captured elsewhere carry absolute paths from that machine, so the
    episode-relative path wins and the absolute one is only a fallback.
    """
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

def collect(
    timeline: dict,
    episode_dir: Path,
    pad_frac: float,
    base_radius: int,
    dry_run: bool,
) -> tuple[dict, dict]:
    """Crop every no-content click. Returns (entries, counters)."""
    entries: dict[str, dict] = {}
    counts = {"clicks": 0, "no_content": 0, "cropped": 0, "skipped": 0}
    statuses: collections.Counter[str] = collections.Counter()

    for item in timeline.get("items", []):
        if item.get("type") != CLICK_TYPE:
            continue
        counts["clicks"] += 1
        if not has_no_content(item):
            continue
        counts["no_content"] += 1

        index = item.get("index")
        label = f"node {index}"
        omni = item.get("omniparser") or {}
        status = match_status(item)
        statuses[status] += 1

        path = screenshot_for(item, episode_dir)
        if path is None:
            print(f"warning: {label}: screenshot not found, skipping.", file=sys.stderr)
            counts["skipped"] += 1
            continue

        try:
            with Image.open(path) as handle:
                image = handle.convert("RGB")
        except OSError as exc:
            print(
                f"warning: {label}: cannot read {path} ({exc}), skipping.",
                file=sys.stderr,
            )
            counts["skipped"] += 1
            continue

        # The recorded size is what OmniParser normalized against; trust the
        # real pixels when the two disagree.
        recorded = omni.get("image_size")
        size = image.size
        if isinstance(recorded, (list, tuple)) and len(recorded) == 2:
            if tuple(recorded) == image.size:
                size = tuple(recorded)

        found = source_bbox(item, size, base_radius)
        if found is None:
            print(f"warning: {label}: no bbox and no click point, skipping.", file=sys.stderr)
            counts["skipped"] += 1
            continue
        box, origin = found

        crop_box = pad_about_center(box, pad_frac, size)
        if crop_box is None:
            print(
                f"warning: {label}: crop falls outside the image, skipping.",
                file=sys.stderr,
            )
            counts["skipped"] += 1
            continue

        entry = {
            "node_index": index,
            "image": path.name,
            "image_size": list(size),
            "click": list(click_point(item) or []),
            "bbox_source": origin,
            "source_bbox": [int(round(v)) for v in box],
            "crop_box": list(crop_box),
            "crop_size": [crop_box[2] - crop_box[0], crop_box[3] - crop_box[1]],
            "match_status": status,
        }

        if dry_run:
            print(
                f"{label}: [{status}] {origin} {entry['source_bbox']} -> "
                f"crop {entry['crop_box']} "
                f"({entry['crop_size'][0]}x{entry['crop_size'][1]}) from {path.name}"
            )
        else:
            entry["base64"] = encode_png(image.crop(crop_box))
            entries[str(index)] = entry

        counts["cropped"] += 1

    counts["statuses"] = dict(statuses)
    return entries, counts


def write_entries(out_path: Path, entries: dict) -> None:
    """Merge into any existing file so a second episode doesn't clobber the first."""
    existing: dict = {}
    if out_path.is_file():
        try:
            loaded = json.loads(out_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except json.JSONDecodeError:
            print(f"warning: {out_path} is not valid JSON, overwriting.", file=sys.stderr)

    existing.update(entries)
    out_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #

def audit(episodes_root: Path) -> int:
    """Report which episodes' timelines have clicks the mapper never touched.

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
            if item.get("type") != CLICK_TYPE:
                continue
            clicks += 1
            statuses[match_status(item)] += 1
        rows.append((episode.name, clicks, statuses[NO_OMNIPARSER], dict(statuses)))

    unenriched = [row for row in rows if row[2]]
    for name, clicks, bare, statuses in rows:
        flag = "  <-- no omniparser data" if bare else ""
        breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(statuses.items()))
        print(f"{name:28} {clicks:5} clicks  {breakdown}{flag}")

    if missing_timeline:
        print(f"\nno {TIMELINE_RELPATH} at all: {', '.join(missing_timeline)}")
    print(
        f"\n{len(unenriched)}/{len(rows)} timelines have clicks with no omniparser "
        f"block ({sum(row[2] for row in unenriched)} clicks total)."
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
            "Half-size of the box used when a click matched nothing "
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

    if args.audit is not None:
        return audit(args.audit.resolve())

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

    entries, counts = collect(
        timeline, episode_dir, args.pad_frac, args.base_radius, args.dry_run
    )

    if entries:
        write_entries(out_path, entries)

    print(
        f"{counts['clicks']} clicks scanned, {counts['no_content']} without content, "
        f"{counts['cropped']} cropped, {counts['skipped']} skipped."
    )
    if counts["statuses"]:
        breakdown = ", ".join(
            f"{status}: {n}" for status, n in sorted(counts["statuses"].items())
        )
        print(f"  by status -- {breakdown}")
    if counts["statuses"].get(NO_OMNIPARSER):
        print(
            f"  note: {counts['statuses'][NO_OMNIPARSER]} clicks carry no omniparser "
            f"block at all -- run the mapper over {timeline_path.name} first if you "
            f"wanted real bboxes rather than click-point boxes."
        )
    if entries:
        print(f"wrote {len(entries)} entries to {out_path}")
    elif not args.dry_run and counts["no_content"]:
        print("nothing written.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
