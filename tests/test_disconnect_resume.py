import subprocess
import unittest
from unittest.mock import Mock,patch
from autostory.common import DeviceUnavailableError
from autostory.device import Device
from autostory.controller import Runner

class DisconnectTests(unittest.TestCase):
    def test_newer_blocked_checkpoint_is_not_hidden_by_old_success(self):
        previous={'budget':{'sanity':179,'limit':3,'count':1,'practice_count':2,'practice_limit':12},
                  'groups':[{'group':'1','outcomes':[]},{'group':'1','blocked':'lost connection'}]}
        runner=Runner(Mock(),Mock());runner.run=Mock(return_value=[])
        with patch('autostory.controller.catalogue',return_value={'profiles':{'1':{}},'stages':{}}), \
             patch('autostory.controller.read_json',side_effect=lambda path,default=None:previous if path.name=='campaign-results.json' else {}), \
             patch('autostory.controller.write_json'),patch('autostory.controller.box_is_stale',return_value=False), \
             patch('autostory.controller.export_coverage'),patch('autostory.controller.log'),patch('autostory.controller.check_stop'):
            runner.run_all(resume=True)
        runner.run.assert_called_once()
        self.assertEqual(runner.run.call_args.kwargs['budget_state']['practice_count'],2)

    def test_adb_failure_is_device_wide(self):
        device=Device.__new__(Device);device.adb='fake-adb'
        result=Mock(returncode=1,stderr=b'error: device offline')
        with patch('autostory.device.run_command',return_value=result):
            with self.assertRaises(DeviceUnavailableError):device.raw('devices')

    def test_adb_timeout_is_device_wide(self):
        device=Device.__new__(Device);device.adb='fake-adb'
        with patch('autostory.device.run_command',side_effect=subprocess.TimeoutExpired('adb',20)):
            with self.assertRaises(DeviceUnavailableError):device.raw('devices')

    def test_user_stop_is_not_reported_as_device_failure(self):
        device=Device.__new__(Device);device.adb='fake-adb'
        with patch('autostory.device.run_command',side_effect=InterruptedError('stop')):
            with self.assertRaises(InterruptedError):device.raw('devices')

    def test_disconnect_does_not_skip_remaining_chapters(self):
        runner=Runner(Mock(),Mock());runner.phase='navigation'
        runner.run=Mock(side_effect=DeviceUnavailableError('offline'))
        with patch('autostory.controller.catalogue',return_value={'profiles':{'1':{},'2':{}},'stages':{}}), \
             patch('autostory.controller.read_json',return_value={}),patch('autostory.controller.write_json') as write, \
             patch('autostory.controller.box_is_stale',return_value=False),patch('autostory.controller.export_coverage'), \
             patch('autostory.controller.log'),patch('autostory.controller.check_stop'):
            with self.assertRaises(DeviceUnavailableError):runner.run_all()
        self.assertEqual(runner.run.call_count,1)
        checkpoint=write.call_args.args[1]
        self.assertEqual(checkpoint['state'],'error')
        self.assertEqual([r['group'] for r in checkpoint['groups']],['1'])

    def test_resume_retains_budget_and_skips_successful_scan(self):
        previous={'budget':{'sanity':20,'limit':3,'count':2},'groups':[{'group':'1','outcomes':[]},{'group':'2','blocked':'offline'}]}
        runner=Runner(Mock(),Mock());calls=[]
        def run(prefix,box,budget_state):
            calls.append((prefix,dict(budget_state)))
            budget_state.update(sanity=0,count=3)
            return []
        runner.run=run
        progress={'1-1:normal':{'code':'1-1','mode':'normal','state':'cleared'}}
        def read(path,default=None):return previous if path.name=='campaign-results.json' else progress
        with patch('autostory.controller.catalogue',return_value={'profiles':{'1':{},'2':{},'3':{}},
                'stages':{'1-1:normal':{'code':'1-1','mode':'normal','group':'1'}}}), \
             patch('autostory.controller.settings',return_value={'max_battles_per_run':3}), \
             patch('autostory.controller.read_json',side_effect=read),patch('autostory.controller.write_json'), \
             patch('autostory.controller.box_is_stale',return_value=False),patch('autostory.controller.export_coverage'), \
             patch('autostory.controller.log'),patch('autostory.controller.check_stop'):
            runner.run_all(resume=True)
        self.assertEqual(calls,[('2',{'sanity':20,'limit':3,'count':2,'practice_count':0,'practice_limit':None})])

if __name__=='__main__':unittest.main()
