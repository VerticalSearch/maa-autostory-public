"""0.9.26 regressions: one-pass collection index and index-ordered scanning.

The index is advisory: it may reorder a round and refuse a chapter that the list
does not contain, but it must never replace title verification and must degrade
to the old behaviour when it is unavailable, stale or not supported.
"""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from autostory import collection_index, common, nav_hints, scanning
from autostory.navigation import CHAPTERS, Navigator

ROOT = Path(__file__).resolve().parents[1]


class TempRuntime(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for target in (patch.object(common, 'DATA', Path(tmp.name)),
                       patch('autostory.navigation.log'),
                       patch.object(nav_hints, 'mark_missing', wraps=nav_hints.mark_missing)):
            target.start()
            self.addCleanup(target.stop)


class IndexStoreTests(TempRuntime):
    def test_absent_then_written_and_read_back(self):
        self.assertIsNone(collection_index.load())
        saved = collection_index.save({'墟': 3, '绿野幻梦': 0}, 6)
        self.assertEqual(saved['pages'], 6)
        self.assertEqual(collection_index.page_of(saved, '墟'), 3)
        self.assertEqual(collection_index.summary(saved), {'indexed': 2, 'pages': 6, 'complete': False})

    def test_titles_are_folded_and_unknown_titles_are_none(self):
        collection_index.save({' 绿野 幻梦 ': 2}, 4)
        index = collection_index.load()
        self.assertEqual(collection_index.page_of(index, '绿野幻梦'), 2)
        self.assertIsNone(collection_index.page_of(index, '墟'))

    def test_short_titles_do_not_match_by_containment(self):
        collection_index.save({'废墟': 1}, 2)
        index = collection_index.load()
        self.assertIsNone(collection_index.page_of(index, '墟'))
        self.assertEqual(collection_index.page_of(index, '废墟'), 1)

    def test_stale_or_malformed_index_is_ignored(self):
        index = collection_index.save({'墟': 3}, 6)
        data = common.read_json(Path(common.DATA) / collection_index.FILE)
        data['at'] = time.time() - collection_index.TTL - 60
        common.write_json(Path(common.DATA) / collection_index.FILE, data)
        self.assertIsNone(collection_index.load())
        for broken in ({'pages_by_title': {}}, {'schema': 99}, [], 'x'):
            common.write_json(Path(common.DATA) / collection_index.FILE, broken)
            self.assertIsNone(collection_index.load(), broken)

    def test_read_only_runtime_does_not_raise(self):
        with patch.object(common, 'write_json', side_effect=OSError('denied')):
            self.assertIsNone(collection_index.save({'墟': 3}, 6))
        self.assertIsNone(collection_index.load())


class OrderingTests(unittest.TestCase):
    def setUp(self):
        self.index = {'pages_by_title': {collection_index.key(CHAPTERS['AT']['title']): 4,
                                         collection_index.key(CHAPTERS['AD']['title']): 1},
                      'pages': 8, 'at': 0.0}

    def test_main_chapters_first_then_list_order_then_unknown(self):
        groups = ['AT', '17', 'ZZ', 'AD', '1']
        ordered = scanning.order_by_index(groups, self.index)
        self.assertEqual(ordered[:2], ['17', '1'])
        self.assertEqual(ordered[2:4], ['AD', 'AT'])
        self.assertEqual(ordered[4], 'ZZ')          # unknown page sorts last

    def test_equal_pages_keep_the_planned_order(self):
        groups = ['AT-EX', 'AT', 'AT-S']
        self.assertEqual(scanning.order_by_index(groups, self.index), groups)


class EnsureIndexTests(TempRuntime):
    def test_a_plain_mock_navigator_cannot_claim_the_capability(self):
        nav = Mock()
        self.assertIsNone(scanning.ensure_collection_index(nav, ['AT']))
        nav.build_collection_index.assert_not_called()

    def test_disabled_by_settings_and_no_side_story_groups(self):
        nav = Navigator.__new__(Navigator)
        nav.build_collection_index = Mock(return_value={'pages_by_title': {'x': 1}, 'pages': 2, 'at': 0.0})
        with patch.object(scanning, 'settings', Mock(return_value={'collection_index': {'enabled': False}})):
            self.assertIsNone(scanning.ensure_collection_index(nav, ['AT']))
        self.assertIsNone(scanning.ensure_collection_index(nav, ['17', '1']))
        nav.build_collection_index.assert_not_called()

    def test_cached_index_is_reused_and_stale_one_is_rebuilt(self):
        nav = Navigator.__new__(Navigator)
        nav.build_collection_index = Mock(return_value={'pages_by_title': {'a': 1}, 'pages': 2, 'at': 0.0})
        collection_index.save({CHAPTERS['AT']['title']: 2}, 5)
        cached = scanning.ensure_collection_index(nav, ['AT'])
        self.assertEqual(cached['pages'], 5)
        nav.build_collection_index.assert_not_called()
        data = common.read_json(Path(common.DATA) / collection_index.FILE)
        data['at'] = time.time() - collection_index.TTL - 60
        common.write_json(Path(common.DATA) / collection_index.FILE, data)
        built = scanning.ensure_collection_index(nav, ['AT'])
        self.assertEqual(built['pages'], 2)
        nav.build_collection_index.assert_called_once()


class EnterWithIndexTests(TempRuntime):
    def nav(self, observes=()):
        nav = Navigator.__new__(Navigator)
        nav.catalog = Mock(); nav.catalog.stages.return_value = ['fixture-stage']
        nav.d = Mock()
        nav.v = Mock()
        nav.v.texts_zoom = Mock(return_value=[])
        nav.core = None
        nav.stories = Mock()
        nav.visible_stages = Mock(return_value=True)
        sequence = list(observes)

        def observe(*args, **kwargs):
            return sequence.pop(0) if sequence else (np.zeros((720, 1280, 3), np.uint8),
                                                     [{'text': '默认进度', 'x': 600, 'y': 120}])

        nav.observe = Mock(side_effect=observe)
        return nav

    def test_a_complete_index_does_not_prove_absence(self):
        # A complete walk only means the list was traversed. Decorative titles
        # are missed by OCR, so the chapter must still be searched for.
        nav = self.nav()
        nav.collection_index = {'pages_by_title': {collection_index.key('别的活动'): 2},
                                'pages': 5, 'complete': True, 'at': 0.0}
        image = np.zeros((720, 1280, 3), np.uint8)
        nav.observe.side_effect = [
            (image, [{'text': '乐章收录', 'x': 1150, 'y': 60}]),
            (image, [{'text': '默认进度', 'x': 600, 'y': 120}]),
            (image, [{'text': '默认进度'}, {'text': CHAPTERS['AT']['title'], 'x': 620, 'y': 300}]),
            (image, [{'text': '活动主页', 'x': 640, 'y': 300}]),
            (image, [{'text': '活动主页', 'x': 640, 'y': 300}]),
        ]
        nav.enter('AT')
        nav.stories.assert_called_once()
        self.assertEqual(nav.d.tap.call_count, 2)      # 乐章收录 + the card
        self.assertIsNone(nav_hints.missing('AT'))

    def test_a_partial_index_never_refuses_a_chapter(self):
        # MT was reachable in the field while absent from a 24-title partial index,
        # so an incomplete index must only reorder, never refuse.
        nav = self.nav()
        nav.collection_index = {'pages_by_title': {collection_index.key('别的活动'): 2},
                                'pages': 5, 'complete': False, 'at': 0.0}
        image = np.zeros((720, 1280, 3), np.uint8)
        nav.observe.side_effect = ([(image, [{'text': '乐章收录', 'x': 1150, 'y': 60}]),
                                    (image, [{'text': '默认进度', 'x': 600, 'y': 120}])]
                                   + [(image, [{'text': '默认进度', 'x': 600, 'y': 120}])] * 10)
        with self.assertRaises(RuntimeError) as ctx:
            nav.enter('AT')
        message = str(ctx.exception)
        self.assertIn('未找到章节入口', message)
        self.assertNotIn('已完整核对', message)
        nav.stories.assert_called_once()

    def test_indexed_position_is_used_as_the_first_guess(self):
        nav = self.nav()
        nav.collection_index = {'pages_by_title': {collection_index.key(CHAPTERS['AT']['title']): 3},
                                'pages': 6, 'at': 0.0}
        title = CHAPTERS['AT']['title']
        image = np.zeros((720, 1280, 3), np.uint8)
        nav.observe.side_effect = [
            (image, [{'text': '乐章收录', 'x': 1150, 'y': 60}]),
            (image, [{'text': '默认进度', 'x': 600, 'y': 120}]),
            (image, [{'text': '默认进度'}, {'text': title, 'x': 620, 'y': 300}]),
            (image, [{'text': '活动主页', 'x': 640, 'y': 300}]),
            (image, [{'text': '活动主页', 'x': 640, 'y': 300}]),
        ]
        nav.enter('AT')
        swipes = nav.d.swipe.call_args_list
        self.assertEqual(len(swipes), 3)
        self.assertTrue(all(call.kwargs.get('duration') == 320 for call in swipes))
        self.assertEqual(nav_hints.page_hint('AT'), {'direction': 1, 'pages': 3})


class BuildIndexTests(TempRuntime):
    def test_one_pass_records_pages_and_stops_on_a_still_list(self):
        nav = Navigator.__new__(Navigator)
        nav.d = Mock()
        nav.v = Mock()
        nav.v.texts_zoom = Mock(return_value=[])
        nav.core = None
        nav.stories = Mock()
        nav.click_text = Mock(return_value=True)
        image = np.zeros((720, 1280, 3), np.uint8)
        title_a, title_b = CHAPTERS['AT']['title'], CHAPTERS['AD']['title']
        pages = [
            [{'text': '默认进度', 'x': 600, 'y': 120}],      # pre-click page
            [{'text': '默认进度', 'x': 600, 'y': 120}],      # verified list page
            [{'text': title_a, 'x': 620, 'y': 300}],        # list page 0
            [{'text': title_b, 'x': 620, 'y': 300}],        # list page 1
            [{'text': '无关文字', 'x': 620, 'y': 300}],     # page 2: list stops moving
        ]
        sequence = [(image, texts) for texts in pages]
        nav.observe = Mock(side_effect=lambda *a, **k: sequence.pop(0) if sequence else (image, pages[-1]))
        index = nav.build_collection_index([title_a, title_b], max_pages=6)
        self.assertEqual(index['pages_by_title'][collection_index.key(title_a)], 0)
        self.assertEqual(index['pages_by_title'][collection_index.key(title_b)], 1)
        self.assertEqual(collection_index.summary(index)['indexed'], 2)
        self.assertIsNotNone(collection_index.load())


if __name__ == '__main__':
    unittest.main()



