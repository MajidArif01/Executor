"""
accessibility_tree.py
---------------------
Walk one episode's timeline and, for every mouse click, resolve that click's
captured accessibility (AX) tree, hit-test the click point against it, and
append the found element back onto the timeline item under an "accessibility"
key.

The hit-test logic is reused from `find_element.py` (`find_element(tree, x, y)`),
which returns {window_context, target, ancestor_chain}.

Layout assumed for an episode directory:
    <episode>/processdata/timeline.json
    <episode>/rawdata/accessibility/<YYYYMMDD_HHMMSS_ffffff>_accessibility.json

Usage:
    python Accesibilitytree/accessibility_tree.py "<episode dir | timeline.json>"

Examples:
    python Accesibilitytree/accessibility_tree.py "C:/Users/majid/Desktop/Orca_Observation/episode/google-weather-search"
    python Accesibilitytree/accessibility_tree.py "C:/.../google-weather-search/processdata/timeline.json"

For each click item (source == "mouse", type == "click") an "accessibility"
block is written next to "omniparser":

    {
        "ax_source": "20260908_111307_871369_accessibility.json",
        "matched": true,
        "click": {"x": 1519, "y": 1571},
        "window_context": { ... },
        "target": { ...deepest node under the click... },
        "ancestor_chain": [ ...root -> target... ]
    }

Keyboard nodes (source == "keyboard": Typing / KeyPress / special) have no
point to hit-test, but keys always go to the element holding keyboard focus,
and every captured AX node carries `has_keyboard_focus`. So each keyboard node
gets the same block, found by `find_focused_element(tree)` instead, plus:

    "kind": "keyboard", "key": "what", "node_type": "Typing",
    "match_strategy": "keyboard_focus" | "focus_disambiguated_by_click"
                      | "click_hit_editable" | "inherited_from_previous_key"
                      | "inherited_from_click",
    "inherited_from_index": 44          # only when inherited

When several editable controls report focus at once (Chrome: omnibox + page
document + omnibox popup), the previous click breaks the tie: the field whose
rectangle contains the click point wins ("disambiguation" records the
candidates and why). If nothing editable is focused but the click landed on an
editable control, that control is used ("click_hit_editable").

When a capture shows no focused editable control (a popup, or only the window
reporting focus), the element is inherited from the previous keypress, or else
from the last click, since that is where focus still was.

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
    from .find_element import (find_editables_at, find_element, find_focused_element,
                               find_typing_candidates, parse_resolution, pick_by_click)
    from .verify_typing import accuracy_line, verify_timeline
except ImportError:  # pragma: no cover - direct-script fallback
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from find_element import (find_editables_at, find_element, find_focused_element,
                              find_typing_candidates, parse_resolution, pick_by_click)
    from verify_typing import accuracy_line, verify_timeline


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
    """Derive the capture prefix (e.g. '20260908_111307_871369') used to name
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


def is_mouse_click(item: dict) -> bool:
    return item.get("source") == "mouse" and item.get("type") == "click"


def is_keyboard(item: dict) -> bool:
    """Typing, KeyPress and special nodes — anything the keyboard produced."""
    return item.get("source") == "keyboard"


# ── keypress → focused element ──────────────────────────────────────────

def load_ax_tree(item: dict, ax_dir: Path) -> tuple[dict | None, str | None, str | None]:
    """Return (tree, ax_name, failure_reason) for a timeline item."""
    ax_file, ax_name = ax_path_for(item, ax_dir)
    if ax_file is None or not ax_file.exists():
        return None, ax_name, f"accessibility file not found: {ax_name}"
    with open(ax_file, encoding="utf-8") as f:
        ax_data = json.load(f)
    tree = ax_data.get("tree")
    if tree is None:
        return None, ax_name, ax_data.get("reason") or "no accessibility tree available"
    return tree, ax_name, None


def inherit(source: dict, strategy: str, base: dict) -> dict:
    """Copy a previous node's resolved element onto this keypress."""
    return {
        **base,
        "matched": True,
        "match_strategy": strategy,
        "inherited_from_index": source["index"],
        "window_context": source["block"]["window_context"],
        "target": source["block"]["target"],
        "ancestor_chain": source["block"]["ancestor_chain"],
    }


# Special keys that move focus on their own, so the last click no longer says
# which field has it.
FOCUS_MOVING_KEYS = {"tab", "enter", "esc", "escape"}


def moves_focus(item: dict) -> bool:
    return item.get("type") == "special" and str(item.get("value") or "").lower() in FOCUS_MOVING_KEYS


