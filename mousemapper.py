"""For every click and scroll node in an episode's timeline.json, find the
OmniParser element its exact (x, y) landed on and append it to the node, in place.

``Omniparser_Runner`` already parses every pointer node's screenshot and writes
one JSON per node to
``AnotatedData/AnotatedJson/{mouse,scroll}/node_{index}_{stem}.json`` — an
``elements`` dict of ``icon N -> {type, bbox, interactivity, content, source}``.
``timeline.json``'s pointer nodes only know *where* the event happened
(``events[0].x/y``); they don't know *what* was under the cursor.

This script closes that gap by mutating ``timeline.json`` directly: each click
and scroll item gets a single ``omniparser`` key whose field set mirrors
``timeline_enriched.json``, so both artifacts can be read by one consumer::

    "omniparser": {
      "kind": "mouse",                 # or "scroll"
      "node_type": "click",            # the timeline node type
      "annotated_json_path": ...,      # the OmniParser record this used
      "annotated_image": ...,          # the annotated PNG for that screenshot
      "image_path": ..., "image_name": ..., "image_size": [w, h],
      "screen": 1, "screen_matched": true,
      "element_count": 46,
      "match_strategy": "click_point_in_bbox",
      "match_status": "bbox",
      "matched_element": {..., "match": "bbox"},
      "candidates": [],
      "match_query": {"x": 1132, "y": 1530},
      "normalized_query": [0.442188, 0.95625],
      "match_count": 1
    }

The block holds the element whose bbox contains the event's exact pixel
coordinates (smallest box wins when boxes are nested), reported as
``match_status: "bbox"``. If no bbox contains the point, the nearest element
within ``NEAREST_RADIUS_PX`` pixels is used instead (OmniParser's boxes don't
always reach the true edge of a small icon), reported as ``"tolerant"`` so it
stays distinguishable from a real containment match. A coordinate that falls
outside the screenshot itself (a bad capture, not a missing element) is reported
as ``out_of_bounds``. ``match_query`` and its companions appear only once a
geometric match was actually attempted — an early exit such as
``screen_mismatch`` has no point to report.

Scroll nodes additionally carry ``scroll_dx`` / ``scroll_dy``, parsed from the
event's ``detail`` (``"dx=0,dy=-1"``). Those are properties of the event rather
than of the match, so they are recorded whatever the outcome.

Because this edits the source file, a one-time backup (``timeline.json.bak``) is
made before the first write, and a node that already carries a block is left
alone on a re-run — including blocks written under this script's older
``omniparser_mouse`` / ``omniparser_scroll`` keys.

Running this before ``Omniparser_Runner`` has parsed the episode is harmless: a
node with no OmniParser record yet (``no_json`` / ``no_elements``) is reported
but *not* written, so it stays eligible once the parser has run. Placeholder
blocks left by an earlier version of this script are cleared on the next run.

Usage::

    python mousemapper.py                                  # every episode under EPISODES_ROOT
    python mousemapper.py --episode gourmet-tree
    python mousemapper.py --episode gourmet-tree --types scroll
    python mousemapper.py --episode-dir "D:/.../episode/gourmet-tree"
    python mousemapper.py --episode gourmet-tree --dry-run  # match + print, write nothing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

# Make the repository root importable so `Omniparser_Runner` resolves no matter
# what directory this script is run from.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Omniparser_Runner.config import (  # noqa: E402
    ANNOTATED_ROOT,
    ANNOTATED_SUFFIX,
    CLICK_TYPE,
    EPISODES_ROOT,
    JSON_DIR,
    MOUSE,
    MOUSE_JSON_DIR,
    SCROLL,
    SCROLL_JSON_DIR,
    SCROLL_TYPE,
    TIMELINE_RELPATH,
)
from Omniparser_Runner.timeline_reader import (  # noqa: E402
    IMAGE_SUBDIRS,
    event_screen,
    load_timeline,
    parse_scroll_delta,
    pick_event_image,
    resolve_image,
)

DEFAULT_EPISODE = None          # e.g. "gourmet-tree"; None = every episode

# One key for every node kind — the block's own ``kind`` / ``node_type`` fields
# say what it describes, matching the schema of ``timeline_enriched.json``.
OMNIPARSER_KEY = "omniparser"

# Keys written by earlier versions of this script. A node carrying one of these
# is treated as already enriched and left untouched, so a re-run neither
# rewrites old data nor stacks a second block beside it.
LEGACY_KEYS = ("omniparser_mouse", "omniparser_scroll")

# The pointer node types this script enriches. Each entry says how the node
# describes itself in the block, which AnotatedJson/ subfolder holds the
# OmniParser output for its screenshot, and how the match was arrived at.
POINTER_TYPES = {
    CLICK_TYPE: {
        "kind": MOUSE,
        "node_type": CLICK_TYPE,
        "json_subdir": MOUSE_JSON_DIR,
        "match_strategy": "click_point_in_bbox",
    },
    SCROLL_TYPE: {
        "kind": SCROLL,
        "node_type": SCROLL_TYPE,
        "json_subdir": SCROLL_JSON_DIR,
        "match_strategy": "scroll_point_in_bbox",
    },
}

# Vocabulary shared with ``timeline_enriched.json``: a containment hit is
# ``bbox``; a hit found only by relaxing to the nearest edge is ``tolerant``.
STATUS_BBOX = "bbox"
STATUS_TOLERANT = "tolerant"
STATUS_NOT_FOUND = "not_found"
STATUS_NO_JSON = "no_json"
STATUS_NO_ELEMENTS = "no_elements"
STATUS_SCREEN_MISMATCH = "screen_mismatch"
STATUS_NO_COORDS = "no_click_coords"
STATUS_NO_IMAGE_SIZE = "no_image_size"
STATUS_OUT_OF_BOUNDS = "out_of_bounds"

# Fallback for a click that lands in the gap between two bboxes: OmniParser's
# boxes don't always extend to the true edge of a small icon or button, so a
# click a few pixels outside every box can still be a real hit. Bounded so an
# empty region of the page is reported honestly as not_found rather than
# guessed at.
NEAREST_RADIUS_PX = 40

# Outcomes that say nothing about the node itself, only that OmniParser hasn't
# produced its output yet: no record file, or a record with no elements. Writing
# these would be worse than writing nothing — the already-enriched guard below
# would then treat the node as done forever, so running this script before
# ``Omniparser_Runner`` would permanently poison the timeline. Such nodes are
# left untouched, and a block already carrying one of these statuses (written by
# an earlier run) is re-matched rather than skipped.
RETRYABLE_STATUSES = frozenset({STATUS_NO_JSON, STATUS_NO_ELEMENTS})


def is_pending(item: dict) -> bool:
    """True if ``item`` carries only a retryable placeholder block."""
    for key in (OMNIPARSER_KEY, *LEGACY_KEYS):
        block = item.get(key)
        if isinstance(block, dict):
            return block.get("match_status") in RETRYABLE_STATUSES
    return False


# --------------------------------------------------------------------------- #
# Locating a click node's OmniParser JSON
# --------------------------------------------------------------------------- #

def record_json_dir(episode_dir: Path, json_subdir: str) -> Path:
    return episode_dir / ANNOTATED_ROOT / JSON_DIR / json_subdir


def resolve_record_json(
    episode_dir: Path,
    node_index: int | None,
    image_stem: str,
    json_subdir: str,
    kind: str,
) -> Path | None:
    """``node_{index}_{stem}.json``, falling back to a glob on the index alone."""
    kind_dir = record_json_dir(episode_dir, json_subdir)

    if node_index is not None:
        by_convention = kind_dir / f"node_{node_index}_{image_stem}.json"
        if by_convention.is_file():
            return by_convention

        matches = sorted(kind_dir.glob(f"node_{node_index}_*.json"))
        if matches:
            return matches[0]

    by_stem = kind_dir / f"{image_stem}_{kind}.json"
    return by_stem if by_stem.is_file() else None


def load_record(json_path: Path) -> dict | None:
    try:
        with open(json_path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


# --------------------------------------------------------------------------- #
# Point-in-bbox matching
# --------------------------------------------------------------------------- #

def bbox_of(element: dict) -> list[float] | None:
    """The element's normalized xyxy bbox, or None if it is unusable."""
    raw = element.get("bbox") if isinstance(element, dict) else None
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        return [float(v) for v in raw]
    except (TypeError, ValueError):
        return None


