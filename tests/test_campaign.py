import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import cv2
import numpy as np
from autostory.jobs import Catalog,supports_mode
from autostory.navigation import Navigator,header_matches,classify_stars,classify_raid,is_settlement
from autostory.controller import Runner,box_is_stale
from datetime import datetime,timedelta,timezone
from autostory.campaign import catalogue,stage_info,execution_blockers

FIXTURES=Path(__file__).parent/'fixtures'
def picture(name):return cv2.imdecode(np.fromfile(FIXTURES/name,np.uint8),cv2.IMREAD_COLOR)

class CampaignTests(unittest.TestCase):
    def test_stale_box_must_refresh(self):
        now=datetime.now(timezone.utc)
        self.assertFalse(box_is_stale({'syncTime':now.isoformat()}))
        self.assertTrue(box_is_stale({'syncTime':(now-timedelta(hours=1)).isoformat()}))
        self.assertTrue(box_is_stale({'syncTime':'invalid'}))
    def test_declared_job_difficulty(self):
        for difficulty,normal,raid in ((1,True,False),(2,False,True),(3,True,True)):
            self.assertEqual(supports_mode({'difficulty':difficulty},'normal'),normal)
            self.assertEqual(supports_mode({'difficulty':difficulty},'raid'),raid)
        self.assertFalse(supports_mode({'doc':{'title':'普通关作业'}},'raid'))
        self.assertTrue(supports_mode({'doc':{'title':'突袭关作业'}},'raid'))
        self.assertFalse(supports_mode({'doc':{'title':'突袭关作业'}},'normal'))

    def test_raid_completion_needs_white_medal(self):
        reward=[{'text':'三星获得','x':1000,'y':520}]
        self.assertEqual(classify_raid(picture('raid-uncleared.png'),reward)[0],'uncleared')
        self.assertEqual(classify_raid(picture('raid-cleared.png'),[])[0],'cleared')
        self.assertEqual(classify_raid(np.zeros((720,1280,3),np.uint8),[])[0],'unknown')

    def test_sn_layout_requires_bottom_medals(self):
        im=picture('sn-cleared.png')
        self.assertEqual(classify_stars(im,'sn')[0],'cleared')
        self.assertNotEqual(classify_stars(im)[0],'cleared')

    def test_settlement_code_is_not_a_map_node(self):
        texts=[{'text':'CW-EX-8','x':240,'y':91},{'text':'行动结束','x':190,'y':220}]
        self.assertTrue(is_settlement(texts))
        self.assertEqual(Navigator(None,None).visible_stages(texts,'CW-EX'),[])

    def test_exit_result_waits_for_loading(self):
        nav=Mock();nav.d=Mock();nav.v=Mock()
        result=[{'text':'CW-EX-8','x':240,'y':91},{'text':'行动结束','x':190,'y':220}]
        maptext=[{'text':'CW-EX-8','x':1000,'y':260}]
        nav.observe.side_effect=[(None,result),(None,[]),(None,maptext)]
        nav.visible_stages.side_effect=[[],[],[maptext[0]]]
        runner=Runner(Mock(),nav);runner.current_prefix='CW-EX'
        with patch('autostory.controller.time.sleep'),patch('autostory.controller.check_stop'):
            runner.exit_result()
        self.assertEqual(nav.observe.call_count,3)
        nav.d.tap.assert_called_once_with(1255,180,1.2)

    def test_wrong_stage_settlement_is_rejected(self):
        nav=Mock();nav.v.match.return_value={'score':.99}
        nav.v.texts.return_value=[{'text':'CW-EX-7','x':240,'y':91}]
        runner=Runner(Mock(),nav)
        with patch('autostory.controller.write_json'),patch('autostory.controller.check_stop'):
            with self.assertRaisesRegex(RuntimeError,'关卡编号未确认'):
                runner.result('CW-EX-8',True,'raid')
        nav.d.tap.assert_not_called()

    def test_no_false_stage_suffix_match(self):
        self.assertTrue(header_matches([{'text':'OPERATlONCW-EX-8','x':900,'y':90}],'CW-EX-8'))
        self.assertFalse(header_matches([{'text':'1CW-EX-8','x':900,'y':90}],'CW-EX-8'))

    def test_main8_includes_different_prefixes(self):
        stages=Catalog().stages('8')
        self.assertIn('R8-1',stages);self.assertIn('JT8-3',stages)
        self.assertNotIn('17-1',Catalog().stages('17'))

    def test_catalogue_is_grouped_by_chapter_not_training_number(self):
        self.assertEqual(stage_info('TR-28')['chapter'],'17')
        self.assertIn('CW-S-1-A',catalogue()['profiles']['CW-S']['codes'])
        self.assertEqual(stage_info('CW-EX-8','raid')['cost'],20)

    def test_special_constraints_are_explicit(self):
        stage={'maa_map':True,'mode':'normal','predefined':False,'can_practice':True}
        self.assertEqual(execution_blockers(stage),[])
        for key,value in [('maa_map',False),('mode','tough'),('predefined',True),('can_practice',False)]:
            self.assertTrue(execution_blockers({**stage,key:value}))

    def test_campaign_shares_budget_and_refresh(self):
        core=Mock();core.refresh_box.return_value={'syncTime':'fresh'}
        runner=Runner(core,Mock());seen=[]
        def run(prefix,box,budget_state):
            seen.append((prefix,budget_state['count'],budget_state['sanity'],box))
            budget_state['sanity']-=20;budget_state['count']+=1
            return []
        runner.run=run
        with patch('autostory.controller.catalogue',return_value={'profiles':{'A':{},'B':{},'C':{}},'stages':{}}), \
             patch('autostory.controller.settings',return_value={'max_battles_per_run':2,'max_sanity_per_run':40}), \
             patch('autostory.controller.read_json',return_value={}),patch('autostory.controller.write_json'), \
             patch('autostory.controller.box_is_stale',return_value=False), \
             patch('autostory.controller.export_coverage'),patch('autostory.controller.log'),patch('autostory.controller.check_stop'):
            runner.run_all()
        self.assertEqual([(a,b,c) for a,b,c,d in seen],[('A',0,40),('B',1,20)])
        core.refresh_box.assert_called_once()

if __name__=='__main__':unittest.main()
