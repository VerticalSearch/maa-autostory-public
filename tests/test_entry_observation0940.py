import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import numpy as np

from autostory.navigation import Navigator
from autostory.motion import UnsettledFrameError
from test_sn_detail0939 import make_rows


class EntryObservationTests(unittest.TestCase):
    def navigator(self):
        nav=Navigator.__new__(Navigator);nav.d=Mock();nav.v=Mock()
        nav.d._needs_settle=True;nav.d._settle_rois=()
        nav.d.last_settle={'confirmed':False,'rois':[]}
        frame=np.zeros((720,1280,3),np.uint8)
        nav.d.shot.return_value=frame
        return nav,frame

    def test_two_unidentified_fades_allow_one_last_fresh_strict_selection(self):
        nav,frame=self.navigator();rows=make_rows()
        nav.v.texts.side_effect=[[],[],rows,rows]
        nav.d.stable_shot.side_effect=[UnsettledFrameError('fade'),UnsettledFrameError('fade'),frame]
        with tempfile.TemporaryDirectory() as tmp,patch('autostory.navigation.DATA',Path(tmp)),patch('autostory.navigation.log'):
            result=nav.observe()
            self.assertEqual(len(list((Path(tmp)/'screens/observation').glob('*.json'))),2)
        self.assertEqual(result[1],rows)
        self.assertEqual(nav.d.stable_shot.call_count,3)
        self.assertEqual(nav.d.invalidate_frame.call_args.kwargs,{'kind':'map'})
        nav.d.tap.assert_not_called();nav.d.swipe.assert_not_called();nav.d.back.assert_not_called()

    def test_known_moving_page_does_not_get_the_extra_fade_retry(self):
        nav,frame=self.navigator();nav.v.texts.return_value=make_rows()
        nav.d.stable_shot.side_effect=UnsettledFrameError('moving')
        with tempfile.TemporaryDirectory() as tmp,patch('autostory.navigation.DATA',Path(tmp)),patch('autostory.navigation.log'):
            with self.assertRaises(UnsettledFrameError):nav.observe()
        self.assertEqual(nav.d.stable_shot.call_count,2)
        nav.d.tap.assert_not_called()

    def test_persistent_unknown_movement_is_bounded(self):
        nav,frame=self.navigator();nav.v.texts.return_value=[]
        nav.d.stable_shot.side_effect=UnsettledFrameError('moving')
        with tempfile.TemporaryDirectory() as tmp,patch('autostory.navigation.DATA',Path(tmp)),patch('autostory.navigation.log'):
            with self.assertRaises(UnsettledFrameError):nav.observe()
        self.assertEqual(nav.d.stable_shot.call_count,3)
        nav.d.tap.assert_not_called()

if __name__=='__main__':unittest.main()
