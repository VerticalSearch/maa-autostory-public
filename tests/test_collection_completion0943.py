import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from autostory import collection_completion as cc
from autostory.completion import activity_progress_groups, full_activity_groups
from autostory.controller import Runner


PROFILES = {
    'GT': {'kind': 'side', 'base': 'GT', 'title': '骑兵与猎人', 'codes': ['GT-1']},
    'GT-EX': {'kind': 'side', 'base': 'GT', 'title': '骑兵与猎人', 'codes': ['GT-EX-1']},
    'EP': {'kind': 'side', 'base': 'EP', 'title': '出苍白海', 'codes': ['EP-1']},
    'SN': {'kind': 'side', 'base': 'SN', 'title': '愚人号', 'codes': ['SN-1']},
    '1': {'kind': 'main', 'codes': ['1-1']},
}


def row(text, box):
    return {'text': text, 'box': box, 'score': .99,
            'x': (box[0]+box[2])/2, 'y': (box[1]+box[3])/2}


def scene():
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[55:59, 921:999] = (220, 180, 10)
    image[272:312, 64:104] = (160, 130, 10)
    texts = [row('默认进度', [697, 28, 780, 55]), row('奖励进度', [808, 28, 892, 55]),
             row('完成进度', [920, 28, 1002, 55]), row('39/39', [106, 277, 178, 307]),
             row('骑兵与猎人', [210, 380, 296, 407])]
    return image, texts


def sample(base='GT', value=39, total=39):
    return {'base_group': base, 'title': PROFILES[base]['title'], 'screen_kind': 'collection',
            'completion_tab': True, 'current': value, 'total': total, 'score': .99,
            'probe_count': 2, 'counter_box': [106, 277, 178, 307], 'title_box': [210, 380, 296, 407]}


def proof(base='GT', value=39, total=39):
    reading = sample(base, value, total)
    return {'schema': 1, 'source': 'activity_counter', 'base_group': base,
            'current': value, 'total': total, 'observations': [reading, reading.copy()],
            'frames': [{'path': '1.png', 'sha256': 'a'*64}, {'path': '2.png', 'sha256': 'b'*64}],
            'evidence': 'proof.json', 'new_clear': False}


class CollectionViewTests(unittest.TestCase):
    def test_selected_completion_requires_headers_and_blue_underline(self):
        image, texts = scene()
        self.assertTrue(cc.selected(image, texts))
        self.assertFalse(cc.selected(np.zeros_like(image), texts))
        self.assertFalse(cc.selected(image, texts[1:]))

    def test_two_spatially_bound_passes_accept_full_card(self):
        image, texts = scene()
        with patch.object(cc.collection_ocr, 'columns', return_value=texts):
            out = cc.inspect_page(image, texts, PROFILES, Mock())
        self.assertEqual(out['GT']['current'], 39)
        self.assertEqual(out['GT']['screen_kind'], 'collection')

    def test_wrong_tab_never_requests_card_ocr(self):
        image, texts = scene(); image[55:59] = 0
        with patch.object(cc.collection_ocr, 'columns') as columns:
            self.assertEqual(cc.inspect_page(image, texts, PROFILES, Mock()), {})
        columns.assert_not_called()

    def test_badge_required_even_with_equal_numbers(self):
        image, texts = scene(); image[272:312] = 0
        with patch.object(cc.collection_ocr, 'columns', return_value=texts):
            self.assertEqual(cc.inspect_page(image, texts, PROFILES, Mock()), {})

    def test_disagreeing_ocr_pass_does_not_skip(self):
        image, texts = scene(); other = copy.deepcopy(texts); other[-2]['text'] = '38/39'
        with patch.object(cc.collection_ocr, 'columns', return_value=other):
            self.assertEqual(cc.inspect_page(image, texts, PROFILES, Mock()), {})

    def test_partial_card_is_returned_for_target_selection(self):
        image, texts = scene(); texts[-2]['text'] = '38/39'
        with patch.object(cc.collection_ocr, 'columns', return_value=texts):
            out = cc.inspect_page(image, texts, PROFILES, Mock())
        self.assertEqual((out['GT']['current'], out['GT']['total']), (38, 39))

    def test_duplicate_activity_in_one_page_is_ambiguous(self):
        image, texts = scene()
        texts += [row('38/39', [106, 487, 178, 518]), row('骑兵与猎人', [210, 617, 296, 648])]
        with patch.object(cc.collection_ocr, 'columns', return_value=texts):
            self.assertEqual(cc.inspect_page(image, texts, PROFILES, Mock()), {})

    def test_collection_proof_is_valid_for_family_but_partial_is_not_full(self):
        book = {'profiles': PROFILES}
        self.assertEqual(set(full_activity_groups(book, proof())), {'GT', 'GT-EX'})
        partial = proof('EP', 55, 56)
        self.assertEqual(activity_progress_groups(book, partial), ('EP',))
        self.assertEqual(full_activity_groups(book, partial), ())

    def test_wrong_title_or_missing_selected_tab_invalidates_proof(self):
        for field, value in [('title', '愚人号'), ('completion_tab', False), ('counter_box', [1, 1, 20, 20])]:
            bad = proof(); bad['observations'][0][field] = value
            self.assertEqual(full_activity_groups({'profiles': PROFILES}, bad), ())


