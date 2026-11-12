import copy,json,tempfile,unittest
import cv2
import numpy as np
from pathlib import Path
from unittest.mock import Mock,patch
from autostory import siracusa as s
from autostory.navigation import Navigator
ROOT=Path(__file__).resolve().parents[1]
EVIDENCE=ROOT/'release/evidence/flow0935'

def labels():return json.loads((EVIDENCE/'entry-IS.json').read_text('utf-8'))['texts']

class SiracusaTests(unittest.TestCase):
    def setUp(self):
        stopped=patch.object(s,'check_stop');stopped.start();self.addCleanup(stopped.stop)

    def test_stop_is_respected_before_device_input(self):
        nav=Mock()
        with patch.object(s,'check_stop',side_effect=InterruptedError('stop')):
            with self.assertRaises(InterruptedError):s.find(nav,'IS-1')
        nav.observe.assert_not_called();nav.tap_stage.assert_not_called()

    def test_mapping_matches_retained_game_data_and_excludes_ambiguous_tasks(self):
        stages=json.loads((ROOT/'research/gamedata/stage_table.json').read_text('utf-8-sig'))['stages']
        data=json.loads((ROOT/'research/gamedata/activity_table.json').read_text('utf-8-sig'))['siracusaData']
        expected={stages[p['stageId']]['code']:data['pointDataMap'][p['pointId']]['pointName']
            for p in data['stagePointList'] if not p['isTaskStage'] and stages[p['stageId']]['code'] in s.POINTS}
        self.assertEqual(s.POINTS,expected);self.assertEqual(len(s.POINTS),24)
        self.assertNotIn('IS-QT',s.POINTS)

    def test_actual_overview_provides_multiple_named_anchors(self):
        self.assertTrue(s.is_overview(labels()))
        self.assertIn('新城区大街',s.places(labels()))
        from autostory.home_observation import navigation_regions
        self.assertEqual(navigation_regions(labels())[0],'is_overview')

    def test_startup_accepts_verified_city_but_not_wrong_app_or_overlay(self):
        from autostory.startup_screens import classify,GAME_PACKAGE
        from autostory.home_observation import navigation_regions
        page=navigation_regions(labels())[0]
        self.assertEqual(classify(labels(),GAME_PACKAGE,page)['state'],'ready')
        self.assertEqual(classify(labels(),'example.other.app',page)['state'],'unknown')
        overlay=labels()+[dict(text='是否花费源石兑换合成玉',score=1,x=600,y=300)]
        self.assertEqual(classify(overlay,GAME_PACKAGE,page)['state'],'attention')

    def test_actual_normal_and_ex_detail_are_not_overview(self):
        for name in ('新城区大街','咖啡店'):
            texts=json.loads((EVIDENCE/f'is-{name}.json').read_text('utf-8'))['texts']
            self.assertFalse(s.is_overview(texts));self.assertTrue(s.is_detail(texts))

    def test_verified_long_is_raid_code_uses_complete_contrast_strip(self):
        from autostory.stage_header import contrast_header
        from autostory.navigation import header_matches
        data=json.loads((EVIDENCE/'is-ex1-raid-header.json').read_text('utf-8'))
        image=cv2.imdecode(np.fromfile(EVIDENCE/'is-ex1-raid-header.png',np.uint8),1)
        vision=Mock();vision.texts.return_value=data['contrast']
        self.assertTrue(header_matches(contrast_header(vision,image,'IS-EX-1','raid'),'IS-EX-1'))
        self.assertFalse(header_matches(data['contrast'],'IS-EX-2'))
        for invalid in ('IS-EX-10','IS-S-10','NL-EX-1','SN-EX-1'):
            self.assertEqual(contrast_header(vision,image,invalid,'raid'),[])

    def test_missing_or_ambiguous_landmarks_do_not_supply_click(self):
        text=next(t for t in labels() if t['text']=='新城区大街')
        self.assertNotIn('新城区大街',s.places([text,text]))
        self.assertFalse(s.is_overview([text]))
        self.assertFalse(s.is_overview([{**t,'score':.7} for t in labels()]))
        self.assertFalse(s.is_overview([{**t,'box':[0,0,20,20]} for t in labels()]))

    def test_overlay_or_locked_detail_blocks_landmark_backdrop(self):
        for row in ({'text':'开始行动'},{'text':'确认'},
                    {'text':'IS-1','x':950,'y':96,'score':1}):
            self.assertFalse(s.is_overview(labels()+[row]))

    def test_find_taps_one_verified_building_and_requires_exact_code(self):
        nav=Mock();nav.observe.return_value=(None,labels())
        nav.detail.return_value={'code':'IS-1','mode':'normal','state':'cleared'}
        result=s.find(nav,'IS-1')
        self.assertEqual(result['code'],'IS-1')
        self.assertEqual(nav.tap_stage.call_count,1)
        self.assertEqual(nav.tap_stage.call_args.args[0]['text'],'新城区大街')
        self.assertEqual(nav.tap_stage.call_args.args[1],'IS-1')
        nav.d.swipe.assert_not_called()

    def test_find_never_taps_unknown_or_overlay(self):
        nav=Mock();nav.observe.return_value=(None,labels()+[{'text':'确认'}])
        with self.assertRaises(RuntimeError):s.find(nav,'IS-1')
        nav.tap_stage.assert_not_called();nav.d.swipe.assert_not_called()
        with self.assertRaises(ValueError):s.find(nav,'IS-QT')

    def test_wrong_header_error_is_not_replayed(self):
        nav=Mock();nav.observe.return_value=(None,labels())
        nav.tap_stage.side_effect=RuntimeError('wrong header')
        with self.assertRaisesRegex(RuntimeError,'wrong header'):s.find(nav,'IS-1')
        self.assertEqual(nav.tap_stage.call_count,1);nav.detail.assert_not_called()

    def test_missing_building_has_bounded_pans_and_no_taps(self):
        nav=Mock();nav.observe.return_value=(None,labels())
        with self.assertRaisesRegex(RuntimeError,'未找到'):s.find(nav,'IS-10',max_swipes=4)
        self.assertLessEqual(nav.d.swipe.call_count,16);nav.tap_stage.assert_not_called()

    def test_search_reads_other_vertical_rows_before_selecting(self):
        nav=Mock();shifted=[False]
        target={'text':'次级核心区指挥塔','score':1,'x':600,'y':350,'box':[500,335,700,365]}
        nav.observe.side_effect=lambda *a:(None,labels()+([target] if shifted[0] else []))
        def swipe(x1,y1,x2,y2):
            if x1==x2 and y1>y2:shifted[0]=True
        nav.d.swipe.side_effect=swipe
        nav.detail.return_value={'code':'IS-10','state':'cleared','mode':'normal'}
        self.assertEqual(s.find(nav,'IS-10')['code'],'IS-10')
        self.assertTrue(shifted[0]);nav.tap_stage.assert_called_once_with(target,'IS-10')

    def test_close_detail_returns_to_verified_building_map(self):
        nav=Navigator.__new__(Navigator);nav.d=Mock();nav.catalog=Mock()
        nav.observe=Mock(return_value=(None,[{'text':'开始行动'}]))
        def await_state(predicate,*_):self.assertTrue(predicate(None,labels()))
        nav.await_state=await_state
        nav.close_detail();nav.d.back.assert_called_once()

    def test_missing_raid_is_read_and_kept_separate_from_normal(self):
        nav=Mock();nav.catalog.stages.return_value=['IS-EX-1']
        nav.observe.side_effect=[(None,[{'text':'突袭模式'}]),(None,labels())]
        nav.detail.return_value={'code':'IS-EX-1','mode':'raid','state':'uncleared'}
        normal={'code':'IS-EX-1','mode':'normal','state':'cleared'}
        with tempfile.TemporaryDirectory() as folder,patch.object(s,'DATA',Path(folder)), \
             patch.object(s,'find',return_value=normal),patch.object(s,'read_json',return_value={}), \
             patch('autostory.workflow.update'),patch.object(s,'log'):
            rows=s.scan(nav,'IS-EX',incremental=True,only_missing=True)
        self.assertEqual([r['state'] for r in rows],['cleared','uncleared'])
        nav.set_mode.assert_called_once_with('raid')

    def test_confirmed_resume_skips_reads_without_erasing_records(self):
        nav=Mock();nav.catalog.stages.return_value=['IS-1']
        row={'code':'IS-1','mode':'normal','state':'cleared','at':'2026-11-01','evidence':'retained.png'}
        with tempfile.TemporaryDirectory() as folder,patch.object(s,'DATA',Path(folder)), \
             patch.object(s,'read_json',return_value={'IS-1:normal':row}),patch.object(s,'log'):
            self.assertEqual(s.scan(nav,'IS',incremental=True,only_missing=True),[row])
        nav.observe.assert_not_called();nav.d.tap.assert_not_called()

if __name__=='__main__':unittest.main()
