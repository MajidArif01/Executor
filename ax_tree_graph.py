"""
ax_tree_graph.py — Accessibility-tree visualizer using Graphviz.

Usage:
    python ax_tree_graph.py <input.json> [output_name]
        [--format png|pdf|svg]
        [--max-depth N]
        [--no-zero-rect]
        [--direction TB|LR|RL|BT]

Examples:
    python ax_tree_graph.py ax_tree.json
    python ax_tree_graph.py ax_tree.json my_tree --format png
    python ax_tree_graph.py ax_tree.json --max-depth 3
    python ax_tree_graph.py ax_tree.json --no-zero-rect
    python ax_tree_graph.py ax_tree.json --direction LR
"""

import argparse
import hashlib
import json
from pathlib import Path

try:
    import graphviz
except ImportError:
    raise SystemExit("Install the Python package first: uv add graphviz")


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


def uid(node, depth, index):
    """Create a deterministic ID for a node."""
    raw = (
        f"{depth}-{index}-"
        f"{node.get('class_name', '')}-"
        f"{node.get('name', '')}"
    )
    return "n" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:10]


def is_zero_rect(rect):
    if not rect:
        return True

    return (
        rect["left"] == 0
        and rect["top"] == 0
        and rect["right"] == 0
        and rect["bottom"] == 0
    )


def state_badges(node):
    """Return a short description of relevant node states."""
    parts = []

    if node.get("has_keyboard_focus"):
        parts.append("⌨ focus")
    if not node.get("is_enabled", True):
        parts.append("⛔ disabled")
    if node.get("is_offscreen"):
        parts.append("👁 offscreen")
    if is_zero_rect(node.get("bounding_rectangle")):
        parts.append("⬜ zero-rect")

    return "  ".join(parts)


def rect_label(rect):
    if not rect or is_zero_rect(rect):
        return ""

    width = rect["right"] - rect["left"]
    height = rect["bottom"] - rect["top"]

    return f"{width}×{height} @ ({rect['left']},{rect['top']})"


def _esc(value):
    """Escape text used in Graphviz HTML-like labels."""
    value = str(value)
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_label(node):
    """Build the HTML-like label displayed inside a graph node."""
    name = str(node.get("name") or "(unnamed)")
    if len(name) > 40:
        name = name[:38] + "…"

    control_type = str(node.get("control_type") or "").replace("Control", "")
    localized_type = node.get("localized_control_type") or ""
    class_name = node.get("class_name") or ""
    automation_id = node.get("automation_id") or ""
    badges = state_badges(node)
    bounds = rect_label(node.get("bounding_rectangle"))

    rows = [f"<b>{_esc(name)}</b>"]

    type_row = f'<font point-size="9" color="#666666">{_esc(control_type)}'
    if localized_type:
        type_row += f" ({_esc(localized_type)})"
    type_row += "</font>"
    rows.append(type_row)

    if class_name:
        rows.append(
            f'<font point-size="8" color="#888888">'
            f"cls: {_esc(class_name)}</font>"
        )

    if automation_id:
        rows.append(
            f'<font point-size="8" color="#888888">'
            f"id: {_esc(automation_id)}</font>"
        )

    if bounds:
        rows.append(
            f'<font point-size="8" color="#555555">'
            f"📐 {_esc(bounds)}</font>"
        )

    if badges:
        rows.append(
            f'<font point-size="8" color="#b45309">'
            f"{_esc(badges)}</font>"
        )

    children_count = len(node.get("children", []))
    if children_count:
        rows.append(
            f'<font point-size="8" color="#999999">'
            f"children: {children_count}</font>"
        )

    return "<" + "<br/>".join(rows) + ">"


def add_node_recursive(
    graph,
    node,
    parent_id,
    depth,
    index,
    max_depth,
    skip_zero,
):
    """Add a node and its descendants to the Graphviz graph."""
    if skip_zero and is_zero_rect(node.get("bounding_rectangle")):
        return 0

    if max_depth is not None and depth > max_depth:
        return 0

    node_id = uid(node, depth, index)
    control_type = node.get("control_type", "")
    fill, border = CONTROL_COLORS.get(control_type, DEFAULT_COLOR)

    if not node.get("is_enabled", True):
        fill = "#f3f4f6"
        border = "#d1d5db"

    penwidth = "1.2"
    if node.get("has_keyboard_focus"):
        border = "#2563eb"
        penwidth = "2.5"

    graph.node(
        node_id,
        label=build_label(node),
        style="filled,rounded",
        fillcolor=fill,
        color=border,
        penwidth=penwidth,
        fontname="Helvetica",
        fontsize="10",
    )

    if parent_id is not None:
        graph.edge(
            parent_id,
            node_id,
            color="#94a3b8",
            penwidth="0.8",
        )

    count = 1
    for child_index, child in enumerate(node.get("children", [])):
        count += add_node_recursive(
            graph,
            child,
            node_id,
            depth + 1,
            child_index,
            max_depth,
            skip_zero,
        )

    return count


def build_graph(tree_data, max_depth=None, skip_zero=False):
    """Build a vertical accessibility-tree graph."""
    tree = tree_data.get("tree", tree_data)

    dot = graphviz.Digraph(
        name="AccessibilityTree",
        graph_attr={
            "rankdir": "TB",  # Top to bottom
            "bgcolor": "transparent",
            "splines": "ortho",
            "nodesep": "0.3",
            "ranksep": "0.6",
            "pad": "0.4",
            "dpi": "150",
        },
        node_attr={
            "shape": "box",
            "margin": "0.15,0.08",
        },
        edge_attr={
            "arrowsize": "0.6",
        },
    )

    total = add_node_recursive(
        dot,
        tree,
        parent_id=None,
        depth=0,
        index=0,
        max_depth=max_depth,
        skip_zero=skip_zero,
    )

    return dot, total


def main():
    parser = argparse.ArgumentParser(
        description="Visualize an accessibility tree as a Graphviz graph."
    )
    parser.add_argument("input", help="Path to the accessibility JSON file")
    parser.add_argument(
        "output",
        nargs="?",
        default=None,
        help="Output file name without extension",
    )
    parser.add_argument(
        "--format",
        default="pdf",
        choices=["png", "pdf", "svg"],
        help="Output format (default: pdf)",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=None,
        help="Maximum tree depth to render",
    )
    parser.add_argument(
        "--no-zero-rect",
        action="store_true",
        help="Skip nodes with a zero bounding rectangle",
    )
    parser.add_argument(
        "--direction",
        default="TB",
        choices=["TB", "LR", "RL", "BT"],
        help="Graph direction (default: TB, top to bottom)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"File not found: {input_path}")

    with input_path.open("r", encoding="utf-8-sig") as file:
        data = json.load(file)

    output_name = args.output or input_path.stem

    dot, total = build_graph(
        data,
        max_depth=args.max_depth,
        skip_zero=args.no_zero_rect,
    )
    dot.graph_attr["rankdir"] = args.direction

    output_path = dot.render(
        output_name,
        format=args.format,
        cleanup=True,
    )
    print(f"Rendered {total} nodes → {output_path}")


if __name__ == "__main__":
    main()