import copy,json,tempfile,unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock,patch
from autostory.jobs import match_job
from autostory.skill_training import training_policy,training_plan,SkillTrainer
from autostory.core import CoreTaskError
from autostory.formation_recovery import formation_rejection
import test_retry_flow as support
from test_formation0919 import events as formation_events

POLICY={'auto_skill_training':{'enabled':True,'rarities':[3,4,5],'max_skill_level':7,'synthesize_missing':True}}

def box(rank=6,rarity=3,elite=1):
    return {'syncTime':'2026-10-31T22:00:00+08:00','own_opers':[{'own':True,'id':'char_test','name':'卡缇',
        'rarity':rarity,'elite':elite,'level':55,'potential':6,'mainSkillLevel':rank}]}

def doc(target=7):
    return {'opers':[{'name':'卡缇','skill':1,'requirements':{'skill_level':target}}]}

def plan():return {'training':[{'id':'char_test','name':'卡缇','rarity':3,'target':7}]}


class TrainingPlanTests(unittest.TestCase):
    def test_only_exact_authorized_policy_is_enabled(self):
        self.assertIsNone(training_policy({}))
        for key,value in [('rarities',[3,4,5,6]),('max_skill_level',10),('synthesize_missing',False)]:
            config=copy.deepcopy(POLICY);config['auto_skill_training'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):training_policy(config)

    def test_trainable_basic_rank_is_planned_without_modifying_snapshot(self):
        for rarity in (3,4,5):
            roster=box(rarity=rarity);before=copy.deepcopy(roster)
            result=training_plan(doc(),roster,POLICY,match_job)
            self.assertEqual(result['status'],'training_required');self.assertEqual(result['training'][0]['target'],7)
            self.assertEqual(roster,before)

    def test_no_six_star_mastery_promotion_or_unowned_upgrade(self):
        for roster,job in [(box(rarity=6),doc()),(box(rank=7,elite=1),doc(8)),(box(elite=0),doc())]:
            result=training_plan(job,roster,POLICY,match_job)
            self.assertEqual(result['status'],'blocked');self.assertNotIn('training',result)
        roster=box();roster['own_opers'][0]['own']=False
        self.assertEqual(training_plan(doc(),roster,POLICY,match_job)['status'],'blocked')

    def test_unknown_rank_cannot_bypass_elite_prerequisite_even_when_disabled(self):
        for elite,required in ((0,7),(1,10)):
            roster=box(rank=None,elite=elite)
            self.assertEqual(match_job(doc(required),roster)['status'],'blocked')

    def test_unknown_basic_rank_requires_live_read_and_maxed_rank_does_not_retrain(self):
        self.assertEqual(training_plan(doc(),box(None),POLICY,match_job)['status'],'training_required')
        self.assertEqual(training_plan(doc(),box(7),POLICY,match_job)['status'],'trial_required')
        self.assertEqual(training_plan(doc(),box(),{},match_job)['status'],'blocked')

    def test_other_missing_operator_does_not_cause_pointless_training(self):
        job=doc();job['opers'].append({'name':'不存在'})
        result=training_plan(job,box(),POLICY,match_job)
        self.assertEqual(result['status'],'blocked');self.assertNotIn('training',result)

    def test_elite_zero_rank_four_is_allowed_but_cannot_invent_mastery(self):
        result=training_plan(doc(4),box(3,elite=0),POLICY,match_job)
        self.assertEqual(result['training'][0]['target'],4)
        roster=box(6,elite=2);roster['own_opers'][0]['skills']=[{'level':3}]
        result=training_plan(doc(10),roster,POLICY,match_job)
        self.assertNotEqual(result['status'],'training_required')


class TrainingExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for key,value in [('DATA',self.root/'runtime'),('MAA',self.root),('check_stop',Mock()),('log',Mock())]:
            self.stack.enter_context(patch('autostory.skill_training.'+key,value))
        self.core=Mock();self.core.asst.get_version.return_value='v6.20.1'
        self.core.run.return_value=[];self.nav=Mock()
        self.trainer=SkillTrainer(self.core,self.nav,lambda:POLICY)
        self.trainer._read_rank=Mock(side_effect=[{'mainSkillLevel':6},{'mainSkillLevel':7}])

    def receipts(self):return [json.loads(p.read_text('utf-8')) for p in (self.root/'runtime/training').glob('*/receipt.json')]

    def test_native_upgrade_only_contains_name_and_base_rank_then_syncs_both_snapshots(self):
        crafting={'what':'MaterialSynthesisCraft','details':{'item_id':'skill3','count':2}}
        self.core.run.side_effect=[[],[crafting,{'what':'OperProgressDetail','details':{'name':'卡缇','result':'Success','skill_level':7}}]]
        result,ok,reason=self.trainer.apply(box(),plan())
        self.assertTrue(ok);self.assertEqual(reason,'')
        self.assertEqual([c.args[:2] for c in self.core.run.call_args_list],[('OperProgress',{'plans':[{'name':'卡缇'}]}),('OperProgress',{'plans':[{'name':'卡缇','skill_level':7}]})])
        self.assertEqual(result['own_opers'][0]['mainSkillLevel'],7)
        for path in ('data/OperBoxData.json','runtime/operators.json'):
            self.assertEqual(json.loads((self.root/path).read_text('utf-8')),result)
        receipt=self.receipts()[0];self.assertEqual(receipt['state'],'verified');self.assertEqual(receipt['crafting'],[crafting])

    def test_unverified_identity_or_rank_never_submits_upgrade(self):
        self.trainer._read_rank.return_value=None;self.trainer._read_rank.side_effect=None
        _,ok,_=self.trainer.apply(box(),plan())
        self.assertFalse(ok);self.assertEqual(self.core.run.call_count,1)
        self.assertFalse((self.root/'data/OperBoxData.json').exists())

    def test_native_success_without_visual_proof_is_not_success_or_snapshot_change(self):
        self.trainer._read_rank.side_effect=[{'mainSkillLevel':6},None]
        _,ok,_=self.trainer.apply(box(),plan())
        self.assertFalse(ok);self.assertFalse((self.root/'data/OperBoxData.json').exists())
        self.assertEqual(self.receipts()[0]['state'],'not_completed')

    def test_resource_shortage_keeps_actual_rank_and_is_not_repeated_in_same_worker(self):
        self.core.run.side_effect=[[],[{'what':'OperProgressDetail','details':{'result':'Failed','result_detail':'ResourceInsufficient'}}]]
        self.trainer._read_rank.side_effect=[{'mainSkillLevel':6},{'mainSkillLevel':6}]
        roster,ok,_=self.trainer.apply(box(),plan());self.assertFalse(ok)
        self.assertEqual(roster['own_opers'][0]['mainSkillLevel'],6)
        _,ok,_=self.trainer.apply(roster,plan());self.assertFalse(ok)
        self.assertEqual(self.core.run.call_count,2)

    def test_already_satisfied_live_rank_does_not_spend(self):
        self.trainer._read_rank.side_effect=[{'mainSkillLevel':7},{'mainSkillLevel':7}]
        _,ok,_=self.trainer.apply(box(),plan());self.assertTrue(ok)
        self.assertEqual(self.core.run.call_count,1)

    def test_wrong_version_or_scope_rejected_before_navigation(self):
        for roster,target in [(box(rarity=6),7),(box(),8),(box(elite=0),7)]:
            p=plan();p['training'][0]['target']=target
            with self.assertRaises(ValueError):self.trainer.apply(roster,p)
        self.core.asst.get_version.return_value='v6.20.2'
        with self.assertRaises(RuntimeError):self.trainer.apply(box(),plan())
        self.core.run.assert_not_called();self.nav.home.assert_not_called()

    def test_interruption_keeps_receipt_and_does_not_continue(self):
        self.core.run.side_effect=[[],InterruptedError('stop')]
        with self.assertRaises(InterruptedError):self.trainer.apply(box(),plan())
        self.assertEqual(self.receipts()[0]['state'],'interrupted')
        self.assertFalse((self.root/'data/OperBoxData.json').exists())


class TrainingFlowTests(unittest.TestCase):
    setUp=support.RetryFlowTests.setUp
    execute=support.RetryFlowTests.execute
    def test_training_success_still_requires_practice_before_formal(self):
        training={'status':'training_required','assignment':[],**plan()};ready={'status':'trial_required','assignment':[]}
        trainer=Mock();trainer.apply.side_effect=lambda roster,match:(roster,True,'')
        with patch('autostory.controller.SkillTrainer',return_value=trainer),patch('autostory.controller.candidate_match',side_effect=[training,training,ready]):
            result=self.execute([support.stage('DV-1',[1])])
        self.assertIsNone(result['error']);trainer.apply.assert_called_once()
        self.assertEqual([(c[1],c[2]) for c in result['calls']],[(1,True),(1,False)])

    def test_training_failure_skips_candidate_without_reserving_practice_or_sanity(self):
        training={'status':'training_required','assignment':[],**plan()}
        trainer=Mock();trainer.apply.side_effect=lambda roster,match:(roster,False,'missing materials')
        with patch('autostory.controller.SkillTrainer',return_value=trainer),patch('autostory.controller.candidate_match',return_value=training):
            result=self.execute([support.stage('DV-1',[1])])
        self.assertIsNone(result['error']);self.assertEqual(result['calls'],[])
        self.assertEqual((result['ledger']['sanity'],result['ledger']['practice_count']),(200,0))


class InterleavedCallbacksTests(unittest.TestCase):
    def test_capture_metrics_do_not_hide_proven_prebattle_failure_but_disconnect_does(self):
        rows=formation_events();rows.insert(2,{'msg':2,'what':'ScreencapCost','details':{'avg':100}})
        rows.append({'msg':3,'taskid':34,'taskchain':'Copilot','finished_tasks':[34]})
        self.assertIsNotNone(formation_rejection(CoreTaskError('Copilot',34,rows)))
        rows[2]['what']='Disconnected'
        self.assertIsNone(formation_rejection(CoreTaskError('Copilot',34,rows)))


if __name__=='__main__':unittest.main()
