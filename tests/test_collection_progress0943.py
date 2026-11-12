"""0.9.43 regression tests for the collection completion-card parser.

The fixture is the retained pure OCR rows for the collection list's active
「完成进度」 tab (``evidence/collection-progress-ocr.json``); profiles come from
``data/catalogue.json``.  Synthetic rows cover spatial binding, ambiguity,
malformed input, profile filtering and value/score boundaries.  The module
under test performs no OCR, image, tab, device or filesystem work.
"""
import copy
import json
import unittest
from pathlib import Path

from autostory.collection_progress import cards_from_rows

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads(
    (ROOT / 'evidence' / 'collection-progress-ocr.json').read_text(
        encoding='utf-8'))
CATALOGUE = json.loads(
    (ROOT / 'data' / 'catalogue.json').read_text(encoding='utf-8'))
PROFILES = CATALOGUE['profiles']

EXPECTED = [
    ('GT', '骑兵与猎人', 39, 39, True),
    ('SV', '覆潮之下', 59, 59, True),
    ('SN', '愚人号', 89, 89, True),
    ('BP', '生路', 59, 59, True),
    ('EP', '出苍白海', 55, 56, False),
]

# Column 1/2 synthetic anchors.  Counter centres are 115; title centres 195.
C1 = (70, 100, 110, 130)
T1 = (140, 180, 220, 210)
C2 = (350, 100, 390, 130)
T2 = (430, 180, 500, 210)


def row(text, box, score=.99):
    """One vision-shaped OCR row."""
    x0, y0, x1, y1 = box
    return {'text': text, 'score': score,
            'x': (x0 + x1) / 2.0, 'y': (y0 + y1) / 2.0,
            'box': list(box)}


def side(base, title):
    return {'kind': 'side', 'base': base, 'title': title}


def profiles(*pairs):
    return {base: side(base, title) for base, title in pairs}


def summary(cards):
    return [(card['base_group'], card['title'], card['current'],
             card['total'], card['full']) for card in cards]


