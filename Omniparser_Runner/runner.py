"""Episode-level orchestration: discover episodes, extract, annotate, index."""

from __future__ import annotations

from pathlib import Path

from Omniparser_Runner.annotator import annotate_nodes
from Omniparser_Runner.config import (
    ANNOTATED_ROOT,
    INDEX_FILENAME,
    JSON_DIR,
    KEYBOARD,
    MOUSE,
    SCROLL,
    TIMELINE_RELPATH,
)
from Omniparser_Runner.models import cuda_available
from Omniparser_Runner.outputs import write_json
from Omniparser_Runner.timeline_reader import extract_nodes, load_timeline


def discover_episodes(episodes_root: Path) -> list[Path]:
    """Episode folders under ``episodes_root`` that have a timeline.json."""
    if not episodes_root.is_dir():
        raise FileNotFoundError(f"Episodes root not found: {episodes_root}")
    episodes = sorted(
        p for p in episodes_root.iterdir()
        if p.is_dir() and (p / TIMELINE_RELPATH).is_file()
    )
    if not episodes:
        raise ValueError(f"No episodes with {TIMELINE_RELPATH} in: {episodes_root}")
    return episodes


def extract_episode(
    episode_dir: Path,
    *,
    timeline_path: Path | None = None,
    output_root: Path | None = None,
    kinds: set[str] | None = None,
    device: str | None = None,
    quiet: bool = False,
    **parse_kwargs,
) -> dict:
    """Extract and annotate the typing / click / scroll nodes of one episode."""
    episode_dir = episode_dir.resolve()
    timeline_path = timeline_path or (episode_dir / TIMELINE_RELPATH)
    output_root = (output_root or episode_dir).resolve()

    timeline = load_timeline(timeline_path)
    nodes = extract_nodes(timeline, episode_dir)
    if kinds:
        nodes = [n for n in nodes if n.kind in kinds]

    typing_count = sum(1 for n in nodes if n.kind == KEYBOARD)
    mouse_count = sum(1 for n in nodes if n.kind == MOUSE)
    scroll_count = sum(1 for n in nodes if n.kind == SCROLL)

    if not quiet:
        device_label = device or ("cuda" if cuda_available() else "cpu")
        print(f"Episode: {episode_dir.name}")
        print(f"Timeline: {timeline_path}")
        print(f"Output root: {output_root / ANNOTATED_ROOT}")
        print(
            f"Nodes: {len(nodes)} ({typing_count} typing, {mouse_count} mouse, "
            f"{scroll_count} scroll) | device={device_label}"
        )

    if not nodes:
        raise ValueError(
            f"No typing, mouse-click or scroll nodes found in {timeline_path}"
        )

    entries, failures = annotate_nodes(
        nodes,
        output_root,
        device=device,
        quiet=quiet,
        **parse_kwargs,
    )

    index = {
        "episode_name": timeline.get("episode_name", episode_dir.name),
        "episode_dir": str(episode_dir),
        "timeline": str(timeline_path),
        "annotated_root": str(output_root / ANNOTATED_ROOT),
        "node_count": len(nodes),
        "typing_count": typing_count,
        "mouse_count": mouse_count,
        "scroll_count": scroll_count,
        "annotated_count": len(entries),
        "failures": failures,
        "records": entries,
    }
    index_path = write_json(
        index,
        output_root / ANNOTATED_ROOT / JSON_DIR / INDEX_FILENAME,
    )
    index["index_path"] = str(index_path)

    if not quiet:
        print(f"Wrote {len(entries)} record(s). Index -> {index_path}")
        if failures:
            print(f"{failures} image(s) failed.")

    return index
