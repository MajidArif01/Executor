"""
accessibility_tree.py
---------------------
Walk one episode's timeline and, for every mouse click, mouse drag, and typed
string, resolve its captured accessibility (AX) tree, find the element it acted
on, and append that element back onto the timeline item under an "accessibility"
key.

This module owns the files: reading the timeline and AX captures, shaping the
record stored for each item, and saving the timeline. The algorithms live in:
    ClickElement.py        find_element(capture, x, y)  -> element under the click
    TypingElement.py       find_typing_field(capture)   -> focused text-entry field
    find_drag_elements.py  DragElementFinder            -> pick/drop under drag points

Layout assumed for an episode directory:
    <episode>/processdata/timeline.json
    <episode>/rawdata/accessibility/<YYYYMMDD_HHMMSS_ffffff>_accessibility.json

Usage:
    python Accesibilitytree/accessibility_tree.py "<episode dir | timeline.json>"

Examples:
    python Accesibilitytree/accessibility_tree.py "C:/Users/majid/Desktop/Orca_Observation/episode/google-weather-search"
    python Accesibilitytree/accessibility_tree.py "C:/.../google-weather-search/processdata/timeline.json"

For each click item (source == "mouse", type == "click", not a drag) an
"accessibility" block is written next to "omniparser":

    {
        "ax_source": "20260908_111307_871369_accessibility.json",
        "matched": true,
        "click": {"x": 1519, "y": 1571},
        "window_context": { ... },
        "target": { ...deepest node under the click... },
        "ancestor_chain": [ ...root -> target... ]
    }

For each drag item (kind == "mouse_drag") pick/drop snapshots named on from/to
are hit-tested; matched elements are stored on from.element / to.element and in
accessibility.pick / accessibility.drop.

For each typing item (source == "keyboard", type == "Typing") the capture of
the LAST typed character is used (the last image, with its _screenN suffix
stripped, names the AX file), and TypingElement's saved report is stored:

    {
        "ax_source": "20260929_132743_045512_accessibility.json",
        "matched": true,                      <- status == inferred_target
        "typed": {"value": "1234", "last_char_time": "2026-09-29T13:27:43.045512"},
        "status": "inferred_target", "reason": "...",
        "window_context": { ...same shape as a click... },
        "target": { ...focused text field, TypingElement's saved target... },
        "ancestor_chain": [ ...same shape as a click: root (level 0) -> target... ]
    }

The timeline.json is overwritten in place; a timeline.json.<UTC>.bak copy is
written first as a safety net.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running both as a module (python -m) and as a script.
try:
    from .ClickElement import _build_result, find_element, parse_resolution
    from .TypingElement import build_report, find_typing_field
    from .find_drag_elements import DragElementFinder
except ImportError:  # pragma: no cover - direct-script fallback
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from Accesibilitytree.ClickElement import _build_result, find_element, parse_resolution
    from Accesibilitytree.TypingElement import build_report, find_typing_field
    from Accesibilitytree.find_drag_elements import DragElementFinder


# ── path resolution ────────────────────────────────────────────────────

def normalize_path_arg(arg: str) -> str:
    """Strip whitespace, optional wrapping quotes, and trailing path separators."""
    s = arg.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1].strip()
    while len(s) > 1 and s[-1] in "\\/":
        s = s[:-1]
    return s


def resolve_paths(arg: str) -> tuple[Path, Path]:
    """From an episode folder OR a timeline.json path, return
    (timeline_path, accessibility_dir)."""
    raw = arg.strip()
    if " --" in raw:
        raise SystemExit(
            "Invalid path: extra CLI flags were merged into the path argument. "
            "In PowerShell, do not put a trailing backslash before the closing "
            'quote (…\\sienna-grid\\" escapes the quote). Use:\n'
            '  "...\\sienna-grid" --index 11 --dry-run\n'
            "or omit the trailing backslash."
        )

    p = Path(normalize_path_arg(raw))
    if not p.exists():
        raise SystemExit(f"Path not found: {p}")

    if p.is_file():
        timeline_path = p
        # timeline.json lives in <episode>/processdata/, AX in <episode>/rawdata/accessibility
        episode_dir = p.parent.parent
    else:
        timeline_path = p / "processdata" / "timeline.json"
        episode_dir = p

    if not timeline_path.exists():
        raise SystemExit(f"timeline.json not found: {timeline_path}")

    ax_dir = episode_dir / "rawdata" / "accessibility"
    if not ax_dir.is_dir():
        raise SystemExit(f"accessibility directory not found: {ax_dir}")

    return timeline_path, ax_dir


# ── item → accessibility file mapping ───────────────────────────────────

SCREEN_SUFFIX = re.compile(r"_screen\d+\.png$", re.IGNORECASE)
DRAG_ARROW = re.compile(
    r"\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)\s*->\s*\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)"
)
DRAG_FROM = re.compile(r"from=\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)")


def prefix_from_item(item: dict, last: bool = False) -> str | None:
    """Derive the capture prefix (e.g. '20260908_111307_871369') used to name
    the accessibility file. Prefer the image name; fall back to the timestamp.

    last=False uses the first image (a click); last=True uses the last image
    (the final typed character). One AX file is captured per timestamp, so the
    _screenN suffix is dropped whichever screen the image belongs to."""
    images = item.get("images") or []
    if images:
        name = images[-1 if last else 0].get("value") or ""
        stripped = SCREEN_SUFFIX.sub("", name)
        if stripped and stripped != name:
            return stripped

    # Fallback: build from an ISO timestamp -> YYYYMMDD_HHMMSS_ffffff
    events = item.get("events") or []
    candidates = [item.get("time"), item.get("timestamp")]
    if last:
        candidates += [events[-1].get("timestamp") if events else None, item.get("endtime")]
    for time_str in candidates:
        if not time_str:
            continue
        try:
            return datetime.fromisoformat(time_str).strftime("%Y%m%d_%H%M%S_%f")
        except ValueError:
            pass
    return None


def ax_path_for(item: dict, ax_dir: Path, last: bool = False) -> tuple[Path | None, str | None]:
    """Return (path, filename) of the accessibility JSON for a timeline item."""
    prefix = prefix_from_item(item, last)
    if not prefix:
        return None, None
    filename = f"{prefix}_accessibility.json"
    return ax_dir / filename, filename


def click_point(item: dict) -> tuple[int, int] | None:
    events = item.get("events") or []
    if not events:
        return None
    ev = events[0]
    x, y = ev.get("x"), ev.get("y")
    if x is None or y is None:
        return None
    return int(x), int(y)


def is_mouse_drag(item: dict) -> bool:
    if item.get("kind") == "mouse_drag":
        return True
    return (
        item.get("source") == "mouse"
        and item.get("type") == "click"
        and item.get("value") == "mouse_drag"
    )


def is_mouse_click(item: dict) -> bool:
    return (
        item.get("source") == "mouse"
        and item.get("type") == "click"
        and item.get("value") != "mouse_drag"
    )


def is_typing(item: dict) -> bool:
    return item.get("source") == "keyboard" and item.get("type") == "Typing"


def point_from_side(side: dict) -> tuple[int, int] | None:
    x, y = side.get("x"), side.get("y")
    if x is None or y is None:
        return None
    return int(x), int(y)


def parse_drag_points(ev: dict) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """ORCA detail: 'drag left (x,y) -> (x2,y2), ...' or legacy 'from=(x,y),...'."""
    detail = str(ev.get("detail") or "")
    arrow = DRAG_ARROW.search(detail)
    if arrow:
        return (int(arrow.group(1)), int(arrow.group(2))), (
            int(arrow.group(3)),
            int(arrow.group(4)),
        )
    legacy = DRAG_FROM.search(detail)
    if legacy and ev.get("x") is not None and ev.get("y") is not None:
        return (int(legacy.group(1)), int(legacy.group(2))), (int(ev["x"]), int(ev["y"]))
    return None


def drag_pick_drop(item: dict) -> tuple[
    tuple[int, int] | None,
    tuple[int, int] | None,
    str | None,
    str | None,
    str | None,
]:
    """Return pick point, drop point, pick ax filename, drop ax filename, or error reason."""
    if item.get("kind") == "mouse_drag":
        pick_side = item.get("from") or {}
        drop_side = item.get("to") or {}
        pick_pt = point_from_side(pick_side)
        drop_pt = point_from_side(drop_side)
        if pick_pt is None or drop_pt is None:
            return None, None, None, None, "drag missing from/to coordinates"
        pick_name = pick_side.get("accessibility")
        drop_name = drop_side.get("accessibility")
        if not pick_name or not drop_name:
            return None, None, None, None, "drag missing from/to accessibility filename"
        return pick_pt, drop_pt, pick_name, drop_name, None

    events = item.get("events") or []
    if not events:
        return None, None, None, None, "drag has no events"
    parsed = parse_drag_points(events[0])
    if parsed is None:
        return None, None, None, None, "drag has no start/end point in event detail"
    pick_pt, drop_pt = parsed
    prefix = prefix_from_item(item)
    if not prefix:
        return None, None, None, None, "could not derive accessibility prefix for drag"
    pick_name = f"{prefix}_accessibility_pick.json"
    drop_name = f"{prefix}_accessibility_drop.json"
    return pick_pt, drop_pt, pick_name, drop_name, None


# ── reading ─────────────────────────────────────────────────────────────

def read_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_capture(item: dict, ax_dir: Path,
                 last: bool = False) -> tuple[dict | None, str | None, str | None]:
    """Read the AX capture for a timeline item (see prefix_from_item for `last`).

    Returns (capture, filename, reason); capture is None and reason says why
    when the file is missing or holds no tree."""
    ax_file, ax_name = ax_path_for(item, ax_dir, last)
    if ax_file is None or not ax_file.exists():
        return None, ax_name, f"accessibility file not found: {ax_name}"

    capture = read_json(ax_file)
    # Some captures have no tree (ax_tree_available == false / tree == null).
    if capture.get("tree") is None:
        return None, ax_name, capture.get("reason") or "no accessibility tree available"
    return capture, ax_name, None


def load_named_capture(
    ax_dir: Path, filename: str | None,
) -> tuple[dict | None, str | None, str | None]:
    if not filename:
        return None, None, "no accessibility filename on drag side"
    ax_file = ax_dir / filename
    if not ax_file.is_file():
        return None, filename, f"accessibility file not found: {filename}"
    try:
        return DragElementFinder.load_snapshot(ax_file), filename, None
    except ValueError as exc:
        return None, filename, str(exc)


# ── the stored "accessibility" block ────────────────────────────────────

def matched_record(ax_name: str, x: int, y: int, found: dict) -> dict:
    return {
        "ax_source": ax_name,
        "matched": True,
        "click": {"x": x, "y": y, "normalized": found["click"]["normalized"]},
        "hit_layer": found["hit_layer"],
        "window_context": found["window_context"],
        "target": found["target"],
        "ancestor_chain": found["ancestor_chain"],
    }


CHILD_INDEX = re.compile(r"\.children\[(\d+)\]")


def nodes_on_path(tree: dict, json_path: str) -> list[dict]:
    """Root -> node list for a TypingElement json_path such as
    '$.tree.children[1].children[0]'."""
    nodes = [tree]
    for index in CHILD_INDEX.findall(json_path):
        nodes.append(nodes[-1]["children"][int(index)])
    return nodes


def typing_record(ax_name: str, item: dict, capture: dict, result: dict) -> dict:
    """Typing block: TypingElement's target as-is, with window_context and
    ancestor_chain in the same shape as a click record (level 0 = root)."""
    events = item.get("events") or []
    report = build_report(result, source_file=ax_name)
    record = {
        "ax_source": ax_name,
        "matched": result["status"] == "inferred_target",
        "typed": {
            "value": item.get("value"),
            "last_char_time": events[-1].get("timestamp") if events else item.get("endtime"),
        },
        "status": report["detection"]["status"],
        "reason": report["detection"]["reason"],
        "window_context": None,
        "target": report["target"],
        "ancestor_chain": [],
    }
    if report["target"]:
        chain = nodes_on_path(capture["tree"], report["target"]["json_path"])
        click_style = _build_result({"element": chain[-1], "parent_chain": chain, "layer": None})
        record["window_context"] = click_style["window_context"]
        record["ancestor_chain"] = click_style["ancestor_chain"]
    return record


def unmatched_record(reason: str, ax_name: str | None = None,
                     point: tuple[int, int] | None = None, typing: bool = False) -> dict:
    """A miss. A click miss without a point has no capture to name either;
    a typing miss always names the capture it looked for."""
    record = {"matched": False, "reason": reason}
    if point is not None or typing:
        record["ax_source"] = ax_name
    if point is not None:
        record["click"] = {"x": point[0], "y": point[1]}
    return record


def drag_side_record(ax_name: str, point: tuple[int, int], hit: dict) -> dict:
    record = {
        "ax_source": ax_name,
        "point": {"x": point[0], "y": point[1]},
        "status": hit.get("status"),
        "element": hit.get("element"),
    }
    if hit.get("status") == "ambiguous":
        record["candidates"] = hit.get("candidates") or []
    return record


def apply_drag_elements(item: dict, result: dict) -> None:
    for side, key in (("from", "pick"), ("to", "drop")):
        hit = result.get(key) or {}
        element = hit.get("element")
        if hit.get("status") != "matched" or not element:
            continue
        if isinstance(item.get(side), dict):
            item[side]["element"] = element
        else:
            item[side] = {"element": element}


def drag_outcome_stat(result: dict) -> str:
    pick = (result.get("pick") or {}).get("status")
    drop = (result.get("drop") or {}).get("status")
    if pick == "matched" and drop == "matched":
        return "drag_matched"
    if pick == "ambiguous" or drop == "ambiguous":
        return "drag_ambiguous"
    if pick == "not_found" or drop == "not_found":
        return "drag_no_match"
    return "drag_partial"


def accessibility_for_click(item: dict, ax_dir: Path) -> tuple[dict, str]:
    """Resolve one click item to (record to store, stats key)."""
    pt = click_point(item)
    if pt is None:
        return unmatched_record("no click point in events"), "no_point"

    capture, ax_name, reason = load_capture(item, ax_dir)
    if capture is None:
        return unmatched_record(reason, ax_name, pt), "missing_tree"

    # Pass the whole capture so find_element can rescale the click from the
    # recorder's screen space into the tree's pixel space.
    click_space = parse_resolution(item["events"][0].get("screen_resolution"))
    found = find_element(capture, pt[0], pt[1], click_space=click_space)
    if found is None:
        return unmatched_record("no element contains the click point", ax_name, pt), "no_match"
    return matched_record(ax_name, pt[0], pt[1], found), "matched"


def accessibility_for_drag(item: dict, ax_dir: Path) -> tuple[dict, str]:
    pick_pt, drop_pt, pick_name, drop_name, reason = drag_pick_drop(item)
    if reason:
        return {"matched": False, "reason": reason}, "drag_no_point"

    pick_snap, pick_ax, pick_err = load_named_capture(ax_dir, pick_name)
    if pick_snap is None:
        return {"matched": False, "reason": pick_err, "ax_source": pick_ax}, "drag_missing_tree"
    drop_snap, drop_ax, drop_err = load_named_capture(ax_dir, drop_name)
    if drop_snap is None:
        return {"matched": False, "reason": drop_err, "ax_source": drop_ax}, "drag_missing_tree"

    result = DragElementFinder().find_drag_elements(pick_snap, drop_snap, pick_pt, drop_pt)
    apply_drag_elements(item, result)
    record = {
        "matched": drag_outcome_stat(result) == "drag_matched",
        "pick": drag_side_record(pick_ax, pick_pt, result["pick"]),
        "drop": drag_side_record(drop_ax, drop_pt, result["drop"]),
    }
    return record, drag_outcome_stat(result)


TYPING_STATUS_STAT = {
    "inferred_target": "typing_matched",
    "ambiguous": "typing_ambiguous",
    "unresolved": "typing_unresolved",
}


def accessibility_for_typing(item: dict, ax_dir: Path) -> tuple[dict, str]:
    """Resolve one typing item, using the capture of its last typed character,
    to (record to store, stats key)."""
    capture, ax_name, reason = load_capture(item, ax_dir, last=True)
    if capture is None:
        return unmatched_record(reason, ax_name, typing=True), "typing_missing_tree"
    try:
        result = find_typing_field(capture)
    except ValueError as exc:  # e.g. ax_tree_available == false
        return unmatched_record(str(exc), ax_name, typing=True), "typing_missing_tree"
    return typing_record(ax_name, item, capture, result), TYPING_STATUS_STAT[result["status"]]


# ── writing ─────────────────────────────────────────────────────────────

def save_timeline(timeline_path: Path, timeline: dict) -> Path:
    """Back up, then overwrite in place. Returns the backup path."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup = timeline_path.with_suffix(timeline_path.suffix + f".{ts}.bak")
    shutil.copy2(timeline_path, backup)

    with open(timeline_path, "w", encoding="utf-8") as f:
        json.dump(timeline, f, indent=2, ensure_ascii=False)
    return backup


