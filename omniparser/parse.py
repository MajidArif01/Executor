
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

# Allow both `python omniparser/parse.py` and `python -m omniparser.parse`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omniparser.util.utils import (  # noqa: E402
    check_ocr_box,
    get_caption_model_processor,
    get_som_labeled_img,
    get_yolo_model,
)

# ===================== DEFAULT CONFIG =====================
# Edit these to run with no command-line arguments: `python omniparser/parse.py`
# Any argument passed on the command line overrides the matching value here.

# Image file, or a folder of images, to parse by default.
IMAGE_PATH = Path(r"C:\Users\majid\Desktop\Executor\20260831_104421_720732_screen1.png")

USE_PADDLEOCR = False    # deprecated: superseded by OCR_ENGINE below
OCR_ENGINE = "rapidocr"   # easyocr | paddleocr | rapidocr
OCR_ENGINES = ("easyocr", "paddleocr", "rapidocr")

# RapidOCR tuning, used when OCR_ENGINE == "rapidocr". Keys are passed verbatim
# to RapidOCR(params=...), so anything in rapidocr's own config.yaml is
# settable here (.venv/Lib/site-packages/rapidocr/config.yaml).
RAPIDOCR_PARAMS = {
    # Recognition confidence cutoff. NOT the same knob as TEXT_THRESHOLD below,
    # which is EasyOCR's detection-heatmap threshold. Raise toward 0.6-0.7 to
    # drop 1-char noise fragments; lower it for more recall.
    "Global.text_score": 0.5,

    # Detection sensitivity. Lower finds more (and smaller) text regions.
    "Det.box_thresh": 0.3,
    "Det.thresh": 0.3,

    # ---- model selection ----
    # model_type is version-dependent:
    #   PP-OCRv6 -> tiny | small | medium
    #   PP-OCRv4/v5 -> mobile | server
    "Det.ocr_version": "PP-OCRv6",
    "Det.model_type": "small",
    "Rec.ocr_version": "PP-OCRv6",
    "Rec.model_type": "small",

    # NOTE: at PP-OCRv6, Det/Rec.lang_type is validated but IGNORED -- the
    # multilingual model is always loaded (hence the occasional CJK misread).
    # For a Latin-only charset, pin v4 and set lang_type:
    #   "Det.ocr_version": "PP-OCRv4", "Det.lang_type": "en",
    #   "Det.model_type": "mobile",
    #   "Rec.ocr_version": "PP-OCRv4", "Rec.lang_type": "en",
    #   "Rec.model_type": "mobile",
    # Or point at your own ONNX file, bypassing model resolution entirely:
    #   "Det.model_path": r"C:\path\to\det.onnx",

    # Run OCR on the GPU; requires `pip install onnxruntime-gpu`.
    # Worth trying: RapidOCR on CPU measured 7.2s vs EasyOCR-on-CUDA 3.8s.
    # "EngineConfig.onnxruntime.use_cuda": True,
}

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
BOX_THRESHOLD = 0.05
IOU_THRESHOLD = 0.1
IMGSZ = 640
BATCH_SIZE = 128
TEXT_THRESHOLD = 0.9
# ==========================================================


@dataclass
class ParseResult:
    """In-memory output from one OmniParser run."""

    source: Path | None
    elements: dict
    annotated: Image.Image | None = None


def collect_images(paths: list[str]) -> list[Path]:
    """Expand files and directories into a sorted list of image paths."""
    found: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            found.extend(
                sorted(c for c in p.iterdir() if c.suffix.lower() in IMAGE_EXT)
            )
        elif p.is_file():
            found.append(p)
        else:
            print(f"warning: no such path, skipping: {p}", file=sys.stderr)
    return found


def draw_config(width: int) -> dict:
    """Scale the annotation overlay to the image, as gradio_demo.py does."""
    ratio = width / 3200
    return {
        "text_scale": 0.8 * ratio,
        "text_thickness": max(int(2 * ratio), 1),
        "text_padding": max(int(3 * ratio), 1),
        "thickness": max(int(3 * ratio), 1),
    }


def parse_image(
    image: Image.Image,
    yolo_model,
    caption_model_processor,
    box_threshold: float,
    iou_threshold: float,
    imgsz: int,
    batch_size: int,
    use_paddleocr: bool,
    ocr_engine: str | None = None,
) -> tuple[dict, Image.Image]:
    """Return ({"icon N": element}, annotated_image) for one screenshot."""
    (ocr_text, ocr_bbox), _ = check_ocr_box(
        image,
        output_bb_format="xyxy",
        goal_filtering=None,
        easyocr_args={"paragraph": False, "text_threshold": TEXT_THRESHOLD},
        use_paddleocr=use_paddleocr,
        ocr_engine=ocr_engine,
        rapidocr_params=RAPIDOCR_PARAMS,
    )

    labeled_img, _, parsed_content_list = get_som_labeled_img(
        image,
        yolo_model,
        BOX_TRESHOLD=box_threshold,
        output_coord_in_ratio=True,
        ocr_bbox=ocr_bbox,
        draw_bbox_config=draw_config(image.size[0]),
        caption_model_processor=caption_model_processor,
        ocr_text=ocr_text,
        iou_threshold=iou_threshold,
        imgsz=imgsz,
        batch_size=batch_size,
    )

    elements = {f"icon {i}": elem for i, elem in enumerate(parsed_content_list)}
    annotated = Image.open(io.BytesIO(base64.b64decode(labeled_img)))
    return elements, annotated


