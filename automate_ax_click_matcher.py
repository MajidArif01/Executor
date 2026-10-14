"""
automate_ax_click_matcher.py
----------------------------
Walk an episode timeline and, for every mouse click, resolve that click's
accessibility tree, find the element under the click point, and append the
match result onto the timeline item.

Reuses `match_click` from Ax_click_matcher.py — the matching logic lives there.

Usage:
    python automate_ax_click_matcher.py <episode_dir | timeline.json>

Examples:
    python automate_ax_click_matcher.py "D:\\...\\episode\\searing-tie"
    python automate_ax_click_matcher.py "D:\\...\\searing-tie\\processdata\\timeline.json"

For each click item (source == "mouse", type == "click") an "accessibility"
block is added next to "omniparser":

    {
        "ax_source": "20260921_111049_116851_accessibility.json",
        "matched": true,
        "click": {"x": 999, "y": 1569},
        "target": { ...deepest node... },
        "depth": 7,
        "ancestor_chain": [ ...root -> target... ]
    }

The original timeline.json is backed up to timeline.json.<UTC-timestamp>.bak
before being overwritten in place.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from Ax_click_matcher import match_click


# ── path resolution ────────────────────────────────────────────────────

def resolve_paths(arg: str) -> tuple[Path, Path]:
    """From an episode folder OR a timeline.json path, return
    (timeline_path, accessibility_dir)."""
    p = Path(arg)
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


# ── click → accessibility file mapping ──────────────────────────────────

_SCREEN_SUFFIX = re.compile(r"_screen\d+\.png$", re.IGNORECASE)


def prefix_from_item(item: dict) -> str | None:
    """Derive the capture prefix (e.g. '20260921_111049_116851') used to name
    the accessibility file. Prefer the image name; fall back to the timestamp."""
    images = item.get("images") or []
    if images:
        name = images[0].get("value") or ""
        stripped = _SCREEN_SUFFIX.sub("", name)
        if stripped and stripped != name:
            return stripped

    # Fallback: build from the ISO "time" field -> YYYYMMDD_HHMMSS_ffffff
    time_str = item.get("time")
    if time_str:
        try:
            dt = datetime.fromisoformat(time_str)
            return dt.strftime("%Y%m%d_%H%M%S_%f")
        except ValueError:
            pass
    return None


def ax_path_for(item: dict, ax_dir: Path) -> tuple[Path | None, str | None]:
    """Return (path, filename) of the accessibility JSON for a click item."""
    prefix = prefix_from_item(item)
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


# ── main enrichment loop ────────────────────────────────────────────────

def is_mouse_click(item: dict) -> bool:
    return item.get("source") == "mouse" and item.get("type") == "click"


def enrich(timeline_path: Path, ax_dir: Path) -> dict:
    with open(timeline_path, encoding="utf-8") as f:
        timeline = json.load(f)

    items = timeline.get("items", [])
    stats = {"clicks": 0, "matched": 0, "no_match": 0, "missing_tree": 0, "no_point": 0}

    for item in items:
        if not is_mouse_click(item):
            continue
        stats["clicks"] += 1

        pt = click_point(item)
        if pt is None:
            item["accessibility"] = {"matched": False, "reason": "no click point in events"}
            stats["no_point"] += 1
            continue

        x, y = pt
        ax_file, ax_name = ax_path_for(item, ax_dir)

        if ax_file is None or not ax_file.exists():
            item["accessibility"] = {
                "matched": False,
                "reason": f"accessibility file not found: {ax_name}",
                "click": {"x": x, "y": y},
                "ax_source": ax_name,
            }
            stats["missing_tree"] += 1
            continue

        with open(ax_file, encoding="utf-8") as f:
            ax_data = json.load(f)

        # Some captures have no tree (ax_tree_available == false / tree == null).
        if ax_data.get("tree") is None:
            item["accessibility"] = {
                "matched": False,
                "reason": ax_data.get("reason") or "no accessibility tree available",
                "click": {"x": x, "y": y},
                "ax_source": ax_name,
            }
            stats["missing_tree"] += 1
            continue

        result = match_click(ax_data, x, y)
        # Record which tree this came from, for traceability.
        result = {"ax_source": ax_name, **result}
        item["accessibility"] = result

        if result.get("matched"):
            stats["matched"] += 1
        else:
            stats["no_match"] += 1

    # Back up, then overwrite in place.
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup = timeline_path.with_suffix(timeline_path.suffix + f".{ts}.bak")
    shutil.copy2(timeline_path, backup)

    with open(timeline_path, "w", encoding="utf-8") as f:
        json.dump(timeline, f, indent=2, ensure_ascii=False)

    stats["backup"] = str(backup)
    return stats


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        print("error: exactly one argument required (episode dir or timeline.json)", file=sys.stderr)
        return 1

    timeline_path, ax_dir = resolve_paths(sys.argv[1])
    stats = enrich(timeline_path, ax_dir)

    print(f"Timeline : {timeline_path}")
    print(f"AX trees : {ax_dir}")
    print(f"Backup   : {stats['backup']}")
    print("-" * 50)
    print(f"Mouse clicks found : {stats['clicks']}")
    print(f"  matched          : {stats['matched']}")
    print(f"  no element hit    : {stats['no_match']}")
    print(f"  missing AX tree   : {stats['missing_tree']}")
    print(f"  no click point    : {stats['no_point']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
