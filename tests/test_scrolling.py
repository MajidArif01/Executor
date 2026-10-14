import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
import json
from Accesibilitytree.Scrolling import scroll_delta, find_scroll_target
from Accesibilitytree.accessibility_tree import accessibility_for_scroll
from executor import _scroll_notches, SCROLL_UNITS_PER_NOTCH
from action_classes import ActionLevel0


def capture():
    return {'tree': {'name': 'Window', 'control_type': 'WindowControl',
        'bounding_rectangle': [0, 0, 300, 300], 'children': [
            {'name': 'List', 'control_type': 'ListControl', 'scrollable': True,
             'bounding_rectangle': [10, 10, 200, 200], 'children': [
                 {'name': 'Label', 'control_type': 'TextControl',
                  'bounding_rectangle': [20, 20, 80, 80]}]}]}}


class ScrollTests(unittest.TestCase):
    def test_merged_totals_do_not_double_count(self):
        node = {'events': [{'detail': 'dx=0,dy=-1'}] * 5,
                'scroll_merge': {'total_dx': 0, 'total_dy': -5},
                'omniparser': {'scroll_dy': -1}}
        self.assertEqual(_scroll_notches(node), (0, -5))
        node.pop('scroll_merge')
        self.assertEqual(scroll_delta(node), (0, -5))

    def test_hover_and_container_are_distinct(self):
        result = find_scroll_target(capture(), 30, 30)
        self.assertEqual(result['hovered_element']['name'], 'Label')
        self.assertEqual(result['target']['name'], 'List')
        self.assertEqual(result['status'], 'scroll_container')

    def test_missing_metadata_is_not_guessed(self):
        data = capture()
        data['tree']['children'][0].pop('scrollable')
        result = find_scroll_target(data, 30, 30)
        self.assertIsNone(result['scroll_container'])
        self.assertEqual(result['target']['name'], 'Label')
        self.assertIsNone(find_scroll_target(data, 400, 400))

    def test_coordinates_scale_and_include_monitor_origin(self):
        data = capture()
        data['screen_bounds'] = {'left': 100, 'top': 0, 'right': 400, 'bottom': 300}
        result = find_scroll_target(data, 60, 60, click_space=(600, 600))
        self.assertEqual(result['point']['normalized'], [130, 30])
        self.assertEqual(result['target']['name'], 'List')

    def test_timestamp_only_scroll_enrichment(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder)
            (path / '20261009_104121_262355_accessibility.json').write_text(json.dumps(capture()))
            item = {'type': 'scroll', 'events': [{'x': 30, 'y': 30,
                'timestamp': '2026-10-09T10:41:21.262355', 'detail': 'dx=0,dy=-1'}]}
            record, status = accessibility_for_scroll(item, path)
            self.assertEqual(status, 'scroll_matched')
            self.assertEqual(record.scroll.dy, -1)

    def test_wheel_backend_receives_five_notches(self):
        class Backend:
            def scroll(self, value):
                self.value = value
        backend = Backend()
        ActionLevel0(pyautogui_backend=backend).scroll(0, -5 * SCROLL_UNITS_PER_NOTCH)
        self.assertEqual(backend.value, -5)

if __name__ == '__main__':
    unittest.main()