def bbox_area(bbox: list[float] | None) -> float:
    if not bbox:
        return float("inf")
    x1, y1, x2, y2 = bbox
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def bbox_pixels(bbox: list[float] | None, width: int, height: int) -> list[int] | None:
    if not bbox:
        return None
    x1, y1, x2, y2 = bbox
    return [round(x1 * width), round(y1 * height), round(x2 * width), round(y2 * height)]


def contains(bbox: list[float] | None, nx: float, ny: float) -> bool:
    if not bbox:
        return False
    x1, y1, x2, y2 = bbox
    return min(x1, x2) <= nx <= max(x1, x2) and min(y1, y2) <= ny <= max(y1, y2)


def edge_distance_px(bbox: list[float], nx: float, ny: float, width: int, height: int) -> float:
    """Pixel distance from (nx, ny) to the nearest edge of bbox, 0 if inside."""
    x1, y1, x2, y2 = bbox
    dx = max(x1 - nx, 0.0, nx - x2) * width
    dy = max(y1 - ny, 0.0, ny - y2) * height
    return (dx * dx + dy * dy) ** 0.5


def _element_entry(element_id: str, element: dict, width: int, height: int, status: str) -> dict:
    bbox = bbox_of(element)
    return {
        "element_id": element_id,
        "type": element.get("type"),
        "bbox": bbox,
        "bbox_pixels": bbox_pixels(bbox, width, height),
        "interactivity": element.get("interactivity"),
        "content": element.get("content"),
        "source": element.get("source"),
        "match": status,
    }


