
import json
from typing import Optional


# ============================================================
# LOAD JSON TREE
# ============================================================

def load_tree(source: str | dict) -> dict:
    """
    Accept a file path or already-parsed dict.
    Handles wrapper formats: {"tree": {...}}, {"root": {...}}, etc.
    Returns the root tree node.
    """
    if isinstance(source, str):
        with open(source, "r", encoding="utf-8") as f:
            data = json.load(f)
    elif isinstance(source, dict):
        data = source
    else:
        raise TypeError(f"source must be a file path or dict, got {type(source).__name__}")

    # Unwrap if needed
    if "tree" in data and isinstance(data["tree"], dict):
        return data["tree"]
    if "bounding_rectangle" in data or "children" in data:
        return data
    for key in ("root", "element", "node", "ax_tree"):
        if key in data and isinstance(data[key], dict):
            return data[key]
    return data


# ============================================================
# HIT-TEST — Map (x, y) to the deepest element
# ============================================================

def _hit_test(tree: dict, x: int, y: int) -> Optional[dict]:
    """
    Find the DEEPEST node whose bounding rect contains (x, y).
    Returns {"element": node, "parent_chain": [root, ..., parent, node]}
    or None.
    """
    best = {"element": None, "depth": -1, "chain": []}
    _walk(tree, x, y, 0, [], best)
    if best["element"] is None:
        return None
    return {"element": best["element"], "parent_chain": best["chain"]}


def _walk(node, x, y, depth, chain, best):
    if not isinstance(node, dict):
        return
    current_chain = chain + [node]
    rect = _get_rect(node)
    if rect:
        l, t, r, b = rect
        if l <= x <= r and t <= y <= b and depth > best["depth"]:
            best["element"] = node
            best["depth"] = depth
            best["chain"] = current_chain
    for child in _get_children(node):
        _walk(child, x, y, depth + 1, current_chain, best)


# ============================================================
# BUILD RESULT
# ============================================================

def _build_result(hit: dict) -> dict:
    """
    Turn a hit-test result into the structured output format.

    Output schema (key order matters):
    {
        "window_context": { ... },       ← always first
        "target": { ... },               ← the clicked element
        "ancestor_chain": [              ← full chain including target
            {"level": 0, ...},           ← level 0 = target itself
            {"level": 1, ...},           ← level 1 = immediate parent
            ...                          ← increasing toward root
        ],
    }
    """
    target = hit["element"]
    chain = hit["parent_chain"]

    # ── Window context (always first) ──
    window_context = {
        "window_name": "",
        "window_class": "",
        "automation_id": "",
    }
    for node in reversed(chain[:-1]):
        ct = _p(node, "type").lower()
        if any(kw in ct for kw in ("window", "pane", "dialog")):
            window_context = {
                "window_name": _p(node, "name"),
                "window_class": _p(node, "class"),
                "automation_id": _p(node, "aid"),
            }
            break

    # ── Target element ──
    target_info = {
        "automation_id": _p(target, "aid"),
        "name": _p(target, "name"),
        "control_type": _p(target, "type"),
        "class_name": _p(target, "class"),
    }

    # ── Ancestor chain (root = level 0, ... parent = N-1, target = N) ──
    ancestor_chain = []
    for i, node in enumerate(chain):
        ancestor_chain.append({
            "level": i,
            "name": _p(node, "name"),
            "control_type": _p(node, "type"),
            "automation_id": _p(node, "aid"),
            "class_name": _p(node, "class"),
        })

    return {
        "window_context": window_context,
        "target": target_info,
        "ancestor_chain": ancestor_chain,
    }


# ============================================================
# PUBLIC API
# ============================================================

def find_element(
    tree: str | dict,
    x: int,
    y: int,
) -> Optional[dict]:
    """
    Find the element at (x, y) in the captured accessibility tree.

    Args:
        tree:  Path to JSON file, or already-parsed dict.
        x:     Mouse click x coordinate.
        y:     Mouse click y coordinate.

    Returns structured descriptor dict, or None if no element at (x, y).
    """
    root = load_tree(tree)
    hit = _hit_test(root, x, y)
    if not hit:
        return None
    return _build_result(hit)


def find_elements(
    tree: str | dict,
    clicks: list[dict],
) -> list[Optional[dict]]:
    """
    Find elements for multiple (x, y) coordinates against the same tree.

    Args:
        tree:    Path to JSON file, or already-parsed dict.
        clicks:  [{"x": 90, "y": 1013}, {"x": 200, "y": 85}, ...]

    Returns a list of results (same order as clicks), None for misses.
    """
    root = load_tree(tree)
    results = []
    for click in clicks:
        hit = _hit_test(root, click["x"], click["y"])
        if hit:
            results.append(_build_result(hit))
        else:
            results.append(None)
    return results


# ============================================================
# JSON HELPERS (private)
# ============================================================

def _p(node: dict, prop: str) -> str:
    """Extract a property, handling multiple naming conventions."""
    keys = {
        "name":  ("Name", "name", "title", "label", "text"),
        "aid":   ("AutomationId", "automationId", "automation_id", "id"),
        "type":  ("ControlType", "controlType", "control_type", "type", "Role", "role"),
        "class": ("ClassName", "className", "class_name", "class"),
    }
    for key in keys.get(prop, (prop,)):
        val = node.get(key)
        if val is not None and val != "":
            return str(val)
    return ""


def _get_children(node: dict) -> list:
    """Get child nodes from any common key name."""
    for key in ("Children", "children", "nodes", "items", "elements", "childNodes"):
        val = node.get(key)
        if isinstance(val, list):
            return val
    return []


def _get_rect(node: dict) -> Optional[tuple]:
    """
    Extract (left, top, right, bottom) from a node.
    Handles dict, list, and direct-key formats.
    """
    for key in ("BoundingRectangle", "bounding_rectangle", "bounding_rect", "Rect", "rect", "bounds"):
        val = node.get(key)
        if isinstance(val, dict):
            if "left" in val:
                return (val["left"], val["top"], val["right"], val["bottom"])
            if "x" in val and "width" in val:
                return (val["x"], val["y"], val["x"] + val["width"], val["y"] + val["height"])
        elif isinstance(val, (list, tuple)) and len(val) == 4:
            if val[2] > val[0] and val[3] > val[1]:
                return tuple(val)
            else:
                return (val[0], val[1], val[0] + val[2], val[1] + val[3])

    if all(k in node for k in ("x", "y", "width", "height")):
        return (node["x"], node["y"], node["x"] + node["width"], node["y"] + node["height"])

    return None


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 4:
        print("Usage: python element_finder.py <tree.json> <x> <y>")
        sys.exit(1)

    result = find_element(sys.argv[1], x=int(sys.argv[2]), y=int(sys.argv[3]))

    if result:
        print(json.dumps(result, indent=2))
    else:
        print(f"No element found at ({sys.argv[2]}, {sys.argv[3]})")
        sys.exit(1)