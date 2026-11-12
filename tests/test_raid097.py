import unittest
from pathlib import Path
from unittest.mock import Mock
import cv2,numpy as np
from autostory.controller import Runner
from autostory.navigation import header_matches
from autostory.stage_header import contrast_header
from autostory.vision import Vision

ROOT=Path(__file__).resolve().parents[1]

class RaidHeaderTests(unittest.TestCase):
    def test_real_raid_id_and_prefix_negative(self):
        v=Vision(backend='cpu')
        cases=[('runtime/campaign-continuation-20261031/R8-8-raid-header-failure.png','R8-8','R8-9'),
               ('runtime/battle-verification-20261031/R8-10-raid-after.png','R8-10','R8-1')]
        for name,code,wrong in cases:
            path=ROOT/name
            if not path.exists():self.skipTest('Live fixture unavailable')
            im=cv2.imdecode(np.fromfile(path,np.uint8),1)
            texts=contrast_header(v,im,code,mode='raid')
            self.assertTrue(header_matches(texts,code),texts)
            self.assertFalse(header_matches(texts,wrong),texts)

    def test_navigation_error_proves_dispatch_absent(self):
        core=Mock();nav=Mock();nav.find.side_effect=RuntimeError('header unknown')
        runner=Runner(core,nav,history=Mock())
        with self.assertRaisesRegex(RuntimeError,'header unknown'):runner.battle('R8-8',Path('job.json'),False,'raid')
        self.assertIs(runner.formal_dispatched,False);core.run.assert_not_called()

    def test_core_exception_is_not_proof_of_absent_start(self):
        core=Mock();nav=Mock();nav.find.return_value={'state':'uncleared'}
        nav.observe.return_value=(None,[{'text':'开始突袭'}]);core.run.side_effect=ConnectionError('lost response')
        runner=Runner(core,nav,history=Mock())
        with self.assertRaises(ConnectionError):runner.battle('R8-8',Path('job.json'),False,'raid')
        self.assertIs(runner.formal_dispatched,True);core.run.assert_called_once()
