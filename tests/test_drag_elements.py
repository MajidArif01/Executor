import unittest
from Accesibilitytree.find_drag_elements import DragElementFinder


def box(name, control_type, rect, *children):
    left, top, right, bottom = rect
    return {'name': name, 'control_type': control_type, 'children': list(children),
            'bounding_rectangle': {'left': left, 'top': top, 'right': right, 'bottom': bottom}}


SNAPSHOT = {'tree': box('Explorer', 'WindowControl', (0, 0, 500, 500),
    box('Files', 'ListControl', (0, 0, 200, 200),
        box('a.txt', 'ListItemControl', (0, 0, 200, 50),
            box('a.txt', 'TextControl', (10, 10, 100, 40)))),
    box('Left', 'PaneControl', (300, 300, 400, 400)),
    box('Right', 'PaneControl', (350, 350, 450, 450)))}


class DragElementTests(unittest.TestCase):
    def find(self, x, y):
        return DragElementFinder().find_element(SNAPSHOT, x, y)

    def test_text_inside_list_item_maps_to_list_item(self):
        result = self.find(20, 20)
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['element'], {'name': 'a.txt', 'control_type': 'ListItemControl',
                                             'automation_id': '', 'window': 'Explorer'})

    def test_overlapping_branches_are_ambiguous(self):
        result = self.find(375, 375)
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual([c['name'] for c in result['candidates']], ['Right', 'Left'])

    def test_outside_every_rectangle_is_not_found(self):
        self.assertEqual(self.find(900, 900), {'status': 'not_found', 'element': None})

    def test_pick_and_drop_are_resolved_separately(self):
        result = DragElementFinder().find_drag_elements(SNAPSHOT, SNAPSHOT, (20, 20), (250, 250))
        self.assertEqual(result['pick']['element']['control_type'], 'ListItemControl')
        self.assertEqual(result['drop']['element']['name'], 'Explorer')


if __name__ == '__main__':
    unittest.main()
