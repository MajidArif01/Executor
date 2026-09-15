"""Run OmniParser over the extracted nodes and write their outputs."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from PIL import Image

from Omniparser_Runner.config import (
    BOX_THRESHOLD,
    DEFAULT_BATCH_SIZE,
    IMGSZ,
    IOU_THRESHOLD,
    OCR_ENGINE,
    TEMP_DIRNAME,
)
from Omniparser_Runner.models import (
    clear_gpu_memory,
    is_cuda_error,
    load_models,
    parse_via_subprocess,
    unload_models,
)
from Omniparser_Runner.outputs import (
    annotated_image_path,
    build_index_entry,
    build_record,
    image_dir_for,
    json_dir_for,
    output_dirs,
    record_json_path,
    write_annotated_image,
    write_json,
)
from Omniparser_Runner.timeline_reader import ExtractedNode
from omniparser.parse import run_parse

# Reduced batch size used once the CUDA context has been declared poisoned.
FALLBACK_BATCH_SIZE = 16


def annotate_nodes(
    nodes: list[ExtractedNode],
    output_root: Path,
    *,
    device: str | None = None,
    box_threshold: float = BOX_THRESHOLD,
    iou_threshold: float = IOU_THRESHOLD,
    imgsz: int = IMGSZ,
    batch_size: int = DEFAULT_BATCH_SIZE,
    ocr_engine: str = OCR_ENGINE,
    quiet: bool = False,
) -> tuple[list[dict], int]:
    """Parse each node's screenshot; write PNGs and per-node JSON.

    Returns ``(index_entries, failure_count)``.
    """
    dirs = output_dirs(output_root)
    temp_dir = output_root / TEMP_DIRNAME
    parse_kwargs = {
        "box_threshold": box_threshold,
        "iou_threshold": iou_threshold,
        "imgsz": imgsz,
        "ocr_engine": ocr_engine,
    }

    entries: list[dict] = []
    failures = 0
    cuda_poisoned = False
    yolo_model = caption_model_processor = None
    models_loaded = False

    for node in nodes:
        if not node.image_path.is_file():
            print(
                f"warning: image not found, skipping: {node.image_path}",
                file=sys.stderr,
            )
            failures += 1
            continue

        started = time.time()
        elements: dict | None = None
        annotated: Image.Image | None = None
        annotated_from_subprocess: Path | None = None

        if cuda_poisoned:
            parsed = parse_via_subprocess(
                node.image_path,
                temp_dir,
                batch_size=min(batch_size, FALLBACK_BATCH_SIZE),
                **parse_kwargs,
            )
            if parsed is not None:
                elements, annotated_from_subprocess = parsed
        else:
            if not models_loaded:
                yolo_model, caption_model_processor = load_models(device)
                models_loaded = True

            try:
                with Image.open(node.image_path) as handle:
                    image = handle.convert("RGB")
                result = run_parse(
                    image,
                    source=node.image_path,
                    yolo_model=yolo_model,
                    caption_model_processor=caption_model_processor,
                    batch_size=batch_size,
                    **parse_kwargs,
                )
                elements = result.elements
                annotated = result.annotated
            except Exception as exc:
                print(
                    f"Error: failed to parse {node.image_path}: {exc}",
                    file=sys.stderr,
                )
                if not is_cuda_error(exc):
                    failures += 1
                    continue

                cuda_poisoned = True
                if models_loaded:
                    unload_models(yolo_model, caption_model_processor)
                    yolo_model = caption_model_processor = None
                    models_loaded = False
                if not quiet:
                    print(
                        "CUDA error detected; retrying this and the remaining "
                        "images in isolated subprocesses.",
                        file=sys.stderr,
                    )
                parsed = parse_via_subprocess(
                    node.image_path,
                    temp_dir,
                    batch_size=min(batch_size, FALLBACK_BATCH_SIZE),
                    **parse_kwargs,
                )
                if parsed is not None:
                    elements, annotated_from_subprocess = parsed

        if elements is None:
            failures += 1
            continue

        annotated_path: Path | None = annotated_image_path(
            image_dir_for(dirs, node), node
        )
        if annotated_from_subprocess is not None:
            annotated_path.write_bytes(annotated_from_subprocess.read_bytes())
        elif annotated is not None:
            write_annotated_image(annotated, annotated_path)
        else:
            print(
                f"warning: no annotated PNG for {node.image_path.name}; "
                f"elements saved only.",
                file=sys.stderr,
            )
            annotated_path = None

        record_path = write_json(
            build_record(node, elements, annotated_path),
            record_json_path(json_dir_for(dirs, node), node),
        )
        entries.append(build_index_entry(node, annotated_path, record_path))

        if not quiet:
            interactive = sum(1 for e in elements.values() if e.get("interactivity"))
            print(
                f"[{node.kind}] node {node.index} {node.value!r}: "
                f"{len(elements)} elements ({interactive} interactive) "
                f"in {time.time() - started:.1f}s -> {record_path.name}"
            )

        if not cuda_poisoned:
            clear_gpu_memory()

    if models_loaded:
        unload_models(yolo_model, caption_model_processor)

    return entries, failures
