import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import numpy as np
from autostory import workflow,dashboard,navigation


class FlowTests(unittest.TestCase):
    def test_workflow_is_scoped_to_run_and_keeps_terminal_evidence(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(workflow,'DATA',Path(tmp)):
            workflow.begin('first','CW-EX')
            workflow.update('practice',code='CW-EX-8',mode='raid')
            self.assertEqual(json.loads((Path(tmp)/'workflow.json').read_text('utf-8'))['phase'],'practice')
            workflow.end('stopped')
            workflow.update('formal')
            self.assertEqual(json.loads((Path(tmp)/'workflow.json').read_text('utf-8'))['phase'],'stopped')
            workflow.begin('second','DV')
            row=json.loads((Path(tmp)/'workflow.json').read_text('utf-8'))
            self.assertEqual((row['run_id'],row['code'],row['mode']),('second','','normal'))
            workflow.end('finished')

    def test_log_tail_is_bounded_and_preserves_chinese(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'log'
            text='行测试abcdefgh\n'*100000
            path.write_text(text,encoding='utf-8')
            self.assertEqual(dashboard.tail_text(path,500),text[-500:])

    def test_coverage_is_cached_until_owning_data_changes(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(dashboard,'DATA',Path(tmp)),patch.object(dashboard,'ROOT',Path(tmp)),patch.object(dashboard,'coverage',return_value={'rows':[]}) as compute,patch.object(dashboard,'_revision',None):
            first,_=dashboard.cached_coverage()
            second,_=dashboard.cached_coverage()
            self.assertEqual(first,second);self.assertEqual(compute.call_count,1)
            (Path(tmp)/'progress.json').write_text('{"x":1}')
            third,_=dashboard.cached_coverage()
            self.assertNotEqual(first,third);self.assertEqual(compute.call_count,2)

    def test_map_loading_waits_without_any_blind_tap(self):
        nav=navigation.Navigator.__new__(navigation.Navigator)
        nav.d=Mock();nav.observe=Mock(side_effect=[(None,[]),(None,[]),(None,['map'])])
        nav.visible_stages=lambda texts,prefix:texts
        with patch.object(navigation,'check_stop'),patch.object(navigation.time,'sleep'):
            nav.wait_for_map('5')
        self.assertEqual(nav.observe.call_count,3);nav.d.tap.assert_not_called()

    def scan_fixture(self,raid_state):
        nav=navigation.Navigator.__new__(navigation.Navigator)
        nav.d=Mock();nav.catalog=Mock();nav.catalog.stages.return_value=['DV-1','DV-2','DV-3']
        nav.close_detail=Mock();nav.tap_stage=Mock();nav.set_mode=Mock()
        nav.observe=Mock(return_value=(np.zeros((720,1280,3),np.uint8),[]))
        nav.visible_stages=Mock(return_value=[])
        def find(code,**kw):return {'code':code,'mode':'normal','state':'locked'}
        nav.find=Mock(side_effect=find)
        db={'DV-1:normal':{'code':'DV-1','mode':'normal','state':'cleared'},
            'DV-2:normal':{'code':'DV-2','mode':'normal','state':'cleared'},
            'DV-3:normal':{'code':'DV-3','mode':'normal','state':'locked'}}
        if raid_state:db['DV-2:raid']={'code':'DV-2','mode':'raid','state':raid_state}
        with patch.object(navigation,'read_json',return_value=db),patch.object(navigation,'write_json'),patch.object(navigation,'check_stop'),patch.object(navigation,'log'),patch.object(navigation,'stage_info',side_effect=lambda code,mode:{'mode':'raid'} if code=='DV-2' else {}):
            nav.scan('DV',incremental=True,max_pages=1)
        return [call.args[0] for call in nav.find.call_args_list]

    def test_incremental_scan_keeps_unknown_raids_and_locked_successors(self):
        self.assertEqual(self.scan_fixture(None),['DV-2','DV-3'])
        self.assertEqual(self.scan_fixture('unknown'),['DV-2','DV-3'])
        self.assertEqual(self.scan_fixture('uncleared'),['DV-2','DV-3'])

    def test_incremental_scan_reuses_only_both_verified_modes(self):
        self.assertEqual(self.scan_fixture('cleared'),['DV-3'])

if __name__=='__main__':unittest.main()
