"""0.9.30 regressions: advisory collection index, bounded entry retries.

The index records positions only. A complete walk does not prove that a
decorative title was recognised, and a negative hint only records a real
targeted lookup failure.
"""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from autostory import collection_index, collection_ocr, common, nav_hints
from autostory.common import DeviceUnavailableError
from autostory.navigation import CHAPTERS, Navigator


def image(seed=0):
    frame = np.zeros((720, 1280, 3), np.uint8)
    frame[:, :] = (seed * 37 % 200, seed * 53 % 200, seed * 71 % 200)
    return frame


def stories_page():
    return [{'text': '乐章收录', 'x': 1150, 'y': 60, 'score': .99}]


def list_page():
    return [{'text': '默认进度', 'x': 600, 'y': 120, 'score': .99}]


def title_page(title):
    return list_page()+[{'text': title, 'x': 620, 'y': 300, 'score': .99}]


def activity_page():
    return [{'text': '活动主页', 'x': 640, 'y': 300, 'score': .99}]


def replay(items):
    """Return queued observations, repeating the last one forever."""
    queue = list(items)

    def observe(*args, **kwargs):
        if len(queue) > 1:
            return queue.pop(0)
        return queue[0]

    return observe


class TempRuntime(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        for target in (patch.object(common, 'DATA', self.data),
                       patch('autostory.navigation.log')):
            target.start()
            self.addCleanup(target.stop)
        patcher = patch.object(nav_hints, 'mark_missing', wraps=nav_hints.mark_missing)
        self.mark_missing = patcher.start()
        self.addCleanup(patcher.stop)

    def nav(self, observe, tail=True):
        nav = Navigator.__new__(Navigator)
        nav.catalog = Mock(); nav.catalog.stages.return_value = ['fixture-stage']
        nav.d = Mock()
        nav.v = Mock()
        nav.v.texts_zoom = Mock(return_value=[])
        nav.core = None
        nav.stories = Mock()
        nav.visible_stages = Mock(return_value=tail)
        nav.collection_index = None
        nav.observe = Mock(side_effect=observe)
        return nav


class TitleMatchTests(unittest.TestCase):
    def test_extra_han_text_is_not_a_decorative_allowance(self):
        self.assertFalse(collection_index.matches('新生路途', '生路'))
        self.assertFalse(collection_index.matches('生路新', '生路'))
        self.assertFalse(collection_index.matches('新的生路', '生路'))

    def test_ascii_and_punctuation_decoration_is_accepted(self):
        self.assertTrue(collection_index.matches('+揭幕者们*', '揭幕者们'))
        self.assertTrue(collection_index.matches('LIEBI尘影余音', '尘影余音'))
        self.assertTrue(collection_index.matches('生路：', '生路'))
        self.assertTrue(collection_index.matches('NEW-生路1', '生路'))

    def test_the_full_title_must_be_present(self):
        self.assertTrue(collection_index.matches('吾导先路', '吾导先路'))
        self.assertFalse(collection_index.matches('吾导先', '吾导先路'))
        self.assertFalse(collection_index.matches('理想城', '理想城：长夏狂欢季'))

    def test_single_glyph_titles_are_exact_only(self):
        self.assertTrue(collection_index.matches('墟', '墟'))
        self.assertFalse(collection_index.matches('废墟', '墟'))
        self.assertFalse(collection_index.matches('墟：', '墟'))
        self.assertFalse(collection_index.matches('墟区', '墟'))

    def test_decoration_length_is_still_bounded(self):
        self.assertFalse(collection_index.matches('ABCDEFG生路', '生路'))


class CandidateTests(TempRuntime):
    def test_index_and_target_lookup_share_card_ocr_without_storyline_caption(self):
        nav=self.nav([])
        nav.v.texts_zoom=Mock(side_effect=lambda *args,**kwargs:[
            {'text':'崔林特尔梅之金','x':770,'y':270}])
        result=nav.chapter_candidates(image(), [], {'title':'崔林特尔梅之金','line':'off-screen'})
        self.assertTrue(result)
        self.assertEqual(nav.v.texts_zoom.call_count,collection_ocr.MAX_CROPS)

    def test_chapter_lookup_does_not_accept_a_longer_single_glyph_label(self):
        nav = self.nav(replay([(image(0), list_page())]))
        self.assertEqual(nav.chapter_candidates(
            image(0), [{'text': '废墟', 'x': 620, 'y': 300}], CHAPTERS['AT']), [])
        found = nav.chapter_candidates(
            image(0), [{'text': '墟', 'x': 620, 'y': 300}], CHAPTERS['AT'])
        self.assertEqual([x['text'] for x in found], ['墟'])

    def test_chapter_lookup_rejects_chinese_prefix_or_suffix(self):
        nav = self.nav(replay([(image(0), list_page())]))
        for label in ('新生路途', '生路新'):
            self.assertEqual(nav.chapter_candidates(
                image(0), [{'text': label, 'x': 620, 'y': 300}], CHAPTERS['BP']), [])
        found = nav.chapter_candidates(
            image(0), [{'text': '生路：', 'x': 620, 'y': 300}], CHAPTERS['BP'])
        self.assertEqual([x['text'] for x in found], ['生路：'])

    def test_cropped_fallback_propagates_stop(self):
        nav = self.nav(replay([(image(0), list_page())]))
        line = [{'text': '高塔迷影', 'x': 100, 'y': 100}]
        profile = {'title': '空想花庭', 'line': '高塔迷影'}
        with patch('autostory.navigation.check_stop',
                   side_effect=InterruptedError('已停止；进度已保存')):
            with self.assertRaises(InterruptedError):
                nav.chapter_candidates(image(0), line, profile)
        nav.v.texts_zoom.assert_not_called()


class EnterTests(TempRuntime):
    def test_complete_index_with_ocr_miss_still_enters_after_search(self):
        nav = self.nav(replay([
            (image(0), stories_page()), (image(0), list_page()),
            (image(1), title_page(CHAPTERS['AT']['title'])),
            (image(1), activity_page()),
        ]))
        nav.collection_index = {'pages_by_title': {collection_index.key('别的活动'): 2},
                                'pages': 5, 'complete': True, 'at': time.time()}
        nav.enter('AT')
        nav.stories.assert_called_once()
        self.assertEqual(nav.d.tap.call_count, 2)      # 乐章收录 + the card
        self.assertIsNone(nav_hints.missing('AT'))

    def test_unknown_chapter_stays_unknown_and_is_not_proven_absent(self):
        nav = self.nav(replay([(image(0), stories_page()), (image(0), list_page())]
                              + [(image(i), list_page()) for i in range(10)]))
        nav.collection_index = {'pages_by_title': {collection_index.key('别的活动'): 2},
                                'pages': 5, 'complete': True, 'at': time.time()}
        with self.assertRaises(RuntimeError) as ctx:
            nav.enter('AT')
        message = str(ctx.exception)
        self.assertIn('未找到章节入口', message)
        self.assertIn('本次已实际翻查', message)
        self.assertNotIn('已完整核对', message)
        self.assertIsNotNone(nav_hints.missing('AT'))
        self.mark_missing.assert_called_once()

    def test_legacy_index_negative_is_ignored(self):
        common.write_json(self.data / nav_hints.FILE,
                          {'pages': {}, 'missing': {'AT': {'at': time.time(), 'attempts': 1}}})
        self.assertIsNone(nav_hints.missing('AT'))
        nav = self.nav(replay([
            (image(0), stories_page()), (image(0), list_page()),
            (image(1), title_page(CHAPTERS['AT']['title'])),
            (image(1), activity_page()),
        ]))
        nav.enter('AT')
        nav.stories.assert_called_once()
        self.assertIsNone(nav_hints.missing('AT'))

    def test_positive_index_observation_overrides_a_negative_hint(self):
        nav_hints.mark_missing('AT')
        self.assertIsNotNone(nav_hints.missing('AT'))
        nav = self.nav(replay([
            (image(0), stories_page()), (image(0), list_page()),
            (image(1), title_page(CHAPTERS['AT']['title'])),
            (image(1), activity_page()),
        ]))
        nav.collection_index = {'pages_by_title': {collection_index.key(CHAPTERS['AT']['title']): 2},
                                'pages': 5, 'complete': True, 'at': time.time()}
        nav.enter('AT')
        self.assertIsNone(nav_hints.missing('AT'))
        self.assertEqual(nav_hints.page_hint('AT'), {'direction': 1, 'pages': 2})

    def test_persistent_visible_title_is_tapped_at_most_three_times(self):
        calls = {'n': 0}

        def observe(*args, **kwargs):
            index = calls['n']
            calls['n'] += 1
            if index == 0:
                return image(0), stories_page()
            if index == 1:
                return image(0), list_page()
            return image(0), title_page(CHAPTERS['AT']['title']) if index % 2 == 0 else list_page()

        nav = self.nav(observe)
        with self.assertRaises(RuntimeError) as ctx:
            nav.enter('AT')
        self.assertIn('入口未确认', str(ctx.exception))
        self.assertIn('未标记为缺席', str(ctx.exception))
        self.assertEqual(nav.d.tap.call_count, 1 + 3)   # 乐章收录 + three card taps
        self.assertIsNone(nav_hints.missing('AT'))
        self.mark_missing.assert_not_called()

    def test_positive_title_with_incomplete_entry_is_not_marked_missing(self):
        nav = self.nav(replay([
            (image(0), stories_page()), (image(0), list_page()),
            (image(1), title_page(CHAPTERS['AT']['title'])),
            (image(1), list_page()),
        ]))
        nav.chapter_candidates = Mock(return_value=[{'text': CHAPTERS['AT']['title'],
                                                     'x': 620, 'y': 300}])
        with self.assertRaises(RuntimeError) as ctx:
            nav.enter('AT')
        self.assertIn('入口未确认', str(ctx.exception))
        self.assertIsNone(nav_hints.missing('AT'))
        self.mark_missing.assert_not_called()


class BuildIndexTests(TempRuntime):
    def build_nav(self, observations, pages=()):
        nav = self.nav(replay(observations))
        nav.click_text = Mock(return_value=True)
        nav.v.texts_zoom = Mock(return_value=[])
        return nav

    def test_repeated_empty_screens_do_not_stop_before_the_bottom(self):
        title = CHAPTERS['AT']['title']
        observations = [(image(0), stories_page()), (image(0), list_page())]
        for page in range(6):
            texts = list_page()
            if page == 5:
                texts = texts + title_page(title)
            observations.append((image(page + 2), texts))
        nav = self.build_nav(observations)
        index = nav.build_collection_index([title], max_pages=6)
        self.assertIsNotNone(index)
        self.assertEqual(index['pages_by_title'][collection_index.key(title)], 5)
        self.assertFalse(index['complete'])
        self.assertEqual(nav.observe.call_count, 2 + 6)
        self.assertLessEqual(nav.v.texts_zoom.call_count, collection_ocr.MAX_CROPS * 6)
        self.assertTrue(all(call.kwargs.get('scale') == 2
                            for call in nav.v.texts_zoom.call_args_list))

    def test_hard_page_bound_is_kept(self):
        nav = self.build_nav([(image(0), stories_page()), (image(0), list_page())]
                             + [(image(i + 2), list_page()) for i in range(3)])
        self.assertIsNone(nav.build_collection_index(['不存在的章节'], max_pages=3))
        self.assertEqual(nav.observe.call_count, 2 + 3)

    def test_card_ocr_is_bounded_per_screen_not_per_title(self):
        title = CHAPTERS['AT']['title']
        nav = self.build_nav([(image(0), stories_page()), (image(0), list_page()),
                              (image(2), list_page())])
        nav.v.texts_zoom = Mock(side_effect=lambda image, roi, scale=2: [
            {'text': title, 'x': 620, 'y': 300, 'score': 1.0}])
        index = nav.build_collection_index([f'标题{i}' for i in range(20)] + [title],
                                           max_pages=1)
        self.assertIsNotNone(index)
        self.assertEqual(nav.v.texts_zoom.call_count, collection_ocr.MAX_CROPS)

    def test_stop_propagates_from_the_index_walk(self):
        nav = self.build_nav([(image(0), stories_page()), (image(0), list_page()),
                              (image(2), list_page())])
        with patch('autostory.navigation.check_stop',
                   side_effect=InterruptedError('已停止；进度已保存')):
            with self.assertRaises(InterruptedError):
                nav.build_collection_index([CHAPTERS['AT']['title']], max_pages=3)

    def test_device_exception_propagates_from_the_index_walk(self):
        nav = self.build_nav([(image(0), stories_page()), (image(0), list_page()),
                              (image(2), list_page())])
        nav.d.swipe.side_effect = DeviceUnavailableError('device offline')
        with self.assertRaises(DeviceUnavailableError):
            nav.build_collection_index([CHAPTERS['AT']['title']], max_pages=3)


class CacheTests(TempRuntime):
    def test_page_24_survives_save_and_load(self):
        self.assertEqual(collection_index.MAX_PAGES, 24)
        saved = collection_index.save({CHAPTERS['AT']['title']: 23}, 24)
        self.assertIsNotNone(saved)
        self.assertEqual(saved['pages'], 24)
        self.assertEqual(saved['pages_by_title'][collection_index.key(CHAPTERS['AT']['title'])], 23)
        loaded = collection_index.load()
        self.assertEqual(collection_index.page_of(loaded, CHAPTERS['AT']['title']), 23)

    def test_collection_index_rejects_nan_and_future_timestamps(self):
        collection_index.save({CHAPTERS['AT']['title']: 1}, 2)
        path = self.data / collection_index.FILE
        for bad in (float('nan'), float('inf'), time.time() + 10 ** 6):
            data = common.read_json(path)
            data['at'] = bad
            common.write_json(path, data)
            self.assertIsNone(collection_index.load(), bad)

    def test_hint_cache_ignores_unsafe_legacy_and_bad_timestamps(self):
        common.write_json(self.data / nav_hints.FILE, {
            'pages': {'BAD': {'direction': 1, 'pages': 2, 'at': float('nan')}},
            'missing': {
                'AT': {'at': float('nan'), 'schema': 2, 'source': 'targeted_search'},
                'AD': {'at': time.time() + 10 ** 6, 'schema': 2, 'source': 'targeted_search'},
                'MT': {'at': time.time(), 'attempts': 'x', 'schema': 2, 'source': 'targeted_search'},
                'LEGACY': {'at': time.time(), 'attempts': 1},
            },
        })
        self.assertIsNone(nav_hints.missing('AT'))
        self.assertIsNone(nav_hints.missing('AD'))
        self.assertIsNone(nav_hints.missing('LEGACY'))
        self.assertIsNone(nav_hints.page_hint('BAD'))
        self.assertIsNotNone(nav_hints.missing('MT'))
        nav_hints.mark_missing('MT')
        data = common.read_json(self.data / nav_hints.FILE)
        self.assertEqual(data['missing']['MT']['attempts'], 1)
        self.assertEqual(nav_hints.summary(), {'pages': 0, 'missing': 1})

    def test_malformed_hint_shapes_do_not_raise(self):
        for broken in ({'missing': ['AT']}, {'pages': 'x', 'missing': []}, []):
            common.write_json(self.data / nav_hints.FILE, broken)
            self.assertIsNone(nav_hints.missing('AT'))
            nav_hints.mark_missing('AT')
            self.assertIsNotNone(nav_hints.missing('AT'))

    def test_unhashable_hint_source_is_ignored(self):
        for source in ([], {}, ['targeted_search']):
            common.write_json(self.data/nav_hints.FILE, {'missing': {
                'AT': {'at': time.time(), 'schema': 2, 'source': source}}})
            self.assertIsNone(nav_hints.missing('AT'))
            self.assertEqual(nav_hints.summary()['missing'], 0)
            with self.assertRaises(ValueError):nav_hints.mark_missing('AT', source=source)


if __name__ == '__main__':
    unittest.main()
