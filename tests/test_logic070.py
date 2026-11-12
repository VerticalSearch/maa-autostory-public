import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import numpy as np

from autostory.attempts import team_fingerprint
from autostory.run_ledger import stage_entry,reserve_practice,transition,validate_stages
from autostory.recovery import StateIntegrityError,PracticeFailure
from autostory.operator_details import parse_profile,merge_details,OperatorInspector,base_attributes
from autostory.common import now
from autostory.core import Core
from autostory.jobs import match_job
from autostory.terminal_navigation import open_terminal
import test_retry_flow as retry_support
from test_retry_flow import stage


class TeamTests(unittest.TestCase):
    def setUp(self):
        self.box={'syncTime':'old','own_opers':[{'id':'a','name':'A','own':True,'elite':2,'level':60},
                                             {'id':'b','name':'B','own':True,'elite':2,'level':30}]}
        self.match={'assignment':[{'id':'a','slot':'s','operator':{'name':'A','skill':2}}]}

    def test_unrelated_operator_and_snapshot_time_do_not_reenable_failed_job(self):
        old=team_fingerprint(self.box,self.match)
        self.box['own_opers'][1]['level']=90;self.box['syncTime']='new'
        self.assertEqual(old,team_fingerprint(self.box,self.match))

    def test_selected_skill_or_environment_change_is_reassessed(self):
        old=team_fingerprint(self.box,self.match)
        self.box['own_opers'][0]['observedSkillLevels']={'2':10}
        self.assertNotEqual(old,team_fingerprint(self.box,self.match))
        self.assertNotEqual(team_fingerprint(self.box,self.match),team_fingerprint(self.box,self.match,'other'))

    def test_missing_assigned_operator_is_not_an_empty_valid_team(self):
        self.box['own_opers']=[]
        with self.assertRaises(ValueError):team_fingerprint(self.box,self.match)


class TerminalTests(unittest.TestCase):
    def setUp(self):
        for target in ('check_stop','_pause','_evidence'):
            item=patch('autostory.terminal_navigation.'+target)
            item.start();self.addCleanup(item.stop)

    def test_navigator_method_uses_terminal_guard(self):
        from autostory.navigation import Navigator
        nav=Mock()
        with patch('autostory.terminal_navigation.open_terminal') as guarded:
            Navigator.terminal(nav)
        guarded.assert_called_once_with(nav)

    def test_home_terminal_label_requires_click_and_real_terminal_evidence(self):
        from test_terminal0930 import home_labels,terminal_labels
        nav=Mock();nav.observe.side_effect=[(None,home_labels()),(None,terminal_labels())]
        open_terminal(nav)
        nav.d.tap.assert_called_once_with(960,180,3.0)
        self.assertEqual(nav.observe.call_count,2)

    def test_lone_terminal_label_on_unknown_page_does_not_trigger_click(self):
        nav=Mock();nav.observe.return_value=(None,[{'text':'终端'}])
        with patch('autostory.terminal_navigation.time.sleep'):
            with self.assertRaises(RuntimeError):open_terminal(nav)
        nav.d.tap.assert_not_called()


class LedgerTests(unittest.TestCase):
    def test_restart_preserves_attempt_limit_and_phase(self):
        ledger={'practice_count':0};attempt=reserve_practice(ledger,'DV-1:raid',3,1,'job','team')
        transition(attempt,'formal_reserved',sanity_reserved=18)
        restored=json.loads(json.dumps(ledger))
        item=stage_entry(restored,'DV-1:raid',7)
        self.assertEqual(item['candidate_limit'],3)
        self.assertEqual(item['practice_count'],1)
        self.assertEqual(item['attempts'][0]['phase'],'formal_reserved')
        validate_stages(restored)

    def test_corrupt_or_underreported_stage_count_fails_closed(self):
        ledger={'practice_count':0};reserve_practice(ledger,'A',3,1,'j','t')
        ledger['practice_count']=0
        with self.assertRaises(StateIntegrityError):validate_stages(ledger)
        ledger['practice_count']=1;ledger['stages']['A']['practice_count']=0
        with self.assertRaises(StateIntegrityError):validate_stages(ledger)


