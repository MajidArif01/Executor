from __future__ import annotations
import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterator
def walk_tree(root: dict[str, Any]) -> Iterator[tuple[dict[str, Any], str]]:
    """Traverse every node without recursion; retain its exact JSON path."""
    stack = [(root, "$.tree")]
    while stack:
        node, path = stack.pop()
        if not isinstance(node, dict):
            raise ValueError(f"Expected an object at {path}")
        yield node, path
        children = node.get("children", [])
        if not isinstance(children, list):
            raise ValueError(f"Expected a list at {path}.children")
        for index in range(len(children) - 1, -1, -1):
            stack.append((children[index], f"{path}.children[{index}]"))
def bool_property(node: dict[str, Any], key: str) -> bool | None:
    """Do not treat strings, numbers, or missing values as Boolean evidence."""
    value = node.get(key)
    return value if isinstance(value, bool) else None
def rectangle(value: Any) -> dict[str, float] | None:
    """Accept only finite, nonempty rectangles; preserve negative screen origins."""
    if not isinstance(value, dict):
        return None
    result = {}
    for key in ("left", "top", "right", "bottom"):
        number = value.get(key)
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            return None
        if not math.isfinite(number):
            return None
        result[key] = number
    if result["right"] <= result["left"] or result["bottom"] <= result["top"]:
        return None
    return result
def lineage_paths(target_path: str, nodes: dict) -> list[str]:
    """Return the exact root-to-target path, including all intermediate nodes."""
    paths = []
    path = target_path
    while path in nodes:
        paths.append(path)
        if path == "$.tree":
            break
        path = path.rsplit(".children[", 1)[0]
    return list(reversed(paths))
def ancestor_records_to_window(target_path: str, nodes: dict) -> list[dict]:
    """Record each parent, nearest first, stopping at the containing window.

    When the snapshot has no WindowControl ancestor, include the recorded
    snapshot root instead; never invent a window node.
    """
    paths = lineage_paths(target_path, nodes)
    window_index = next((i for i in range(len(paths) - 2, -1, -1)
                         if nodes[paths[i]].get("control_type") == "WindowControl"), 0)
    return [node_record(nodes[p], p, i)
            for i, p in reversed(list(enumerate(paths[window_index:-1], start=window_index)))]
def node_record(node: dict, path: str, depth: int) -> dict:
    """Keep identity, geometry, and state without copying descendant subtrees."""
    return {
        "depth": depth,
        "json_path": path,
        "name": node.get("name"),
        "control_type": node.get("control_type"),
        "automation_id": node.get("automation_id"),
        "class_name": node.get("class_name"),
        "role": node.get("role"),
        "bounding_rectangle": node.get("bounding_rectangle"),
        "has_keyboard_focus": bool_property(node, "has_keyboard_focus"),
        "is_enabled": bool_property(node, "is_enabled"),
        "is_offscreen": bool_property(node, "is_offscreen"),
        **{key: node[key] for key in ("process_id", "runtime_id", "native_window_handle") if key in node},
    }
def node_label(node: dict) -> str:
    return str(node.get("name") or node.get("class_name") or node.get("control_type") or "Unnamed element")
