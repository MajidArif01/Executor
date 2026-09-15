#!/usr/bin/env python3
"""
Map a mouse click to the OmniParser element it interacted with.

Given a raw pixel click (x, y), the screenshot the OmniParser ran on, and the
OmniParser JSON (whose bboxes are NORMALIZED 0..1), returns the matching
element id.

Matching logic:
  1. Normalize the click using the image's real pixel size.
  2. Primary: point-in-bbox. If several boxes contain the point, pick the
     smallest-area one (most specific target), preferring interactive elements.
  3. Fallback: if no box contains the point, pick the nearest bbox center,
     but only if it is within --max-dist (normalized). Otherwise: no match.

Usage (CLI):
    python find_element.py --x 1132 --y 1530 \
        --image  "....\20260903_140316_864716_screen1.png" \
        --json   "....\node_0_..._screen1.json"

Programmatic:
    from find_element import find_element
    result = find_element(1132, 1530, image_path, json_path)
    print(result["id"])
"""

import argparse
import json
import sys


def _load_size(image_path):
    """Return (width, height) of the screenshot in pixels."""
    from PIL import Image
    with Image.open(image_path) as im:
        return im.size  # (W, H)


def _iter_elements(elements):
    """Yield (id:int, name:str, element:dict) for each OmniParser element.

    Supports the two common OmniParser shapes:
      - dict:  {"icon 0": {...}, "icon 1": {...}}   (id = trailing number)
      - list:  [{...}, {...}]                        (id = list index)
    """
    if isinstance(elements, dict):
        for name, el in elements.items():
            token = name.split()[-1]
            eid = int(token) if token.isdigit() else name
            yield eid, name, el
    elif isinstance(elements, list):
        for i, el in enumerate(elements):
            yield i, f"element {i}", el
    else:
        raise ValueError("Unsupported 'elements' container: %r" % type(elements))


def find_element(x, y, image_path, json_path, max_dist=0.03):
    """Return the OmniParser element a click landed on.

    Returns a dict:
      {
        "id": <int or None>,          # the element id, None if no match
        "match_type": "inside" | "nearest" | "none",
        "content": str, "interactivity": bool, "type": str,
        "bbox_norm": [x1,y1,x2,y2], "bbox_px": [..],
        "click_px": [x,y], "click_norm": [nx,ny],
        "image_size": [W,H],
        "candidates": [ ...ranked debug list... ]
      }
    """
    W, H = _load_size(image_path)

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    elements = data.get("elements", data)  # tolerate JSON that IS the elements
    nx, ny = x / W, y / H

    inside = []   # (area, dist, eid, name, el)
    ranked = []   # (dist, eid, name, el)  -- all elements, for fallback/debug

    for eid, name, el in _iter_elements(elements):
        bbox = el.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        x1, y1, x2, y2 = bbox
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        dist = ((cx - nx) ** 2 + (cy - ny) ** 2) ** 0.5
        ranked.append((dist, eid, name, el))
        if x1 <= nx <= x2 and y1 <= ny <= y2:
            area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
            inside.append((area, dist, eid, name, el))

    ranked.sort(key=lambda t: t[0])

    def _pack(eid, name, el, match_type):
        b = el["bbox"]
        return {
            "id": eid,
            "name": name,
            "match_type": match_type,
            "content": (el.get("content") or "").strip(),
            "interactivity": el.get("interactivity"),
            "type": el.get("type"),
            "bbox_norm": [round(v, 5) for v in b],
            "bbox_px": [round(b[0] * W), round(b[1] * H),
                        round(b[2] * W), round(b[3] * H)],
            "click_px": [x, y],
            "click_norm": [round(nx, 5), round(ny, 5)],
            "image_size": [W, H],
            "candidates": [
                {"id": e2, "dist": round(d, 4),
                 "content": (el2.get("content") or "").strip(),
                 "interactive": el2.get("interactivity")}
                for d, e2, n2, el2 in ranked[:5]
            ],
        }

    if inside:
        # Prefer interactive; then smallest area (most specific); then nearest center.
        inside.sort(key=lambda t: (0 if t[4].get("interactivity") else 1, t[0], t[1]))
        _, _, eid, name, el = inside[0]
        return _pack(eid, name, el, "inside")

    if ranked and ranked[0][0] <= max_dist:
        dist, eid, name, el = ranked[0]
        return _pack(eid, name, el, "nearest")

    return {
        "id": None,
        "match_type": "none",
        "click_px": [x, y],
        "click_norm": [round(nx, 5), round(ny, 5)],
        "image_size": [W, H],
        "candidates": [
            {"id": e2, "dist": round(d, 4),
             "content": (el2.get("content") or "").strip()}
            for d, e2, n2, el2 in ranked[:5]
        ],
    }


def main():
    ap = argparse.ArgumentParser(description="Find the OmniParser element id for a mouse click.")
    ap.add_argument("--x", type=int, required=True, help="click x in pixels")
    ap.add_argument("--y", type=int, required=True, help="click y in pixels")
    ap.add_argument("--image", required=True, help="path to the screenshot OmniParser ran on")
    ap.add_argument("--json", required=True, help="path to the OmniParser JSON")
    ap.add_argument("--max-dist", type=float, default=0.03,
                    help="max normalized center distance for nearest fallback (default 0.03)")
    ap.add_argument("--quiet", action="store_true", help="print only the id")
    args = ap.parse_args()

    res = find_element(args.x, args.y, args.image, args.json, args.max_dist)

    if args.quiet:
        print(res["id"])
        return

    if res["id"] is None:
        print("No element matched the click.")
        print("Nearest candidates:", json.dumps(res["candidates"], indent=2))
        sys.exit(2)

    print(f"MATCHED element id = {res['id']}  ({res['match_type']})")
    print(f"  content       : {res['content']!r}")
    print(f"  interactivity : {res['interactivity']}   type: {res['type']}")
    print(f"  bbox_norm     : {res['bbox_norm']}")
    print(f"  bbox_px       : {res['bbox_px']}")
    print(f"  click_px      : {res['click_px']}   click_norm: {res['click_norm']}")
    print(f"  image_size    : {res['image_size']}")


if __name__ == "__main__":
    main()