import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from autostory.controller import Runner


class RunnerTests(unittest.TestCase):
    def execute(self,plans,budget=12,fail_trial=False):
        core=Mock();core.refresh_box.return_value={'syncTime':'fresh','own_opers':[]}
        nav=Mock();nav.find.return_value={'state':'cleared'}
        store=Mock();store.catalog.costs={}
        calls=[]
        with tempfile.TemporaryDirectory() as tmp:
            temporary_data=patch('autostory.controller.DATA',Path(tmp));temporary_data.start();self.addCleanup(temporary_data.stop)
            path=Path(tmp)/'job.json';path.write_text('{}')
            store.download.return_value=(path,{},'')
            with patch('autostory.controller.JobStore',return_value=store), \
                 patch('autostory.controller.build_plan',side_effect=plans), \
                 patch('autostory.controller.settings',return_value={'max_battles_per_run':2,'max_sanity_per_run':budget}), \
                 patch('autostory.controller.match_job',return_value={'status':'trial_required','assignment':[]}), \
                 patch('autostory.controller.box_is_stale',return_value=False), \
                 patch('autostory.controller.write_json'),patch('autostory.controller.log'), \
                 patch('autostory.controller.check_stop'):
                history=Mock();history.blocked.return_value=None
                history.blocked_equivalent.return_value=None
                runner=Runner(core,nav,history=history)
                def battle(code,path,trial,mode='normal',prepared=None):
                    calls.append((code,trial))
                    if trial and fail_trial:raise RuntimeError('practice failed')
                    return 'three_star'
                runner.battle=battle
                if fail_trial:
                    with self.assertRaisesRegex(RuntimeError,'practice failed'):runner.run('DV')
                else:runner.run('DV')
        core.refresh_box.assert_called_once()
        return calls,nav

    @staticmethod
    def stage(code,cost=6):
        return {'code':code,'state':'uncleared','cost':cost,
                'candidates':[{'id':1,'match':{'status':'trial_required'}}]}

    def test_trial_failure_never_starts_normal_battle(self):
        calls,_=self.execute([[self.stage('DV-1')]],fail_trial=True)
        self.assertEqual(calls,[('DV-1',True)])

    def test_newly_unlocked_stage_is_rescanned_and_practiced(self):
        calls,nav=self.execute([[self.stage('DV-1')],[self.stage('DV-1'),self.stage('DV-2')]])
        self.assertEqual(calls,[('DV-1',True),('DV-1',False),('DV-2',True),('DV-2',False)])
        self.assertEqual(nav.scan.call_count,2)
        self.assertEqual(nav.scan.call_args_list[0].kwargs,{})
        self.assertEqual(nav.scan.call_args_list[1].kwargs,{'incremental':True})

    def test_over_budget_stage_does_not_hide_affordable_stage(self):
        calls,_=self.execute([[self.stage('DV-1',15),self.stage('DV-2',6)]],budget=6)
        self.assertEqual(calls,[('DV-2',True),('DV-2',False)])


if __name__=='__main__':unittest.main()
