
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from omniparser.parse import ParseResult  # noqa: E402

ANNOTATED_SUFFIX = "_annotated"


def resolve_json_path(out_dir: str | Path | None, image: Path) -> Path:
    """Where the JSON for `image` goes. shot.png -> shot.json."""
    if out_dir is None:
        return image.with_suffix(".json")
    target = Path(out_dir)
    if target.suffix.lower() == ".json":
        target = target.parent
    target.mkdir(parents=True, exist_ok=True)
    return target / f"{image.stem}.json"


def resolve_annotated_path(json_path: Path, suffix: str = ANNOTATED_SUFFIX) -> Path:
    """Annotated PNG path beside the JSON: shot.json -> shot_annotated.png."""
    return json_path.with_name(f"{json_path.stem}{suffix}.png")


def write_elements_json(elements: dict, json_path: Path) -> Path:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(elements, f, indent=2, ensure_ascii=False)
    return json_path


def write_annotated_image(annotated: Image.Image, png_path: Path) -> Path:
    png_path.parent.mkdir(parents=True, exist_ok=True)
    annotated.save(png_path)
    return png_path


def write_parse_result(
    result: ParseResult,
    *,
    out_dir: str | Path | None = None,
    save_annotated: bool = True,
    annotated_suffix: str = ANNOTATED_SUFFIX,
) -> tuple[Path, Path | None]:
    """Write one ParseResult to disk. Returns (json_path, annotated_path | None)."""
    if result.source is None:
        raise ValueError("ParseResult.source is required to write outputs")

    json_path = resolve_json_path(out_dir, result.source)
    write_elements_json(result.elements, json_path)

    annotated_path = None
    if save_annotated and result.annotated is not None:
        annotated_path = resolve_annotated_path(json_path, annotated_suffix)
        write_annotated_image(result.annotated, annotated_path)

    return json_path, annotated_path


def write_combined_json(payload: dict, json_path: Path) -> Path:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return json_path


def resolve_typing_json_path(json_dir: Path, entry: dict) -> Path:
    """Per-record typing JSON: node_{id}_{image_stem}.json or {stem}_typing.json."""
    stem = Path(entry["image_path"]).stem
    node_id = entry.get("node_id")
    if node_id is not None:
        return json_dir / f"node_{node_id}_{stem}.json"
    return json_dir / f"{stem}_typing.json"


def write_typing_entry_json(entry: dict, json_path: Path) -> Path:
    """Write one typing annotation record (text + elements) to its own JSON file."""
    return write_combined_json(entry, json_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write OmniParser results (JSON + annotated PNG) from a "
        "previously saved inference payload.",
    )
    parser.add_argument(
        "payload",
        help="JSON file produced by omniparser/parse.py (--stdout saved to file)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Directory for outputs. Default: beside each source image",
    )
    parser.add_argument(
        "--no-annotated",
        action="store_true",
        help="Skip writing annotated PNGs (elements JSON only)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload_path = Path(args.payload)
    with open(payload_path, encoding="utf-8") as f:
        payload = json.load(f)

    items = payload if isinstance(payload, list) else [payload]
    written: list[str] = []

    for item in items:
        source = Path(item["source"])
        elements = item["elements"]
        json_path = resolve_json_path(args.out, source)
        write_elements_json(elements, json_path)
        written.append(str(json_path))

    print("\n".join(written))


if __name__ == "__main__":
    main()
