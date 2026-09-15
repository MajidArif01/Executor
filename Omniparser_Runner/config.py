"""Shared configuration: paths, output layout and parse defaults."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from omniparser.parse import (  # noqa: E402
    BOX_THRESHOLD,
    IMGSZ,
    IOU_THRESHOLD,
    OCR_ENGINE,
    OCR_ENGINES,
    USE_PADDLEOCR,
)

# ===================== DEFAULT CONFIG =====================
# Edit these to run with no command-line arguments.
EPISODES_ROOT = Path(r"D:\My Desktop\Orca_Observation\episode")
DEFAULT_EPISODE = None          # e.g. "gourmet-tree"; None = every episode
# ==========================================================

# Where each episode's timeline lives, relative to the episode root.
TIMELINE_RELPATH = Path("processdata") / "timeline.json"

# Output layout, created under the episode root:
#   AnotatedData/
#       typing/                 annotated PNGs for typing nodes
#       mouse/                  annotated PNGs for mouse-click nodes
#       scroll/                 annotated PNGs for mouse-scroll nodes
#       AnotatedJson/
#           keyboard/           one JSON per typing node
#           mouse/              one JSON per mouse-click node
#           scroll/             one JSON per mouse-scroll node
#           extractor_index.json
ANNOTATED_ROOT = "AnotatedData"
TYPING_IMAGE_DIR = "typing"
MOUSE_IMAGE_DIR = "mouse"
SCROLL_IMAGE_DIR = "scroll"
JSON_DIR = "AnotatedJson"
KEYBOARD_JSON_DIR = "keyboard"
MOUSE_JSON_DIR = "mouse"
SCROLL_JSON_DIR = "scroll"
INDEX_FILENAME = "extractor_index.json"
ANNOTATED_SUFFIX = "_anotated"
TEMP_DIRNAME = ".extractor_parse_tmp"

# Timeline node types this runner cares about.
TYPING_TYPE = "Typing"
CLICK_TYPE = "click"
SCROLL_TYPE = "scroll"

# Node kinds used throughout: "keyboard" (typing), "mouse" (clicks) and
# "scroll" (wheel events).
KEYBOARD = "keyboard"
MOUSE = "mouse"
SCROLL = "scroll"

# Florence captioning at 128 can use ~4 GB; keep multi-image runs modest.
DEFAULT_BATCH_SIZE = 32

__all__ = [
    "ROOT",
    "EPISODES_ROOT",
    "DEFAULT_EPISODE",
    "TIMELINE_RELPATH",
    "ANNOTATED_ROOT",
    "TYPING_IMAGE_DIR",
    "MOUSE_IMAGE_DIR",
    "SCROLL_IMAGE_DIR",
    "JSON_DIR",
    "KEYBOARD_JSON_DIR",
    "MOUSE_JSON_DIR",
    "SCROLL_JSON_DIR",
    "INDEX_FILENAME",
    "ANNOTATED_SUFFIX",
    "TEMP_DIRNAME",
    "TYPING_TYPE",
    "CLICK_TYPE",
    "SCROLL_TYPE",
    "KEYBOARD",
    "MOUSE",
    "SCROLL",
    "DEFAULT_BATCH_SIZE",
    "BOX_THRESHOLD",
    "IOU_THRESHOLD",
    "IMGSZ",
    "USE_PADDLEOCR",
    "OCR_ENGINE",
    "OCR_ENGINES",
]
