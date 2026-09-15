


from __future__ import annotations

from collections import defaultdict

import argparse
import gc
import json
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Data_ingetion"))
sys.path.insert(0, str(ROOT))

from data_ingestion import EPISODES_ROOT, create_ingestion  # noqa: E402
from keyboard_data import (  # noqa: E402
    enrich_l2_typing_json,
    set_annotated_json_path,
    update_typing_json_annotated_images,
    update_typing_json_annotated_json_paths,
)
from Data_parser.write_outputs import (  # noqa: E402
    resolve_typing_json_path,
    write_annotated_image,
    write_combined_json,
    write_typing_entry_json,
)
from omniparser.parse import (  # noqa: E402
    BOX_THRESHOLD,
    IMGSZ,
    IOU_THRESHOLD,
    OCR_ENGINE,
    add_ocr_engine_args,
    resolve_ocr_engine,
    run_parse,
)
from omniparser.util.utils import (  # noqa: E402
    get_caption_model_processor,
    get_yolo_model,
)

ANNOTATED_DIR = "AnotatedData"
ANNOTATED_SUBDIR = "typing"
JSON_DIR = "TypingAnotatedjson"
INDEX_FILENAME = "typing_annotated_index.json"
ANNOTATED_SUFFIX = "_anotated"
# Florence captioning at 128 can use ~4 GB; lower default for multi-image runs.
DEFAULT_BATCH_SIZE = 32


def is_cuda_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        token in msg
        for token in ("cuda", "cudnn", "illegal memory access", "out of memory")
    )


def clear_gpu_memory() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
    except Exception:
        pass


def load_models(device: str | None = None) -> tuple:
    clear_gpu_memory()
    return get_yolo_model(device=device), get_caption_model_processor(device=device)


def unload_models(yolo_model, caption_model_processor) -> None:
    del yolo_model, caption_model_processor
    clear_gpu_memory()


def parse_image_via_subprocess(
    img_path: Path,
    *,
    batch_size: int,
    box_threshold: float,
    iou_threshold: float,
    imgsz: int,
    ocr_engine: str,
    temp_json_dir: Path,
) -> tuple[dict, Path | None] | None:
    """Parse one image in a fresh process to recover from a poisoned CUDA context."""
    temp_json_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(ROOT / "Data_parser" / "parse_one.py"),
        str(img_path),
        "--out",
        str(temp_json_dir),
        "--annotated",
        "--batch-size",
        str(batch_size),
        "--box-threshold",
        str(box_threshold),
        "--iou-threshold",
        str(iou_threshold),
        "--imgsz",
        str(imgsz),
        "--quiet",
        "--ocr-engine",
        ocr_engine,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "subprocess failed").strip()
        print(f"Error: subprocess parse failed for {img_path}: {err}", file=sys.stderr)
        return None

    json_path = temp_json_dir / f"{img_path.stem}.json"
    if not json_path.is_file():
        print(f"Error: expected JSON not found: {json_path}", file=sys.stderr)
        return None

    with open(json_path, encoding="utf-8") as f:
        elements = json.load(f)

    annotated_path = temp_json_dir / f"{img_path.stem}_annotated.png"
    if not annotated_path.is_file():
        annotated_path = None
    return elements, annotated_path


