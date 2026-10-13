"""Wheel delta extraction and accessibility scroll targeting."""
from .ClickElement import _build_result, _hit_test, _resolve_point, load_tree
import math


def scroll_delta(item):
    merged = item.get("scroll_merge") or {}
    def number(value):
        value = float(value or 0)
        if not math.isfinite(value):
            raise ValueError("Non-finite wheel delta")
        return value
    if "total_dx" in merged or "total_dy" in merged:
        return number(merged.get("total_dx")), number(merged.get("total_dy"))
    totals = {"dx": 0.0, "dy": 0.0}
    found = False
    for event in item.get("events") or []:
        for part in str(event.get("detail") or "").split(","):
            key, _, value = part.strip().partition("=")
            if key.strip() in totals:
                totals[key.strip()] += number(value)
                found = True
    if found:
        return totals["dx"], totals["dy"]
    omni = item.get("omniparser") or {}
    return number(omni.get("scroll_dx")), number(omni.get("scroll_dy"))


def find_scroll_target(capture, x, y, click_space=None, dx=0, dy=-1):
    nx, ny = _resolve_point(capture, x, y, click_space)
    hit = _hit_test(load_tree(capture), nx, ny)
    if not hit:
        return None
    hovered = _build_result(hit)
    chain = hit["parent_chain"]
    container = None
    for i in range(len(chain) - 1, -1, -1):
        metadata = dict(chain[i])
        for key in ("scroll", "scroll_pattern", "ScrollPattern"):
            if isinstance(metadata.get(key), dict):
                metadata.update(metadata[key])
        keys = (["VerticallyScrollable", "vertically_scrollable"] if dy else [])
        keys += (["HorizontallyScrollable", "horizontally_scrollable"] if dx else [])
        patterns = metadata.get("supported_patterns") or metadata.get("patterns") or []
        if metadata.get("scrollable") is True or any(metadata.get(k) is True for k in keys) or "ScrollPattern" in patterns:
            container = _build_result({**hit, "element": chain[i], "parent_chain": chain[:i+1]})
            break
    result = container or hovered
    return {**result, "hovered_element": hovered["target"],
            "scroll_container": container["target"] if container else None,
            "status": "scroll_container" if container else "hovered_only",
            "point": {"raw": [x, y], "normalized": [nx, ny]}}
