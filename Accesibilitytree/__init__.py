"""Accessibility tree coordinate matching.

Hit-test a click point against a captured accessibility tree and, for a whole
episode, append the element under each mouse click onto ``timeline.json``.
"""

from .accessibility_tree import enrich
from .find_element import find_element, find_elements, load_tree

__all__ = ["find_element", "find_elements", "load_tree", "enrich"]