def annotate_records(
    records: list[dict],
    output_root: Path,
    *,
    device: str | None = None,
    yolo_model=None,
    caption_model_processor=None,
    box_threshold: float = BOX_THRESHOLD,
    iou_threshold: float = IOU_THRESHOLD,
    imgsz: int = IMGSZ,
    batch_size: int = DEFAULT_BATCH_SIZE,
    ocr_engine: str = OCR_ENGINE,
    quiet: bool = False,
) -> tuple[list[dict], int]:
    """Parse typing images and write annotated PNGs + per-record JSON files."""
    annotated_dir = output_root / ANNOTATED_DIR / ANNOTATED_SUBDIR
    json_dir = output_root / ANNOTATED_DIR / JSON_DIR
    temp_json_dir = output_root / ".typing_parse_tmp"
    annotated_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)

    records_out: list[dict] = []
    typing_image_updates: dict[str, list[dict]] = defaultdict(list)
    typing_json_updates: dict[str, list[dict]] = defaultdict(list)
    failures = 0
    cuda_poisoned = False
    models_loaded = yolo_model is not None and caption_model_processor is not None

    for rec in records:
        img_path = Path(rec["image_path"])
        if not img_path.is_file():
            print(f"warning: image not found, skipping: {img_path}", file=sys.stderr)
            failures += 1
            continue

        started = time.time()
        elements: dict | None = None
        annotated: Image.Image | None = None
        subprocess_annotated_path: Path | None = None

        if cuda_poisoned:
            parsed = parse_image_via_subprocess(
                img_path,
                batch_size=batch_size,
                box_threshold=box_threshold,
                iou_threshold=iou_threshold,
                imgsz=imgsz,
                ocr_engine=ocr_engine,
                temp_json_dir=temp_json_dir,
            )
            if parsed is not None:
                elements, subprocess_annotated_path = parsed
        else:
            if not models_loaded:
                yolo_model, caption_model_processor = load_models(device)
                models_loaded = True

            try:
                with Image.open(img_path) as handle:
                    image = handle.convert("RGB")
                parsed_result = run_parse(
                    image,
                    source=img_path,
                    yolo_model=yolo_model,
                    caption_model_processor=caption_model_processor,
                    box_threshold=box_threshold,
                    iou_threshold=iou_threshold,
                    imgsz=imgsz,
                    batch_size=batch_size,
                    ocr_engine=ocr_engine,
                )
                elements = parsed_result.elements
                annotated = parsed_result.annotated
            except Exception as exc:
                print(f"Error: failed to parse {img_path}: {exc}", file=sys.stderr)
                if is_cuda_error(exc):
                    cuda_poisoned = True
                    if models_loaded:
                        unload_models(yolo_model, caption_model_processor)
                        yolo_model = caption_model_processor = None
                        models_loaded = False
                    if not quiet:
                        print(
                            "CUDA error detected; retrying this and remaining "
                            "images in isolated subprocesses with a lower batch size.",
                            file=sys.stderr,
                        )
                    parsed = parse_image_via_subprocess(
                        img_path,
                        batch_size=min(batch_size, 16),
                        box_threshold=box_threshold,
                        iou_threshold=iou_threshold,
                        imgsz=imgsz,
                        ocr_engine=ocr_engine,
                        temp_json_dir=temp_json_dir,
                    )
                    if parsed is not None:
                        elements, subprocess_annotated_path = parsed
                else:
                    failures += 1
                    continue

        if elements is None:
            failures += 1
            continue

        annotated_name = f"{img_path.stem}{ANNOTATED_SUFFIX}.png"
        annotated_path = annotated_dir / annotated_name
        if subprocess_annotated_path is not None:
            annotated_path.write_bytes(subprocess_annotated_path.read_bytes())
        elif annotated is not None:
            write_annotated_image(annotated, annotated_path)
        else:
            print(
                f"warning: no annotated PNG for {img_path.name}; elements saved only.",
                file=sys.stderr,
            )
            annotated_path = None

        entry = {
            "image_path": str(img_path),
            "text": rec.get("text", ""),
            "node_id": rec.get("node_id"),
            "starttime": rec.get("starttime"),
            "endtime": rec.get("endtime"),
            "elements": elements,
        }
        entry_json_path = resolve_typing_json_path(json_dir, entry)
        write_typing_entry_json(entry, entry_json_path)

        records_out.append({
            "node_id": entry.get("node_id"),
            "text": entry.get("text", ""),
            "image_path": entry["image_path"],
            "annotated_json_path": str(entry_json_path.resolve()),
        })

        episode_dir = rec.get("episode_dir")
        if episode_dir:
            typing_json_updates[episode_dir].append({
                "node_id": rec.get("node_id"),
                "image_path": str(img_path),
                "annotated_json_path": str(entry_json_path.resolve()),
            })

        if annotated_path is not None:
            if episode_dir:
                typing_image_updates[episode_dir].append(
                    {
                        "node_id": rec.get("node_id"),
                        "image_path": str(img_path),
                        "annotated_image": str(annotated_path.resolve()),
                    }
                )

        if not quiet:
            interactive = sum(1 for e in elements.values() if e.get("interactivity"))
            dest = annotated_path or "(json only)"
            print(
                f"{img_path.name}: {len(elements)} elements "
                f"({interactive} interactive) in {time.time() - started:.1f}s "
                f"-> {dest}, JSON -> {entry_json_path.name}"
            )

        if not cuda_poisoned:
            clear_gpu_memory()

    if models_loaded:
        unload_models(yolo_model, caption_model_processor)

    for episode_dir_str, updates in typing_image_updates.items():
        typing_path, count = update_typing_json_annotated_images(
            Path(episode_dir_str),
            updates,
        )
        if not quiet:
            print(f"Updated {count} annotated image path(s) in {typing_path}")

    for episode_dir_str, updates in typing_json_updates.items():
        typing_path, count = update_typing_json_annotated_json_paths(
            Path(episode_dir_str),
            updates,
        )
        if not quiet:
            print(f"Updated {count} annotated JSON path(s) in {typing_path}")

    return records_out, failures


