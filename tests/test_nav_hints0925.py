"""0.9.25 regressions: collection-list hints and stage-id header matching.

The list hints are an optimisation only: they may skip a re-walk of the
「乐章收录」 list, but they must never turn a found chapter into a missed one,
never write outside the runtime directory and never break navigation when the
file cannot be written.
"""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from autostory import common, nav_hints
from autostory.navigation import CHAPTERS, Navigator, header_matches

ROOT = Path(__file__).resolve().parents[1]


class HintStoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(common, 'DATA', Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_unknown_chapter_has_no_hint_and_is_not_missing(self):
        self.assertIsNone(nav_hints.page_hint('AT'))
        self.assertIsNone(nav_hints.missing('AT'))

    def test_remembered_page_is_returned_and_clears_a_missing_mark(self):
        nav_hints.mark_missing('AT')
        self.assertIsNotNone(nav_hints.missing('AT'))
        nav_hints.remember_page('AT', 1, 7)
        self.assertIsNone(nav_hints.missing('AT'))
        self.assertEqual(nav_hints.page_hint('AT'), {'direction': 1, 'pages': 7})

    def test_marking_missing_drops_the_page_hint(self):
        nav_hints.remember_page('AT', 1, 7)
        nav_hints.mark_missing('AT')
        self.assertIsNone(nav_hints.page_hint('AT'))
        nav_hints.mark_missing('AT')
        self.assertEqual(nav_hints.summary(), {'pages': 0, 'missing': 1})

    def test_stale_marks_expire(self):
        nav_hints.remember_page('AT', -1, 2)
        nav_hints.mark_missing('AD')
        long_ago = time.time() - nav_hints.MISS_TTL - nav_hints.PAGE_TTL - 10
        data = common.read_json(Path(common.DATA) / nav_hints.FILE)
        for entry in list(data['pages'].values()) + list(data['missing'].values()):
            entry['at'] = long_ago
        common.write_json(Path(common.DATA) / nav_hints.FILE, data)
        self.assertIsNone(nav_hints.page_hint('AT'))
        self.assertIsNone(nav_hints.missing('AD'))

    def test_values_are_bounded_and_type_safe(self):
        nav_hints.remember_page('AT', 5, 999)
        hint = nav_hints.page_hint('AT')
        self.assertEqual(hint['direction'], 1)
        self.assertEqual(hint['pages'], nav_hints.MAX_PAGES)
        data = common.read_json(Path(common.DATA) / nav_hints.FILE)
        data['pages']['BAD'] = {'direction': 0, 'pages': 'x', 'at': time.time()}
        data['pages']['HUGE'] = {'direction': 1, 'pages': 500, 'at': time.time()}
        common.write_json(Path(common.DATA) / nav_hints.FILE, data)
        self.assertIsNone(nav_hints.page_hint('BAD'))
        self.assertIsNone(nav_hints.page_hint('HUGE'))

    def test_unwritable_store_is_ignored_instead_of_breaking_navigation(self):
        with patch.object(common, 'write_json', side_effect=OSError('denied')):
            nav_hints.remember_page('AT', 1, 3)
            nav_hints.mark_missing('AD')
        self.assertIsNone(nav_hints.page_hint('AT'))
        self.assertIsNone(nav_hints.missing('AD'))


class EnterHintTests(unittest.TestCase):
    """enter() must verify the title even when a remembered position is used."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(common, 'DATA', Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        quiet = patch('autostory.navigation.log')
        quiet.start()
        self.addCleanup(quiet.stop)
        self.prefix = 'AT'
        self.title = CHAPTERS[self.prefix]['title']
        self.image = np.zeros((720, 1280, 3), np.uint8)
        self.stories_page = [{'text': '乐章收录', 'x': 1150, 'y': 60, 'score': .99}]
        self.list_page = [{'text': '默认进度', 'x': 600, 'y': 120, 'score': .99}]
        self.title_page = self.list_page + [{'text': self.title, 'x': 620, 'y': 300, 'score': .99}]

    def nav(self, pages, tail=True):
        """pages: the list-page sequence returned after the 曲谱 screen."""
        sequence = [(self.image, self.stories_page)] + [(self.image, page) for page in pages]

        def observe(*args, **kwargs):
            return sequence.pop(0) if sequence else (self.image, pages[-1])

        nav = Navigator.__new__(Navigator)
        nav.catalog = Mock(); nav.catalog.stages.return_value = ['fixture-stage']
        nav.d = Mock()
        nav.v = Mock()
        nav.v.texts_zoom = Mock(return_value=[])
        nav.core = None
        nav.stories = Mock()
        nav.visible_stages = Mock(return_value=tail)      # the tail entry succeeds
        nav.observe = Mock(side_effect=observe)
        return nav

    def test_absent_chapter_is_refused_without_touching_the_list(self):
        nav_hints.mark_missing(self.prefix)
        nav = self.nav([self.list_page])
        with self.assertRaisesRegex(RuntimeError, '不重复翻查'):
            nav.enter(self.prefix)
        nav.d.swipe.assert_not_called()
        nav.d.tap.assert_not_called()
        nav.stories.assert_not_called()

    def test_remembered_position_is_scrolled_blindly_then_verified(self):
        nav_hints.remember_page(self.prefix, 1, 4)
        nav = self.nav([self.list_page, self.title_page,
                        [{'text': '活动主页', 'x': 640, 'y': 300}]])
        nav.enter(self.prefix)
        swipes = nav.d.swipe.call_args_list
        self.assertEqual(len(swipes), 4)
        self.assertTrue(all(call.kwargs.get('duration') == 320 for call in swipes))
        self.assertEqual(nav.d.tap.call_count, 2)          # 乐章收录 + the chapter card
        self.assertEqual(nav_hints.page_hint(self.prefix), {'direction': 1, 'pages': 4})

    def test_found_chapter_records_its_position_for_the_next_round(self):
        nav = self.nav([self.list_page, self.title_page,
                        [{'text': '活动主页', 'x': 640, 'y': 300}]])
        nav.enter(self.prefix)
        self.assertIsNotNone(nav_hints.page_hint(self.prefix))
        self.assertIsNone(nav_hints.missing(self.prefix))

    def test_chapter_missing_from_the_list_is_marked_and_reported(self):
        pages = [self.list_page] * 10
        nav = self.nav(pages)
        with self.assertRaisesRegex(RuntimeError, '未找到章节入口'):
            nav.enter(self.prefix)
        self.assertIsNotNone(nav_hints.missing(self.prefix))
        self.assertIsNone(nav_hints.page_hint(self.prefix))


class HeaderMatchTests(unittest.TestCase):
    def tokens(self, *texts):
        return [{'text': text, 'x': 900 + 10 * index, 'y': 90, 'score': .95}
                for index, text in enumerate(texts)]

    def test_caption_dash_is_not_part_of_the_id(self):
        self.assertTrue(header_matches(self.tokens('OPERATION-0-5'), '0-5'))
        self.assertTrue(header_matches(self.tokens('OPERATION', '-0-5'), '0-5'))

    def test_neighbouring_codes_are_not_accepted(self):
        for text in ('0-6', '0-59', '1-5', 'OPERATION-0-55'):
            self.assertFalse(header_matches(self.tokens(text), '0-5'), text)
        self.assertFalse(header_matches(self.tokens('17-11'), '17-1'))

    def test_exact_tokens_still_match(self):
        self.assertTrue(header_matches(self.tokens('0-5'), '0-5'))
        self.assertTrue(header_matches(self.tokens('CW-EX-7'), 'CW-EX-7'))


if __name__ == '__main__':
    unittest.main()
