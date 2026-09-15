
"""Parse a single image in an isolated process (for CUDA recovery)."""

from __future__ import annotations

import argparse
import sys
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
    resolve_ocr_engine,
    run_parse,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse one image and write outputs (subprocess worker).",
    )
    parser.add_argument("image", help="Screenshot to parse")
    parser.add_argument(
        "--out",
        required=True,
        help="Directory to write JSON and annotated PNG",
    )
    parser.add_argument(
        "--annotated",
        action=argparse.BooleanOptionalAction,
        default=True,
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
        help="Print written paths on stdout",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    img_path = Path(args.image)
    if not img_path.is_file():
        print(f"Error: image not found: {img_path}", file=sys.stderr)
        sys.exit(1)

    with Image.open(img_path) as handle:
        image = handle.convert("RGB")

    result = run_parse(
        image,
        source=img_path,
        box_threshold=args.box_threshold,
        iou_threshold=args.iou_threshold,
        imgsz=args.imgsz,
        batch_size=args.batch_size,
        ocr_engine=resolve_ocr_engine(args),
    )
    json_path, annotated_path = write_parse_result(
        result,
        out_dir=args.out,
        save_annotated=args.annotated,
    )

    if args.quiet:
        lines = [str(json_path)]
        if annotated_path is not None:
            lines.append(str(annotated_path))
        print("\n".join(lines))


if __name__ == "__main__":
    main()
