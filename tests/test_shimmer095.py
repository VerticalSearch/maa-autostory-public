import json,unittest
from pathlib import Path
from unittest.mock import Mock
import cv2,numpy as np
from autostory.motion import signature,frame_motion,settle
from autostory.home_observation import navigation_regions

ROOT=Path(__file__).resolve().parents[1]

class MapShimmerTests(unittest.TestCase):
    def setUp(self):
        folder=ROOT/'runtime/diagnostics/map-settle095'
        if not folder.exists():self.skipTest('Live capture fixture unavailable')
        self.frames=[cv2.imdecode(np.fromfile(folder/f'{i}.png',np.uint8),1) for i in range(12)]
        _,self.rois=navigation_regions(json.loads((folder/'labels.json').read_text('utf-8')))

    def test_idle_map_shimmer_settles_in_each_three_frame_window(self):
        for i in range(10):
            _,report=settle(Mock(side_effect=self.frames[i:i+3]),Mock(),Mock(),clock=lambda:0,rois=self.rois)
            self.assertTrue(report['confirmed'])

    def test_real_one_pixel_scroll_is_rejected_in_every_label(self):
        for roi in self.rois:
            self.assertFalse(frame_motion(signature(self.frames[0],roi),signature(np.roll(self.frames[0],1,axis=1),roi))['stable'])

    def test_small_blank_regions_never_settle(self):
        for value in (0,255):
            sig=signature(np.full_like(self.frames[0],value),self.rois[0])
            self.assertFalse(frame_motion(sig,sig)['stable'])
