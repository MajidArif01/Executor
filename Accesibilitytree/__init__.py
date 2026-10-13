"""Accessibility tree element matching.

Find the element a mouse click landed on (``ClickElement``) and the text field
a typed string went into (``TypingElement``); for a whole episode, append both
onto ``timeline.json`` (``accessibility_tree.enrich``).
"""

from .accessibility_tree import enrich
from .ClickElement import find_element, find_elements, load_tree
from .TypingElement import TypingFieldFinder
from .find_drag_elements import DragElementFinder

__all__ = [
    "find_element",
    "find_elements",
    "load_tree",
    "TypingFieldFinder",
    "DragElementFinder",
    "enrich",
]
