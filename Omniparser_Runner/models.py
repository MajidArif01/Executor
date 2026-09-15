"""Model loading, GPU hygiene, and the isolated-subprocess CUDA fallback."""

from __future__ import annotations

import gc
import json
import subprocess
import sys
from pathlib import Path

from Omniparser_Runner.config import ROOT
from omniparser.util.utils import get_caption_model_processor, get_yolo_model

PACKAGE = "Omniparser_Runner"


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


def cuda_available() -> bool:
    try:
        import torch

        return torch.cuda.is_available()
    except Exception:
        return False


def load_models(device: str | None = None) -> tuple:
    clear_gpu_memory()
    return get_yolo_model(device=device), get_caption_model_processor(device=device)


def unload_models(yolo_model, caption_model_processor) -> None:
    del yolo_model, caption_model_processor
    clear_gpu_memory()


def parse_via_subprocess(
    img_path: Path,
    temp_dir: Path,
    *,
    batch_size: int,
    box_threshold: float,
    iou_threshold: float,
    imgsz: int,
    ocr_engine: str,
) -> tuple[dict, Path | None] | None:
    """Parse one image in a fresh process, to recover from a poisoned CUDA context.

    Returns ``(elements, annotated_png | None)``, or None if the worker failed.
    """
    temp_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", PACKAGE,
        "--parse-one", str(img_path),
        "--parse-one-out", str(temp_dir),
        "--batch-size", str(batch_size),
        "--box-threshold", str(box_threshold),
        "--iou-threshold", str(iou_threshold),
        "--imgsz", str(imgsz),
        "--ocr-engine", ocr_engine,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "subprocess failed").strip()
        print(f"Error: subprocess parse failed for {img_path}: {err}", file=sys.stderr)
        return None

    json_path = temp_dir / f"{img_path.stem}.json"
    if not json_path.is_file():
        print(f"Error: expected JSON not found: {json_path}", file=sys.stderr)
        return None

    with open(json_path, encoding="utf-8") as f:
        elements = json.load(f)

    annotated_path = temp_dir / f"{img_path.stem}_annotated.png"
    return elements, annotated_path if annotated_path.is_file() else None
