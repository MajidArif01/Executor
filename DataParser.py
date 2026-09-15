"""Test script: load typing JSON and return the stored text for the input string."""

from __future__ import annotations

import json
import re
from pathlib import Path


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def clean_path_input(raw: str) -> str:
    """Strip whitespace and surrounding quotes from pasted Windows paths."""
    value = (raw or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1].strip()
    return value


def load_json(json_path: Path) -> dict:
    if not json_path.is_file():
        raise FileNotFoundError(f"JSON not found: {json_path}")
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Expected object at top level in {json_path}")
    return data


def find_typing_record_in_list(records: list[dict], typed_text: str) -> dict | None:
    """Find a record whose text matches user typing."""
    target = normalize(typed_text)

    for item in records:
        if normalize(item.get("text", "")) == target:
            return item

    for item in records:
        item_text = normalize(item.get("text", ""))
        if target in item_text or item_text in target:
            return item

    return None


def resolve_record_file(json_path: Path, typed_text: str) -> tuple[dict, Path]:
    """
    Load a typing record from:
    - a per-record JSON (has ``elements`` at top level), or
    - an index JSON (``records`` list with ``annotated_json_path``), or
    - legacy combined JSON (``images`` list with embedded ``elements``).
    """
    payload = load_json(json_path)

    if "elements" in payload:
        return payload, json_path

    if "records" in payload:
        match = find_typing_record_in_list(payload["records"], typed_text)
        if match is None:
            raise ValueError(
                "No matching record in index. Available texts: "
                + ", ".join(repr(r.get("text", "")) for r in payload["records"])
            )
        record_path = Path(match["annotated_json_path"])
        return load_json(record_path), record_path

    if "images" in payload:
        match = find_typing_record_in_list(payload["images"], typed_text)
        if match is None:
            raise ValueError(
                "No matching record in combined JSON. Available texts: "
                + ", ".join(repr(r.get("text", "")) for r in payload["images"])
            )
        return match, json_path

    raise ValueError(
        f"Unrecognized typing JSON format: {json_path} "
        "(expected elements, records, or images)"
    )


def run_test(typed_text: str, annotated_json_path: str | Path) -> dict:
    path = Path(annotated_json_path).resolve()
    record, record_path = resolve_record_file(path, typed_text)

    return {
        "typed_text": typed_text,
        "json_path": str(path),
        "record_json_path": str(record_path),
        "image_path": record.get("image_path"),
        "node_id": record.get("node_id"),
        "text_from_json": record.get("text", ""),
    }


def print_result(result: dict) -> None:
    print("\n=== RESULT ===")
    print(f"Input JSON: {result['json_path']}")
    print(f"Record JSON: {result['record_json_path']}")
    print(f"Searched string: {result['typed_text']!r}")
    print(f"Text from JSON: {result['text_from_json']!r}")
    print(f"Image: {result.get('image_path')}")
    print(f"Node ID: {result.get('node_id')}")


def main() -> None:
    print("=== Typing JSON Test ===")
    typed_text = input("Enter typing string: ").strip()
    json_path = clean_path_input(
        input(
            "Enter path to typing JSON (per-record file or typing_annotated_index.json): "
        )
    )

    if not typed_text:
        print("Error: typing string cannot be empty.")
        return
    if not json_path:
        print("Error: JSON path cannot be empty.")
        return

    try:
        result = run_test(typed_text, json_path)
        print_result(result)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}")


if __name__ == "__main__":
    main()
