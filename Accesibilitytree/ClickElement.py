"""
find_element.py
---------------
The click -> element algorithm: given a captured accessibility tree (already
parsed) and a click point, work out which element the click landed on.

Pure logic, no file I/O. Reading captures and storing results on the timeline
is the job of `accessibility_tree.py`.
"""

import json
from typing import Optional


# ============================================================
# UNWRAP TREE
# ============================================================

def load_tree(data: dict) -> dict:
    """
    Return the root tree node of a parsed capture.
    Handles wrapper formats: {"tree": {...}}, {"root": {...}}, etc.
    """
    if not isinstance(data, dict):
        raise TypeError(f"capture must be a dict, got {type(data).__name__}")

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
# NORMALIZE — Map a recorded click into the tree's pixel space
# ============================================================

def parse_resolution(raw: object) -> Optional[tuple[int, int]]:
    """'2560x1600' -> (2560, 1600); None when unparseable."""
    width, _, height = str(raw or "").lower().partition("x")
    try:
        w, h = int(width), int(height)
    except ValueError:
        return None
    return (w, h) if w > 0 and h > 0 else None


def tree_space(ax_data: dict) -> Optional[tuple[tuple[int, int], tuple[int, int]]]:
    """
    ((width, height), (origin_x, origin_y)) of the screen the tree was captured
    on, in the same physical pixels as its bounding rectangles. None when the
    capture does not record it.
    """
    bounds = ax_data.get("screen_bounds")
    if isinstance(bounds, dict) and all(k in bounds for k in ("left", "top", "right", "bottom")):
        return ((bounds["right"] - bounds["left"], bounds["bottom"] - bounds["top"]),
                (bounds["left"], bounds["top"]))
    size = parse_resolution(ax_data.get("screen_resolution"))
    if size:
        return size, (0, 0)
    return None


def normalize_click(
    x: float,
    y: float,
    click_space: Optional[tuple[int, int]],
    tree_size: Optional[tuple[int, int]],
    tree_origin: tuple[int, int] = (0, 0),
) -> tuple[int, int]:
    """
    Map (x, y) recorded in click_space (w, h) onto the tree's pixel space.
    A no-op when either space is unknown or both are the same size, so clicks
    that were already recorded in physical pixels are left untouched.
    """
    if not click_space or not tree_size or tuple(click_space) == tuple(tree_size):
        return round(tree_origin[0] + x), round(tree_origin[1] + y)
    nx, ny = x / click_space[0], y / click_space[1]
    return (round(tree_origin[0] + nx * tree_size[0]),
            round(tree_origin[1] + ny * tree_size[1]))


def _resolve_point(data, x, y, click_space):
    """Shared by find_element / find_elements so both normalize the same way."""
    space = tree_space(data)
    if space:
        return normalize_click(x, y, click_space, space[0], space[1])
    return round(x), round(y)


# ============================================================
# HIT-TEST — Map (x, y) to the element the point falls in
# ============================================================

# Surfaces drawn above their owner window: a point inside one of these never
# reaches whatever sits behind it. Exact matches only — substring checks like
# "flyout" in cls also caught ordinary controls (e.g. ToggleMenuFlyoutItem,
# ribbon flyout buttons) and made them look like overlay roots.
_OVERLAY_TYPES = {"menucontrol", "tooltipcontrol"}
_OVERLAY_CLASSES = {
    "microsoft.ui.content.popupwindowsitebridge",   # WinUI 3 popups (Win11 Explorer)
    "popup", "popuproot", "xaml_windowedpopupclass",
    "menuflyout", "menuflyoutpresenter", "flyoutpresenter",
    "#32768",                                       # classic Win32 menu
    "combolbox", "tooltips_class32",
}
_OVERLAY_NAMES = {"popuphost"}

# Controls a user actually clicks; a TextBlock / Image inside one is just its label.
_ACTIONABLE_TYPES = {
    "menuitemcontrol", "buttoncontrol", "listitemcontrol", "treeitemcontrol",
    "tabitemcontrol", "hyperlinkcontrol", "checkboxcontrol", "radiobuttoncontrol",
    "splitbuttoncontrol", "comboboxcontrol", "editcontrol", "dataitemcontrol",
}
_LABEL_TYPES = {"textcontrol", "imagecontrol"}

# Pixels of slack when checking that a node sits inside its overlay's bounds
# (menu shadows / borders can stick out a few pixels).
_OVERLAY_TOLERANCE = 16


def _is_overlay(node: dict) -> bool:
    ct = _p(node, "type").lower()
    cls = _p(node, "class").lower()
    name = _p(node, "name").lower()
    return ct in _OVERLAY_TYPES or cls in _OVERLAY_CLASSES or name in _OVERLAY_NAMES


def _valid_rect(rect) -> bool:
    return bool(rect) and rect[2] > rect[0] and rect[3] > rect[1]


def _rect_contains_point(rect, x, y) -> bool:
    l, t, r, b = rect
    return l <= x < r and t <= y < b


