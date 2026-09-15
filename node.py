"""For every Typing node in an episode's timeline.json, find the OmniParser
element whose OCR text is the string that was typed, and append it to the node,
in place.

This is the keyboard counterpart of ``mousemapper.py``. A pointer node knows
*where* it happened (``events[0].x/y``) so its element can be found
geometrically; a typing node only knows *what* was typed (``value``), so its
element has to be found by matching text.

``Omniparser_Runner`` already parses each typing node's last screenshot (the
frame after the whole string is in) and writes one JSON per node to
``AnotatedData/AnotatedJson/keyboard/node_{index}_{stem}.json`` -- an
``elements`` dict of ``icon N -> {type, bbox, interactivity, content, source}``.

This script closes the gap by mutating ``timeline.json`` directly: each Typing
item gets one ``omniparser`` key, the same key and field set ``mousemapper.py``
writes, so a single consumer can read every enriched node::

    "omniparser": {
      "kind": "keyboard",
      "node_type": "Typing",
      "annotated_json_path": ...,      # the OmniParser record this used
      "annotated_image": ...,          # the annotated PNG for that screenshot
      "image_path": ..., "image_name": ...,
      "screen": 1, "screen_matched": true,
      "element_count": 76,
      "match_strategy": "typed_value_fuzzy_ocr",
      "match_status": "match",
      "matched_element": {
        "element": "icon 38",
        "content": "daraz.pk ",
        "score": 1.0,
        "interactivity": true,
        "type": "icon",
        "source": "box_yolo_content_ocr",
        "bbox": [0.13236694, 0.06688233, 0.17514649, 0.08960938]
      },
      "candidates": [],
      "match_query": {
        "value": "daraz.pk",            # this node's own fragment
        "field_value": "daraz.pk",      # the whole field, what was matched
        "normalized": "darazpk",
        "anchor": [0.307, 0.09]         # the click that focused the field
      },
      "match_backend": "rapidfuzz",
      "match_count": 1
    }

Why the match is fuzzy rather than a string lookup: OmniParser's OCR turns
punctuation into noise -- "daraz.pk" is read as "daraz pk" or "Daraz pk", and
the text caret in a focused input comes back as "|" -- while casing and trailing
whitespace vary. Both sides are normalized, scored with several fuzzy signals,
then weighted by how much of the OCR box the typed text accounts for, so a short
string cannot claim a long unrelated box it happens to appear inside.

Three things make the difference between a real match and a coincidence:

* Only OCR-backed elements are considered. A ``box_yolo_content_yolo`` element's
  ``content`` is a Florence caption of an icon ("Copy", "Play"), never something
  a user typed into, and leaving those in gives short strings junk to collide
  with.
* The target is the whole field, not the node. The recorder splits a typed
  string at every space and backspace, so a node's ``value`` may be "uv" while
  the screen shows "america uv"; the keystrokes are replayed to rebuild the
  field before matching.
* Ties break toward the last click, the field the user actually focused, rather
  than toward the top of the screen -- which in a browser is the tab strip.

The best candidate is reported as ``match`` at or above ``MATCH_THRESHOLD`` and
``weak`` at or above ``WEAK_THRESHOLD``; below that the node is ``no_match`` and
``matched_element`` is null. A ``weak`` or ``no_match`` node also carries the
next few ranked ``candidates`` so a bad OCR read can be eyeballed without
re-running the parser.

Because this edits the source file, a one-time backup (``timeline.json.bak``) is
made before the first write, and a node that already carries a block is left
alone on a re-run. Running it before ``Omniparser_Runner`` has parsed the
episode is harmless: a node with no record yet (``no_json`` / ``no_elements``)
is reported but *not* written, so it stays eligible once the parser has run.

Usage::

    python node.py --episode testingcases              # one episode by name
    python node.py                                     # every episode found
    python node.py --episode testingcases --dry-run    # match + print, no write
    python node.py --episode-dir "D:/.../episode/testingcases"
    python node.py --episode testingcases --min-score 0.7 --top 5
    python node.py --inspect path/to/node_4_....json   # score one record only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

# Windows consoles default to cp1252 and crash on CJK/emoji in OCR content.
# Force UTF-8 output so non-Latin text prints safely.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Make the repository root importable so `Omniparser_Runner` resolves no matter
# what directory this script is run from.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Omniparser_Runner.config import (  # noqa: E402
    ANNOTATED_ROOT,
    ANNOTATED_SUFFIX,
    CLICK_TYPE,
    EPISODES_ROOT,
    JSON_DIR,
    KEYBOARD,
    KEYBOARD_JSON_DIR,
    TIMELINE_RELPATH,
    TYPING_TYPE,
)
from Omniparser_Runner.timeline_reader import (  # noqa: E402
    IMAGE_SUBDIRS,
    load_timeline,
    pick_last_image,
    resolve_image,
)

DEFAULT_EPISODE = None          # e.g. "testingcases"; None = every episode

# Same key mousemapper.py writes, so click, scroll and typing nodes all expose
# their OmniParser match under one name.
OMNIPARSER_KEY = "omniparser"

MATCH_STRATEGY = "typed_value_fuzzy_ocr"

# Only these OmniParser sources carry text actually read off the screen. A
# `box_yolo_content_yolo` element's `content` is a Florence caption describing
# an icon ("Copy", "Play"), which nobody ever types into, so leaving those in
# the pool only gives short typed strings junk to collide with.
OCR_SOURCES = frozenset({"box_ocr_content_ocr", "box_yolo_content_ocr"})

# Below this many characters a value is too short to survive the screenshot-lag
# allowance -- dropping the last character of "sy" would match any stray "s".
MIN_TARGET_CHARS = 3

# Timeline node types that move focus somewhere else, ending the current field.
FIELD_RESET_TYPES = frozenset({"click", "scroll"})
# Special keys that commit or leave a field rather than editing it.
FIELD_RESET_KEYS = frozenset({"enter", "return", "tab", "esc", "escape"})

# How the fuzzy score is graded. A typed string usually lands on its OCR box
# almost exactly, so 0.8 is a confident hit; 0.6-0.8 is close enough to record
# but flagged as weak (OCR dropped or added characters); below 0.6 the element
# is not reported at all.
MATCH_THRESHOLD = 0.8
WEAK_THRESHOLD = 0.6

# How many ranked runners-up to keep on a node that did not match cleanly.
DEFAULT_TOP = 3

STATUS_MATCH = "match"
STATUS_WEAK = "weak"
STATUS_NO_MATCH = "no_match"
STATUS_NO_VALUE = "no_typed_value"
STATUS_NO_IMAGE = "no_images"
STATUS_NO_JSON = "no_json"
STATUS_NO_ELEMENTS = "no_elements"

# Outcomes that say nothing about the node itself, only that OmniParser hasn't
# produced its output yet. Writing these would be worse than writing nothing --
# the already-enriched guard would then treat the node as done forever, so
# running this before Omniparser_Runner would permanently poison the timeline.
RETRYABLE_STATUSES = frozenset({STATUS_NO_JSON, STATUS_NO_ELEMENTS})


# --------------------------------------------------------------------------- #
# Fuzzy backend: rapidfuzz when installed, difflib otherwise
# --------------------------------------------------------------------------- #

try:
    from rapidfuzz import fuzz as _rf

    def _ratio(a: str, b: str) -> float:
        return _rf.ratio(a, b) / 100.0

    def _token_set(a: str, b: str) -> float:
        return _rf.token_set_ratio(a, b) / 100.0

    BACKEND = "rapidfuzz"
except ImportError:  # stdlib fallback, always available
    from difflib import SequenceMatcher

    def _ratio(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio()

    def _token_set(a: str, b: str) -> float:
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
    """Lowercase, drop punctuation (incl. the dot), collapse whitespace.

    The pipe is stripped too: OCR reads the text caret sitting in a focused
    input as ``|``, so "america u|" and "america u" are the same field.
    """
    s = (s or "").lower().strip()
    s = re.sub(r"[.\'`,\"!?:;|]", "", s)   # punctuation -> removed
    s = re.sub(r"\s+", " ", s).strip()
    return s


def score_element(target_norm: str, content_norm: str) -> float:
    """Best of several fuzzy signals, in [0, 1]."""
    if not target_norm or not content_norm:
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

    # Weight by how much of the OCR box the typed text actually accounts for.
    # Both the containment bonus and rapidfuzz's token_set_ratio (which returns
    # 1.0 whenever one token set is a subset of the other) are blind to length,
    # so without this "of" scores a confident match against "table of contents".
    #
    # The root softens the penalty as the target grows: "america" inside a
    # decorated row ("Q america - Google Search") is still that string, while
    # two characters inside any long blob is a coincidence.
    coverage = min(len(squished_t) / max(len(squished_c), 1), 1.0)
    return max(scores) * coverage ** (1 / max(len(squished_t), 1))


def score_with_lag(target_norm: str, content_norm: str) -> float:
    """``score_element`` that tolerates the last character not being drawn yet.

    Screenshots are captured on the keystroke, before the UI repaints, so the
    frame a typing node points at is usually one character behind its value.
    """
    score = score_element(target_norm, content_norm)
    if len(target_norm) > MIN_TARGET_CHARS:
        score = max(score, score_element(target_norm[:-1], content_norm))
    return score


def top_row(bbox: object) -> float:
    """Vertical position of the element top edge (0 = top of screen)."""
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 2:
        try:
            return float(bbox[1])
        except (TypeError, ValueError):
            return 1.0
    return 1.0


def anchor_distance(bbox: object, anchor: tuple[float, float]) -> float:
    """Squared distance from an element centre to a normalized screen point."""
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return 2.0
    try:
        x1, y1, x2, y2 = (float(v) for v in bbox[:4])
    except (TypeError, ValueError):
        return 2.0
    return ((x1 + x2) / 2 - anchor[0]) ** 2 + ((y1 + y2) / 2 - anchor[1]) ** 2


def rank_elements(
    value: str,
    elements: dict,
    anchor: tuple[float, float] | None = None,
) -> tuple[str, list[dict]]:
    """Rank ``elements`` by how well their OCR text matches the typed ``value``.

    Returns ``(normalized_value, ranked)``. Each entry is the element shape this
    script writes into the timeline: the element key plus the fields a consumer
    needs to act on it (content, score, interactivity, type, source, bbox).

    ``anchor`` is the normalized point the user last clicked, used to break
    score ties toward the field they actually focused.
    """
    target = normalize(value)
    results = [
        {
            "element": key,
            "content": el.get("content", ""),
            "score": round(
                score_with_lag(target, normalize(el.get("content", ""))), 4
            ),
            "interactivity": el.get("interactivity", False),
            "type": el.get("type"),
            "source": el.get("source"),
            "bbox": el.get("bbox"),
        }
        for key, el in (elements or {}).items()
        if isinstance(el, dict) and el.get("source") in OCR_SOURCES
    ]

    # Sort by score desc, then interactive first. Ties break toward the element
    # nearest the last click -- the field the user focused before typing. With
    # no click to anchor on, fall back to the element nearest the top of the
    # screen, where search and address bars usually live.
    def tie_break(r: dict) -> float:
        if anchor is None:
            return top_row(r["bbox"])
        return anchor_distance(r["bbox"], anchor)

    results.sort(
        key=lambda r: (-r["score"], not r["interactivity"], tie_break(r))
    )
    return target, results


def grade(score: float, min_score: float) -> str:
    """Turn a best-candidate score into a match status.

    A ``--min-score`` above ``MATCH_THRESHOLD`` raises the confident-hit bar
    too, rather than being silently ignored.
    """
    if score >= max(MATCH_THRESHOLD, min_score):
        return STATUS_MATCH
    if score >= min_score:
        return STATUS_WEAK
    return STATUS_NO_MATCH


# --------------------------------------------------------------------------- #
# Field context: what is actually on screen when a typing node ends
# --------------------------------------------------------------------------- #

def click_anchor(item: dict) -> tuple[float, float] | None:
    """A click node's pointer position, normalized against its own screen."""
    event = (item.get("events") or [{}])[0]
    if not isinstance(event, dict):
        return None
    x, y = event.get("x"), event.get("y")
    width, _, height = str(event.get("screen_resolution") or "").lower().partition("x")
    try:
        return x / int(width), y / int(height)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def field_context(items: list[dict]) -> dict:
    """Per typing node: the whole field's text, and where the field was clicked.

    The recorder splits a typed string at every space and backspace, so a node's
    own ``value`` is a fragment ("uv") while the screenshot shows everything
    entered so far ("america uv"). Matching the fragment against the full field
    scores terribly and lets short OCR crumbs win, so replay the keystrokes to
    rebuild what the field actually held when the node ended.
    """
    context: dict = {}
    buffer = ""
    anchor: tuple[float, float] | None = None

    for item in items:
        node_type = item.get("type")

        if node_type == TYPING_TYPE:
            buffer += item.get("value") or ""
            context[item.get("index")] = {"typed": buffer, "anchor": anchor}
        elif node_type == "KeyPress":
            buffer += str(item.get("value") or "")
        elif node_type == "special":
            for token in str(item.get("value") or "").split(","):
                token = token.strip().casefold()
                if token == "space":
                    buffer += " "
                elif token == "backspace":
                    buffer = buffer[:-1]
                elif token in FIELD_RESET_KEYS:
                    buffer = ""
        elif node_type in FIELD_RESET_TYPES:
            # Focus moved; whatever comes next starts a new field.
            buffer = ""
            if node_type == CLICK_TYPE:
                anchor = click_anchor(item) or anchor

    return context


