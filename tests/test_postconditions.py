import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import numpy as np
from autostory import navigation,motion


def text(value,x=900,y=90):return {'text':value,'x':x,'y':y}

class Postconditions(unittest.TestCase):
    def setUp(self):
        self.nav=navigation.Navigator.__new__(navigation.Navigator)
        self.nav.d=Mock();self.nav.v=Mock();self.nav.v.texts_zoom.return_value=[]
        self.nav.catalog=Mock();self.nav.catalog.code=lambda x:x if x=='R8-10' else None
        self.im=np.zeros((720,1280,3),np.uint8)

    def test_open_detail_requires_correct_identity_and_panel(self):
        self.assertFalse(self.nav._detail_identity(self.im,[text('R8-10')],'R8-10'))
        self.assertFalse(self.nav._detail_identity(self.im,[text('R8-11'),text('开始行动')],'R8-10'))
        self.assertTrue(self.nav._detail_identity(self.im,[text('R8-10'),text('开始行动')],'R8-10'))

    def test_settlement_never_passes_detail_identity(self):
        self.assertFalse(self.nav._detail_identity(self.im,[text('R8-10'),text('行动结束'),text('开始行动')],'R8-10'))

    def test_mode_toggle_sends_only_one_input_while_waiting(self):
        self.nav.observe=Mock(side_effect=[(self.im,[text('开始行动'),text('突袭模式',1100,650)]),
            (self.im,[]),(self.im,[text('开始突袭'),text('普通模式')])])
        with patch.object(navigation,'check_stop'):
            self.nav.set_mode('raid')
        self.nav.d.tap.assert_called_once()

    def test_close_waits_for_map_and_never_blindly_taps_again(self):
        self.nav.observe=Mock(side_effect=[(self.im,[text('开始行动')]),(self.im,[]),
            (self.im,[text('R8-10',400,240)])])
        with patch.object(navigation,'check_stop'):
            self.nav.close_detail()
        self.nav.d.back.assert_called_once();self.nav.d.tap.assert_not_called()

    def test_postcondition_timeout_preserves_evidence_and_sends_no_input(self):
        self.nav.observe=Mock(return_value=(self.im,[]))
        with tempfile.TemporaryDirectory() as tmp,patch.object(navigation,'DATA',Path(tmp)),patch.object(navigation,'check_stop'):
            with self.assertRaisesRegex(RuntimeError,'未确认'):
                self.nav.await_state(lambda *_:False,'target',attempts=2)
            self.assertEqual(len(list(Path(tmp).rglob('*.json'))),1)
        self.nav.d.tap.assert_not_called()

class RegionMotion(unittest.TestCase):
    def test_background_animation_can_be_ignored_only_with_stable_target(self):
        a=np.zeros((720,1280,3),np.uint8)
        a[80:130,820:900]=230
        a[200:300,800:1250]=50
        b=a.copy();b[200:600,100:700]=230
        seq=Mock(side_effect=[a,b,a])
        _,report=motion.settle(seq,Mock(),Mock(),clock=lambda:0,rois=((760,65,460,110),))
        self.assertTrue(report['confirmed']);self.assertEqual(report['roi_count'],1)

    def test_moving_small_target_rejects_globally_near_identical_frame(self):
        a=np.zeros((720,1280,3),np.uint8);a[100:600,100:700]=90
        a[80:100,820:840]=250
        b=a.copy();b[80:100,820:840]=0;b[80:100,850:870]=250
        self.assertTrue(motion.frame_motion(motion.signature(a),motion.signature(b))['stable'])
        seq=Mock(side_effect=[a,b])
        _,report=motion.settle(seq,Mock(),Mock(),clock=Mock(side_effect=[0,0,4]),rois=((760,65,460,110),))
        self.assertFalse(report['confirmed'])

if __name__=='__main__':unittest.main()