# ── main enrichment loop ────────────────────────────────────────────────

def enrich(timeline_path: Path, ax_dir: Path) -> dict:
    timeline = read_json(timeline_path)
    stats = {
        "clicks": 0, "matched": 0, "no_match": 0, "missing_tree": 0, "no_point": 0,
        "drags": 0, "drag_matched": 0, "drag_ambiguous": 0, "drag_no_match": 0,
        "drag_partial": 0, "drag_missing_tree": 0, "drag_no_point": 0,
        "typing": 0, "typing_matched": 0, "typing_ambiguous": 0,
        "typing_unresolved": 0, "typing_missing_tree": 0,
    }

    for item in timeline.get("items", []):
        if is_mouse_drag(item):
            stats["drags"] += 1
            item["accessibility"], outcome = accessibility_for_drag(item, ax_dir)
        elif is_mouse_click(item):
            stats["clicks"] += 1
            item["accessibility"], outcome = accessibility_for_click(item, ax_dir)
        elif is_typing(item):
            stats["typing"] += 1
            item["accessibility"], outcome = accessibility_for_typing(item, ax_dir)
        else:
            continue
        stats[outcome] += 1

    stats["backup"] = str(save_timeline(timeline_path, timeline))
    return stats


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="accessibility_tree.py",
        description="Append each mouse click's, drag's, and typed string's accessibility "
        "element onto the episode timeline.",
    )
    parser.add_argument(
        "target", nargs="?",
        help="Episode folder or a timeline.json path (standalone use)",
    )
    parser.add_argument(
        "--episode-dir", dest="episode_dir", default=None,
        help="Episode folder (used when run as a pipeline stage)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    target = args.episode_dir or args.target
    if not target:
        print(__doc__)
        print("error: provide an episode dir / timeline.json, or --episode-dir", file=sys.stderr)
        return 1

    timeline_path, ax_dir = resolve_paths(target)
    stats = enrich(timeline_path, ax_dir)

    print(f"Timeline : {timeline_path}")
    print(f"AX trees : {ax_dir}")
    print(f"Backup   : {stats['backup']}")
    print("-" * 50)
    print(f"Mouse clicks found : {stats['clicks']}")
    print(f"  matched           : {stats['matched']}")
    print(f"  no element hit     : {stats['no_match']}")
    print(f"  missing AX tree    : {stats['missing_tree']}")
    print(f"  no click point     : {stats['no_point']}")
    print(f"Mouse drags found  : {stats['drags']}")
    print(f"  matched           : {stats['drag_matched']}")
    print(f"  ambiguous          : {stats['drag_ambiguous']}")
    print(f"  no element hit     : {stats['drag_no_match']}")
    print(f"  partial            : {stats['drag_partial']}")
    print(f"  missing AX tree    : {stats['drag_missing_tree']}")
    print(f"  bad/missing coords : {stats['drag_no_point']}")
    print(f"Typing items found : {stats['typing']}")
    print(f"  matched           : {stats['typing_matched']}")
    print(f"  ambiguous          : {stats['typing_ambiguous']}")
    print(f"  unresolved         : {stats['typing_unresolved']}")
    print(f"  missing AX tree    : {stats['typing_missing_tree']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