class CollectionWalkTests(unittest.TestCase):
    def prepare(self):
        nav = Mock(); nav.observe.return_value = scene()
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(cc.common, 'DATA', folder))
        self.enterContext(patch.object(cc.common, 'log'))
        self.enterContext(patch.object(cc.common, 'check_stop'))
        return nav, folder

    def test_list_walk_saves_two_frames_without_opening_cards(self):
        nav, folder = self.prepare()
        with patch.object(cc, 'inspect_page', return_value={'GT': sample()}):
            out = cc.collect(nav, PROFILES, max_pages=3)
        self.assertEqual(out['GT']['current'], 39)
        self.assertTrue(Path(out['GT']['evidence']).exists())
        nav.d.tap.assert_not_called(); nav.stories.assert_not_called()
        self.assertEqual(nav.d.swipe.call_count, 4)
        self.assertFalse((folder/'progress.json').exists())
        self.assertFalse((folder/'attempt-history.json').exists())

    def test_changed_second_frame_leaves_activity_unknown(self):
        nav, _ = self.prepare()
        with patch.object(cc, 'inspect_page', side_effect=[{'GT': sample()}, {'GT': sample(value=38)}]*3):
            self.assertEqual(cc.collect(nav, PROFILES, max_pages=3), {})
        self.assertEqual(nav._completed_activities, {})

    def test_list_loss_aborts_without_clicking_or_scrolling_again(self):
        nav, _ = self.prepare(); nav.observe.side_effect = [scene(), (scene()[0], []), (scene()[0], [])]
        with self.assertRaisesRegex(RuntimeError, '列表未确认'):
            cc.collect(nav, PROFILES, max_pages=3)
        nav.d.swipe.assert_not_called(); nav.d.tap.assert_not_called()

    def test_header_crop_recovers_missing_full_frame_label_without_input(self):
        nav, _ = self.prepare(); image, texts = scene()
        nav.observe.return_value = (image, texts[1:])
        nav.v.texts_zoom.return_value = texts[:3]
        image, recovered = cc.observe_selected(nav)
        self.assertTrue(cc.selected(image, recovered))
        nav.d.invalidate_frame.assert_not_called(); nav.d.tap.assert_not_called()

    def test_fresh_observation_recovers_after_one_ambiguous_header(self):
        nav, _ = self.prepare(); image, texts = scene()
        nav.observe.side_effect = [(image, []), (image, texts)]
        nav.v.texts_zoom.return_value = []
        self.assertTrue(cc.selected(*cc.observe_selected(nav)))
        nav.d.invalidate_frame.assert_called_once_with(); nav.d.swipe.assert_not_called()

    def test_wrong_tab_cannot_pass_header_crop_and_saves_rejected_frames(self):
        nav, folder = self.prepare(); image, texts = scene(); image[55:59] = 0
        nav.observe.return_value = (image, texts); nav.v.texts_zoom.return_value = texts[:3]
        with self.assertRaisesRegex(RuntimeError, '列表未确认'):
            cc.observe_selected(nav)
        self.assertEqual(len(list((folder/'screens/activity-progress-rejected').rglob('*.png'))), 2)
        nav.d.tap.assert_not_called(); nav.d.swipe.assert_not_called()

    def test_stop_propagates_before_any_observation(self):
        nav, _ = self.prepare()
        with patch.object(cc.common, 'check_stop', side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):cc.collect(nav, PROFILES)
        nav.observe.assert_not_called()


class CollectionControllerTests(unittest.TestCase):
    def test_full_and_unknown_never_enter_only_incomplete_activity_is_processed(self):
        class Nav:
            supports_activity_progress = True
            collect_activity_progress = Mock(return_value={'GT': proof(), 'EP': proof('EP', 55, 56)})
        runner = Runner.__new__(Runner); runner.nav = Nav(); runner.core = Mock()
        runner.recover_previous_failures = Mock()
        runner.run = Mock(return_value=[])
        book = {'profiles': PROFILES, 'stages': {
            code+':normal': {'code': code, 'mode': 'normal', 'group': group}
            for group, p in PROFILES.items() for code in p['codes']}}
        ledger = {'sanity': 17, 'count': 10, 'limit': None, 'practice_count': 31,
                  'practice_limit': None, 'stages': {}}
        prior = {'budget': ledger, 'groups': []}; writes = []
        with patch('autostory.controller.catalogue', return_value=book), \
             patch('autostory.controller.settings', return_value={}), \
             patch('autostory.controller.assert_execution_unlocked'), \
             patch('autostory.controller.log'), patch('autostory.controller.export_coverage'), \
             patch('autostory.controller.workflow.update'), patch('autostory.controller.box_is_stale', return_value=False), \
             patch('autostory.controller.read_json', side_effect=lambda p, d=None: prior if p.name=='campaign-results.json' else {}), \
             patch('autostory.controller.write_json', side_effect=lambda p, v:writes.append((p.name, copy.deepcopy(v)))):
            runner.run_all(resume=True)
        self.assertEqual([c.args[0] for c in runner.run.call_args_list], ['1', 'EP'])
        latest = [v for n, v in writes if n=='campaign-results.json'][-1]
        groups = {r['group']: r for r in latest['groups']}
        self.assertEqual(groups['GT']['skip_reason'], 'activity_counter_full')
        self.assertEqual(groups['GT-EX']['skip_reason'], 'activity_counter_full')
        self.assertEqual(groups['SN']['error_kind'], 'activity_progress_unconfirmed')
        self.assertEqual(latest['budget'], ledger)


if __name__ == '__main__':
    unittest.main()
