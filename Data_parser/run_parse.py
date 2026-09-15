
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from Data_parser.write_outputs import write_parse_result  # noqa: E402
from omniparser.parse import (  # noqa: E402
    BATCH_SIZE,
    BOX_THRESHOLD,
    IMGSZ,
    IOU_THRESHOLD,
    add_ocr_engine_args,
    collect_images,
    resolve_ocr_engine,
    run_parse,
)
from omniparser.util.utils import (  # noqa: E402
    get_caption_model_processor,
    get_yolo_model,
)

# ===================== DEFAULT CONFIG =====================
IMAGE_PATH = Path(r"C:\Users\majid\Desktop\Executor\20260831_104421_720732_screen1.png")
OUTPUT_DIR = None
SAVE_ANNOTATED = True
# ==========================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse screenshots with OmniParser and write JSON + annotated PNGs.",
    )
    parser.add_argument(
        "images",
        nargs="*",
        default=[str(IMAGE_PATH)],
        help=f"Image files and/or folders (default: {IMAGE_PATH})",
    )
    parser.add_argument(
        "--out",
        default=OUTPUT_DIR,
        help="Output directory. Default: beside each image (shot.png -> shot.json)",
    )
    parser.add_argument(
        "--annotated",
        action=argparse.BooleanOptionalAction,
        default=SAVE_ANNOTATED,
        help="Save <name>_annotated.png overlays (use --no-annotated to skip)",
    )
    add_ocr_engine_args(parser)
    parser.add_argument(
        "--box-threshold", type=float, default=BOX_THRESHOLD,
    )
    parser.add_argument(
        "--iou-threshold", type=float, default=IOU_THRESHOLD,
    )
    parser.add_argument(
        "--imgsz", type=int, default=IMGSZ,
    )
    parser.add_argument(
        "--batch-size", type=int, default=BATCH_SIZE,
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Only print paths written",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ocr_engine = resolve_ocr_engine(args)
    images = collect_images(args.images)
    if not images:
        print(f"Error: no images found in {', '.join(args.images)}.", file=sys.stderr)
        sys.exit(1)

    if not args.quiet:
        print(f"Loading models ({len(images)} image(s)) ...")

    yolo_model = get_yolo_model()
    caption_model_processor = get_caption_model_processor()
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
            result = run_parse(
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
            json_path, annotated_path = write_parse_result(
                result,
                out_dir=args.out,
                save_annotated=args.annotated,
            )
        except Exception as exc:
            print(f"Error: failed on {path}: {exc}", file=sys.stderr)
            failures += 1
            continue

        written = [str(json_path)]
        if annotated_path is not None:
            written.append(str(annotated_path))

        if args.quiet:
            print("\n".join(written))
        else:
            interactive = sum(
                1 for e in result.elements.values() if e.get("interactivity")
            )
            print(
                f"{path.name}: {len(result.elements)} elements "
                f"({interactive} interactive) in {time.time() - started:.1f}s "
                f"-> {' , '.join(written)}"
            )

    if failures:
        print(f"{failures} image(s) failed.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