def find_element_at_point(
    x: float,
    y: float,
    width: int,
    height: int,
    elements: dict,
    nearest_radius_px: float = NEAREST_RADIUS_PX,
) -> tuple[dict | None, str]:
    """The element under the click, and how it was found.

    First pass: the smallest bbox that actually contains the point — ties go to
    the interactive element, since a YOLO-detected control is a better answer
    than an OCR text box drawn over the same pixels.

    If nothing contains the point, OmniParser's boxes may simply not extend to
    the true edge of a small icon or button: fall back to the closest bbox
    edge, but only within ``nearest_radius_px`` — beyond that the click is
    genuinely in an empty region and reporting a guess would be worse than
    
    reporting nothing.
    """
    nx, ny = x / width, y / height
    contained: list[tuple[float, bool, str, dict]] = []

    for element_id, element in elements.items():
        if not isinstance(element, dict):
            continue
        bbox = bbox_of(element)
        if contains(bbox, nx, ny):
            contained.append((bbox_area(bbox), not bool(element.get("interactivity")), element_id, element))

    if contained:
        contained.sort(key=lambda hit: (hit[0], hit[1], hit[2]))
        _, _, element_id, element = contained[0]
        return _element_entry(element_id, element, width, height, STATUS_BBOX), STATUS_BBOX

    nearest: list[tuple[float, bool, str, dict]] = []
    for element_id, element in elements.items():
        if not isinstance(element, dict):
            continue
        bbox = bbox_of(element)
        if not bbox:
            continue
        dist = edge_distance_px(bbox, nx, ny, width, height)
        if dist <= nearest_radius_px:
            nearest.append((dist, not bool(element.get("interactivity")), element_id, element))

    if nearest:
        nearest.sort(key=lambda hit: (hit[0], hit[1], hit[2]))
        _, _, element_id, element = nearest[0]
        return _element_entry(element_id, element, width, height, STATUS_TOLERANT), STATUS_TOLERANT

    return None, STATUS_NOT_FOUND


