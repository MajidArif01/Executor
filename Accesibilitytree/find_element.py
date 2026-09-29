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
    space = tree_space(data) if isinstance(data, dict) else None
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

    result = {
        "window_context": window_context,
        "target": target_info,
        "ancestor_chain": ancestor_chain,
    }
    if "layer" in hit:
        result["hit_layer"] = hit["layer"]
    return result


# ============================================================
# PUBLIC API
# ============================================================

def find_element(
    tree: str | dict,
    x: int,
    y: int,
    click_space: Optional[tuple[int, int]] = None,
) -> Optional[dict]:
    """
    Find the element at (x, y) in the captured accessibility tree.

    Args:
        tree:         Path to JSON file, or already-parsed dict (full capture
                      or bare tree).
        x:            Mouse click x coordinate.
        y:            Mouse click y coordinate.
        click_space:  (width, height) the click was recorded in (the event's
                      screen_resolution, or the screenshot size). When given and
                      the capture records its own screen size, (x, y) is
                      rescaled into the tree's pixel space first.

    Returns structured descriptor dict (with "click": {"raw", "normalized"}),
    or None if no element at (x, y).
    """
    data = tree
    if isinstance(tree, str):
        with open(tree, "r", encoding="utf-8") as f:
            data = json.load(f)
    root = load_tree(data)

    nx, ny = _resolve_point(data, x, y, click_space)
    hit = _hit_test(root, nx, ny)
    if not hit:
        return None
    result = _build_result(hit)
    result["click"] = {"raw": [x, y], "normalized": [nx, ny]}
    return result


# ============================================================
# FOCUS SEARCH — Map a keypress to the focused element
# ============================================================

# Controls that actually receive typed text.
_EDITABLE_TYPES = {"editcontrol", "comboboxcontrol", "documentcontrol", "spinnercontrol"}
# A focused container only means "focus is somewhere inside me".
_CONTAINER_TYPES = {"windowcontrol", "panecontrol", "groupcontrol", "customcontrol"}


def _walk_focus(node, depth, chain, found):
    if not isinstance(node, dict):
        return
    current_chain = chain + [node]
    if node.get("has_keyboard_focus") is True:
        found.append({"element": node, "depth": depth, "chain": current_chain})
    for child in _get_children(node):
        _walk_focus(child, depth + 1, current_chain, found)


def _has_focused_child(node: dict) -> bool:
    return any(
        isinstance(c, dict) and c.get("has_keyboard_focus") is True
        for c in _get_children(node)
    )


def _focus_rank(cand: dict) -> tuple:
    """Sort key: editable first, containers last, deeper wins within a tier."""
    node = cand["element"]
    ct = _p(node, "type").lower()
    if ct in _EDITABLE_TYPES and not (ct == "documentcontrol" and _has_focused_child(node)):
        tier = 0
    elif ct in _CONTAINER_TYPES:
        tier = 2
    else:
        tier = 1
    # Within the editable tier, a real edit box beats a Chrome document that
    # also reports focus.
    sub = 0 if ct in ("editcontrol", "comboboxcontrol", "spinnercontrol") else 1
    return (tier, sub, -cand["depth"])


def find_focused_element(tree: str | dict) -> Optional[dict]:
    """
    Find the element that held keyboard focus when the tree was captured —
    the keyboard counterpart of `find_element`.

    Returns the same structure as `find_element`, plus:
        "strong":           False when only a container (window/pane) is focused
        "focus_candidates": how many nodes reported has_keyboard_focus
    or None when no node is focused at all.
    """
    root = load_tree(tree)
    found: list[dict] = []
    _walk_focus(root, 0, [], found)
    if not found:
        return None

    best = min(found, key=_focus_rank)
    result = _build_result({"element": best["element"], "parent_chain": best["chain"]})
    result["strong"] = _focus_rank(best)[0] < 2
    result["focus_candidates"] = len(found)
    return result


# ============================================================
# TYPING CANDIDATES — Use the previous click to pick the typed-into field
# ============================================================

def _is_editable(node: dict) -> bool:
    return _p(node, "type").lower() in _EDITABLE_TYPES


def _usable(node: dict) -> bool:
    return node.get("is_offscreen") is not True and node.get("is_enabled") is not False


def _candidate(node: dict, chain: list, depth: int) -> dict:
    result = _build_result({"element": node, "parent_chain": chain})
    result["rect"] = _get_rect(node)
    result["depth"] = depth
    return result


