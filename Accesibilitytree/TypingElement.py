"""Find the text field a typed string went into, from one AX capture.

Only a node with keyboard focus and text-entry semantics (Edit control type,
textbox/searchbox role, or explicit is_editable) can be the target. Several
focused candidates are reported as ambiguous, never resolved by tree order.
"""

from __future__ import annotations

from dataclasses import dataclass


def flag(node: dict, key: str) -> bool | None:
    """Only real booleans count as evidence."""
    value = node.get(key)
    return value if isinstance(value, bool) else None


@dataclass
class TypingResult:
    status: str                       # inferred_target | ambiguous | unresolved
    reason: str
    target: dict | None = None
    chain: list[dict] | None = None   # root -> target nodes


class TypingFieldFinder:
    def __init__(self, capture: dict):
        if not isinstance(capture, dict):
            raise ValueError("Snapshot must be a JSON object")
        if capture.get("ax_tree_available") is False:
            raise ValueError("Snapshot reports that the accessibility tree is unavailable")
        if not isinstance(capture.get("tree"), dict):
            raise ValueError("Snapshot must contain a 'tree' object")
        self.tree = capture["tree"]

    def walk(self):
        """Yield (node, root->node chain) in document order."""
        stack = [(self.tree, [self.tree])]
        while stack:
            node, chain = stack.pop()
            yield node, chain
            children = node.get("children") or []
            for i in range(len(children) - 1, -1, -1):
                if not isinstance(children[i], dict):
                    raise ValueError("Expected an object in a 'children' list")
                stack.append((children[i], chain + [children[i]]))

    def field(self, node: dict) -> dict | None:
        """Target record for a focused text-entry node, or None if it isn't one."""
        editable, read_only = flag(node, "is_editable"), flag(node, "is_read_only")
        enabled = flag(node, "is_enabled")
        text_like = (str(node.get("control_type") or "").lower() in {"editcontrol", "edit"}
                     or str(node.get("role") or "").lower() in {"textbox", "searchbox"})
        if not text_like and editable is not True:
            return None
        conflict = (editable is True and read_only is True) or (editable is False and read_only is False)
        return {
            "name": node.get("name"),
            "control_type": node.get("control_type"),
            "automation_id": node.get("automation_id"),
            "class_name": node.get("class_name"),
            "role": node.get("role"),
            "bounding_rectangle": node.get("bounding_rectangle"),
            "has_keyboard_focus": True,
            "is_enabled": enabled,
            "is_offscreen": flag(node, "is_offscreen"),
            **{key: node[key] for key in ("process_id", "runtime_id", "native_window_handle") if key in node},
            "availability": ("conflicting" if conflict else
                             "unavailable" if enabled is False or editable is False or read_only is True else
                             "candidate"),
        }

    def find(self) -> TypingResult:
        """Exactly one focused, available text field wins; anything else is reported."""
        focused = []
        for node, chain in self.walk():
            if flag(node, "has_keyboard_focus") is True:
                record = self.field(node)
                if record:
                    focused.append((record, chain))
        if any(record["availability"] == "conflicting" for record, _ in focused):
            return TypingResult("ambiguous", "A focused text field contains conflicting editability evidence.")
        eligible = [(record, chain) for record, chain in focused if record["availability"] == "candidate"]
        if len(eligible) > 1:
            return TypingResult("ambiguous", "Multiple text-entry candidates report keyboard focus.")
        if not eligible:
            return TypingResult("unresolved", "No available text-entry candidate reports keyboard focus.")
        target, chain = eligible[0]
        return TypingResult("inferred_target",
                            "Exactly one available text-entry candidate reports keyboard focus.",
                            target, chain)
