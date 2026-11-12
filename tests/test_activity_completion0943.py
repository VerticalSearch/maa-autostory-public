"""Activity-wide skip acceptance without any emulator or account access."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from autostory.activity_completion import inspect_full_counter, ActivityAlreadyComplete
from autostory.completion import summarize_execution, full_activity_groups
from autostory.controller import Runner
from autostory.navigation import Navigator


PROFILES = {
    'CB': {'kind': 'side', 'base': 'CB', 'title': '喧闹法则', 'codes': ['CB-1', 'CB-2']},
    'CB-EX': {'kind': 'side', 'base': 'CB', 'title': '喧闹法则', 'codes': ['CB-EX1']},
    'DH': {'kind': 'side', 'base': 'DH', 'title': '多索雷斯假日', 'codes': ['DH-1', 'DH-2']},
    '1': {'kind': 'main', 'codes': ['1-1', '1-2']},
}
BOOK = {'profiles': PROFILES, 'stages': {
    'CB-1:normal': {'code': 'CB-1', 'mode': 'normal', 'group': 'CB'},
    'CB-EX1:raid': {'code': 'CB-EX1', 'mode': 'raid', 'group': 'CB-EX'},
    'DH-1:normal': {'code': 'DH-1', 'mode': 'normal', 'group': 'DH'},
}}
LEDGER = {'sanity': 17, 'count': 10, 'limit': None, 'practice_count': 31,
          'practice_limit': None, 'stages': {}}


def row(text, box, score=.99):
    return {'text': text, 'box': list(box), 'score': score,
            'x': (box[0]+box[2])/2, 'y': (box[1]+box[3])/2}


def scene(kind='map'):
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    if kind == 'map':
        image[640:696, 20:74] = (220, 190, 20)
        texts = [row('CB-1', (300, 300, 380, 325)), row('CB-2', (700, 350, 780, 375))]
        ratio = row('62/62', (83, 644, 185, 687))
    else:
        image[595:640, 752:796] = (220, 190, 20)
        texts = [row('多索雷斯假日', (960, 77, 1101, 119)),
                 row('进入活动', (1091, 604, 1180, 630)), row('本章回想', (75, 108, 158, 134)),
                 row('3/3', (1130, 36, 1180, 66))]
        ratio = row('78/78', (802, 601, 890, 635))
    return image, texts, ratio


def proof():
    sample = {'base_group': 'CB', 'screen_kind': 'map', 'current': 62,
              'total': 62, 'score': .98, 'probe_count': 2}
    return {'schema': 1, 'source': 'activity_counter', 'base_group': 'CB', 'current': 62,
            'total': 62, 'new_clear': False, 'observations': [sample.copy(), sample.copy()],
            'frames': [{'path': '1.png', 'sha256': 'a'*64}, {'path': '2.png', 'sha256': 'b'*64}],
            'evidence': 'proof.json'}


class CounterContextTests(unittest.TestCase):
    def inspect(self, kind='map', transform=None, prefix=None):
        image, texts, ratio = scene(kind)
        if transform: image, texts, ratio = transform(image, texts, ratio)
        vision = Mock(); vision.texts_zoom.return_value = [ratio]
        out = inspect_full_counter(image, texts, prefix or ('CB' if kind == 'map' else 'DH'), PROFILES, vision)
        return out, vision

    def test_map_full_counter_accepts_whole_activity_from_partition_request(self):
        result, vision = self.inspect(prefix='CB-EX')
        self.assertEqual((result['base_group'], result['current'], result['screen_kind']), ('CB', 62, 'map'))
        self.assertEqual(vision.texts_zoom.call_count, 2)

    def test_intro_accepts_full_counter_without_entry(self):
        result, _ = self.inspect('intro')
        self.assertEqual((result['base_group'], result['current'], result['screen_kind']), ('DH', 78, 'intro'))

    def test_story_node_corroborates_one_catalogued_battle_node(self):
        for label in ('CB-ST1', 'CB-ST-2'):
            result, _ = self.inspect(transform=lambda im, t, r: (im,
                [t[0], row(label, (700, 350, 800, 380))], r))
            self.assertEqual(result['base_group'], 'CB')

    def test_story_nodes_alone_or_unknown_battle_node_do_not_establish_identity(self):
        for first in ('CB-ST-1', 'CB-999'):
            result, vision = self.inspect(transform=lambda im, t, r: (im,
                [row(first, (300, 300, 400, 330)), row('CB-ST-2', (700, 350, 800, 380))], r))
            self.assertIsNone(result); vision.texts_zoom.assert_not_called()

    def test_story_node_requires_same_activity_exact_token_and_map_position(self):
        cases = [('FC-ST-2', (700, 350, 800, 380), .99),
                 ('CB-ST-2x', (700, 350, 800, 380), .99),
                 ('CB-ST-0', (700, 350, 800, 380), .99),
                 ('CB-ST-2', (700, 650, 800, 680), .99),
                 ('CB-ST-2', (700, 350, 800, 380), .89)]
        for label, box, score in cases:
            result, vision = self.inspect(transform=lambda im, t, r: (im,
                [t[0], row(label, box, score)], r))
            self.assertIsNone(result); vision.texts_zoom.assert_not_called()

    def test_fc_end_of_map_with_story_nodes_uses_real_catalogue(self):
        from autostory.campaign import catalogue
        im, _, _ = scene(); vision = Mock()
        vision.texts_zoom.return_value = [row('56/56', (83, 644, 185, 687))]
        texts = [row('FC-ST-2', (163, 237, 265, 269)),
                 row('FC-8', (325, 365, 387, 393)), row('FC-ST-3', (589, 430, 689, 460))]
        for prefix in ('FC', 'FC-EX', 'FC-TR'):
            out = inspect_full_counter(im, texts, prefix, catalogue()['profiles'], vision)
            self.assertEqual((out['base_group'], out['current'], out['total']), ('FC', 56, 56))

    def test_wrong_activity_and_main_story_cannot_inherit_a_full_counter(self):
        for prefix in ('DH', '1', 'CURRENT', 'missing'):
            with self.subTest(prefix=prefix):
                result, _ = self.inspect(prefix=prefix)
                self.assertIsNone(result)

    def test_incomplete_counter_continues_normal_flow(self):
        result, _ = self.inspect(transform=lambda im, t, r: (im, t, {**r, 'text': '61/62'}))
        self.assertIsNone(result)

    def test_badge_is_required(self):
        result, vision = self.inspect(transform=lambda im, t, r: (np.zeros_like(im), t, r))
        self.assertIsNone(result); vision.texts_zoom.assert_not_called()

    def test_detail_view_is_not_a_full_map(self):
        result, _ = self.inspect(transform=lambda im, t, r: (im, t+[row('开始行动', (1000, 650, 1200, 690))], r))
        self.assertIsNone(result)

    def test_intro_top_right_resource_is_not_completion(self):
        result, _ = self.inspect('intro', transform=lambda im, t, r: (im, t, row('3/3', (1130, 36, 1180, 66))))
        self.assertIsNone(result)

    def test_intro_wrong_title_or_only_carousel_title_is_not_identity(self):
        for title in ('其他活动', '多索雷斯假日'):
            result, _ = self.inspect('intro', transform=lambda im, t, r: (im,
                [row(title, (336, 594, 408, 622))]+t[1:], r))
            self.assertIsNone(result)

    def test_two_crop_readings_must_agree(self):
        im, texts, ratio = scene(); v = Mock()
        v.texts_zoom.side_effect = [[ratio], [{**ratio, 'text': '63/63'}]]
        self.assertIsNone(inspect_full_counter(im, texts, 'CB', PROFILES, v))

    def test_low_confidence_does_not_skip(self):
        result, _ = self.inspect(transform=lambda im, t, r: (im, t, {**r, 'score': .8}))
        self.assertIsNone(result)


class NavigatorSkipTests(unittest.TestCase):
    def prepare(self):
        nav = Navigator.__new__(Navigator); nav.d = Mock(); nav.v = Mock()
        nav._full_activity_skip_enabled = True; nav._completed_activities = {}
        im, texts, ratio = scene(); nav.observe = Mock(return_value=(im, texts))
        nav.v.texts_zoom.return_value = [ratio]
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch('autostory.navigation.DATA', Path(folder)))
        self.enterContext(patch('autostory.navigation.CHAPTERS', PROFILES))
        return nav, Path(folder)

    def test_two_fresh_observations_save_proof_without_clicking(self):
        nav, folder = self.prepare()
        with self.assertRaises(ActivityAlreadyComplete) as raised:nav._maybe_skip_full_activity('CB-EX')
        self.assertEqual(nav.observe.call_count, 2)
        nav.d.invalidate_frame.assert_called_once_with()
        nav.d.tap.assert_not_called(); nav.d.swipe.assert_not_called(); nav.d.back.assert_not_called()
        self.assertTrue(Path(raised.exception.proof['evidence']).is_file())
        self.assertEqual(set(full_activity_groups(BOOK, raised.exception.proof)), {'CB', 'CB-EX'})
        self.assertFalse((folder/'progress.json').exists())
        self.assertFalse((folder/'attempt-history.json').exists())

    def test_changed_second_frame_does_not_skip(self):
        nav, folder = self.prepare(); im, texts, ratio = scene()
        nav.v.texts_zoom.side_effect = [[ratio], [ratio], [{**ratio, 'text': '61/62'}]]
        nav._maybe_skip_full_activity('CB')
        self.assertEqual(nav._completed_activities, {})
        self.assertFalse(list(folder.rglob('proof.json')))

    def test_skip_disabled_during_recovery_and_verification(self):
        nav, _ = self.prepare(); nav._full_activity_skip_enabled = False
        nav._maybe_skip_full_activity('CB'); nav.observe.assert_not_called()

    def test_entry_skips_before_resource_check_or_zone_navigation(self):
        nav, _ = self.prepare(); nav.catalog = Mock()
        nav.catalog.stages.side_effect = AssertionError('should not require a zone map for a full activity')
        with patch('autostory.navigation.log'), patch('autostory.workflow.update'):
            with self.assertRaises(ActivityAlreadyComplete):nav.enter('CB-EX')
        nav.catalog.stages.assert_not_called(); nav.d.tap.assert_not_called()

    def test_stop_is_honoured_before_cached_skip(self):
        nav, _ = self.prepare(); nav._completed_activities = {'CB': proof()}
        with patch('autostory.navigation.check_stop', side_effect=InterruptedError('stopped')):
            with self.assertRaises(InterruptedError):nav._maybe_skip_full_activity('CB')


class AggregateSummaryTests(unittest.TestCase):
    def test_full_activity_is_separate_from_stage_wins(self):
        progress = {'CB-1:normal': {'state': 'uncleared'}}; before = copy.deepcopy(progress)
        reports = [{'group': group, 'activity_completion': proof(), 'outcomes': []} for group in ['CB', 'CB-EX']]
        out = summarize_execution(BOOK, progress, ['CB', 'CB-EX'], LEDGER, reports)
        self.assertEqual(out['state'], 'done')
        self.assertEqual(out['summary']['cleared'], 0)
        self.assertEqual(out['summary']['activity_complete'], 2)
        self.assertEqual(progress, before)

    def test_other_activities_remain_pending(self):
        out = summarize_execution(BOOK, {}, ['CB', 'DH'], LEDGER,
            [{'group': 'CB', 'activity_completion': proof(), 'outcomes': []}])
        self.assertEqual(out['state'], 'partial')
        self.assertEqual(out['summary']['unknown'], 1)

    def test_malformed_single_frame_or_incomplete_evidence_is_rejected(self):
        for field, value in [('total', 63), ('observations', proof()['observations'][:1]),
                             ('new_clear', True), ('base_group', 'DH'), ('frames', [])]:
            with self.subTest(field=field):
                bad = {**proof(), field: value}
                self.assertEqual(full_activity_groups(BOOK, bad), ())


class ControllerSkipTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('autostory.controller.workflow.update'))
        self.enterContext(patch('autostory.controller.assert_execution_unlocked'))
        self.enterContext(patch('autostory.controller.log'))
        self.enterContext(patch('autostory.controller.export_coverage'))
        self.enterContext(patch('autostory.controller.catalogue', return_value=BOOK))
        self.enterContext(patch('autostory.controller.settings', return_value={}))
        self.writes=[]
        self.enterContext(patch('autostory.controller.write_json', side_effect=lambda p,v:self.writes.append((p.name,copy.deepcopy(v)))))

    def test_full_activity_does_not_scan_plan_or_start_battle(self):
        runner=Runner.__new__(Runner);runner.nav=Mock();runner.core=Mock()
        runner.nav._full_activity_skip_enabled=False
        runner.nav.enter.side_effect=ActivityAlreadyComplete(proof())
        ledger=copy.deepcopy(LEDGER)
        self.enterContext(patch('autostory.controller.read_json', return_value={}))
        plan=self.enterContext(patch('autostory.controller.build_plan'))
        self.assertEqual(runner.run('CB-EX',box={},budget_state=ledger),[])
        runner.nav.scan.assert_not_called();plan.assert_not_called();runner.core.run.assert_not_called()
        self.assertEqual(ledger,LEDGER);self.assertIs(runner.nav._full_activity_skip_enabled,False)

    def test_campaign_skips_every_sibling_after_first_full_activity_observation(self):
        runner=Runner.__new__(Runner);runner.core=Mock();runner.recover_previous_failures=Mock()
        previous={'budget':copy.deepcopy(LEDGER),'groups':[{'group':'CB','blocked':'old zone error'}]}
        progress={'CB-EX1:raid':{'state':'uncleared'}}
        self.enterContext(patch('autostory.controller.read_json',side_effect=lambda p,d=None:
            previous if p.name=='campaign-results.json' else progress if p.name=='progress.json' else d))
        self.enterContext(patch('autostory.controller.box_is_stale',return_value=False))
        def run(prefix,box,budget_state):
            runner.last_activity_skip=proof() if prefix.startswith('CB') else None
            return []
        runner.run=Mock(side_effect=run)
        runner.run_all(resume=True)
        self.assertEqual([c.args[0] for c in runner.run.call_args_list],['CB-EX','1','DH'])
        latest={r['group']:r for name,value in self.writes if name=='campaign-results.json' for r in value['groups']}
        for group in ['CB','CB-EX']:
            self.assertEqual(latest[group]['skip_reason'],'activity_counter_full')
            self.assertNotIn('blocked',latest[group])
        final=[v for n,v in self.writes if n=='campaign-results.json'][-1]
        self.assertEqual(final['budget'],LEDGER)


if __name__ == '__main__':unittest.main()