class ResumeCapTests(unittest.TestCase):
    setUp=retry_support.RetryFlowTests.setUp
    execute=retry_support.RetryFlowTests.execute
    def test_resume_cannot_try_fourth_candidate_in_same_campaign(self):
        ledger={'sanity':200,'limit':1,'count':0}
        self.execute([stage('DV-1',[1,2,3])],{(i,True):PracticeFailure('loss') for i in (1,2,3)},ledger=ledger)
        second=self.execute([stage('DV-1',[4,5])],ledger=json.loads(json.dumps(ledger)))
        self.assertEqual(second['calls'],[])
        self.assertEqual(second['ledger']['practice_count'],3)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.row={'id':'a','name':'A','own':True,'elite':2,'level':60,'potential':1}
        self.im=np.zeros((720,1280,3),np.uint8)
        self.master=patch('autostory.operator_details.mastery_level',return_value=3).start()
        self.addCleanup(patch.stopall)

    def vision(self,name='A',rank='7',ambiguous=False):
        v=Mock();v.texts.side_effect=lambda im,*args:[{'text':name}] if im.shape[1]==1280 else [{'text':rank}]
        def match(im,template,roi,threshold):
            if template.startswith('OperFilesSkillMaster'):
                if template.endswith('3'):return {'score':.98}
                if ambiguous and template.endswith('2'):return {'score':.97}
                return None
            return {'score':.98}
        v.match.side_effect=match;return v

    def test_positive_profile_reads_rank_and_mastery(self):
        result=parse_profile(self.im,self.vision(),self.row)
        self.assertEqual(result['mainSkillLevel'],7)
        self.assertEqual(result['observedSkillLevels'],{'1':10,'2':10,'3':10})

    def test_wrong_name_missing_page_marker_and_bad_rank_are_unknown(self):
        self.assertIsNone(parse_profile(self.im,self.vision(name='B'),self.row))
        v=self.vision();v.match.return_value=None;v.match.side_effect=None
        self.assertIsNone(parse_profile(self.im,v,self.row))
        self.assertIsNone(parse_profile(self.im,self.vision(rank='77'),self.row))
        self.assertIsNone(parse_profile(self.im,self.vision(rank='6'),self.row))

    def test_ambiguous_mastery_never_defaults_to_zero(self):
        self.master.return_value=None
        result=parse_profile(self.im,self.vision(ambiguous=True),self.row)
        self.assertEqual(result['observedSkillLevels'],{})
        self.assertNotIn('skills',result)

    def test_old_or_changed_base_does_not_reuse_detail(self):
        box={'own_opers':[self.row]}
        cache={'a':{'at':now(),'base':base_attributes(self.row),'mainSkillLevel':7}}
        self.assertEqual(merge_details(box,cache)['own_opers'][0]['mainSkillLevel'],7)
        cache['a']['at']='2000-01-01T00:00:00+00:00'
        self.assertNotIn('mainSkillLevel',merge_details(box,cache)['own_opers'][0])
        cache['a']['at']=now();cache['a']['base']['level']=59
        self.assertNotIn('mainSkillLevel',merge_details(box,cache)['own_opers'][0])

    def test_inspection_only_sends_name_and_requires_two_equal_reads(self):
        with tempfile.TemporaryDirectory() as tmp,patch('autostory.operator_details.DATA',Path(tmp)), \
             patch('autostory.operator_details.MAA',Path(tmp)),patch('autostory.operator_details.check_stop'), \
             patch('autostory.operator_details.log'),patch('autostory.operator_details.time.sleep'):
            core=Mock();core.asst.get_version.return_value='v6.20.1'
            core.run.return_value=[{'what':'OperProgressSummary','details':{'failed':0}}]
            device=Mock();device.shot.return_value=self.im
            inspector=OperatorInspector(core,device,self.vision())
            result,moved=inspector.inspect({'own_opers':[self.row]},
                {'assignment':[{'id':'a','operator':{'skill':2}}]})
            self.assertTrue(moved)
            self.assertEqual(core.run.call_args.args,('OperProgress',{'plans':[{'name':'A'}]}))
            self.assertEqual(result['own_opers'][0]['observedSkillLevels']['2'],10)
            inspector.inspect(result,{'assignment':[{'id':'a','operator':{'skill':2}}]})
            self.assertEqual(core.run.call_count,1)

    def test_observed_skill_blocks_low_training_and_module_types_match_maa(self):
        self.row.update(observedSkillLevels={'3':8},equips=[{'type':'A','level':2}])
        doc={'opers':[{'name':'A','skill':3,'requirements':{'elite':2,'level':60,'skill_level':10}}]}
        self.assertEqual(match_job(doc,{'own_opers':[self.row]})['status'],'blocked')
        doc['opers'][0]['requirements'].update(skill_level=8,module=3,module_level=2)
        result=match_job(doc,{'own_opers':[self.row]})
        self.assertEqual(result['status'],'trial_required')
        self.assertEqual(result['assignment'][0]['unknown'],[])
        doc['opers'][0]['requirements']['module_level']=3
        self.assertEqual(match_job(doc,{'own_opers':[self.row]})['status'],'blocked')


