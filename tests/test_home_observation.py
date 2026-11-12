import json,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import cv2,numpy as np
from autostory.home_observation import home_regions,blocked_overlay,navigation_regions
from autostory.motion import settle,frame_motion,signature
from autostory.navigation import Navigator

ROOT=Path(__file__).resolve().parents[1]

class HomeObservationTests(unittest.TestCase):
    def setUp(self):
        self.texts=[{'text':s,'score':.99,'x':(box[0]+box[2])/2,'y':(box[1]+box[3])/2,'box':box}
            for s,box in [('采购中心',[753,464,886,528]),('公开招募',[956,498,1064,543]),('干员寻访',[1116,511,1236,555])]]

    def test_only_three_correctly_located_unique_labels_select_home(self):
        self.assertEqual(len(home_regions(self.texts)),3)
        self.assertFalse(home_regions(self.texts[:2]))
        self.assertFalse(home_regions(self.texts+[self.texts[0]]))
        wrong=[dict(t,x=100) for t in self.texts]
        self.assertFalse(home_regions(wrong))

    def test_exchange_and_battle_overlays_are_not_home(self):
        for label in ('是否花费1至纯源石兑换180合成玉','放弃行动 继续行动'):
            texts=self.texts+[{'text':label}]
            self.assertTrue(blocked_overlay(texts));self.assertFalse(home_regions(texts))

    def test_story_overview_requires_both_fixed_controls(self):
        texts=[{'text':'乐章收录','score':.99,'x':1170,'y':50,'box':[1130,35,1210,65]},
               {'text':'曲谱','score':.99,'x':318,'y':670,'box':[295,652,342,688]}]
        self.assertEqual(navigation_regions(texts)[0],'stories')
        self.assertEqual(navigation_regions(texts[:1]),(None,()))
        self.assertEqual(navigation_regions([{'text':'默认进度'}]),(None,()))

    def test_observation_confirms_fresh_home_after_selecting_regions(self):
        nav=Navigator.__new__(Navigator);nav.d=Mock();nav.v=Mock()
        nav.d._needs_settle=True;nav.d._settle_rois=()
        nav.v.texts.side_effect=[self.texts,self.texts]
        nav.observe()
        nav.d.invalidate_frame.assert_called_once_with(home_regions(self.texts))
        nav.d.stable_shot.assert_called_once();nav.d.tap.assert_not_called()

    def test_page_change_during_wait_cannot_be_used_as_home(self):
        nav=Navigator.__new__(Navigator);nav.d=Mock();nav.v=Mock()
        nav.d._needs_settle=True;nav.d._settle_rois=()
        nav.v.texts.side_effect=[self.texts,[{'text':'未知页面'}]]
        with self.assertRaisesRegex(RuntimeError,'发生变化'):nav.observe()
        nav.d.tap.assert_not_called()

    def test_exchange_reports_actionable_reason_without_any_input(self):
        nav=Navigator.__new__(Navigator);nav.d=Mock();nav.v=Mock()
        nav.d._needs_settle=True;nav.d._settle_rois=()
        nav.v.texts.return_value=[{'text':'是否花费1至纯源石兑换180合成玉'}]
        with self.assertRaisesRegex(RuntimeError,'先取消弹窗'):nav.observe()
        nav.d.stable_shot.assert_not_called();nav.d.tap.assert_not_called();nav.d.back.assert_not_called()

    def test_real_animated_home_settles_using_controls_not_background(self):
        folder=ROOT/'runtime/screens/scan-start092'
        if not folder.exists():self.skipTest('Live regression frames unavailable')
        frames=[cv2.imdecode(np.fromfile(folder/f'{i}.png',np.uint8),1) for i in range(3)]
        labels=json.loads((folder/'2.json').read_text('utf-8'))
        self.assertFalse(frame_motion(signature(frames[0]),signature(frames[2]))['stable'])
        _,report=settle(Mock(side_effect=frames),Mock(),Mock(),clock=lambda:0,rois=home_regions(labels))
        self.assertTrue(report['confirmed']);self.assertEqual(report['roi_count'],3)

if __name__=='__main__':unittest.main()
