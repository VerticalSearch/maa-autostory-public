"""A transient map classification may refresh once; input is never replayed."""
import unittest
from unittest.mock import Mock, patch, call

from autostory.common import DeviceUnavailableError
from autostory.navigation import Navigator


class MapRecheckTests(unittest.TestCase):
    def navigator(self):
        nav = Navigator.__new__(Navigator)
        nav.d = Mock()
        nav.v = Mock()
        nav.d._needs_settle = True
        nav.d._settle_rois = ()
        nav.v.texts.return_value = []
        def invalidate(rois=(), kind='fixed'):
            nav.d._settle_rois = rois
        nav.d.invalidate_frame.side_effect = invalidate
        return nav

    def no_inputs(self, nav):
        nav.d.tap.assert_not_called()
        nav.d.swipe.assert_not_called()
        nav.d.back.assert_not_called()

    def run_observation(self, nav, pages):
        with patch('autostory.home_observation.navigation_regions', side_effect=pages), \
                patch('autostory.home_observation.blocked_overlay', return_value=None), \
                patch('autostory.navigation.log'):
            return nav.observe()

    def test_map_identity_refresh_uses_new_anchors_and_new_frame(self):
        nav = self.navigator()
        first, fresh = object(), object()
        old = ((30, 150, 70, 35), (160, 150, 70, 35))
        new = ((80, 150, 70, 35), (210, 150, 70, 35))
        nav.d.stable_shot.side_effect = [first, fresh]
        result = self.run_observation(nav, [('map', old), (None, ()), ('map', new), ('map', new)])
        self.assertIs(result[0], fresh)
        self.assertEqual(nav.d.shot.call_count, 2)
        self.assertEqual(nav.d.invalidate_frame.call_args_list,
                         [call(old, kind='map'), call(), call(new, kind='map')])
        self.no_inputs(nav)

    def test_repeated_classification_loss_is_bounded(self):
        nav = self.navigator()
        region = ((30, 150, 70, 35), (160, 150, 70, 35))
        with self.assertRaisesRegex(RuntimeError, '发生变化'):
            self.run_observation(nav, [('map', region), (None, ())] * 2)
        self.assertEqual(nav.d.stable_shot.call_count, 2)
        self.no_inputs(nav)

    def test_a_different_stable_page_is_not_accepted_as_the_map(self):
        nav = self.navigator()
        region = ((30, 150, 70, 35), (160, 150, 70, 35))
        with self.assertRaisesRegex(RuntimeError, '仍未确认'):
            self.run_observation(nav, [('map', region), (None, ()), ('home', region), ('home', region)])
        self.no_inputs(nav)

    def test_unknown_page_after_refresh_does_not_become_confirmed(self):
        nav = self.navigator()
        region = ((30, 150, 70, 35), (160, 150, 70, 35))
        with self.assertRaisesRegex(RuntimeError, '仍未确认'):
            self.run_observation(nav, [('map', region), (None, ()), (None, ()), (None, ())])
        self.no_inputs(nav)

    def test_device_error_and_user_stop_during_refresh_propagate(self):
        for error in (DeviceUnavailableError('offline'), InterruptedError('stop')):
            nav = self.navigator()
            region = ((30, 150, 70, 35), (160, 150, 70, 35))
            nav.d.stable_shot.side_effect = [object(), error]
            with self.assertRaises(type(error)):
                self.run_observation(nav, [('map', region), (None, ()), ('map', region)])
            self.assertEqual(nav.d.stable_shot.call_count, 2)
            self.no_inputs(nav)


if __name__ == '__main__':
    unittest.main()
