"""Find the UI element at a mouse coordinate in an accessibility tree JSON.

Usage:
    python find_ui_element.py <accessibility.json> <x> <y>

Prints the deepest (smallest) visible element whose bounding_rectangle
contains the point (x, y), along with its ancestor chain.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


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


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    tree_path = Path(argv[0])
    try:
        x, y = int(argv[1]), int(argv[2])
    except ValueError:
        print("error: x and y must be integers", file=sys.stderr)
        return 2

    try:
        with tree_path.open(encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read {tree_path}: {exc}", file=sys.stderr)
        return 1

    # Accept either a full snapshot ({"tree": {...}}) or a bare tree node.
    tree = data.get("tree") if isinstance(data, dict) and "tree" in data else data
    if not isinstance(tree, dict):
        print("error: no accessibility tree found in file", file=sys.stderr)
        return 1

    hit = find_element_at_point(tree, x, y)
    if hit is None:
        print(f"No element contains the point ({x}, {y}).")
        return 1

    node, path, _ = hit
    result = {
        "point": {"x": x, "y": y},
        "element": {
            "name": node.get("name"),
            "control_type": node.get("control_type"),
            "localized_control_type": node.get("localized_control_type"),
            "automation_id": node.get("automation_id"),
            "class_name": node.get("class_name"),
            "bounding_rectangle": node.get("bounding_rectangle"),
            "clickable_point": node.get("clickable_point"),
            "is_enabled": node.get("is_enabled"),
        },
        "ancestors": [n.get("name") for n in path[:-1]],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