# --------------------------------------------------------------------------- #
# Per-node enrichment
# --------------------------------------------------------------------------- #

def image_size(path: Path) -> tuple[int | None, int | None]:
    try:
        with Image.open(path) as handle:
            return handle.size
    except (OSError, ValueError):
        return None, None


def annotated_image_for(episode_dir: Path, kind: str, image_stem: str) -> Path:
    """Where ``Omniparser_Runner`` wrote this node's annotated PNG."""
    return (
        episode_dir / ANNOTATED_ROOT / IMAGE_SUBDIRS[kind]
        / f"{image_stem}{ANNOTATED_SUFFIX}.png"
    )


def pointer_block(episode_dir: Path, item: dict, spec: dict) -> dict:
    """The ``omniparser`` block for one click or scroll node.

    ``spec`` is the node type's ``POINTER_TYPES`` entry: it supplies the block's
    self-description (``kind`` / ``node_type`` / ``match_strategy``) and the
    AnotatedJson/ subfolder its OmniParser output is read from.

    The field set mirrors ``timeline_enriched.json`` so both artifacts can be
    read by the same consumer. ``match_query`` / ``normalized_query`` /
    ``match_count`` appear only once a geometric match was actually attempted —
    an early exit such as ``screen_mismatch`` has no point to report. Scroll
    nodes additionally carry their wheel deltas.
    """
    node_index = item.get("index")
    image, screen_matched = pick_event_image(item)
    is_scroll = spec["kind"] == SCROLL

    block = {
        "kind": spec["kind"],
        "node_type": spec["node_type"],
        "annotated_json_path": None,
        "annotated_image": None,
        "image_path": None,
        "image_name": None,
        "image_size": None,
        "screen": None,
        "screen_matched": screen_matched,
        "element_count": 0,
        "match_strategy": spec["match_strategy"],
        "match_status": None,
        "matched_element": None,
        "candidates": [],
    }

    def finish(status: str, **extra) -> dict:
        """Stamp the outcome, then append the fields that trail it in the schema
        (query details, then a scroll's deltas) so key order stays stable."""
        block["match_status"] = status
        block.update(extra)
        if is_scroll:
            events = item.get("events") or []
            detail = events[0].get("detail") if events else None
            block["scroll_dx"], block["scroll_dy"] = parse_scroll_delta(detail)
        return block

    if image is None:
        return finish(STATUS_NO_COORDS)

    image_path = resolve_image(episode_dir, image)
    width, height = image_size(image_path)
    json_path = resolve_record_json(
        episode_dir, node_index, image_path.stem, spec["json_subdir"], spec["kind"]
    )
    record = load_record(json_path) if json_path else None
    elements = record.get("elements") if record else None
    elements = elements if isinstance(elements, dict) else {}

    # The runner records the annotated PNG it wrote; fall back to the
    # conventional path when this node has no record yet.
    annotated_image = (record or {}).get("annotated_image") or str(
        annotated_image_for(episode_dir, spec["kind"], image_path.stem)
    )

    block.update({
        "annotated_json_path": str(json_path) if json_path else None,
        "annotated_image": annotated_image,
        "image_path": str(image_path),
        "image_name": image.get("value") or image_path.name,
        "image_size": [width, height] if width and height else None,
        "screen": image.get("screen"),
        "element_count": len(elements),
    })

    if json_path is None:
        return finish(STATUS_NO_JSON)

    if not elements:
        return finish(STATUS_NO_ELEMENTS)

    # The event landed on a display this frame doesn't show; its bboxes
    # describe a different monitor, so pixel coordinates cannot be trusted.
    if not screen_matched and event_screen(item) is not None:
        return finish(STATUS_SCREEN_MISMATCH)

    events = item.get("events") or []
    if not events or events[0].get("x") is None or events[0].get("y") is None:
        return finish(STATUS_NO_COORDS)

    if not width or not height:
        return finish(STATUS_NO_IMAGE_SIZE)

    x, y = events[0]["x"], events[0]["y"]

    # A pointer coordinate outside the screenshot it's paired with cannot be
    # normalized meaningfully — every bbox test would silently fail. Surface
    # this as its own status rather than mixing it into not_found, since it
    # means the capture itself is suspect (e.g. a stale multi-monitor
    # coordinate), not that the element genuinely isn't there.
    if not (0 <= x <= width and 0 <= y <= height):
        return finish(STATUS_OUT_OF_BOUNDS, match_query={"x": x, "y": y})

    matched, status = find_element_at_point(x, y, width, height, elements)

    block["matched_element"] = matched
    return finish(
        status,
        match_query={"x": x, "y": y},
        normalized_query=[round(x / width, 6), round(y / height, 6)],
        match_count=1 if matched else 0,
    )