def find_typing_candidates(tree: str | dict) -> list[dict]:
    """
    Every focused node that could be receiving typed text — the editable tier
    of `_focus_rank`, best-ranked first. Containers, offscreen and disabled
    nodes are dropped. Chrome often reports focus on the omnibox, the page
    document and the omnibox popup at once; this returns all of them so the
    caller can break the tie with the previous click.
    """
    root = load_tree(tree)
    found: list[dict] = []
    _walk_focus(root, 0, [], found)
    editable = [c for c in found if _focus_rank(c)[0] == 0 and _usable(c["element"])]
    editable.sort(key=_focus_rank)
    return [_candidate(c["element"], c["chain"], c["depth"]) for c in editable]


def find_editables_at(tree: str | dict, x: int, y: int) -> list[dict]:
    """
    Every editable node (focused or not) under (x, y), tightest first. Uses the
    same overlay rules as the click hit-test, so a click on a popup does not
    match a field drawn behind it.
    """
    root = load_tree(tree)
    hits: list[dict] = []
    _walk(root, x, y, 0, [], None, hits, [0])
    overlay = [h for h in hits if h["overlay"]]
    pool = overlay or [h for h in hits if not h["under_overlay"]]
    editable = [h for h in pool if _is_editable(h["chain"][-1]) and _usable(h["chain"][-1])]
    editable.sort(key=lambda h: (h["area"], -h["depth"], h["order"]))
    return [_candidate(h["chain"][-1], h["chain"], h["depth"]) for h in editable]


def _same_identity(target: dict, other: dict) -> bool:
    """automation_id when both have one, else name + control_type."""
    if target.get("automation_id") and other.get("automation_id"):
        return (target["automation_id"] == other["automation_id"]
                and target.get("control_type") == other.get("control_type"))
    return bool(target.get("name")) and (
        target.get("name") == other.get("name")
        and target.get("control_type") == other.get("control_type"))


def pick_by_click(
    candidates: list[dict],
    x: int,
    y: int,
    click_target: Optional[dict] = None,
    click_chain: Optional[list[dict]] = None,
) -> tuple[Optional[dict], str]:
    """
    Pick the typing candidate the previous click landed in.

    1. Candidates whose rect contains (x, y); the tightest wins.
    2. Otherwise a candidate matching the clicked element (or one of its
       ancestors) by identity — the field may have moved or grown since the
       click was captured.
    Returns (candidate, reason), or (None, "click_outside_all").
    """
    inside = [c for c in candidates
              if _valid_rect(c["rect"]) and _rect_contains_point(c["rect"], x, y)]
    if len(inside) == 1:
        return inside[0], "click_inside"
    if inside:
        best = min(inside, key=lambda c: ((c["rect"][2] - c["rect"][0]) * (c["rect"][3] - c["rect"][1]),
                                          -c["depth"]))
        return best, "click_inside_tightest"

    clicked = ([click_target] if click_target else []) + list(reversed(click_chain or []))
    for node in clicked:
        for c in candidates:
            if _same_identity(c["target"], node):
                return c, "click_identity"
    return None, "click_outside_all"


def find_elements(
    tree: str | dict,
    clicks: list[dict],
    click_space: Optional[tuple[int, int]] = None,
) -> list[Optional[dict]]:
    """
    Find elements for multiple (x, y) coordinates against the same tree.

    Args:
        tree:         Path to JSON file, or already-parsed dict.
        clicks:       [{"x": 90, "y": 1013}, {"x": 200, "y": 85}, ...]
        click_space:  Same meaning as in find_element.

    Returns a list of results (same order as clicks), None for misses.
    """
    data = tree
    if isinstance(tree, str):
        with open(tree, "r", encoding="utf-8") as f:
            data = json.load(f)
    root = load_tree(data)

    results = []
    for click in clicks:
        x, y = click["x"], click["y"]
        nx, ny = _resolve_point(data, x, y, click_space)
        hit = _hit_test(root, nx, ny)
        if hit:
            result = _build_result(hit)
            result["click"] = {"raw": [x, y], "normalized": [nx, ny]}
            results.append(result)
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

    if len(sys.argv) == 3 and sys.argv[1] == "--focus":
        result = find_focused_element(sys.argv[2])
        if result:
            print(json.dumps(result, indent=2))
        else:
            print("No focused element in tree")
            sys.exit(1)
        sys.exit(0)

    if len(sys.argv) < 4:
        print("Usage: python element_finder.py <tree.json> <x> <y>")
        print("       python element_finder.py --focus <tree.json>")
        sys.exit(1)

    result = find_element(sys.argv[1], x=int(sys.argv[2]), y=int(sys.argv[3]))

    if result:
        print(json.dumps(result, indent=2))
    else:
        print(f"No element found at ({sys.argv[2]}, {sys.argv[3]})")
        sys.exit(1)