def describe_location(target_path: str, nodes: dict, snapshot: dict) -> dict:
    """Describe recorded semantic ancestry and the target's window-relative position."""
    lineage = [nodes[path] for path in lineage_paths(target_path, nodes)]
    target = lineage[-1]
    # Use the nearest recorded containing window, if present.
    window_index = next((i for i in range(len(lineage) - 2, -1, -1)
                         if lineage[i].get("control_type") == "WindowControl"), 0)
    window = lineage[window_index]
    ancestors = lineage[window_index + 1:-1]
    window_title = window.get("name") or "Unnamed captured window"
    field_name = target.get("name") or "Unnamed text field"
    labels = []
    context = None
    for node in ancestors:
        kind = node.get("control_type")
        name = node.get("name")
        class_name = node.get("class_name")
        if class_name == "BrowserRootView":
            continue  # Redundant window wrapper, often containing the profile name.
        if kind == "ToolBarControl":
            label = name or ("Browser navigation toolbar" if class_name == "ToolbarView"
                             and target.get("class_name") == "OmniboxViewViews" else "Toolbar")
            context = label
        elif kind == "GroupControl":
            label = name or ("Address bar container" if class_name == "LocationBarView" else "Group")
        elif kind == "DocumentControl":
            label = f"Document: {name}" if name else "Document content"
            context = label
        elif name and kind in {"PaneControl", "TabControl", "TabItemControl", "CustomControl"}:
            label = name
            context = label
        else:
            continue
        if not labels or label != labels[-1]:
            labels.append(label)
    bounds = rectangle(target.get("bounding_rectangle"))
    window_bounds = rectangle(window.get("bounding_rectangle"))
    relative = None
    region = None
    vertical = None
    if bounds and window_bounds:
        relative = {
            "left": bounds["left"] - window_bounds["left"],
            "top": bounds["top"] - window_bounds["top"],
            "right": bounds["right"] - window_bounds["left"],
            "bottom": bounds["bottom"] - window_bounds["top"],
        }
        width = window_bounds["right"] - window_bounds["left"]
        height = window_bounds["bottom"] - window_bounds["top"]
        x = (relative["left"] + relative["right"]) / (2 * width)
        y = (relative["top"] + relative["bottom"]) / (2 * height)
        if 0 <= x <= 1 and 0 <= y <= 1:
            horizontal = "left" if x < 1/3 else "right" if x > 2/3 else "center"
            vertical = "top" if y < 1/3 else "bottom" if y > 2/3 else "middle"
            region = f"{vertical}-{horizontal}"
        else:
            region = "outside recorded window bounds"
    description = f"'{field_name}'"
    if context:
        description += f" is inside {context}"
    else:
        description += " is inside the captured window"
    if vertical:
        description += f", in the {vertical} part of '{window_title}'."
    else:
        description += f" ('{window_title}')."
    return {
        "description": description,
        "window_title": window_title,
        "capture_scope": snapshot.get("scope"),
        "window_recorded_as_foreground": True
            if window_index == 0 and snapshot.get("scope") == "foreground_window" else None,
        "ui_hierarchy": [window_title, *labels, field_name],
        "screen_bounds": bounds,
        "window_bounds": window_bounds,
        "bounds_relative_to_window": relative,
        "target_center_region": region,
        "position_method": "Center of target rectangle in a 3-by-3 window grid" if region else None,
    }
def build_report(result: dict, source_file: str | None = None,include_diagnostics: bool = False) -> dict:
    """Build the stable saved schema, independently of terminal formatting."""
    target = result["target"]
    report = {
        "schema_version": "2.0",
        "capture": {"source_file": source_file, **result["capture"]},
        "detection": {
            "status": result["status"],
            "reason": result["reason"],
            "nodes_scanned": result["nodes_scanned"],
            "typing_occurrence_confirmed": False,
            "warnings": result["warnings"],
        },
        "window": result["containing_window"],
        "target": None,
        "ancestor_order": "immediate_parent_to_containing_window",
        "ancestor_chain": result["ancestor_chain"],
        "presentation": {
            "summary": result["reason"],
            "breadcrumb": None,
            "full_ancestor_path": None,
            "location": None,
        },
    }
    if target:
        report["target"] = {
            **result["target_record"],
            "availability": target["availability"],
            "editability": target["editability"],
            "evidence": target["evidence"],
        }
        report["presentation"] = {
            "summary": target["location"]["description"],
            "breadcrumb": " → ".join(target["location"]["ui_hierarchy"]),
            "full_ancestor_path": " → ".join(
                node_label(n) for n in [result["target_record"], *result["ancestor_chain"]]),
            "location": target["location"],
        }
    else:
        report["candidate_fields"] = [
            {key: field[key] for key in ("name", "json_path", "has_keyboard_focus", "availability", "ancestor_chain")}
            for field in result["fields"]
        ]
    if include_diagnostics:
        report["diagnostics"] = {"fields": result["fields"], "focused_nodes": result["focused_nodes"]}
    return report
