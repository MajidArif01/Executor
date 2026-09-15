
import argparse
import json
import re
import sys

# Windows consoles default to cp1252 and crash on CJK/emoji in OCR content.
# Force UTF-8 output so non-Latin text prints safely.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# --- optional faster/stronger fuzzy backend ---------------------------------
try:
    from rapidfuzz import fuzz as _rf

    def _ratio(a, b):
        return _rf.ratio(a, b) / 100.0

    def _token_set(a, b):
        return _rf.token_set_ratio(a, b) / 100.0

    BACKEND = "rapidfuzz"
except ImportError:  # stdlib fallback, always available
    from difflib import SequenceMatcher

    def _ratio(a, b):
        return SequenceMatcher(None, a, b).ratio()

    def _token_set(a, b):
        # order-independent approximation of token_set_ratio using difflib
        ta, tb = set(a.split()), set(b.split())
        if not ta or not tb:
            return 0.0
        inter = " ".join(sorted(ta & tb))
        left = " ".join(sorted(ta))
        right = " ".join(sorted(tb))
        if inter:
            return max(_ratio(inter, left), _ratio(inter, right), _ratio(left, right))
        return _ratio(left, right)

    BACKEND = "difflib"


def normalize(s: str) -> str:
    """Lowercase, drop punctuation (incl. the '.'), collapse whitespace."""
    s = (s or "").lower().strip()
    s = re.sub(r"[.\'`,\"!?:;]", "", s)   # punctuation -> removed
    s = re.sub(r"\s+", " ", s).strip()
    return s


def score_element(target_norm: str, content_norm: str) -> float:
    """Best of several fuzzy signals, in [0, 1]."""
    if not content_norm:
        return 0.0
    scores = [
        _ratio(target_norm, content_norm),
        _token_set(target_norm, content_norm),
    ]
    # reward the target being contained in a longer OCR box (partial match)
    squished_t = target_norm.replace(" ", "")
    squished_c = content_norm.replace(" ", "")
    if squished_t and squished_t in squished_c:
        scores.append(0.95)
    return max(scores)


def top_row(bbox):
    """Vertical position of the element's top edge (0 = top of screen)."""
    return bbox[1] if bbox and len(bbox) >= 2 else 1.0


def rank(data: dict):
    """Return elements ranked by match quality against data['value']."""
    value = data.get("value", "")
    target = normalize(value)
    results = []
    for key, el in (data.get("elements") or {}).items():
        content = el.get("content", "")
        s = score_element(target, normalize(content))
        results.append(
            {
                "element": key,
                "content": content,
                "score": round(s, 4),
                "interactivity": el.get("interactivity", False),
                "type": el.get("type"),
                "source": el.get("source"),
                "bbox": el.get("bbox"),
            }
        )

    # Sort by: score desc, then interactive first, then nearer the top of the
    # screen (a typed value usually lives in a search/address bar up top, not in
    # the autocomplete suggestions below it).
    results.sort(
        key=lambda r: (
            -r["score"],
            not r["interactivity"],
            top_row(r["bbox"]),
        )
    )
    return target, results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json_path", help="Path to the OmniParser annotation JSON")
    ap.add_argument("--top", type=int, default=5, help="How many candidates to show (default 5)")
    ap.add_argument("--json", action="store_true", dest="as_json", help="Print best match as JSON only")
    args = ap.parse_args(argv)

    try:
        with open(args.json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: could not read JSON: {e}", file=sys.stderr)
        return 2

    target, results = rank(data)

    if not results:
        print("ERROR: no 'elements' found in JSON", file=sys.stderr)
        return 1

    best = results[0]

    if args.as_json:
        print(json.dumps(best, indent=2))
        return 0

    value = data.get("value", "")
    print(f"Backend        : {BACKEND}")
    print(f"Node           : {data.get('node_type')} / {data.get('kind')}")
    print(f"Typed value    : {value!r}   (normalized: {target!r})")
    print("-" * 68)
    best_conf = best["score"]
    verdict = "MATCH" if best_conf >= 0.8 else ("WEAK" if best_conf >= 0.6 else "NO MATCH")
    print(f"Best match     : {best['element']}  ->  {best['content']!r}")
    print(f"Confidence     : {best_conf:.2%}  [{verdict}]")
    print(f"bbox           : {best['bbox']}")
    print(f"interactive    : {best['interactivity']}   source: {best['source']}")
    print("-" * 68)
    print(f"Top {args.top} candidates:")
    for r in results[: args.top]:
        print(f"  {r['score']:.2%}  {r['element']:<8} {r['content']!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