def run_parse(
    image: Image.Image,
    *,
    source: Path | None = None,
    yolo_model=None,
    caption_model_processor=None,
    box_threshold: float = BOX_THRESHOLD,
    iou_threshold: float = IOU_THRESHOLD,
    imgsz: int = IMGSZ,
    batch_size: int = BATCH_SIZE,
    use_paddleocr: bool = USE_PADDLEOCR,
    ocr_engine: str | None = None,
) -> ParseResult:
    """Parse one image and return in-memory results (no file I/O)."""
    if yolo_model is None:
        yolo_model = get_yolo_model()
    if caption_model_processor is None:
        caption_model_processor = get_caption_model_processor()

    elements, annotated = parse_image(
        image,
        yolo_model,
        caption_model_processor,
        box_threshold,
        iou_threshold,
        imgsz,
        batch_size,
        use_paddleocr,
        ocr_engine,
    )
    return ParseResult(source=source, elements=elements, annotated=annotated)


def add_ocr_engine_args(parser: argparse.ArgumentParser) -> None:
    """Add the --ocr-engine flag plus its deprecated --paddleocr alias."""
    parser.add_argument(
        "--ocr-engine",
        choices=OCR_ENGINES,
        default=None,
        help=f"OCR engine to use (default {OCR_ENGINE})",
    )
    parser.add_argument(
        "--paddleocr",
        action=argparse.BooleanOptionalAction,
        default=USE_PADDLEOCR,
        help="Deprecated alias for --ocr-engine paddleocr",
    )


def resolve_ocr_engine(args: argparse.Namespace) -> str:
    """--ocr-engine wins; fall back to the legacy --paddleocr boolean."""
    if getattr(args, "ocr_engine", None):
        return args.ocr_engine
    return "paddleocr" if getattr(args, "paddleocr", False) else OCR_ENGINE


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run OmniParser inference on screenshots (stdout JSON only). "
        "Use ExecutionLayer/run_parse.py to write JSON and annotated PNGs.",
    )
    parser.add_argument(
        "images",
        nargs="*",
        default=[str(IMAGE_PATH)],
        help=f"Image files and/or folders of images to parse "
        f"(default: {IMAGE_PATH})",
    )
    add_ocr_engine_args(parser)
    parser.add_argument(
        "--box-threshold", type=float, default=BOX_THRESHOLD,
        help=f"Icon detection confidence cutoff (default {BOX_THRESHOLD})",
    )
    parser.add_argument(
        "--iou-threshold", type=float, default=IOU_THRESHOLD,
        help=f"Overlap cutoff for merging boxes (default {IOU_THRESHOLD})",
    )
    parser.add_argument(
        "--imgsz", type=int, default=IMGSZ,
        help=f"Detector input size (default {IMGSZ})",
    )
    parser.add_argument(
        "--batch-size", type=int, default=BATCH_SIZE,
        help=f"Caption batch size; lower it if the GPU OOMs (default {BATCH_SIZE})",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress progress messages on stderr",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ocr_engine = resolve_ocr_engine(args)

    images = collect_images(args.images)
    if not images:
        print(
            f"Error: no images found in {', '.join(args.images)}.\n"
            f"Pass a path on the command line, or edit IMAGE_PATH at the top "
            f"of {Path(__file__).name}.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not args.quiet:
        print(f"Loading models ({len(images)} image(s) to parse) ...", file=sys.stderr)

    yolo_model = get_yolo_model()
    caption_model_processor = get_caption_model_processor()

    results: list[dict] = []
    failures = 0

    for path in images:
        try:
            with Image.open(path) as handle:
                image = handle.convert("RGB")
        except Exception as exc:
            print(f"Error: cannot read {path}: {exc}", file=sys.stderr)
            failures += 1
            continue

        started = time.time()
        try:
            parsed = run_parse(
                image,
                source=path,
                yolo_model=yolo_model,
                caption_model_processor=caption_model_processor,
                box_threshold=args.box_threshold,
                iou_threshold=args.iou_threshold,
                imgsz=args.imgsz,
                batch_size=args.batch_size,
                ocr_engine=ocr_engine,
            )
        except Exception as exc:
            print(f"Error: failed to parse {path}: {exc}", file=sys.stderr)
            failures += 1
            continue

        payload = {
            "source": str(path),
            "elements": parsed.elements,
        }
        results.append(payload)

        if not args.quiet:
            interactive = sum(
                1 for e in parsed.elements.values() if e.get("interactivity")
            )
            print(
                f"{path.name}: {len(parsed.elements)} elements "
                f"({interactive} interactive) in {time.time() - started:.1f}s",
                file=sys.stderr,
            )

    if failures:
        print(f"{failures} image(s) failed.", file=sys.stderr)
        sys.exit(1)

    if len(results) == 1:
        json.dump(results[0], sys.stdout, indent=2, ensure_ascii=False)
    else:
        json.dump(results, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