def format_report(report: dict) -> str:
    """Readable terminal presentation; JSON remains the authoritative saved data."""
    detection = report["detection"]
    target = report["target"]
    lines = [f"Status: {detection['status']}",
             f"Captured: {report['capture'].get('timestamp') or 'Unknown'}",
             report["presentation"]["summary"]]
    if target:
        window = report["window"]
        lines += [f"Window: {node_label(window) if window else 'Not recorded'}",
                  f"Target: {node_label(target)} ({target.get('control_type') or 'Unknown type'})",
                  f"Automation ID: {target.get('automation_id') or 'Not recorded'}",
                  f"Class: {target.get('class_name') or 'Not recorded'}",
                  f"Readable path: {report['presentation']['breadcrumb']}",
                  "", "Ancestors (immediate parent to containing window):"]
        for index, node in enumerate(report["ancestor_chain"], 1):
            lines.append(f"  {index}. {node_label(node)} ({node.get('control_type') or 'Unknown type'})"
                         f" | id={node.get('automation_id') or '(none)'} | {node['json_path']}")
        if not report["ancestor_chain"]:
            lines.append("  No ancestors are present in this snapshot.")
        lines.append(f"Target path: {target['json_path']}")
        location = report["presentation"]["location"]
        bounds = location["screen_bounds"]
        if bounds:
            lines.append(f"Screen bounds: left={bounds['left']}, top={bounds['top']}, "
                         f"right={bounds['right']}, bottom={bounds['bottom']}")
        if location["target_center_region"]:
            lines.append(f"Position in window (target center): {location['target_center_region']}")
    else:
        for field in report.get("candidate_fields", []):
            lines.append(f"Candidate: {field['name'] or 'Unnamed'} | {field['json_path']}")
    for warning in detection["warnings"]:
        lines.append(f"Note: {warning}")
    return "\n".join(lines)
