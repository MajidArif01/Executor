"""L2 keyboard/typing data loader — child of DataIngestion."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterator

from data_ingestion import DataIngestion


def enrich_l2_typing_json(episode_dir: Path) -> tuple[Path, int]:
    """Write resolved ``image_path`` into each item in processdata/L2/typing.json."""
    typing_path = episode_dir / "processdata" / "L2" / KeyboardDataIngestion.L2_FILENAME
    if not typing_path.is_file():
        raise FileNotFoundError(f"L2 typing data not found: {typing_path}")

    with open(typing_path, encoding="utf-8") as f:
        payload = json.load(f)

    updated = 0
    for item in payload.get("items", []):
        rel_image = item.get("last_image_path")
        if not rel_image:
            continue
        item["image_path"] = str((episode_dir / rel_image).resolve())
        updated += 1

    with open(typing_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return typing_path, updated


def set_annotated_json_path(episode_dir: Path, annotated_json_path: Path) -> Path:
    """Write the annotated JSON output path into processdata/L2/typing.json."""
    typing_path = episode_dir / "processdata" / "L2" / KeyboardDataIngestion.L2_FILENAME
    if not typing_path.is_file():
        raise FileNotFoundError(f"L2 typing data not found: {typing_path}")

    with open(typing_path, encoding="utf-8") as f:
        payload = json.load(f)

    payload["annotated_json_path"] = str(annotated_json_path.resolve())

    with open(typing_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return typing_path


def update_typing_json_annotated_images(
    episode_dir: Path,
    updates: list[dict],
) -> tuple[Path, int]:
    """Write ``annotated_image`` paths into matching items in processdata/L2/typing.json."""
    typing_path = episode_dir / "processdata" / "L2" / KeyboardDataIngestion.L2_FILENAME
    if not typing_path.is_file():
        raise FileNotFoundError(f"L2 typing data not found: {typing_path}")

    with open(typing_path, encoding="utf-8") as f:
        payload = json.load(f)

    items = payload.get("items", [])
    updated = 0

    for update in updates:
        annotated_image = update.get("annotated_image")
        if not annotated_image:
            continue

        node_id = update.get("node_id")
        image_path = Path(update["image_path"]).resolve()
        matched = False

        for item in items:
            if node_id is not None and item.get("node_id") == node_id:
                item["annotated_image"] = annotated_image
                updated += 1
                matched = True
                break

            rel_image = item.get("last_image_path")
            if rel_image and (episode_dir / rel_image).resolve() == image_path:
                item["annotated_image"] = annotated_image
                updated += 1
                matched = True
                break

        if not matched:
            print(
                f"warning: no typing.json item matched for annotated image: {image_path}",
                file=sys.stderr,
            )

    with open(typing_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return typing_path, updated


def update_typing_json_annotated_json_paths(
    episode_dir: Path,
    updates: list[dict],
) -> tuple[Path, int]:
    """Write ``annotated_json_path`` into matching items in processdata/L2/typing.json."""
    typing_path = episode_dir / "processdata" / "L2" / KeyboardDataIngestion.L2_FILENAME
    if not typing_path.is_file():
        raise FileNotFoundError(f"L2 typing data not found: {typing_path}")

    with open(typing_path, encoding="utf-8") as f:
        payload = json.load(f)

    items = payload.get("items", [])
    updated = 0

    for update in updates:
        annotated_json_path = update.get("annotated_json_path")
        if not annotated_json_path:
            continue

        node_id = update.get("node_id")
        image_path = Path(update["image_path"]).resolve()
        matched = False

        for item in items:
            if node_id is not None and item.get("node_id") == node_id:
                item["annotated_json_path"] = annotated_json_path
                updated += 1
                matched = True
                break

            rel_image = item.get("last_image_path")
            if rel_image and (episode_dir / rel_image).resolve() == image_path:
                item["annotated_json_path"] = annotated_json_path
                updated += 1
                matched = True
                break

        if not matched:
            print(
                f"warning: no typing.json item matched for annotated JSON: {image_path}",
                file=sys.stderr,
            )

    with open(typing_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return typing_path, updated


class KeyboardDataIngestion(DataIngestion):
    """Traverses L2 typing data and sends text + image path to the parent."""

    L1_FILENAME = "keyboard.json"
    L2_FILENAME = "typing.json"

    @property
    def data_type(self) -> str:
        return "keyboard"

    def _resolve_image_path(self, episode_dir: Path, relative_path: str) -> Path:
        return (episode_dir / relative_path).resolve()

    def on_record(self, record: dict) -> None:
        """Receive each (text, image_path) pair from traversal."""
        if not hasattr(self, "_records"):
            self._records = []
        self._records.append(record)

    def traverse_l2(self, episode_dir: Path) -> Iterator[dict]:
        """Walk L2 typing.json and yield normalized records."""
        path = episode_dir / "processdata" / "L2" / self.L2_FILENAME
        if not path.is_file():
            raise FileNotFoundError(f"L2 typing data not found: {path}")

        with open(path, encoding="utf-8") as f:
            payload = json.load(f)

        for item in payload.get("items", []):
            if item.get("status") != "ok":
                continue

            text = item.get("typed_value", "")
            rel_image = item.get("last_image_path")
            if not text or not rel_image:
                continue

            if item.get("image_path"):
                image_path = Path(item["image_path"])
            else:
                image_path = self._resolve_image_path(episode_dir, rel_image)
            record = {
                "text": text,
                "image_path": str(image_path),
                "episode_dir": str(episode_dir.resolve()),
                "node_id": item.get("node_id"),
                "starttime": item.get("starttime"),
                "endtime": item.get("endtime"),
            }
            self.on_record(record)
            yield record

    def load(self, episode_dir: Path) -> dict:
        self._records = []

        if self.level == "L2":
            records = list(self.traverse_l2(episode_dir))
            return {
                "episode_name": episode_dir.name,
                "level": "L2",
                "source_file": str(
                    episode_dir / "processdata" / "L2" / self.L2_FILENAME
                ),
                "record_count": len(records),
                "records": records,
            }

        path = episode_dir / "processdata" / "L1" / self.L1_FILENAME
        if not path.is_file():
            raise FileNotFoundError(f"L1 keyboard data not found: {path}")

        with open(path, encoding="utf-8") as f:
            payload = json.load(f)

        records = []
        for item in payload.get("Organized_Keyboard_Data", []):
            if item.get("type") != "Typing":
                continue
            record = {
                "text": item.get("value", ""),
                "image_path": None,
                "index": item.get("index"),
                "starttime": item.get("starttime"),
                "endtime": item.get("endtime"),
            }
            self.on_record(record)
            records.append(record)

        return {
            "episode_name": episode_dir.name,
            "level": "L1",
            "source_file": str(path),
            "record_count": len(records),
            "records": records,
        }