# --------------------------------------------------------------------------- #
# Episode orchestration
# --------------------------------------------------------------------------- #

def backup_timeline(timeline_path: Path) -> Path:
    """One-time backup before the first in-place write."""
    backup_path = timeline_path.with_suffix(timeline_path.suffix + ".bak")
    if not backup_path.is_file():
        with open(timeline_path, "rb") as src, open(backup_path, "wb") as dst:
            dst.write(src.read())
    return backup_path


def write_json_atomic(payload: dict, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def enrich_episode(
    episode_dir: Path,
    *,
    timeline_path: Path | None = None,
    node_types: set[str] | None = None,
    dry_run: bool = False,
    quiet: bool = False,
) -> dict:
    """Mutate one episode's ``timeline.json`` in place with pointer matches.

    ``node_types`` selects which of ``POINTER_TYPES`` to process (default: all).
    Returns ``{node_type: {"total", "already_done", "pending", "statuses"}}``,
    where ``statuses`` counts the outcomes of every node matched this run and
    ``pending`` counts those whose OmniParser output doesn't exist yet — matched,
    reported, but deliberately not written.
    """
    episode_dir = Path(episode_dir).resolve()
    timeline_path = timeline_path or (episode_dir / TIMELINE_RELPATH)
    node_types = node_types or set(POINTER_TYPES)

    timeline = load_timeline(timeline_path)
    items = timeline.get("items") or []

    counts = {
        node_type: {"total": 0, "already_done": 0, "pending": 0, "statuses": Counter()}
        for node_type in sorted(node_types)
    }
    wrote_anything = False

    for item in items:
        node_type = item.get("type")
        if node_type not in counts:
            continue
        spec = POINTER_TYPES[node_type]
        tally = counts[node_type]
        tally["total"] += 1

        # Leave anything already enriched alone — including blocks written under
        # this script's older per-kind keys, so a re-run neither rewrites them
        # nor adds a second block beside them. A placeholder block left by a run
        # that happened before OmniParser produced its output is not enrichment,
        # so it is re-matched (and dropped again if still unresolvable).
        has_block = OMNIPARSER_KEY in item or any(key in item for key in LEGACY_KEYS)
        if has_block and not is_pending(item):
            tally["already_done"] += 1
            continue

        block = pointer_block(episode_dir, item, spec)
        status = block["match_status"]
        tally["statuses"][status] += 1

        # Drop any placeholder an earlier run left, under whichever key it used,
        # so it is neither kept beside the new block nor mistaken for enrichment
        # if this node is still unresolvable.
        for key in (OMNIPARSER_KEY, *LEGACY_KEYS):
            if item.pop(key, None) is not None:
                wrote_anything = True

        if status in RETRYABLE_STATUSES:
            tally["pending"] += 1
            continue

        item[OMNIPARSER_KEY] = block
        wrote_anything = True

    if not quiet:
        print(f"Episode: {timeline.get('episode_name', episode_dir.name)}")
        print(f"Timeline: {timeline_path}")
        for node_type, tally in counts.items():
            breakdown = ", ".join(
                f"{count} {status}"
                for status, count in sorted(tally["statuses"].items())
            ) or "nothing new"
            print(
                f"{node_type} nodes: {tally['total']} total | {breakdown}"
                f" | {tally['already_done']} already enriched"
            )
        pending = sum(tally["pending"] for tally in counts.values())
        if pending:
            print(
                f"Note: {pending} node(s) have no OmniParser output yet and were left "
                f"unenriched. Run Omniparser_Runner on this episode, then re-run this "
                f"script."
            )
            print(f"      Expected under: {episode_dir / ANNOTATED_ROOT / JSON_DIR}"
            )

    if dry_run:
        return counts

    if wrote_anything:
        backup_path = backup_timeline(timeline_path)
        write_json_atomic(timeline, timeline_path)
        if not quiet:
            print(f"Backup  -> {backup_path}")
            print(f"Updated -> {timeline_path}")
    elif not quiet:
        print("Nothing new to write.")

    return counts


def discover_episodes(episodes_root: Path) -> list[Path]:
    if not episodes_root.is_dir():
        raise FileNotFoundError(f"Episodes root not found: {episodes_root}")
    episodes = sorted(
        p for p in episodes_root.iterdir()
        if p.is_dir() and (p / TIMELINE_RELPATH).is_file()
    )
    if not episodes:
        raise ValueError(f"No episodes with {TIMELINE_RELPATH} in: {episodes_root}")
    return episodes


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mousemapper.py",
        description="Match each click / scroll node's exact pixel coordinates to "
        "the OmniParser element under it, and append the result to timeline.json "
        "in place.",
    )
    parser.add_argument("--episodes-root", default=str(EPISODES_ROOT))
    parser.add_argument("--episode", default=DEFAULT_EPISODE)
    parser.add_argument("--episode-dir", default=None)
    parser.add_argument("--timeline", default=None)
    parser.add_argument(
        "--types", default="click,scroll",
        help="Comma-separated node types to enrich: click, scroll (default: both)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Match and print, write nothing")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