def find_typing_field(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return candidates and one inferred target, or an explicit unresolved state.
    No names, Chrome classes, coordinates, or automation IDs are hard-coded.
    Multiple focused candidates are never resolved by traversal order or size.
    Document/Custom controls require explicit editability evidence; their focus
    flag alone does not establish a text-entry field.
    """
    if not isinstance(snapshot, dict):
        raise ValueError("Snapshot must be a JSON object")
    if snapshot.get("ax_tree_available") is False:
        raise ValueError("Snapshot reports that the accessibility tree is unavailable")
    root = snapshot.get("tree")
    if not isinstance(root, dict):
        raise ValueError("Snapshot must contain a 'tree' object")
    fields = []
    focused_nodes = []
    warnings = []
    nodes_scanned = 0
    nodes = {}
    for node, path in walk_tree(root):
        nodes[path] = node
        nodes_scanned += 1
        focused = bool_property(node, "has_keyboard_focus")
        control_type = str(node.get("control_type") or "").lower()
        role = str(node.get("role") or "").lower()
        editable = bool_property(node, "is_editable")
        read_only = bool_property(node, "is_read_only")
        enabled = bool_property(node, "is_enabled")
        if focused is True:
            focused_nodes.append({
                "json_path": path,
                "name": node.get("name"),
                "control_type": node.get("control_type"),
            })
        text_semantics = (
            control_type in {"editcontrol", "edit"}
            or role in {"textbox", "searchbox"}
        )
        if not text_semantics and editable is not True:
            continue
        evidence = []
        if text_semantics:
            evidence.append("Text-entry control type or role")
        if editable is True:
            evidence.append("Explicit recorded editability")
        conflict = (
            (editable is True and read_only is True)
            or (editable is False and read_only is False)
        )
        if conflict:
            availability = "conflicting"
        elif enabled is False or editable is False or read_only is True:
            availability = "unavailable"
        else:
            availability = "candidate"
        if focused is True:
            evidence.append("Recorded keyboard focus")
        if enabled is True:
            evidence.append("Recorded enabled state")
        if read_only is False:
            evidence.append("Recorded non-read-only state")
        fields.append({
            "json_path": path,
            "name": node.get("name"),
            "control_type": node.get("control_type"),
            "role": node.get("role"),
            "automation_id": node.get("automation_id"),
            "class_name": node.get("class_name"),
            "bounding_rectangle": node.get("bounding_rectangle"),
            "has_keyboard_focus": focused,
            "is_enabled": enabled,
            "is_offscreen": bool_property(node, "is_offscreen"),
            "availability": availability,
            "editability": (
                "conflicting" if conflict else
                "read_only" if editable is False or read_only is True else
                "editable" if editable is True or read_only is False else
                "unknown"
            ),
            "evidence": evidence,
        })
    focused_fields = [f for f in fields if f["has_keyboard_focus"] is True]
    eligible = [f for f in focused_fields if f["availability"] == "candidate"]
    conflicting = [f for f in focused_fields if f["availability"] == "conflicting"]
    target = None
    if conflicting:
        status = "ambiguous"
        reason = "A focused text field contains conflicting editability evidence."
    elif len(eligible) > 1:
        status = "ambiguous"
        reason = "Multiple text-entry candidates report keyboard focus."
    elif len(eligible) == 1:
        status = "inferred_target"
        target = eligible[0]
        reason = "Exactly one available text-entry candidate reports keyboard focus."
        if target["editability"] == "unknown":
            warnings.append("Target editability was not recorded; it may be read-only.")
        if target["is_enabled"] is None:
            warnings.append("Target enabled state was not recorded.")
        if target["is_offscreen"] is True:
            warnings.append("Target reports both focus and off-screen state; verify capture consistency.")
    else:
        status = "unresolved"
        reason = "No available text-entry candidate reports keyboard focus."
    if snapshot.get("truncated") is True:
        warnings.append("Snapshot is truncated; other candidates may be absent.")
    if len(focused_nodes) > 1:
        warnings.append("Multiple nodes report focus; target selection also uses text-entry semantics.")
    for field in fields:
        field["ancestor_chain"] = ancestor_records_to_window(field["json_path"], nodes)
    target_record = None
    containing_window = None
    ancestor_chain = []
    if target:
        target["location"] = describe_location(target["json_path"], nodes, snapshot)
        ancestor_chain = target["ancestor_chain"]
        target_record = node_record(nodes[target["json_path"]], target["json_path"],
                                    len(lineage_paths(target["json_path"], nodes)) - 1)
        containing_window = next((n for n in ancestor_chain
                                  if n.get("control_type") == "WindowControl"), None)
    return {
        "capture": {key: snapshot.get(key) for key in
                    ("timestamp", "source", "scope", "screen", "truncated", "ax_tree_available")},
        "target_record": target_record,
        "ancestor_chain": ancestor_chain,
        "containing_window": containing_window,
        "status": status,
        "reason": reason,
        "snapshot_timestamp": snapshot.get("timestamp"),
        "window_name": root.get("name"),
        "nodes_scanned": nodes_scanned,
        "target": target,
        "fields": fields,
        "focused_nodes": focused_nodes,
        "warnings": warnings,
        "typing_occurrence_confirmed": False,
    }
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("snapshot", type=Path, help="Recorded accessibility JSON")
    parser.add_argument("--out", type=Path, help="Optional destination for the result JSON")
    parser.add_argument("--details", action="store_true", help="Add all candidate/focus diagnostics to JSON")
    parser.add_argument("--json", action="store_true", help="Print structured JSON instead of a readable summary")
    args = parser.parse_args()
    try:
        if args.out and args.out.resolve() == args.snapshot.resolve():
            raise ValueError("Output must not overwrite the input snapshot")
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8-sig"))
        result = find_typing_field(snapshot)
        report = build_report(result, str(args.snapshot.resolve()), args.details)
        output = json.dumps(report, ensure_ascii=False, indent=2)
        if args.out:
            args.out.write_text(output + "\n", encoding="utf-8")
        print(output if args.json else format_report(report))
        if args.out and not args.json:
            print(f"Saved structured JSON: {args.out.resolve()}")
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
