"""
OmniParser Runner
=================

Reads an episode's ``processdata/timeline.json``, keeps the typing, mouse-click
and mouse-scroll nodes, picks one screenshot per node, and runs OmniParser on it.

Which screenshot each node contributes:
  * ``Typing``  -> the LAST image of the node (the screen after the full string
                   has been typed).
  * ``click``   -> the image whose ``screen`` matches the click event's screen
                   (the display the user actually clicked on).
  * ``scroll``  -> the image whose ``screen`` matches the scroll event's screen
                   (the display the wheel event landed on).

Outputs, written under the episode root::

    AnotatedData/
        typing/                 annotated PNGs for typing nodes
        mouse/                  annotated PNGs for mouse-click nodes
        scroll/                 annotated PNGs for mouse-scroll nodes
        AnotatedJson/
            keyboard/           one JSON per typing node
            mouse/              one JSON per mouse-click node
            scroll/             one JSON per mouse-scroll node
            extractor_index.json

Modules:
  * ``config``           paths, output layout, parse defaults
  * ``timeline_reader``  timeline.json -> ``ExtractedNode`` list
  * ``outputs``          folder layout and JSON/PNG writers
  * ``models``           model loading, GPU hygiene, CUDA-recovery subprocess
  * ``annotator``        runs OmniParser over the nodes
  * ``runner``           per-episode orchestration
  * ``cli``              argument parsing and ``main()``

Usage::

    python -m Omniparser_Runner
    python -m Omniparser_Runner --episode gourmet-tree
    python -m Omniparser_Runner --episode-dir "D:/.../episode/gourmet-tree"
    python -m Omniparser_Runner --timeline "D:/.../processdata/timeline.json"
    python -m Omniparser_Runner --types mouse
    python -m Omniparser_Runner --types scroll
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the repository root importable so `omniparser` resolves no matter the cwd.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

__all__ = [
    "annotator",
    "cli",
    "config",
    "models",
    "outputs",
    "runner",
    "timeline_reader",
]
