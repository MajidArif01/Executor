"""Identify elements at drag start/end in recorded accessibility snapshots.

Coordinates must use the same global desktop coordinate system as the JSON
rectangles. The drop element is the item under the release point in the drop
snapshot, which may be the moved item itself rather than the destination container.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any


class DragElementFinder:
    """Hit-test pick and drop points against recorded accessibility snapshots."""

    @staticmethod
    def load_snapshot(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8-sig") as file:
            snapshot = json.load(file)
        if not isinstance(snapshot, dict):
            raise ValueError(f"{path}: expected a JSON object")
        if snapshot.get("ax_tree_available") is False:
            raise ValueError(f"{path}: accessibility tree unavailable: {snapshot.get('reason')}")
        if not isinstance(snapshot.get("tree"), dict):
            raise ValueError(f"{path}: missing or invalid 'tree'")
        if snapshot.get("truncated"):
            print(f"Warning: {path.name} contains a truncated tree.", file=sys.stderr)
        return snapshot

    @staticmethod
    def contains(node: dict[str, Any], x: int, y: int) -> bool:
        rectangle = node.get("bounding_rectangle")
        if node.get("is_offscreen") is True or not isinstance(rectangle, dict):
            return False
        values = [rectangle.get(key) for key in ("left", "top", "right", "bottom")]
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                   for v in values):
            return False
        left, top, right, bottom = values
        return left < right and top < bottom and left <= x < right and top <= y < bottom

    @staticmethod
    def element_data(chain: list[dict[str, Any]]) -> dict[str, str]:
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
        hits: dict[tuple[int, ...], list[dict[str, Any]]] = {}
        stack = [(snapshot["tree"], (), [])]
        while stack:
            node, path, ancestors = stack.pop()
            chain = ancestors + [node]
            if self.contains(node, x, y):
                hits[path] = chain
            # Check all children even when a parent rectangle does not contain the
            # point: some providers expose incomplete parent rectangles.
            children = node.get("children") or []
            if isinstance(children, list):
                for index, child in enumerate(children):
                    if isinstance(child, dict):
                        stack.append((child, path + (index,), chain))

        if not hits:
            return {"status": "not_found", "element": None}

        ancestors_with_hits = {path[:depth] for path in hits for depth in range(len(path))}
        candidates: dict[tuple[int, ...], list[dict[str, Any]]] = {}
        for path, chain in hits.items():
            if path in ancestors_with_hits:
                continue
            if chain[-1].get("control_type") in {"TextControl", "ImageControl"}:
                for index in range(len(chain) - 2, -1, -1):
                    if chain[index].get("control_type") == "ListItemControl" and self.contains(chain[index], x, y):
                        chain, path = chain[: index + 1], path[:index]
                        break
            candidates[path] = chain

        if len(candidates) > 1:
            return {
                "status": "ambiguous",
                "element": None,
                "candidates": [self.element_data(chain) for chain in candidates.values()],
            }
        return {"status": "matched", "element": self.element_data(next(iter(candidates.values())))}

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