class CollectionProgressTests(unittest.TestCase):
    # ---------------------------------------------------------- actual fixture
    def test_fixture_full_observation(self):
        cards = cards_from_rows(FIXTURE['full'], PROFILES)
        self.assertEqual(summary(cards), EXPECTED)

    def test_fixture_columns_observation(self):
        cards = cards_from_rows(FIXTURE['columns'], PROFILES)
        self.assertEqual(summary(cards), EXPECTED)

    def test_fixture_cards_are_json_safe_and_typed(self):
        for observation in ('full', 'columns'):
            with self.subTest(observation=observation):
                cards = cards_from_rows(FIXTURE[observation], PROFILES)
                self.assertEqual(json.loads(json.dumps(cards, ensure_ascii=False)),
                                 cards)
                for card in cards:
                    self.assertEqual(set(card), {
                        'base_group', 'title', 'current', 'total', 'full',
                        'score', 'counter_box', 'title_box'})
                    self.assertIs(type(card['base_group']), str)
                    self.assertIs(type(card['title']), str)
                    self.assertIs(type(card['current']), int)
                    self.assertIs(type(card['total']), int)
                    self.assertIs(type(card['full']), bool)
                    self.assertIs(type(card['score']), float)
                    self.assertIs(type(card['counter_box']), list)
                    self.assertIs(type(card['title_box']), list)
                    for box in (card['counter_box'], card['title_box']):
                        self.assertEqual(len(box), 4)
                        self.assertTrue(all(type(value) is float for value in box))

    def test_top_clipped_titles_do_not_take_a_counter_below(self):
        for observation in ('full', 'columns'):
            with self.subTest(observation=observation):
                cards = cards_from_rows(FIXTURE[observation], PROFILES)
                bases = {card['base_group'] for card in cards}
                titles = {card['title'] for card in cards}
                self.assertFalse(bases & {'MB', 'DV', 'CW', 'UT'})
                self.assertFalse(titles & {'孤岛风云', '绿野幻梦', '孤星',
                                           '未许之地'})

    def test_missing_lower_counter_is_omitted(self):
        cards = cards_from_rows(FIXTURE['columns'], PROFILES)
        self.assertEqual(set(card['title'] for card in cards),
                         {'骑兵与猎人', '覆潮之下', '愚人号', '生路', '出苍白海'})
        self.assertNotIn('昨日海', {card['title'] for card in cards})

    def test_actual_fixture_has_the_incomplete_card(self):
        cards = cards_from_rows(FIXTURE['full'], PROFILES)
        ep = [card for card in cards if card['base_group'] == 'EP']
        self.assertEqual(len(ep), 1)
        self.assertEqual((ep[0]['current'], ep[0]['total'], ep[0]['full']),
                         (55, 56, False))

    # ------------------------------------------------------ spatial binding
    def test_counter_and_title_in_same_column_bind(self):
        rows = [row('1/2', C1), row('甲', T1)]
        cards = cards_from_rows(rows, profiles(('AAA', '甲')))
        self.assertEqual(summary(cards), [('AAA', '甲', 1, 2, False)])

    def test_counter_and_title_in_different_columns_do_not_bind(self):
        rows = [row('1/2', C1), row('甲', T2)]
        self.assertEqual(cards_from_rows(rows, profiles(('AAA', '甲'))), [])

    def test_title_above_counter_does_not_bind(self):
        rows = [row('1/2', (70, 300, 110, 330)), row('甲', T1)]
        self.assertEqual(cards_from_rows(rows, profiles(('AAA', '甲'))), [])

    def test_title_gap_boundaries(self):
        data = profiles(('AAA', '甲'))
        counter = row('1/2', C1)
        # Counter centre 115; accepted 45 and 175, rejected just outside.
        for centre, accepted in ((160.0, True), (159.9, False),
                                 (290.0, True), (290.1, False)):
            with self.subTest(centre=centre):
                title = row('甲', (140, centre - 15, 220, centre + 15))
                cards = cards_from_rows([counter, title], data)
                self.assertEqual(bool(cards), accepted)

    def test_counter_must_sit_in_the_left_half(self):
        data = profiles(('AAA', '甲'))
        # Column 1 is 50..328, midpoint 189.
        for counter_box in ((169, 100, 209, 130),   # centre 189
                            (170, 100, 210, 130)):  # centre 190
            with self.subTest(counter_box=counter_box):
                rows = [row('1/2', counter_box), row('甲', T1)]
                self.assertEqual(cards_from_rows(rows, data), [])

    def test_crossing_a_column_boundary_is_rejected(self):
        data = profiles(('AAA', '甲'))
        self.assertEqual(cards_from_rows(
            [row('1/2', (300, 100, 340, 130)), row('甲', T1)], data), [])
        self.assertEqual(cards_from_rows(
            [row('1/2', C1), row('甲', (300, 180, 340, 210))], data), [])

    def test_vertical_band_boundaries(self):
        data = profiles(('AAA', '甲'))
        # Top y=75 accepted, 74 rejected.
        self.assertTrue(cards_from_rows(
            [row('1/2', (70, 75, 110, 105)),
             row('甲', (140, 145, 220, 175))], data))
        self.assertEqual(cards_from_rows(
            [row('1/2', (70, 74, 110, 104)),
             row('甲', (140, 145, 220, 175))], data), [])
        # Bottom y=695 accepted, 696 rejected.
        self.assertTrue(cards_from_rows(
            [row('1/2', (70, 600, 110, 630)),
             row('甲', (140, 660, 220, 695))], data))
        self.assertEqual(cards_from_rows(
            [row('1/2', (70, 600, 110, 630)),
             row('甲', (140, 660, 220, 696))], data), [])

    # ----------------------------------------------------------- value/score
    def test_full_and_incomplete_values(self):
        data = profiles(('AAA', '甲'), ('BBB', '乙'))
        self.assertEqual(summary(cards_from_rows(
            [row('5/5', C1), row('甲', T1)], data)),
            [('AAA', '甲', 5, 5, True)])
        self.assertEqual(summary(cards_from_rows(
            [row('0/5', C1), row('甲', T1)], data)),
            [('AAA', '甲', 0, 5, False)])
        self.assertEqual(summary(cards_from_rows(
            [row('9999/9999', C1), row('甲', T1)], data)),
            [('AAA', '甲', 9999, 9999, True)])

    def test_ratio_confidence_boundary(self):
        data = profiles(('AAA', '甲'))
        self.assertEqual(cards_from_rows(
            [row('1/2', C1, score=.879), row('甲', T1)], data), [])
        self.assertTrue(cards_from_rows(
            [row('1/2', C1, score=.88), row('甲', T1)], data))

    def test_title_confidence_boundary(self):
        data = profiles(('AAA', '甲'))
        self.assertEqual(cards_from_rows(
            [row('1/2', C1), row('甲', T1, score=.899)], data), [])
        self.assertTrue(cards_from_rows(
            [row('1/2', C1), row('甲', T1, score=.9)], data))

    def test_zero_denominator_and_over_total_rejected(self):
        data = profiles(('AAA', '甲'))
        for text in ('0/0', '5/0', '5/4', '10000/10000'):
            with self.subTest(text=text):
                self.assertEqual(cards_from_rows(
                    [row(text, C1), row('甲', T1)], data), [])

    def test_whitespace_and_fullwidth_slash_allowed(self):
        data = profiles(('AAA', '甲'))
        for text in (' 1 ／ 2 ', '1 / 2', '1/ 2', '1 /2'):
            with self.subTest(text=text):
                self.assertEqual(summary(cards_from_rows(
                    [row(text, C1), row('甲', T1)], data)),
                    [('AAA', '甲', 1, 2, False)])

    def test_missing_slash_and_split_fragments_are_not_guessed(self):
        data = profiles(('AAA', '甲'))
        for rows in ([row('1/2 前往', C1), row('甲', T1)],
                     [row('1', C1), row('/2', (112, 100, 150, 130)),
                      row('甲', T1)],
                     [row('1', C1), row('2', (112, 100, 150, 130)),
                      row('甲', T1)]):
            with self.subTest(rows=rows):
                self.assertEqual(cards_from_rows(rows, data), [])

    def test_non_integer_and_control_forms_are_rejected(self):
        data = profiles(('AAA', '甲'))
        for text in ('-1/2', '1/-2', '+1/2', '1/2%', '1.0/2',
                     '1/2 前往', '1//2', '１/２'):
            with self.subTest(text=text):
                self.assertEqual(cards_from_rows(
                    [row(text, C1), row('甲', T1)], data), [])

    # ------------------------------------------------------------ ambiguity
    def test_counter_with_two_titles_is_dropped(self):
        data = profiles(('AAA', '甲'), ('BBB', '乙'), ('CCC', '丙'))
        rows = [
            row('1/2', C1),
            row('甲', (140, 160, 220, 190)),   # gap 60
            row('乙', (140, 190, 220, 220)),   # gap 90
            row('3/4', C2), row('丙', T2),
        ]
        self.assertEqual(summary(cards_from_rows(rows, data)),
                         [('CCC', '丙', 3, 4, False)])

    def test_title_with_two_counters_drops_both(self):
        data = profiles(('AAA', '甲'), ('CCC', '丙'))
        rows = [
            row('1/2', (70, 90, 110, 120)),    # centre 105
            row('2/3', (70, 130, 110, 160)),   # centre 145
            row('甲', (140, 200, 220, 230)),   # centre 215
            row('3/4', C2), row('丙', T2),
        ]
        self.assertEqual(summary(cards_from_rows(rows, data)),
                         [('CCC', '丙', 3, 4, False)])

    def test_identical_overlapping_duplicates_dedup(self):
        data = profiles(('AAA', '甲'))
        rows = [
            row('1/2', C1, score=.95), row('1/2', C1, score=.99),
            row('甲', T1, score=.95), row('甲', T1, score=.99),
        ]
        cards = cards_from_rows(rows, data)
        self.assertEqual(summary(cards), [('AAA', '甲', 1, 2, False)])
        self.assertEqual(cards[0]['score'], .99)

    def test_disjoint_duplicate_counters_are_ambiguous(self):
        data = profiles(('AAA', '甲'))
        rows = [
            row('1/2', (70, 90, 110, 120)),
            row('1/2', (70, 130, 110, 160)),
            row('甲', (140, 200, 220, 230)),
        ]
        self.assertEqual(cards_from_rows(rows, data), [])

    def test_disjoint_duplicate_titles_are_ambiguous(self):
        data = profiles(('AAA', '甲'))
        rows = [
            row('1/2', C1),
            row('甲', (140, 150, 220, 180)),
            row('甲', (140, 190, 220, 220)),
        ]
        self.assertEqual(cards_from_rows(rows, data), [])

    # --------------------------------------------------------- profiles
    def test_missing_base_uses_mapping_key(self):
        data = {'AAA': {'kind': 'side', 'title': '甲'}}
        self.assertEqual(summary(cards_from_rows(
            [row('1/2', C1), row('甲', T1)], data)),
            [('AAA', '甲', 1, 2, False)])

    def test_wrong_base_is_skipped(self):
        data = {'AAA-TR': side('AAA', '甲')}
        self.assertEqual(cards_from_rows(
            [row('1/2', C1), row('甲', T1)], data), [])

    def test_main_story_profile_is_skipped(self):
        data = {'0': {'kind': 'main', 'base': '0', 'title': '甲'}}
        self.assertEqual(cards_from_rows(
            [row('1/2', C1), row('甲', T1)], data), [])

    def test_duplicate_title_mapping_is_rejected(self):
        data = {'AAA': side('AAA', '同名'), 'BBB': side('BBB', '同名')}
        self.assertEqual(cards_from_rows(
            [row('1/2', C1), row('同名', T1)], data), [])

    def test_bounded_decoration_is_allowed(self):
        data = {'LE': side('LE', '尘影余音')}
        cards = cards_from_rows(
            [row('1/2', C1), row('LIEBI尘影余音', T1)], data)
        self.assertEqual(summary(cards), [('LE', '尘影余音', 1, 2, False)])

    def test_extra_han_or_fuzzy_title_is_rejected(self):
        data = {'LE': side('LE', '尘影余音')}
        for text in ('尘影余音外传', '尘影余', '尘影余音传'):
            with self.subTest(text=text):
                self.assertEqual(cards_from_rows(
                    [row('1/2', C1), row(text, T1)], data), [])

    def test_list_profiles_are_accepted(self):
        data = [{'key': 'AAA', 'title': '甲', 'base': 'AAA', 'kind': 'side'}]
        self.assertEqual(summary(cards_from_rows(
            [row('1/2', C1), row('甲', T1)], data)),
            [('AAA', '甲', 1, 2, False)])

    def test_list_profile_with_mismatched_key_is_skipped(self):
        data = [{'key': 'AAA-TR', 'title': '甲', 'base': 'AAA', 'kind': 'side'}]
        self.assertEqual(cards_from_rows(
            [row('1/2', C1), row('甲', T1)], data), [])

    def test_wrapped_list_profiles_are_accepted(self):
        data = {'profiles': [
            {'key': 'AAA', 'title': '甲', 'base': 'AAA', 'kind': 'side'}]}
        self.assertEqual(summary(cards_from_rows(
            [row('1/2', C1), row('甲', T1)], data)),
            [('AAA', '甲', 1, 2, False)])

    def test_full_catalogue_mapping_is_accepted(self):
        cards = cards_from_rows(
            [row('1/2', C1), row('骑兵与猎人', T1)], CATALOGUE)
        self.assertEqual(summary(cards), [('GT', '骑兵与猎人', 1, 2, False)])

    def test_fixture_with_full_catalogue_object(self):
        cards = cards_from_rows(FIXTURE['full'], CATALOGUE)
        self.assertEqual(summary(cards), EXPECTED)

    # -------------------------------------------------- malformed / tab text
    def test_malformed_rows_are_skipped_safely(self):
        data = profiles(('AAA', '甲'))
        good = [row('1/2', C1), row('甲', T1)]
        bad = [
            None, 'not-a-row', 42,
            {'text': '1/2', 'score': .99},
            {'text': '1/2', 'box': C1},
            {'score': .99, 'box': C1},
            {'text': 123, 'score': .99, 'box': C1},
            {'text': '1/2', 'score': float('nan'), 'box': C1},
            {'text': '1/2', 'score': True, 'box': C1},
            {'text': '1/2', 'score': .99, 'box': [float('nan'), 100, 110, 130]},
            {'text': '1/2', 'score': .99, 'box': [True, 100, 110, 130]},
            {'text': '1/2', 'score': .99, 'x': float('nan'), 'y': 115,
             'box': C1},
            {'text': '1/2', 'score': .99, 'x': 90, 'y': True, 'box': C1},
            {'text': '1/2', 'score': .99, 'x': -1, 'y': 115, 'box': C1},
            {'text': '1/2', 'score': .99, 'x': 1281, 'y': 115, 'box': C1},
            {'text': '1/2', 'score': .99, 'x': 90, 'y': 721, 'box': C1},
        ]
        cards = cards_from_rows(good + bad, data)
        self.assertEqual(summary(cards), [('AAA', '甲', 1, 2, False)])

    def test_optional_centres_may_be_absent(self):
        data = profiles(('AAA', '甲'))
        rows = [
            {'text': '1/2', 'score': .99, 'box': list(C1)},
            {'text': '甲', 'score': .99, 'box': list(T1)},
        ]
        self.assertEqual(summary(cards_from_rows(rows, data)),
                         [('AAA', '甲', 1, 2, False)])

    def test_unusable_containers_return_empty_list(self):
        data = profiles(('AAA', '甲'))
        good = [row('1/2', C1), row('甲', T1)]
        for rows in (None, '', 42, [], [None]):
            with self.subTest(rows=rows):
                self.assertEqual(cards_from_rows(rows, data), [])
        for profiles_value in (None, '', 42, [], {'bad': 'value'}):
            with self.subTest(profiles=profiles_value):
                self.assertEqual(cards_from_rows(good, profiles_value), [])

    def test_tab_labels_are_ignored(self):
        rows = [
            row('默认进度', (697, 28, 780, 55)),
            row('奖励进度', (808, 28, 892, 55)),
            row('完成进度', (920, 28, 1002, 56)),
            row('乐章', (1187, 29, 1231, 55)),
        ]
        self.assertEqual(cards_from_rows(rows, PROFILES), [])

    # -------------------------------------------------------------- purity
    def test_inputs_are_not_mutated(self):
        rows = copy.deepcopy(FIXTURE['full'])
        profiles_data = copy.deepcopy(PROFILES)
        rows_snapshot = copy.deepcopy(rows)
        profiles_snapshot = copy.deepcopy(profiles_data)
        cards = cards_from_rows(rows, profiles_data)
        self.assertTrue(cards)
        self.assertEqual(rows, rows_snapshot)
        self.assertEqual(profiles_data, profiles_snapshot)
        self.assertIsNot(cards[0]['counter_box'], rows[0]['box'])


if __name__ == '__main__':
    unittest.main()