def _cuda_available() -> bool:
    try:
        import torch

        return torch.cuda.is_available()
    except Exception:
        return False


def annotate_episode(
    episodes_root: Path,
    episode_name: str | None,
    output_root: Path | None,
    *,
    device: str | None = None,
    quiet: bool = False,
    **parse_kwargs,
) -> dict:
    """Load typing.json for episode(s) and run OmniParser on each image."""
    ingestion = create_ingestion("keyboard", episodes_root, episode_name, "L2")
    for episode_dir in ingestion.resolve_episodes():
        typing_path, count = enrich_l2_typing_json(episode_dir)
        if not quiet:
            print(f"Updated {count} image path(s) in {typing_path}")

    result = ingestion.load_all()

    records: list[dict] = []
    for ep in result["episodes"]:
        records.extend(ep["data"].get("records", []))

    if not records:
        raise ValueError("No typing records with image paths found.")

    if output_root is None:
        if episode_name:
            output_root = episodes_root / episode_name
        elif len(result["episodes"]) == 1:
            output_root = Path(result["episodes"][0]["root"])
        else:
            output_root = ROOT

    output_root = output_root.resolve()
    if not quiet:
        print(f"Output root: {output_root}")
        batch_size = parse_kwargs.get("batch_size", DEFAULT_BATCH_SIZE)
        device_label = device or ("cuda" if _cuda_available() else "cpu")
        print(
            f"Loading models ({len(records)} image(s), "
            f"device={device_label}, batch_size={batch_size}) ..."
        )

    yolo_model, caption_model_processor = load_models(device)

    records_out, failures = annotate_records(
        records,
        output_root,
        device=device,
        yolo_model=yolo_model,
        caption_model_processor=caption_model_processor,
        quiet=quiet,
        **parse_kwargs,
    )

    index: dict = {
        "episodes_root": str(episodes_root),
        "level": "L2",
        "type": "keyboard",
    }
    if episode_name:
        index["episode"] = episode_name
    else:
        index["episodes"] = [ep["name"] for ep in result["episodes"]]

    index_path = output_root / ANNOTATED_DIR / JSON_DIR / INDEX_FILENAME
    index["annotated_json_index_path"] = str(index_path.resolve())
    index["record_count"] = len(records_out)
    index["records"] = records_out

    write_combined_json(index, index_path)

    for ep in result["episodes"]:
        set_annotated_json_path(Path(ep["root"]), index_path)
        if not quiet:
            print(
                f"Updated annotated JSON index path in "
                f"{Path(ep['root']) / 'processdata' / 'L2' / 'typing.json'}"
            )

    if not quiet:
        print(f"Index JSON -> {index_path}")
        print(f"Wrote {len(records_out)} per-record JSON file(s) in {index_path.parent}")

    if failures:
        print(f"{failures} image(s) failed.", file=sys.stderr)

    return index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run OmniParser on typing.json images and write annotated outputs.",
    )
    parser.add_argument(
        "--episodes-root",
        default=str(EPISODES_ROOT),
        help="Root folder containing episode subfolders",
    )
    parser.add_argument(
        "--episode",
        default=None,
        help="One episode name (default: all episodes with typing data)",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help="Where to write AnotatedData/ (typing PNGs + TypingAnotatedjson/) "
        "(default: episode folder, or project root for multiple episodes)",
    )
    add_ocr_engine_args(parser)
    parser.add_argument(
        "--box-threshold",
        type=float,
        default=BOX_THRESHOLD,
        help=f"Icon detection confidence cutoff (default {BOX_THRESHOLD})",
    )
    parser.add_argument(
        "--iou-threshold",
        type=float,
        default=IOU_THRESHOLD,
        help=f"Overlap cutoff for merging boxes (default {IOU_THRESHOLD})",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=IMGSZ,
        help=f"Detector input size (default {IMGSZ})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Caption batch size; lower to 16 or 8 if GPU OOMs "
        f"(default {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--device",
        choices=("cuda", "cpu"),
        default=None,
        help="Force cuda or cpu (default: cuda when available)",
    )
    parser.add_argument(
        "--cpu",
        action="store_true",
        help="Run on CPU (slower, but avoids CUDA/cuDNN errors)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print errors and final JSON path",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    episodes_root = Path(args.episodes_root)
    output_root = Path(args.output_root) if args.output_root else None
    device = "cpu" if args.cpu else args.device

    try:
        annotate_episode(
            episodes_root,
            args.episode,
            output_root,
            device=device,
            quiet=args.quiet,
            ocr_engine=resolve_ocr_engine(args),
            box_threshold=args.box_threshold,
            iou_threshold=args.iou_threshold,
            imgsz=args.imgsz,
            batch_size=args.batch_size,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
