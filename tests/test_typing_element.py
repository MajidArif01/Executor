import unittest
from Accesibilitytree.TypingElement import TypingFieldFinder


def edit(name, **flags):
    return {'name': name, 'control_type': 'EditControl', 'has_keyboard_focus': True, **flags}


def find(*children):
    return TypingFieldFinder({'tree': {'name': 'Window', 'control_type': 'WindowControl',
                                       'children': list(children)}}).find()


class TypingFieldTests(unittest.TestCase):
    def test_single_focused_field_is_target(self):
        result = find({'name': 'Pane', 'children': [edit('Search', is_enabled=True)]})
        self.assertEqual(result.status, 'inferred_target')
        self.assertEqual([n['name'] for n in result.chain], ['Window', 'Pane', 'Search'])

    def test_two_focused_fields_are_ambiguous(self):
        self.assertEqual(find(edit('A'), edit('B')).status, 'ambiguous')

    def test_conflicting_editability_is_ambiguous(self):
        self.assertEqual(find(edit('A', is_editable=True, is_read_only=True)).status, 'ambiguous')

    def test_no_focused_field_is_unresolved(self):
        result = find(edit('A', has_keyboard_focus=False), {'name': 'Text', 'has_keyboard_focus': True})
        self.assertEqual(result.status, 'unresolved')
        self.assertIsNone(result.target)

    def test_missing_tree_raises(self):
        with self.assertRaises(ValueError):
            TypingFieldFinder({'ax_tree_available': False, 'tree': None})


if __name__ == '__main__':
    unittest.main()