def _rect_inside(inner, outer, tol=0) -> bool:
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def _hit_test(tree: dict, x: int, y: int) -> Optional[dict]:
    """
    Find the element the point actually falls in.

    1. Collect every node whose (non-empty, on-screen) rect contains (x, y).
    2. Nodes under a popup/menu overlay count as "overlay" hits ONLY if the
       point is inside the overlay's own bounds AND the node's rect lies within
       those bounds. This drops wrapper nodes that report a rect in the wrong
       coordinate space (e.g. InputSiteWindowClass = 0,-8,1706,126), which
       otherwise hijack clicks on the toolbar / address bar.
    3. If any valid overlay hit exists, only overlay hits count — the overlay
       is drawn on top of everything behind it.
    4. Pick the tightest rect (smallest area); ties -> deeper -> earlier in tree.
    5. Promote a bare label (TextBlock/Image) to its clickable ancestor.

    Returns {"element", "parent_chain", "layer"} or None.
    """
    candidates: list[dict] = []
    _walk(tree, x, y, 0, [], None, candidates, [0])
    if not candidates:
        return None

    overlay = [c for c in candidates if c["overlay"]]
    base = [c for c in candidates if not c["under_overlay"]]
    pool = overlay or base or candidates
    best = min(pool, key=lambda c: (c["area"], -c["depth"], c["order"]))

    chain = best["chain"]
    if _p(chain[-1], "type").lower() in _LABEL_TYPES:
        for i in range(len(chain) - 2, -1, -1):
            if _p(chain[i], "type").lower() in _ACTIONABLE_TYPES:
                chain = chain[: i + 1]
                break

    return {
        "element": chain[-1],
        "parent_chain": chain,
        "layer": "overlay" if overlay else "base",
    }


def _walk(node, x, y, depth, chain, overlay_bounds, out, counter):
    """
    overlay_bounds: None when not inside an overlay; otherwise the rect of the
    outermost overlay ancestor that has a real rect (e.g. PopupHost), or
    "unknown" if we're inside an overlay whose root had no usable rect yet.
    """
    if not isinstance(node, dict):
        return
    order = counter[0]
    counter[0] += 1
    current_chain = chain + [node]
    rect = _get_rect(node)

    if _is_overlay(node):
        if overlay_bounds in (None, "unknown"):
            overlay_bounds = rect if _valid_rect(rect) else "unknown"
    under_overlay = overlay_bounds is not None

    if _valid_rect(rect) and _rect_contains_point(rect, x, y) and not node.get("is_offscreen"):
        if not under_overlay:
            is_overlay_hit = False
        elif overlay_bounds == "unknown":
            is_overlay_hit = True
        else:
            is_overlay_hit = (_rect_contains_point(overlay_bounds, x, y)
                              and _rect_inside(rect, overlay_bounds, _OVERLAY_TOLERANCE))
        # A node under an overlay that fails the bounds check has a bogus rect:
        # it is neither a valid overlay hit nor part of the page behind it.
        if is_overlay_hit or not under_overlay:
            out.append({
                "chain": current_chain,
                "depth": depth,
                "order": order,
                "area": (rect[2] - rect[0]) * (rect[3] - rect[1]),
                "overlay": is_overlay_hit,
                "under_overlay": under_overlay,
            })

    # Zero-size / offscreen wrappers (e.g. the XAML "Popup" host reports
    # 0,0,0,0) can't be hit themselves, but their children can: keep descending.
    for child in _get_children(node):
        _walk(child, x, y, depth + 1, current_chain, overlay_bounds, out, counter)


# ============================================================
# BUILD RESULT
# ============================================================

def _build_result(hit: dict) -> dict:
    """
    Turn a hit-test result into the structured output format.

    Output schema (key order matters):
    {
        "window_context": { ... },       <- always first
        "target": { ... },               <- the clicked element
        "ancestor_chain": [              <- full chain including target
            {"level": 0, ...},           <- level 0 = root
            ...                          <- increasing toward the target
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
        if any(kw in ct for kw in ("window", "pane", "dialog")) or ct == "menucontrol":
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
    rect = _get_rect(target)
    if rect:
        l, t, r, b = rect
        target_info["bounding_rectangle"] = {"left": l, "top": t, "right": r, "bottom": b}

    # ── Ancestor chain (root = level 0, ... target = N) ──
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
        "hit_layer": hit["layer"],
    }


# ============================================================
# PUBLIC API
# ============================================================

def _locate(capture: dict, root: dict, x, y, click_space) -> Optional[dict]:
    """One click: normalize the point, hit-test it, shape the result."""
    nx, ny = _resolve_point(capture, x, y, click_space)
    hit = _hit_test(root, nx, ny)
    if not hit:
        return None
    result = _build_result(hit)
    result["click"] = {"raw": [x, y], "normalized": [nx, ny]}
    return result


def find_element(
    capture: dict,
    x: int,
    y: int,
    click_space: Optional[tuple[int, int]] = None,
) -> Optional[dict]:
    """
    Find the element at (x, y) in a captured accessibility tree.

    Args:
        capture:      Parsed capture JSON (full capture or bare tree).
        x:            Mouse click x coordinate.
        y:            Mouse click y coordinate.
        click_space:  (width, height) the click was recorded in (the event's
                      screen_resolution, or the screenshot size). When given and
                      the capture records its own screen size, (x, y) is
                      rescaled into the tree's pixel space first.

    Returns structured descriptor dict (with "click": {"raw", "normalized"}),
    or None if no element at (x, y).
    """
    return _locate(capture, load_tree(capture), x, y, click_space)


def find_elements(
    capture: dict,
    clicks: list[dict],
    click_space: Optional[tuple[int, int]] = None,
) -> list[Optional[dict]]:
    """
    Find elements for multiple (x, y) coordinates against the same capture.

    Args:
        capture:      Parsed capture JSON.
        clicks:       [{"x": 90, "y": 1013}, {"x": 200, "y": 85}, ...]
        click_space:  Same meaning as in find_element.

    Returns a list of results (same order as clicks), None for misses.
    """
    root = load_tree(capture)
    return [_locate(capture, root, c["x"], c["y"], click_space) for c in clicks]


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
        print("Usage: python find_element.py <tree.json> <x> <y>")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        capture = json.load(f)
    result = find_element(capture, x=int(sys.argv[2]), y=int(sys.argv[3]))

    if result:
        print(json.dumps(result, indent=2))
    else:
        print(f"No element found at ({sys.argv[2]}, {sys.argv[3]})")
        sys.exit(1)