# --------------------------------------------------------------------------- #
# Locating a typing node's OmniParser JSON
# --------------------------------------------------------------------------- #

def record_json_dir(episode_dir: Path) -> Path:
    return episode_dir / ANNOTATED_ROOT / JSON_DIR / KEYBOARD_JSON_DIR


def resolve_record_json(
    episode_dir: Path,
    node_index: int | None,
    image_stem: str,
) -> Path | None:
    """``node_{index}_{stem}.json``, falling back to a glob on the index alone.

    The stem is the screenshot the runner picked; if the timeline has since been
    re-cut, the node index alone still identifies the record unambiguously.
    """
    kind_dir = record_json_dir(episode_dir)

    if node_index is not None:
        by_convention = kind_dir / f"node_{node_index}_{image_stem}.json"
        if by_convention.is_file():
            return by_convention

        matches = sorted(kind_dir.glob(f"node_{node_index}_*.json"))
        if matches:
            return matches[0]

    by_stem = kind_dir / f"{image_stem}_{KEYBOARD}.json"
    return by_stem if by_stem.is_file() else None


def load_record(json_path: Path) -> dict | None:
    try:
        with open(json_path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def annotated_image_for(episode_dir: Path, image_stem: str) -> Path:
    """Where ``Omniparser_Runner`` wrote this node's annotated PNG."""
    return (
        episode_dir / ANNOTATED_ROOT / IMAGE_SUBDIRS[KEYBOARD]
        / f"{image_stem}{ANNOTATED_SUFFIX}.png"
    )


# --------------------------------------------------------------------------- #
# Building one node's block
# --------------------------------------------------------------------------- #

def typing_block(
    episode_dir: Path,
    item: dict,
    *,
    context: dict | None = None,
    min_score: float = WEAK_THRESHOLD,
    top: int = DEFAULT_TOP,
) -> dict:
    """The ``omniparser`` block for one Typing node.

    The field set mirrors the one ``mousemapper.py`` writes so both artifacts
    can be read by the same consumer. ``match_query`` and its companions appear
    only once a match was actually attempted -- an early exit such as
    ``no_json`` has no query to report.

    ``context`` is this node's entry from :func:`field_context`: the whole
    field's text to match on, and the click that focused it.
    """
    node_index = item.get("index")
    value = item.get("value") or ""
    context = context or {}
    anchor = context.get("anchor")

    # The frame after the whole string is in -- the same screenshot the runner
    # parsed for this node.
    image = pick_last_image(item)

    block = {
        "kind": KEYBOARD,
        "node_type": TYPING_TYPE,
        "annotated_json_path": None,
        "annotated_image": None,
        "image_path": None,
        "image_name": None,
        "screen": None,
        "screen_matched": True,
        "element_count": 0,
        "match_strategy": MATCH_STRATEGY,
        "match_status": None,
        "matched_element": None,
        "candidates": [],
    }

    def finish(status: str, **extra) -> dict:
        """Stamp the outcome, then append the fields that trail it in the
        schema so key order stays stable across nodes."""
        block["match_status"] = status
        block.update(extra)
        return block

    if image is None:
        return finish(STATUS_NO_IMAGE)

    image_path = resolve_image(episode_dir, image)
    json_path = resolve_record_json(episode_dir, node_index, image_path.stem)
    record = load_record(json_path) if json_path else None
    elements = (record or {}).get("elements")
    elements = elements if isinstance(elements, dict) else {}

    # The runner records the annotated PNG it wrote; fall back to the
    # conventional path when this node has no record yet.
    annotated_image = (record or {}).get("annotated_image") or str(
        annotated_image_for(episode_dir, image_path.stem)
    )

    block.update({
        "annotated_json_path": str(json_path) if json_path else None,
        "annotated_image": annotated_image,
        "image_path": str(image_path),
        "image_name": image.get("value") or image_path.name,
        "screen": image.get("screen"),
        "element_count": len(elements),
    })

    if json_path is None:
        return finish(STATUS_NO_JSON)

    if not elements:
        return finish(STATUS_NO_ELEMENTS)

    # The record carries the value the runner saw; prefer the timeline value,
    # but fall back to it if this item somehow has none.
    if not value:
        value = (record or {}).get("value") or ""

    # Match on the whole field, not this node's fragment -- the screenshot shows
    # everything typed so far, not just the characters this node contributed.
    field_value = context.get("typed") or value
    if not normalize(field_value):
        return finish(STATUS_NO_VALUE)

    target, ranked = rank_elements(field_value, elements, anchor)

    # Every element was a Florence icon caption rather than OCR'd text, so there
    # was nothing here that could hold the typed string.
    status = grade(ranked[0]["score"], min_score) if ranked else STATUS_NO_MATCH

    # A clean hit stands on its own; anything less keeps its runners-up so a bad
    # OCR read can be eyeballed straight from the timeline.
    keep = max(top, 1)
    if status == STATUS_MATCH:
        block["matched_element"] = ranked[0]
    elif status == STATUS_WEAK:
        block["matched_element"] = ranked[0]
        block["candidates"] = ranked[1 : 1 + keep]
    else:
        block["candidates"] = ranked[:keep]

    return finish(
        status,
        match_query={
            "value": value,
            "field_value": field_value,
            "normalized": target,
            "anchor": list(anchor) if anchor else None,
        },
        match_backend=BACKEND,
        match_count=1 if block["matched_element"] else 0,
    )


# --------------------------------------------------------------------------- #
# Episode orchestration
# --------------------------------------------------------------------------- #

def backup_timeline(timeline_path: Path) -> Path:
    """One-time backup before the first in-place write."""
    backup_path = timeline_path.with_suffix(timeline_path.suffix + ".bak")
    if not backup_path.is_file():
        with open(timeline_path, "rb") as src, open(backup_path, "wb") as dst:
            dst.write(src.read())
    return backup_path


def write_json_atomic(payload: dict, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def is_pending(item: dict) -> bool:
    """True if ``item`` carries only a retryable placeholder block."""
    block = item.get(OMNIPARSER_KEY)
    return (
        isinstance(block, dict)
        and block.get("match_status") in RETRYABLE_STATUSES
    )


def enrich_episode(
    episode_dir: Path,
    *,
    timeline_path: Path | None = None,
    min_score: float = WEAK_THRESHOLD,
    top: int = DEFAULT_TOP,
    force: bool = False,
    dry_run: bool = False,
    quiet: bool = False,
) -> dict:
    """Mutate one episode timeline.json in place with typing matches.

    Returns ``{"total", "already_done", "pending", "statuses"}``, where
    ``statuses`` counts the outcomes of every node matched this run and
    ``pending`` counts those whose OmniParser output does not exist yet --
    matched, reported, but deliberately not written.
    """
    episode_dir = Path(episode_dir).resolve()
    timeline_path = timeline_path or (episode_dir / TIMELINE_RELPATH)

    timeline = load_timeline(timeline_path)
    items = timeline.get("items") or []
    context = field_context(items)

    tally = {"total": 0, "already_done": 0, "pending": 0, "statuses": Counter()}
    wrote_anything = False
    rows: list[tuple[dict, dict]] = []

    for item in items:
        if item.get("type") != TYPING_TYPE:
            continue
        tally["total"] += 1

        # Leave anything already enriched alone, so a re-run neither rewrites
        # good data nor stacks a second block beside it. A placeholder left by a
        # run that happened before OmniParser produced its output is not
        # enrichment, so it is re-matched.
        if OMNIPARSER_KEY in item and not force and not is_pending(item):
            tally["already_done"] += 1
            continue

        block = typing_block(
            episode_dir,
            item,
            context=context.get(item.get("index")),
            min_score=min_score,
            top=top,
        )
        status = block["match_status"]
        tally["statuses"][status] += 1
        rows.append((item, block))

        # Drop any placeholder an earlier run left, so it is neither kept beside
        # the new block nor mistaken for enrichment if still unresolvable.
        if item.pop(OMNIPARSER_KEY, None) is not None:
            wrote_anything = True

        if status in RETRYABLE_STATUSES:
            tally["pending"] += 1
            continue

        item[OMNIPARSER_KEY] = block
        wrote_anything = True

    if not quiet:
        print(f"Episode : {timeline.get('episode_name', episode_dir.name)}")
        print(f"Timeline: {timeline_path}")
        print(f"Backend : {BACKEND}")
        print("-" * 78)
        for item, block in rows:
            matched = block["matched_element"]
            query = block.get("match_query") or {}
            target = query.get("field_value") or query.get(
                "value", item.get("value")
            )
            detail = (
                f"{matched['element']} -> {matched['content']!r} "
                f"({matched['score']:.2%})"
                if matched
                else "-"
            )
            print(
                f"  node {str(item.get('index')):>3}  {block['match_status']:<12}"
                f" {str(target)!r:<16} {detail}"
            )
            for cand in block["candidates"]:
                print(
                    f"        candidate  {cand['score']:.2%}  "
                    f"{cand['element']:<8} {cand['content']!r}"
                )
        print("-" * 78)
        breakdown = ", ".join(
            f"{count} {status}" for status, count in sorted(tally["statuses"].items())
        ) or "nothing new"
        print(
            f"Typing nodes: {tally['total']} total | {breakdown}"
            f" | {tally['already_done']} already enriched"
        )
        if tally["pending"]:
            print(
                f"Note: {tally['pending']} node(s) have no OmniParser output yet "
                f"and were left unenriched. Run Omniparser_Runner on this "
                f"episode, then re-run this script."
            )
            print(f"      Expected under: {record_json_dir(episode_dir)}")

    if dry_run:
        if not quiet:
            print("Dry run: nothing written.")
        return tally

    if wrote_anything:
        backup_path = backup_timeline(timeline_path)
        write_json_atomic(timeline, timeline_path)
        if not quiet:
            print(f"Backup  -> {backup_path}")
            print(f"Updated -> {timeline_path}")
    elif not quiet:
        print("Nothing new to write.")

    return tally


def discover_episodes(episodes_root: Path) -> list[Path]:
    if not episodes_root.is_dir():
        raise FileNotFoundError(f"Episodes root not found: {episodes_root}")
    episodes = sorted(
        p for p in episodes_root.iterdir()
        if p.is_dir() and (p / TIMELINE_RELPATH).is_file()
    )
    if not episodes:
        raise ValueError(f"No episodes with {TIMELINE_RELPATH} in: {episodes_root}")
    return episodes


# --------------------------------------------------------------------------- #
# Single-record inspection
# --------------------------------------------------------------------------- #

def inspect_record(json_path: Path, *, top: int, min_score: float) -> int:
    """Score one keyboard record against its own ``value`` and print the rank.

    A read-only view of the matching this script would write, for tuning
    thresholds against a single node without touching the timeline.
    """
    record = load_record(json_path)
    if record is None:
        print(f"ERROR: could not read JSON: {json_path}", file=sys.stderr)
        return 2

    elements = record.get("elements")
    if not isinstance(elements, dict) or not elements:
        print(f"ERROR: no elements in {json_path}", file=sys.stderr)
        return 1

    value = record.get("value", "")
    target, ranked = rank_elements(value, elements)
    if not ranked:
        print(
            f"ERROR: no OCR-backed elements in {json_path} "
            f"({len(elements)} element(s), all icon captions)",
            file=sys.stderr,
        )
        return 1

    best = ranked[0]
    status = grade(best["score"], min_score)

    print(f"Backend     : {BACKEND}")
    print(f"Node        : {record.get('node_index')} "
          f"{record.get('node_type')} / {record.get('kind')}")
    print(f"Typed value : {value!r}   (normalized: {target!r})")
    print("Note        : this record holds one fragment; a full-episode run "
          "matches the whole field.")
    print("-" * 68)
    print(f"Best match  : {best['element']}  ->  {best['content']!r}")
    print(f"Confidence  : {best['score']:.2%}  [{status}]")
    print(f"bbox        : {best['bbox']}")
    print(f"interactive : {best['interactivity']}   source: {best['source']}")
    print("-" * 68)
    print(f"Top {top} candidates:")
    for cand in ranked[:top]:
        print(f"  {cand['score']:.2%}  {cand['element']:<8} {cand['content']!r}")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="node.py",
        description="Match each Typing node typed string to the OmniParser "
        "element that shows it, and append the result to timeline.json in "
        "place.",
    )
    parser.add_argument("--episodes-root", default=str(EPISODES_ROOT))
    parser.add_argument(
        "--episode", default=DEFAULT_EPISODE,
        help="Episode folder name under --episodes-root (default: all episodes)",
    )
    parser.add_argument("--episode-dir", default=None, help="Full path to one episode")
    parser.add_argument(
        "--timeline", default=None, help="Path to a specific timeline.json"
    )
    parser.add_argument(
        "--min-score", type=float, default=WEAK_THRESHOLD,
        help=f"Lowest score still reported as a (weak) match "
        f"(default {WEAK_THRESHOLD})",
    )
    parser.add_argument(
        "--top", type=int, default=DEFAULT_TOP,
        help=f"Ranked runners-up to keep on a node that did not match cleanly "
        f"(default {DEFAULT_TOP})",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-match nodes that already carry a block",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Match and print, write nothing"
    )
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--inspect", default=None,
        help="Score a single keyboard record JSON and print its ranking; "
        "writes nothing",
    )
    return parser.parse_args(argv)


def resolve_jobs(args: argparse.Namespace) -> list[tuple[Path, Path | None]]:
    if args.timeline:
        timeline_path = Path(args.timeline).resolve()
        episode_dir = (
            Path(args.episode_dir).resolve() if args.episode_dir
            else timeline_path.parents[1]
        )
        return [(episode_dir, timeline_path)]
    if args.episode_dir:
        return [(Path(args.episode_dir).resolve(), None)]
    if args.episode:
        return [((Path(args.episodes_root) / args.episode).resolve(), None)]
    return [(d, None) for d in discover_episodes(Path(args.episodes_root))]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.inspect:
        return inspect_record(
            Path(args.inspect), top=max(args.top, 1), min_score=args.min_score
        )

    try:
        jobs = resolve_jobs(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    failed = 0
    for episode_dir, timeline_path in jobs:
        try:
            enrich_episode(
                episode_dir,
                timeline_path=timeline_path,
                min_score=args.min_score,
                top=args.top,
                force=args.force,
                dry_run=args.dry_run,
                quiet=args.quiet,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"Error [{episode_dir.name}]: {exc}", file=sys.stderr)
            failed += 1

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
