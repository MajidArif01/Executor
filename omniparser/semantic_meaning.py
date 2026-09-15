"""Describe a screenshot using the Florence caption model only (no YOLO/OCR)."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import torch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from omniparser.util.utils import get_caption_model_processor  # noqa: E402

DEFAULT_IMAGE = Path(r"C:\Users\majid\Desktop\Executor\20260831_104421_720732_screen1.png")
DEFAULT_PROMPT = "<DETAILED_CAPTION>"

FLORENCE_TASKS = {
    "caption": "<CAPTION>",
    "detailed": "<DETAILED_CAPTION>",
    "more-detailed": "<MORE_DETAILED_CAPTION>",
}

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


def normalize_prompt(prompt: str) -> str:
    """
    Accept Florence task tokens or plain English questions.

    Plain text like "what page is this?" is wrapped as ``<VQA>...``.
    Shorthand aliases: caption, detailed, more-detailed.
    """
    value = (prompt or "").strip()
    if not value:
        return DEFAULT_PROMPT

    alias = FLORENCE_TASKS.get(value.lower())
    if alias is not None:
        return alias

    if value.startswith("<") and ">" in value:
        return value

    return f"<VQA>{value}"


@torch.inference_mode()
def describe_image(
    image: Image.Image,
    caption_model_processor: dict,
    *,
    prompt: str = DEFAULT_PROMPT,
    max_new_tokens: int = 1024,
) -> str:
    """Run Florence on one full image and return a text description."""
    model = caption_model_processor["model"]
    processor = caption_model_processor["processor"]
    device = model.device

    if model.device.type == "cuda":
        inputs = processor(
            images=image,
            text=prompt,
            return_tensors="pt",
        ).to(device=device, dtype=torch.float16)
    else:
        inputs = processor(
            images=image,
            text=prompt,
            return_tensors="pt",
        ).to(device=device)

    generated_ids = model.generate(
        input_ids=inputs["input_ids"],
        pixel_values=inputs["pixel_values"],
        max_new_tokens=max_new_tokens,
        num_beams=1,
        do_sample=False,
    )

    text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
    return text.strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Describe an image with the Florence caption model.",
    )
    parser.add_argument(
        "image",
        nargs="?",
        default=str(DEFAULT_IMAGE),
        help=f"Image path (default: {DEFAULT_IMAGE})",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help=(
            "Florence task token (<CAPTION>, <DETAILED_CAPTION>, ...), "
            "a plain question (auto-wrapped as <VQA>...), "
            "or shorthand: caption, detailed, more-detailed"
        ),
    )
    parser.add_argument(
        "--device",
        choices=("cuda", "cpu"),
        default=None,
        help="Force device (default: cuda if available)",
    )
    parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=1024,
        help="Max tokens to generate",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    image_path = Path(args.image)

    if not image_path.is_file():
        print(f"Error: image not found: {image_path}", file=sys.stderr)
        sys.exit(1)
    if image_path.suffix.lower() not in IMAGE_EXT:
        print(f"Error: unsupported image type: {image_path.suffix}", file=sys.stderr)
        sys.exit(1)

    print("Loading Florence model...")
    caption_model_processor = get_caption_model_processor(device=args.device)

    prompt = normalize_prompt(args.prompt)

    with Image.open(image_path) as handle:
        image = handle.convert("RGB")

    started = time.time()
    description = describe_image(
        image,
        caption_model_processor,
        prompt=prompt,
        max_new_tokens=args.max_new_tokens,
    )
    elapsed = time.time() - started

    print(f"\nImage: {image_path}")
    print(f"Prompt: {prompt}")
    print(f"Time: {elapsed:.1f}s\n")
    print("Description:")
    print(description)


if __name__ == "__main__":
    main()
