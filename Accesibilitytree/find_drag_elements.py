"""Identify elements at drag start/end in recorded accessibility snapshots.

Edit default pick/drop paths on ``DragElementFinder``, then run:
    uv run python find_drag_elements.py

Coordinates must use the same global desktop coordinate system as the JSON
rectangles. This script reads recordings; it does not move the mouse.
The drop element is the item under the release point in the drop snapshot,
which may be the moved item itself rather than the destination container.
No third-party packages required.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


DEFAULT_PICK_JSON_PATH = Path(
    r"C:\Users\majid\AppData\Roaming\ORCA\Episode\breezy-contour\accessibility\20261001_160444_749938_accessibility_pick.json"
)
DEFAULT_DROP_JSON_PATH = Path(
    r"C:\Users\majid\AppData\Roaming\ORCA\Episode\breezy-contour\accessibility\20261001_160444_749938_accessibility_drop.json"
)

# Backward-compatible aliases for scripts that import these names.
PICK_JSON_PATH = DEFAULT_PICK_JSON_PATH
DROP_JSON_PATH = DEFAULT_DROP_JSON_PATH


class DragElementFinder:
    """Hit-test pick and drop points against recorded accessibility snapshots."""

    def __init__(
        self,
        pick_json: Path | None = None,
        drop_json: Path | None = None,
    ) -> None:
        self.pick_json = pick_json or DEFAULT_PICK_JSON_PATH
        self.drop_json = drop_json or DEFAULT_DROP_JSON_PATH

    @staticmethod
    def load_snapshot(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8-sig") as file:
            snapshot = json.load(file)
        if not isinstance(snapshot, dict):
            raise ValueError(f"{path}: expected a JSON object")
        if snapshot.get("ax_tree_available") is False:
            raise ValueError(
                f"{path}: accessibility tree unavailable: {snapshot.get('reason')}"
            )
        if not isinstance(snapshot.get("tree"), dict):
            raise ValueError(f"{path}: missing or invalid 'tree'")
        if snapshot.get("truncated"):
            print(f"Warning: {path.name} contains a truncated tree.", file=sys.stderr)
        return snapshot

    @staticmethod
    def _contains(node: dict[str, Any], x: int, y: int) -> bool:
        rectangle = node.get("bounding_rectangle")
        if node.get("is_offscreen") is True or not isinstance(rectangle, dict):
            return False
        values = [rectangle.get(key) for key in ("left", "top", "right", "bottom")]
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in values
        ):
            return False
        left, top, right, bottom = values
        return left < right and top < bottom and left <= x < right and top <= y < bottom

    @staticmethod
    def _element_data(chain: list[dict[str, Any]]) -> dict[str, str]:
        node = chain[-1]
        # Window-at-point snapshots can have a PaneControl root (e.g. Progman).
        windows = [n for n in chain if n.get("control_type") == "WindowControl"]
        window = windows[-1] if windows else chain[0]
        return {
            "name": node.get("name") or "",
            "control_type": node.get("control_type") or "",
            "automation_id": node.get("automation_id") or "",
            "window": window.get("name") or "",
        }

    def find_element(self, snapshot: dict[str, Any], x: int, y: int) -> dict[str, Any]:
        """Return one structural hit, or explicitly report ambiguous/no matches.

        Ancestor hits are removed in favor of descendant hits. Text/image children
        of list items map to the owning list item. Overlapping hits on separate
        branches remain ambiguous: a saved rectangle tree does not provide z-order.
        """
        root = snapshot.get("tree")
        if not isinstance(root, dict):
            raise ValueError("Snapshot has no accessibility tree")

        hits: dict[tuple[int, ...], list[dict[str, Any]]] = {}
        stack: list[tuple[dict[str, Any], tuple[int, ...], list[dict[str, Any]]]] = [
            (root, (), [])
        ]
        while stack:
            node, path, ancestors = stack.pop()
            chain = ancestors + [node]
            if self._contains(node, x, y):
                hits[path] = chain
            # Check all children even when a parent rectangle does not contain the
            # point: some providers expose incomplete parent rectangles.
            children = node.get("children") or []
            if not isinstance(children, list):
                continue
            for index, child in enumerate(children):
                if isinstance(child, dict):
                    stack.append((child, path + (index,), chain))

        if not hits:
            return {"status": "not_found", "element": None}

        ancestors_with_hits: set[tuple[int, ...]] = set()
        for path in hits:
            for depth in range(len(path)):
                ancestors_with_hits.add(path[:depth])

        candidates: dict[tuple[int, ...], list[dict[str, Any]]] = {}
        for path, chain in hits.items():
            if path in ancestors_with_hits:
                continue
            if chain[-1].get("control_type") in {"TextControl", "ImageControl"}:
                for index in range(len(chain) - 2, -1, -1):
                    if (
                        chain[index].get("control_type") == "ListItemControl"
                        and self._contains(chain[index], x, y)
                    ):
                        chain = chain[: index + 1]
                        path = path[:index]
                        break
            candidates[path] = chain

        if len(candidates) > 1:
            return {
                "status": "ambiguous",
                "element": None,
                "candidates": [self._element_data(chain) for chain in candidates.values()],
            }

        chain = next(iter(candidates.values()))
        return {"status": "matched", "element": self._element_data(chain)}

    def find_drag_elements(
        self,
        pick_snapshot: dict[str, Any],
        drop_snapshot: dict[str, Any],
        pick_point: tuple[int, int],
        drop_point: tuple[int, int],
    ) -> dict[str, Any]:
        return {
            "pick": self.find_element(pick_snapshot, *pick_point),
            "drop": self.find_element(drop_snapshot, *drop_point),
        }

    def resolve_from_paths(
        self,
        pick_point: tuple[int, int],
        drop_point: tuple[int, int],
        *,
        pick_json: Path | None = None,
        drop_json: Path | None = None,
    ) -> dict[str, Any]:
        """Load snapshots from disk and resolve pick/drop elements at the given points."""
        pick_snapshot = self.load_snapshot(pick_json or self.pick_json)
        drop_snapshot = self.load_snapshot(drop_json or self.drop_json)
        return self.find_drag_elements(
            pick_snapshot, drop_snapshot, pick_point, drop_point
        )

    @staticmethod
    def ask_coordinates(label: str) -> tuple[int, int]:
        while True:
            entered = input(f"Enter {label} coordinates as x y (or x,y): ")
            parts = entered.replace(",", " ").split()
            try:
                if len(parts) != 2:
                    raise ValueError
                return int(parts[0]), int(parts[1])
            except ValueError:
                print("Enter two integers, for example: 966 661", file=sys.stderr)


def load_snapshot(path: Path) -> dict[str, Any]:
    return DragElementFinder.load_snapshot(path)


def find_element(snapshot: dict[str, Any], x: int, y: int) -> dict[str, Any]:
    return DragElementFinder().find_element(snapshot, x, y)


def find_drag_elements(
    pick_snapshot: dict[str, Any],
    drop_snapshot: dict[str, Any],
    pick_point: tuple[int, int],
    drop_point: tuple[int, int],
) -> dict[str, Any]:
    return DragElementFinder().find_drag_elements(
        pick_snapshot, drop_snapshot, pick_point, drop_point
    )


def ask_coordinates(label: str) -> tuple[int, int]:
    return DragElementFinder.ask_coordinates(label)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pick-json", type=Path, default=DEFAULT_PICK_JSON_PATH)
    parser.add_argument("--drop-json", type=Path, default=DEFAULT_DROP_JSON_PATH)
    parser.add_argument("--pick", type=int, nargs=2, metavar=("X", "Y"))
    parser.add_argument("--drop", type=int, nargs=2, metavar=("X", "Y"))
    args = parser.parse_args()
    finder = DragElementFinder(pick_json=args.pick_json, drop_json=args.drop_json)
    try:
        pick_point = (
            tuple(args.pick) if args.pick is not None else finder.ask_coordinates("pick")
        )
        drop_point = (
            tuple(args.drop) if args.drop is not None else finder.ask_coordinates("drop")
        )
        result = finder.resolve_from_paths(pick_point, drop_point)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if all(item["status"] == "matched" for item in result.values()) else 1
    except (OSError, ValueError, EOFError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
