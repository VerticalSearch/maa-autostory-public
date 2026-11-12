import unittest
from unittest.mock import Mock,patch
from autostory.motion import UnsettledFrameError
from autostory.navigation import Navigator


class SettleRecoveryTests(unittest.TestCase):
    def nav(self):
        nav=Navigator.__new__(Navigator);nav.d=Mock();nav.v=Mock()
        nav.d._needs_settle=True;nav.d._settle_rois=()
        def invalidate(rois=(),kind='fixed'):
            nav.d._settle_rois=rois;nav.d._settle_kind=kind
        nav.d.invalidate_frame.side_effect=invalidate
        return nav

    def test_stale_map_regions_are_reselected_without_replaying_input(self):
        nav=self.nav();old=((30,150,70,35),(160,150,70,35))
        new=((80,150,70,35),(210,150,70,35))
        nav.d.stable_shot.side_effect=[UnsettledFrameError('moving'),object()]
        with patch('autostory.home_observation.navigation_regions',side_effect=[('map',old),('map',new),('map',new)]),patch('autostory.home_observation.blocked_overlay',return_value=None),patch('autostory.navigation.log'):
            nav.observe()
        self.assertEqual(nav.d.shot.call_count,2)
        self.assertEqual(nav.d.invalidate_frame.call_args_list, [unittest.mock.call(old,kind='map'),unittest.mock.call(),unittest.mock.call(new,kind='map')])
        nav.d.tap.assert_not_called();nav.d.swipe.assert_not_called();nav.d.back.assert_not_called()

    def test_persistent_motion_stops_after_one_refresh_and_clears_old_regions(self):
        nav=self.nav();nav.d.stable_shot.side_effect=UnsettledFrameError('moving')
        with patch('autostory.home_observation.navigation_regions',return_value=('map',((30,150,70,35),(160,150,70,35)))),patch('autostory.home_observation.blocked_overlay',return_value=None),patch('autostory.navigation.log'):
            with self.assertRaises(UnsettledFrameError):nav.observe()
        self.assertEqual(nav.d.stable_shot.call_count,2)
        self.assertEqual(nav.d._settle_rois,())
        nav.d.tap.assert_not_called()

    def test_connection_and_stop_errors_are_not_retried(self):
        for error in (ConnectionError('offline'),InterruptedError('stop')):
            nav=self.nav();nav.d.stable_shot.side_effect=error
            with patch('autostory.home_observation.navigation_regions',return_value=(None,())),patch('autostory.home_observation.blocked_overlay',return_value=None):
                with self.assertRaises(type(error)):nav.observe()
            self.assertEqual(nav.d.stable_shot.call_count,1)

    def test_recovered_frame_still_requires_same_page(self):
        nav=self.nav();regions=((30,150,70,35),(160,150,70,35))
        # A lost map label may refresh once, but neither observation can be
        # returned unless the selected map identity is confirmed again.
        nav.d.stable_shot.side_effect=[UnsettledFrameError('moving'),object(),object()]
        with patch('autostory.home_observation.navigation_regions',side_effect=[('map',regions),('map',regions),(None,()),('map',regions),(None,())]),patch('autostory.home_observation.blocked_overlay',return_value=None),patch('autostory.navigation.log'):
            with self.assertRaisesRegex(RuntimeError,'发生变化'):nav.observe()
        nav.d.tap.assert_not_called()
