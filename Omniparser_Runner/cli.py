"""Command-line entry point for the OmniParser timeline runner."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

from Omniparser_Runner.config import (
    ANNOTATED_ROOT,
    BOX_THRESHOLD,
    DEFAULT_BATCH_SIZE,
    DEFAULT_EPISODE,
    EPISODES_ROOT,
    IMGSZ,
    IOU_THRESHOLD,
    KEYBOARD,
    MOUSE,
    SCROLL,
)
from Omniparser_Runner.outputs import write_json
from Omniparser_Runner.runner import discover_episodes, extract_episode
from omniparser.parse import add_ocr_engine_args, resolve_ocr_engine, run_parse

KIND_ALIASES = {
    "typing": KEYBOARD,
    "keyboard": KEYBOARD,
    "mouse": MOUSE,
    "click": MOUSE,
    "scroll": SCROLL,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m Omniparser_Runner",
        description="Extract typing / mouse-click / mouse-scroll nodes from "
        "timeline.json and annotate their screenshots with OmniParser.",
    )
    parser.add_argument(
        "--episodes-root", default=str(EPISODES_ROOT),
        help="Root folder containing episode subfolders",
    )
    parser.add_argument(
        "--episode", default=DEFAULT_EPISODE,
        help="One episode name (default: every episode under --episodes-root)",
    )
    parser.add_argument(
        "--episode-dir", default=None,
        help="Path to a single episode folder (overrides --episodes-root/--episode)",
    )
    parser.add_argument(
        "--timeline", default=None,
        help="Path to a timeline.json (its episode root is inferred as ../..)",
    )
    parser.add_argument(
        "--output-root", default=None,
        help=f"Where {ANNOTATED_ROOT}/ is created (default: the episode folder)",
    )
    parser.add_argument(
        "--types", default="typing,mouse,scroll",
        help="Comma-separated node kinds to process: typing, mouse, scroll "
             "(default: all three)",
    )
    parser.add_argument("--device", default=None, help="cuda, cuda:0 or cpu")
    add_ocr_engine_args(parser)
    parser.add_argument("--box-threshold", type=float, default=BOX_THRESHOLD)
    parser.add_argument("--iou-threshold", type=float, default=IOU_THRESHOLD)
    parser.add_argument("--imgsz", type=int, default=IMGSZ)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--quiet", action="store_true")

    # Internal: single-image worker used for CUDA recovery.
    parser.add_argument("--parse-one", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--parse-one-out", default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def resolve_kinds(raw: str) -> set[str]:
    kinds = set()
    for token in raw.split(","):
        token = token.strip().lower()
        if not token:
            continue
        if token not in KIND_ALIASES:
            raise ValueError(
                f"Unknown node kind {token!r} (use typing, mouse and/or scroll)"
            )
        kinds.add(KIND_ALIASES[token])
    if not kinds:
        raise ValueError("--types selected nothing")
    return kinds


def resolve_jobs(args: argparse.Namespace) -> list[tuple[Path, Path | None]]:
    """Return the (episode_dir, timeline_path | None) pairs to process."""
    if args.timeline:
        timeline_path = Path(args.timeline).resolve()
        episode_dir = (
            Path(args.episode_dir).resolve()
            if args.episode_dir
            else timeline_path.parents[1]
        )
        return [(episode_dir, timeline_path)]
    if args.episode_dir:
        return [(Path(args.episode_dir).resolve(), None)]
    if args.episode:
        return [((Path(args.episodes_root) / args.episode).resolve(), None)]
    return [(d, None) for d in discover_episodes(Path(args.episodes_root))]


def run_parse_one_worker(args: argparse.Namespace) -> None:
    """Parse a single image in this process and write JSON + annotated PNG."""
    img_path = Path(args.parse_one)
    out_dir = Path(args.parse_one_out or img_path.parent)
    out_dir.mkdir(parents=True, exist_ok=True)

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
    write_json(result.elements, out_dir / f"{img_path.stem}.json")
    if result.annotated is not None:
        result.annotated.save(out_dir / f"{img_path.stem}_annotated.png")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    if args.parse_one:
        run_parse_one_worker(args)
        return

    try:
        kinds = resolve_kinds(args.types)
        jobs = resolve_jobs(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    parse_kwargs = {
        "box_threshold": args.box_threshold,
        "iou_threshold": args.iou_threshold,
        "imgsz": args.imgsz,
        "batch_size": args.batch_size,
        "ocr_engine": resolve_ocr_engine(args),
    }

    output_root = Path(args.output_root).resolve() if args.output_root else None
    failed_episodes = 0

    for episode_dir, timeline_path in jobs:
        try:
            extract_episode(
                episode_dir,
                timeline_path=timeline_path,
                output_root=output_root,
                kinds=kinds,
                device=args.device,
                quiet=args.quiet,
                **parse_kwargs,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"Error [{episode_dir.name}]: {exc}", file=sys.stderr)
            failed_episodes += 1

    if failed_episodes:
        sys.exit(1)
