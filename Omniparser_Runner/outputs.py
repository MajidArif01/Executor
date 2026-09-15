"""Output layout: where annotated PNGs and per-node JSON files are written."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from Omniparser_Runner.config import (
    ANNOTATED_ROOT,
    ANNOTATED_SUFFIX,
    JSON_DIR,
    KEYBOARD,
    KEYBOARD_JSON_DIR,
    MOUSE,
    MOUSE_IMAGE_DIR,
    MOUSE_JSON_DIR,
    SCROLL,
    SCROLL_IMAGE_DIR,
    SCROLL_JSON_DIR,
    TYPING_IMAGE_DIR,
)
from Omniparser_Runner.timeline_reader import ExtractedNode

# Which key of ``output_dirs`` each node kind writes to.
IMAGE_DIR_KEYS = {KEYBOARD: "typing_images", MOUSE: "mouse_images", SCROLL: "scroll_images"}
JSON_DIR_KEYS = {KEYBOARD: "keyboard_json", MOUSE: "mouse_json", SCROLL: "scroll_json"}


def output_dirs(output_root: Path) -> dict[str, Path]:
    """Create and return the AnotatedData tree under ``output_root``."""
    root = output_root / ANNOTATED_ROOT
    dirs = {
        "root": root,
        "typing_images": root / TYPING_IMAGE_DIR,
        "mouse_images": root / MOUSE_IMAGE_DIR,
        "scroll_images": root / SCROLL_IMAGE_DIR,
        "json_root": root / JSON_DIR,
        "keyboard_json": root / JSON_DIR / KEYBOARD_JSON_DIR,
        "mouse_json": root / JSON_DIR / MOUSE_JSON_DIR,
        "scroll_json": root / JSON_DIR / SCROLL_JSON_DIR,
    }
    for key, path in dirs.items():
        if key != "root":
            path.mkdir(parents=True, exist_ok=True)
    return dirs


def image_dir_for(dirs: dict[str, Path], node: ExtractedNode) -> Path:
    return dirs[IMAGE_DIR_KEYS[node.kind]]


def json_dir_for(dirs: dict[str, Path], node: ExtractedNode) -> Path:
    return dirs[JSON_DIR_KEYS[node.kind]]


def annotated_image_path(image_dir: Path, node: ExtractedNode) -> Path:
    return image_dir / f"{node.image_path.stem}{ANNOTATED_SUFFIX}.png"


def record_json_path(json_dir: Path, node: ExtractedNode) -> Path:
    """Per-node JSON: node_{index}_{stem}.json, or {stem}_{kind}.json."""
    stem = node.image_path.stem
    if node.index is not None:
        return json_dir / f"node_{node.index}_{stem}.json"
    return json_dir / f"{stem}_{node.kind}.json"


def write_json(payload: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def write_annotated_image(annotated: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    annotated.save(path)
    return path


def build_record(
    node: ExtractedNode,
    elements: dict,
    annotated_path: Path | None,
) -> dict:
    """The full per-node record written to AnotatedJson/{keyboard,mouse}/."""
    record = {
        "node_index": node.index,
        "node_type": node.node_type,
        "kind": node.kind,
        "value": node.value,
        "screen": node.screen,
        "screen_matched": node.screen_matched,
        "image_name": node.image_name,
        "image_path": str(node.image_path),
        "annotated_image": str(annotated_path) if annotated_path else None,
        **node.times,
        "elements": elements,
    }
    if node.click is not None:
        record["click"] = node.click
    if node.scroll is not None:
        record["scroll"] = node.scroll
    return record


def build_index_entry(
    node: ExtractedNode,
    annotated_path: Path | None,
    record_path: Path,
) -> dict:
    """The compact entry listed in extractor_index.json."""
    return {
        "node_index": node.index,
        "node_type": node.node_type,
        "kind": node.kind,
        "value": node.value,
        "image_path": str(node.image_path),
        "annotated_image": str(annotated_path) if annotated_path else None,
        "annotated_json_path": str(record_path),
    }