def _summary(cand: dict) -> dict:
    target = cand["target"]
    return {k: target.get(k) for k in ("name", "control_type", "automation_id", "bounding_rectangle")}


def _root_name(block_or_tree: dict) -> str:
    chain = block_or_tree.get("ancestor_chain")
    if chain:
        return chain[0].get("name") or ""
    return block_or_tree.get("name") or ""


def disambiguate_by_click(tree: dict, last_click: dict | None
                          ) -> tuple[dict | None, str | None, dict | None]:
    """Use the previous click to choose between several possible typing fields.

    Returns (candidate, match_strategy, evidence). candidate is None when the
    click cannot decide; evidence is None when there was nothing to decide
    (exactly one focused field, or none and the click hit no field).
    """
    cands = find_typing_candidates(tree)
    # One real edit box holding focus is the answer. A lone page document is
    # not: Chrome reports it while typing goes into the omnibox, so it is
    # still checked against the click.
    if len(cands) == 1 and cands[0]["target"]["control_type"] != "DocumentControl":
        return None, None, None

    evidence: dict = {"candidates": [_summary(c) for c in cands]}
    point = ((last_click or {}).get("block") or {}).get("click", {}).get("normalized")
    if not point:
        if not cands:
            return None, None, None
        return None, None, {**evidence, "reason": "no_prior_click"}

    block = last_click["block"]
    evidence.update(click_index=last_click["index"], click_point=list(point))
    if _root_name(block) != _root_name(tree):
        return None, None, ({**evidence, "reason": "different_window"} if cands else None)

    x, y = point
    choice = None
    if cands:
        choice, reason = pick_by_click(cands, x, y, block["target"], block["ancestor_chain"])
        evidence["reason"] = reason
        if choice is not None and choice["target"]["control_type"] != "DocumentControl":
            return choice, "focus_disambiguated_by_click", evidence

    # Nothing better than a whole page document reports focus: prefer a real
    # edit box under the click (a page search box the capture didn't mark
    # focused), else keep the document.
    hits = [h for h in find_editables_at(tree, x, y)
            if h["target"]["control_type"] != "DocumentControl"]
    if not hits:
        if choice is not None:
            return choice, "focus_disambiguated_by_click", evidence
        return None, None, (evidence if cands else None)
    evidence.update(candidates=[_summary(h) for h in hits], reason="click_hit_editable")
    return hits[0], "click_hit_editable", evidence


def resolve_keypress(item: dict, ax_dir: Path, last_key: dict | None,
                     last_click: dict | None) -> tuple[dict, str]:
    """Build the accessibility block for one keyboard node.

    Returns (block, outcome) where outcome is one of
    focus / click / inherited / no_match / missing_tree.
    """
    base = {"kind": "keyboard", "key": item.get("value"), "node_type": item.get("type")}
    tree, ax_name, reason = load_ax_tree(item, ax_dir)
    base["ax_source"] = ax_name

    if tree is not None:
        choice, strategy, evidence = disambiguate_by_click(tree, last_click)
        if evidence is not None:
            base["disambiguation"] = evidence
        if choice is not None:
            return {
                **base,
                "matched": True,
                "match_strategy": strategy,
                "window_context": choice["window_context"],
                "target": choice["target"],
                "ancestor_chain": choice["ancestor_chain"],
            }, "click"
        if evidence is not None and len(evidence["candidates"]) > 1:
            base["ambiguous"] = True

    focused = find_focused_element(tree) if tree is not None else None
    # Chrome's page document reports focus alongside (or instead of) the
    # omnibox; mid-field, the previous keypress's element is the better answer.
    doc_only = focused is not None and focused["target"]["control_type"] == "DocumentControl"
    if focused and focused["strong"] and not (doc_only and last_key is not None):
        return {
            **base,
            "matched": True,
            "match_strategy": "keyboard_focus",
            "focus_candidates": focused["focus_candidates"],
            "window_context": focused["window_context"],
            "target": focused["target"],
            "ancestor_chain": focused["ancestor_chain"],
        }, "focus"

    # The capture shows no usable focus (popup, container-only focus, or no
    # tree at all): the keys still went to the field focused just before.
    if last_key is not None:
        return inherit(last_key, "inherited_from_previous_key", base), "inherited"
    if last_click is not None:
        return inherit(last_click, "inherited_from_click", base), "inherited"

    if tree is None:
        return {**base, "matched": False, "reason": reason}, "missing_tree"
    return {
        **base,
        "matched": False,
        "reason": "no focused editable element and no earlier element to inherit",
    }, "no_match"