TYPE_ALIASES = {
    "click": CLICK_TYPE,
    "mouse": CLICK_TYPE,
    "scroll": SCROLL_TYPE,
}


def resolve_node_types(raw: str) -> set[str]:
    node_types = set()
    for token in raw.split(","):
        token = token.strip().lower()
        if not token:
            continue
        if token not in TYPE_ALIASES:
            raise ValueError(
                f"Unknown node type {token!r} (use click and/or scroll)"
            )
        node_types.add(TYPE_ALIASES[token])
    if not node_types:
        raise ValueError("--types selected nothing")
    return node_types


def resolve_jobs(args: argparse.Namespace) -> list[tuple[Path, Path | None]]:
    if args.timeline:
        timeline_path = Path(args.timeline).resolve()
        episode_dir = (
            Path(args.episode_dir).resolve() if args.episode_dir else timeline_path.parents[1]
        )
        return [(episode_dir, timeline_path)]
    if args.episode_dir:
        return [(Path(args.episode_dir).resolve(), None)]
    if args.episode:
        return [((Path(args.episodes_root) / args.episode).resolve(), None)]
    return [(d, None) for d in discover_episodes(Path(args.episodes_root))]


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    try:
        node_types = resolve_node_types(args.types)
        jobs = resolve_jobs(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    failed = 0
    for episode_dir, timeline_path in jobs:
        try:
            enrich_episode(
                episode_dir,
                timeline_path=timeline_path,
                node_types=node_types,
                dry_run=args.dry_run,
                quiet=args.quiet,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"Error [{episode_dir.name}]: {exc}", file=sys.stderr)
            failed += 1

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
