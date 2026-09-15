"""
Episode data orchestrator.

Parent class that resolves episode paths and delegates loading to
type-specific child classes (keyboard, mouse, etc.).

Usage:
    python data_ingestion.py
    python data_ingestion.py --episode gourmet-tree --level L2 --type keyboard
"""

from __future__ import annotations

import argparse
import sys
from abc import ABC, abstractmethod
from pathlib import Path

# ===================== DEFAULT CONFIG =====================
# Edit this path to point at your episode root.
EPISODES_ROOT = Path(r"D:\My Desktop\Orca_Observation\episode")
DEFAULT_EPISODE = None          # e.g. "gourmet-tree"; None = all episodes
DEFAULT_LEVEL = "L2"            # "L1" or "L2"
DEFAULT_TYPE = "keyboard"
# ==========================================================

def _ingestion_classes() -> dict[str, type["DataIngestion"]]:
    from keyboard_data import KeyboardDataIngestion

    return {
        "keyboard": KeyboardDataIngestion,
    }


class DataIngestion(ABC):
    """Parent orchestrator — discovers episodes and loads processed data."""

    def __init__(
        self,
        episodes_root: Path,
        episode_name: str | None = None,
        level: str = "L2",
    ) -> None:
        self.episodes_root = episodes_root.resolve()
        self.episode_name = episode_name
        self.level = level

    def discover_episodes(self) -> list[Path]:
        """Return episode folders that contain rawdata/ and processdata/."""
        if not self.episodes_root.is_dir():
            raise FileNotFoundError(f"Episodes root not found: {self.episodes_root}")

        episodes = sorted(
            p for p in self.episodes_root.iterdir()
            if p.is_dir()
            and (p / "rawdata").is_dir()
            and (p / "processdata").is_dir()
        )
        if not episodes:
            raise ValueError(f"No episodes found in: {self.episodes_root}")
        return episodes

    def resolve_episodes(self) -> list[Path]:
        if self.episode_name:
            episode_dir = self.episodes_root / self.episode_name
            if not episode_dir.is_dir():
                raise FileNotFoundError(f"Episode not found: {episode_dir}")
            return [episode_dir]
        return self.discover_episodes()

    @abstractmethod
    def load(self, episode_dir: Path) -> dict:
        """Load data for one episode. Implemented by child classes."""
        raise NotImplementedError

    def load_all(self) -> dict:
        """Orchestrate loading across one or all episodes."""
        episode_dirs = self.resolve_episodes()
        episodes = [
            {
                "name": d.name,
                "root": str(d),
                "level": self.level,
                "data": self.load(d),
            }
            for d in episode_dirs
        ]
        return {
            "episodes_root": str(self.episodes_root),
            "level": self.level,
            "type": self.data_type,
            "episode_count": len(episodes),
            "episodes": episodes,
        }

    def collect_all_records(self) -> list[dict]:
        """Flatten text + image records from all loaded episodes."""
        result = self.load_all()
        all_records: list[dict] = []
        for ep in result["episodes"]:
            for rec in ep["data"].get("records", []):
                all_records.append({"episode": ep["name"], **rec})
        return all_records

    @property
    @abstractmethod
    def data_type(self) -> str:
        raise NotImplementedError


def create_ingestion(
    data_type: str,
    episodes_root: Path,
    episode_name: str | None = None,
    level: str = "L2",
) -> DataIngestion:
    classes = _ingestion_classes()
    cls = classes.get(data_type)
    if cls is None:
        available = ", ".join(sorted(classes))
        raise ValueError(f"Unknown type {data_type!r}. Available: {available}")
    return cls(episodes_root, episode_name, level)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load processed episode data from Orca Observation episodes.",
    )
    parser.add_argument(
        "--episodes-root",
        default=str(EPISODES_ROOT),
        help="Root folder containing episode subfolders",
    )
    parser.add_argument(
        "--episode",
        default=DEFAULT_EPISODE,
        help="One episode name, e.g. gourmet-tree (default: all episodes)",
    )
    parser.add_argument(
        "--level",
        choices=("L1", "L2"),
        default=DEFAULT_LEVEL,
        help="Processed-data layer to read",
    )
    parser.add_argument(
        "--type",
        default=DEFAULT_TYPE,
        help="Data type to load, e.g. keyboard",
    )
    return parser.parse_args()


def main() -> dict:
    args = parse_args()
    root = Path(args.episodes_root)

    try:
        ingestion = create_ingestion(args.type, root, args.episode, args.level)
        result = ingestion.load_all()
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Root: {result['episodes_root']}")
    print(f"Level: {result['level']}  Type: {result['type']}")
    print(f"Episodes: {result['episode_count']}\n")

    for ep in result["episodes"]:
        records = ep["data"].get("records", [])
        print(f"  {ep['name']}: {len(records)} records")
        for rec in records:
            print(f"    text={rec['text']!r}")
            print(f"    image={rec['image_path']}")

    return result


if __name__ == "__main__":
    main()