class CoreHookTests(unittest.TestCase):
    def test_refresh_returns_to_verified_home_before_opening_list(self):
        core=Core.__new__(Core);core.device=Mock();core.profile_vision=Mock()
        core.profile_vision.match.return_value={'score':.99}
        core.run=Mock(return_value=[{'what':'OperBoxInfo','details':{'done':True,'own_opers':[
            {'id':'a','name':'A','elite':2,'level':60,'own':True}]}}])
        with patch('autostory.core.read_json',return_value={}),patch('autostory.core.write_json'), \
             patch('autostory.core.log'),patch('autostory.operator_details.read_json',return_value={}), \
             patch('autostory.navigation.Navigator.home') as home:
            core.refresh_box()
        home.assert_called_once()
        self.assertEqual(core.run.call_args_list[0].args,('Custom',{'task_names':['OperBox-Entry']}))

    def test_full_refresh_explicitly_resets_filtered_list_before_operbox(self):
        core=Core.__new__(Core)
        core.run=Mock(return_value=[{'what':'OperBoxInfo','details':{'done':True,'own_opers':[
            {'id':'a','name':'A','elite':2,'level':60,'own':True}]}}])
        with patch('autostory.core.read_json',return_value={}),patch('autostory.core.write_json'), \
             patch('autostory.core.log'),patch('autostory.operator_details.read_json',return_value={}):
            core.refresh_box()
        calls=[call.args for call in core.run.call_args_list]
        self.assertEqual(calls[-1],('OperBox',{}))
        self.assertEqual(calls[2],('Custom',{'task_names':['BattleQuickFormationRole-Pioneer']}))
        self.assertEqual(calls[3],('Custom',{'task_names':['BattleQuickFormationRole-All','BattleQuickFormationRole-All-OCR']}))

    def test_start_callback_is_persisted_even_when_task_completes_immediately(self):
        core=Core.__new__(Core);core.asst=Mock();core.asst.running.return_value=False
        def start():
            core.events=[{'msg':20002,'details':{'task':'BattleStartExercise'}},
                         {'msg':10002,'taskchain':'Copilot'}];return True
        core.asst.start.side_effect=start;seen=[]
        with patch('autostory.core.check_stop'),patch('autostory.core.assert_gui_idle'):
            core.run('Copilot',{},event_hook=seen.append)
        self.assertEqual(seen,core.events)


class ProfileImageTests(unittest.TestCase):
    def test_real_profile_and_wrong_operator_negative(self):
        import cv2
        from autostory.device import Vision
        from autostory.operator_details import mastery_level
        path=Path(__file__).resolve().parents[1]/'runtime/screens/operator-details/1793377917275644400-char_2025_shu/0.png'
        if not path.exists():self.skipTest('Real profile evidence unavailable')
        image=cv2.imdecode(np.fromfile(path,np.uint8),1);vision=Vision()
        row={'id':'char_2025_shu','name':'黍','elite':2}
        result=parse_profile(image,vision,row)
        self.assertEqual(result['mainSkillLevel'],7)
        self.assertEqual(result['observedSkillLevels'],{'1':10,'2':7,'3':10})
        self.assertEqual(result['equips'],[{'type':'X','level':3}])
        self.assertIsNone(parse_profile(image,vision,{'id':'char_103_angel','name':'能天使','elite':2}))
        self.assertIsNone(mastery_level(np.zeros_like(image),vision,(876,328,38,33)))


if __name__=='__main__':unittest.main()
