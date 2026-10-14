"""Render a Windows accessibility tree and highlight a typing target's ancestry.

Requires the Graphviz Python package and the Graphviz `dot` executable.

Examples (PowerShell):
    uv run python ax_tree_graph_highlight.py accessibility.json --highlight-report testing4.json
    uv run python ax_tree_graph_highlight.py accessibility.json --highlight-report testing4.json --path-only
    uv run python ax_tree_graph_highlight.py accessibility.json --highlight-report testing4.json --format svg

The report and accessibility tree must describe the SAME capture. Paths and node
identities are validated before rendering, so repeated labels cannot highlight
an unrelated sibling.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from html import unescape
from pathlib import Path
from typing import Any

try:
    import graphviz
except ImportError:
    raise SystemExit("Install the Python package: uv add graphviz")

CONTROL_COLORS = {
    "WindowControl": ("#dbeafe", "#2563eb"),
    "PaneControl": ("#ede9fe", "#7c3aed"),
    "ButtonControl": ("#fef3c7", "#d97706"),
    "EditControl": ("#d1fae5", "#059669"),
    "TextControl": ("#f3f4f6", "#4b5563"),
    "ToolBarControl": ("#fce7f3", "#db2777"),
    "TabControl": ("#ccfbf1", "#0d9488"),
    "TabItemControl": ("#ccfbf1", "#0d9488"),
    "GroupControl": ("#ffedd5", "#ea580c"),
    "DocumentControl": ("#cffafe", "#0891b2"),
    "SeparatorControl": ("#e5e7eb", "#6b7280"),
    "SliderControl": ("#ede9fe", "#7c3aed"),
    "MenuControl": ("#fee2e2", "#dc2626"),
    "ListControl": ("#dbeafe", "#2563eb"),
}
DEFAULT_COLOR = ("#f1f5f9", "#475569")
PATH_PATTERN = re.compile(r"\$\.tree((?:\.children\[\d+\])*)\Z")
INDEX_PATTERN = re.compile(r"\.children\[(\d+)\]")


def node_id(path: str) -> str:
    """Use the complete tree path so identically named siblings stay distinct."""
    return "n" + hashlib.sha256(path.encode("utf-8")).hexdigest()[:20]


def parent_path(path: str) -> str | None:
    return path.rsplit(".children[", 1)[0] if path != "$.tree" else None


def node_at_path(tree: dict[str, Any], path: str) -> dict[str, Any]:
    match = PATH_PATTERN.fullmatch(path)
    if not match:
        raise ValueError(f"Invalid report JSON path: {path!r}")
    node = tree
    for number in INDEX_PATTERN.findall(match.group(1)):
        children = node.get("children")
        index = int(number)
        if not isinstance(children, list) or index >= len(children):
            raise ValueError(f"Report path is missing from this accessibility tree: {path}")
        node = children[index]
        if not isinstance(node, dict):
            raise ValueError(f"Expected an object at {path}")
    return node


def verify_record(tree: dict[str, Any], record: dict[str, Any]) -> str:
    path = record.get("json_path")
    if not isinstance(path, str):
        raise ValueError("A report node has no json_path")
    node = node_at_path(tree, path)
    for key in ("name", "control_type", "automation_id", "class_name", "bounding_rectangle"):
        expected = record.get(key)
        if expected is not None and node.get(key) != expected:
            raise ValueError(
                f"Snapshot mismatch at {path}: {key} is {node.get(key)!r}, "
                f"but the report says {expected!r}. Use the snapshot that produced the report."
            )
    return path


def highlight_paths(snapshot: dict[str, Any], report: dict[str, Any]) -> tuple[set[str], str]:
    tree = snapshot.get("tree", snapshot)
    if not isinstance(tree, dict):
        raise ValueError("Accessibility JSON must contain a tree object")
    target = report.get("target")
    ancestors = report.get("ancestor_chain")
    if not isinstance(target, dict) or not isinstance(ancestors, list):
        raise ValueError("The report needs target and ancestor_chain records")
    captured = report.get("capture", {})
    if captured.get("timestamp") and snapshot.get("timestamp") and captured["timestamp"] != snapshot["timestamp"]:
        raise ValueError("The report and accessibility JSON have different capture timestamps")
    target_path = verify_record(tree, target)
    paths = [target_path] + [verify_record(tree, record) for record in ancestors]
    if not all(parent_path(paths[i]) == paths[i + 1] for i in range(len(paths) - 1)):
        raise ValueError("ancestor_chain does not follow the target's actual parent path")
    window = report.get("window")
    if isinstance(window, dict) and paths[-1] != window.get("json_path"):
        raise ValueError("The final ancestor does not match the report's containing window")
    return set(paths), target_path


def is_zero_rect(rect: Any) -> bool:
    if not isinstance(rect, dict):
        return True
    return all(rect.get(k) == 0 for k in ("left", "top", "right", "bottom"))


def escape(value: Any) -> str:
    """Escape Graphviz HTML-like label text; decode existing HTML entities once."""
    value = unescape(str(value))
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def build_label(node: dict[str, Any], mark: str = "") -> str:
    name = str(node.get("name") or "(unnamed)")
    if len(name) > 40:
        name = name[:38] + "…"
    kind = str(node.get("control_type") or "").replace("Control", "")
    localized = node.get("localized_control_type") or ""
    rows = []
    if mark:
        rows.append(f'<font color="{("#a21caf" if mark == "TARGET" else "#c2410c")}"><b>{mark}</b></font>')
    rows.append(f"<b>{escape(name)}</b>")
    rows.append(f'<font point-size="9" color="#666666">{escape(kind)}'
                + (f" ({escape(localized)})" if localized else "") + "</font>")
    for prefix, key in (("cls", "class_name"), ("id", "automation_id")):
        if node.get(key):
            rows.append(f'<font point-size="8" color="#555555">{prefix}: {escape(node[key])}</font>')
    rect = node.get("bounding_rectangle")
    if isinstance(rect, dict) and not is_zero_rect(rect):
        width = rect["right"] - rect["left"]
        height = rect["bottom"] - rect["top"]
        rows.append(f'<font point-size="8" color="#555555">'
                    f'{escape(f"{width}×{height} @ ({rect["left"]},{rect["top"]})")}</font>')
    states = []
    if node.get("has_keyboard_focus"):
        states.append("focus")
    if node.get("is_enabled") is False:
        states.append("disabled")
    if node.get("is_offscreen"):
        states.append("offscreen")
    if is_zero_rect(rect):
        states.append("zero-rect")
    if states:
        rows.append(f'<font point-size="8" color="#b45309">{escape(", ".join(states))}</font>')
    children = node.get("children", [])
    if isinstance(children, list) and children:
        rows.append(f'<font point-size="8" color="#999999">children: {len(children)}</font>')
    return "<" + "<br/>".join(rows) + ">"


def build_graph(
    snapshot: dict[str, Any], report: dict[str, Any], *, direction: str = "TB",
    max_depth: int | None = None, skip_zero: bool = False, path_only: bool = False,
) -> tuple[graphviz.Digraph, int]:
    tree = snapshot.get("tree", snapshot)
    path_set, target_path = highlight_paths(snapshot, report)
    if max_depth is not None and max_depth < target_path.count(".children["):
        raise ValueError("--max-depth excludes the highlighted target")
    if skip_zero and any(is_zero_rect(node_at_path(tree, path).get("bounding_rectangle")) for path in path_set):
        raise ValueError("--no-zero-rect excludes a node on the highlighted path")
    dot = graphviz.Digraph(
        name="AccessibilityTree",
        graph_attr={"rankdir": direction, "bgcolor": "transparent", "splines": "ortho",
                    "nodesep": "0.3", "ranksep": "0.6", "pad": "0.4", "dpi": "150"},
        node_attr={"shape": "box", "margin": "0.15,0.08"},
        edge_attr={"arrowsize": "0.6"},
    )
    start_path = min(path_set, key=lambda path: path.count(".children[")) if path_only else "$.tree"
    stack = [(node_at_path(tree, start_path), start_path,
              start_path.count(".children["), None)]
    total = 0
    while stack:
        node, path, depth, parent = stack.pop()
        if not isinstance(node, dict):
            raise ValueError(f"Expected a node object at {path}")
        if max_depth is not None and depth > max_depth:
            continue
        if skip_zero and is_zero_rect(node.get("bounding_rectangle")):
            continue
        if path_only and path not in path_set:
            continue
        current_id = node_id(path)
        kind = node.get("control_type", "")
        fill, border = CONTROL_COLORS.get(kind, DEFAULT_COLOR)
        penwidth = "1.2"
        if node.get("is_enabled") is False:
            fill, border = "#f3f4f6", "#d1d5db"
        if node.get("has_keyboard_focus"):
            border, penwidth = "#2563eb", "2.5"
        mark = ""
        if path == target_path:
            fill, border, penwidth, mark = "#fce7f3", "#a21caf", "4", "TARGET"
        elif path in path_set:
            fill, border, penwidth, mark = "#ffedd5", "#ea580c", "3", "ANCESTOR"
        dot.node(current_id, label=build_label(node, mark), style="filled,rounded",
                 fillcolor=fill, color=border, penwidth=penwidth,
                 fontname="Helvetica", fontsize="10")
        if parent is not None:
            on_path = path in path_set and parent in path_set
            dot.edge(node_id(parent), current_id,
                     color="#ea580c" if on_path else "#94a3b8",
                     penwidth="2.5" if on_path else "0.8")
        total += 1
        children = node.get("children", [])
        if not isinstance(children, list):
            raise ValueError(f"Expected a children list at {path}")
        for i in range(len(children) - 1, -1, -1):
            child_path = f"{path}.children[{i}]"
            if not path_only or child_path in path_set:
                stack.append((children[i], child_path, depth + 1, path))
    if total < len(path_set):
        raise ValueError("A highlighted path node was excluded from the graph")
    return dot, total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="Accessibility snapshot JSON")
    parser.add_argument("output", nargs="?", type=Path, help="Output name without extension")
    parser.add_argument("--highlight-report", required=True, type=Path, help="Typing report JSON with ancestor_chain")
    parser.add_argument("--format", default="pdf", choices=("pdf", "png", "svg"))
    parser.add_argument("--direction", default="TB", choices=("TB", "LR", "RL", "BT"))
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--no-zero-rect", action="store_true")
    parser.add_argument("--path-only", action="store_true", help="Show only target and ancestors")
    args = parser.parse_args()
    try:
        snapshot = json.loads(args.input.read_text(encoding="utf-8-sig"))
        report = json.loads(args.highlight_report.read_text(encoding="utf-8-sig"))
        dot, total = build_graph(snapshot, report, direction=args.direction,
                                 max_depth=args.max_depth, skip_zero=args.no_zero_rect,
                                 path_only=args.path_only)
        output = args.output or args.input.with_name(args.input.stem + "_highlighted")
        rendered = dot.render(str(output), format=args.format, cleanup=True)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    except graphviz.ExecutableNotFound:
        parser.exit(1, "Error: Graphviz 'dot' executable is missing. Install Graphviz and add its bin directory to PATH.\n")
    print(f"Rendered {total} nodes -> {rendered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
