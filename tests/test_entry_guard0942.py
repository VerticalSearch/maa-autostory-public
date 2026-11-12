"""Only a confirmed collection traversal can produce a local unavailable entry."""
from unittest.mock import Mock

from autostory.navigation import CHAPTERS
from autostory.navigation_errors import ChapterEntryUnavailable
from autostory import nav_hints
from test_collection0930 import TempRuntime, replay, image, stories_page, list_page


class EntryGuardTests(TempRuntime):
    def test_lost_list_does_not_click_title_or_write_negative_hint(self):
        foreign = [{'text': CHAPTERS['AD']['title'], 'x': 620, 'y': 300}]
        nav = self.nav(replay([(image(), stories_page()), (image(), list_page()), (image(), foreign)]))
        nav.chapter_candidates = Mock(return_value=foreign)
        with self.assertRaisesRegex(RuntimeError, '未确认乐章收录列表') as caught:
            nav.enter('AD')
        self.assertNotIsInstance(caught.exception, ChapterEntryUnavailable)
        self.assertEqual(nav.d.tap.call_count, 1)  # Only opening the collection.
        nav.d.swipe.assert_not_called()
        nav.chapter_candidates.assert_not_called()
        self.mark_missing.assert_not_called()

    def test_confirmed_actual_search_and_cache_have_distinct_metadata(self):
        nav = self.nav(replay([(image(), stories_page()), (image(), list_page())]))
        with self.assertRaises(ChapterEntryUnavailable) as actual:
            nav.enter('AD')
        self.assertEqual(actual.exception.base, 'AD')
        self.assertFalse(actual.exception.cached)
        self.assertIsNotNone(nav_hints.missing('AD'))
        later = self.nav([])
        with self.assertRaises(ChapterEntryUnavailable) as cached:
            later.enter('AD-EX')
        self.assertEqual(cached.exception.base, 'AD')
        self.assertTrue(cached.exception.cached)
        later.observe.assert_not_called()
        later.d.tap.assert_not_called()
        later.d.swipe.assert_not_called()