def typed_into(block: dict) -> dict:
    """Short answer to "which field did these keys go into?"."""
    target = block["target"]
    return {
        "name": target.get("name", ""),
        "control_type": target.get("control_type", ""),
        "automation_id": target.get("automation_id", ""),
        "window_name": block["window_context"].get("window_name", ""),
        "match_strategy": block["match_strategy"],
    }


# ── main enrichment loop ────────────────────────────────────────────────

def enrich(timeline_path: Path, ax_dir: Path) -> dict:
    with open(timeline_path, encoding="utf-8") as f:
        timeline = json.load(f)

    items = timeline.get("items", [])
    stats = {"clicks": 0, "matched": 0, "no_match": 0, "missing_tree": 0, "no_point": 0,
             "keys": 0, "keys_focus": 0, "keys_click": 0, "keys_inherited": 0, "keys_no_match": 0,
             "keys_missing_tree": 0}

    # Most recent resolved click / keypress, as {"index", "block"} — the
    # fallback for a keypress whose own capture shows no usable focus.
    last_click: dict | None = None
    last_key: dict | None = None

    for item in items:
        if is_keyboard(item):
            stats["keys"] += 1
            block, outcome = resolve_keypress(item, ax_dir, last_key, last_click)
            if block.get("matched"):
                block["typed_into"] = typed_into(block)
            item["accessibility"] = block
            stats[f"keys_{outcome}"] += 1
            if block.get("matched"):
                last_key = {"index": item.get("index"), "block": block}
            # Tab / Enter / Esc move focus without a click: stop trusting the
            # click to say which field later keys go into.
            if moves_focus(item):
                last_click = None
            continue

        if not is_mouse_click(item):
            continue
        # A click moves focus, so earlier keypresses — and earlier clicks — no
        # longer say where typing goes. Only a matched click becomes the new
        # fallback.
        last_key = None
        last_click = None
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
        tree = ax_data.get("tree")
        if tree is None:
            item["accessibility"] = {
                "matched": False,
                "reason": ax_data.get("reason") or "no accessibility tree available",
                "click": {"x": x, "y": y},
                "ax_source": ax_name,
            }
            stats["missing_tree"] += 1
            continue

        # Pass the whole capture so find_element can rescale the click from the
        # recorder's screen space into the tree's pixel space.
        click_space = parse_resolution((item["events"][0]).get("screen_resolution"))
        found = find_element(ax_data, x, y, click_space=click_space)
        if found is None:
            item["accessibility"] = {
                "ax_source": ax_name,
                "matched": False,
                "reason": "no element contains the click point",
                "click": {"x": x, "y": y},
            }
            stats["no_match"] += 1
            continue

        item["accessibility"] = {
            "ax_source": ax_name,
            "matched": True,
            "click": {"x": x, "y": y, "normalized": found["click"]["normalized"]},
            "hit_layer": found["hit_layer"],
            "window_context": found["window_context"],
            "target": found["target"],
            "ancestor_chain": found["ancestor_chain"],
        }
        stats["matched"] += 1
        last_click = {"index": item.get("index"), "block": item["accessibility"]}

    # Cross-check every keyboard field against where OCR saw the typed text.
    verdicts = []
    for pos, check in verify_timeline(items, timeline_path.parent.parent).items():
        items[pos]["accessibility"]["verification"] = check
        verdicts.append(check["verdict"])
    stats["verification"] = accuracy_line(verdicts)

    # Back up, then overwrite in place.
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup = timeline_path.with_suffix(timeline_path.suffix + f".{ts}.bak")
    shutil.copy2(timeline_path, backup)

    with open(timeline_path, "w", encoding="utf-8") as f:
        json.dump(timeline, f, indent=2, ensure_ascii=False)

    stats["backup"] = str(backup)
    return stats


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="accessibility_tree.py",
        description="Append each mouse click's accessibility element onto the "
        "episode timeline.",
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
    print(f"Keyboard nodes found : {stats['keys']}")
    print(f"  focused element    : {stats['keys_focus']}")
    print(f"  picked by click    : {stats['keys_click']}")
    print(f"  inherited          : {stats['keys_inherited']}")
    print(f"  no match           : {stats['keys_no_match']}")
    print(f"  missing AX tree    : {stats['keys_missing_tree']}")
    print(f"Field accuracy (AX vs OCR) : {stats['verification']}")
    print("  details: python Accesibilitytree/verify_typing.py <episode>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
