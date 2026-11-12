"""0.9.43 regression tests for the pure activity-counter parser.

The fixtures use the real OCR row geometry retained in
``evidence/native-BB-81-of-81.png`` (1280x720) and
``evidence/user-CB-62-of-62.png`` (3200x1808), plus synthetic boundary cases.
The module under test does no OCR, device, network, filesystem or state work.
"""
import copy
import unittest

from autostory.activity_counter import ratio_from_rows


def row(text, box, score=.99, **extra):
    """Build one vision-shaped OCR row with a consistent centre."""
    x0, y0, x1, y1 = box
    item = {
        'text': text,
        'score': score,
        'x': (x0 + x1) / 2.0,
        'y': (y0 + y1) / 2.0,
        'box': list(box),
    }
    item.update(extra)
    return item


class ActivityCounterTests(unittest.TestCase):
    # ------------------------------------------------------------ real frames
    def test_complete_ratio_has_exact_plain_dict_contract(self):
        result = ratio_from_rows([row('62/62', (84, 645, 210, 687))])
        self.assertIs(type(result), dict)
        self.assertEqual(set(result), {'current', 'total', 'full', 'score', 'text'})
        self.assertEqual(result, {
            'current': 62,
            'total': 62,
            'full': True,
            'score': .99,
            'text': '62/62',
        })
        self.assertIs(type(result['current']), int)
        self.assertIs(type(result['total']), int)
        self.assertIs(type(result['full']), bool)
        self.assertIs(type(result['score']), float)

    def test_real_native_81_of_81_split_from_evidence(self):
        # Exact rows produced by local RapidOCR on the evidencing 1280x720
        # native frame.  The two boxes overlap horizontally and vertically.
        rows = [
            {'text': '81', 'score': .9988, 'x': 111.5, 'y': 666.0,
             'box': [84.0, 645.0, 139.0, 687.0]},
            {'text': '/81', 'score': .9777, 'x': 155.5, 'y': 670.5,
             'box': [127.0, 655.0, 184.0, 686.0]},
        ]
        self.assertEqual(ratio_from_rows(rows), {
            'current': 81, 'total': 81, 'full': True,
            'score': .9777, 'text': '81/81',
        })

    def test_real_scaled_62_of_62_split_from_evidence(self):
        # Exact rows from the 3200x1808 user frame; the ROI is scaled by
        # width/1280 and height/720 independently.
        rows = [
            {'text': '62', 'score': .9987, 'x': 278.0, 'y': 1672.0,
             'box': [205.0, 1619.0, 351.0, 1725.0]},
            {'text': '/62', 'score': .974, 'x': 394.5, 'y': 1683.5,
             'box': [329.0, 1646.0, 460.0, 1721.0]},
        ]
        self.assertEqual(ratio_from_rows(rows, width=3200, height=1808), {
            'current': 62, 'total': 62, 'full': True,
            'score': .974, 'text': '62/62',
        })

    def test_real_evidence_scale2_merged_token(self):
        # counter-ocr-probes.json, 125x55 crop at scale 2: one merged token.
        rows = [{'text': '62/62', 'score': .981546938419342,
                 'x': 135.125, 'y': 665.5,
                 'box': [80.5, 640.5, 190.0, 690.5]}]
        result = ratio_from_rows(rows)
        self.assertEqual(result['text'], '62/62')
        self.assertTrue(result['full'])

    def test_real_evidence_scale2_native_split(self):
        # counter-ocr-probes.json, 125x55 crop at scale 2: split native rows.
        rows = [
            {'text': '81', 'score': .9991097152233124,
             'x': 109.875, 'y': 666.0,
             'box': [84.0, 644.5, 136.0, 687.5]},
            {'text': '/81', 'score': .9622377753257751,
             'x': 155.5, 'y': 670.75,
             'box': [128.0, 656.0, 183.0, 685.5]},
        ]
        result = ratio_from_rows(rows)
        self.assertEqual(result['text'], '81/81')
        self.assertAlmostEqual(result['score'], .9622377753257751)

    def test_real_full_frame_control_merge_is_rejected(self):
        # The retained full-frame OCR merges the 前往 control into the ratio;
        # that token (and its box crossing the ROI) must not become a counter.
        rows = [
            {'text': '62/62前往》', 'score': .9744682312011719,
             'x': 181.0, 'y': 667.0,
             'box': [80.0, 642.0, 282.0, 692.0]},
            {'text': '81/81前往》', 'score': .9303465560078621,
             'x': 181.0, 'y': 667.5,
             'box': [83.0, 643.0, 279.0, 692.0]},
        ]
        self.assertIsNone(ratio_from_rows(rows[:1]))
        self.assertIsNone(ratio_from_rows(rows[1:]))

    def test_real_low_confidence_denominator_is_rejected(self):
        # The retained 1280-normalised user ROI row scores /62 at 0.854.
        rows = [
            {'text': '62', 'score': .9989270865917206,
             'x': 111.66666666666666, 'y': 666.0,
             'box': [82.66666666666667, 644.6666666666666,
                     140.66666666666669, 687.3333333333334]},
            {'text': '/62', 'score': .8539645274480184,
             'x': 157.83333333333331, 'y': 670.5,
             'box': [131.66666666666666, 655.0, 184.0, 686.0]},
        ]
        self.assertIsNone(ratio_from_rows(rows))

    def test_real_partial_control_box_outside_roi_does_not_poison(self):
        rows = [
            {'text': '62/62', 'score': .99, 'x': 135, 'y': 666,
             'box': [84, 645, 190, 687]},
            {'text': '前', 'score': .9997859597206116,
             'x': 227.33333333333334, 'y': 667.1666666666666,
             'box': [220.0, 658.0, 234.66666666666666, 676.3333333333334]},
        ]
        self.assertEqual(ratio_from_rows(rows)['text'], '62/62')

    def test_scaled_1080p_split(self):
        rows = [
            row('81', (126, 967.5, 208.5, 1030.5)),
            row('/81', (190.5, 982.5, 276, 1029)),
        ]
        result = ratio_from_rows(rows, width=1920, height=1080)
        self.assertEqual((result['current'], result['total'], result['full']),
                         (81, 81, True))

    # ---------------------------------------------------------- token layouts
    def test_two_token_split_with_right_slash(self):
        rows = [
            row('62', (84, 645, 139, 687)),
            row('/62', (127, 655, 184, 686)),
        ]
        self.assertEqual(ratio_from_rows(rows)['text'], '62/62')

    def test_two_token_split_with_left_slash(self):
        rows = [
            row('62/', (84, 645, 139, 687)),
            row('62', (141, 655, 190, 686)),
        ]
        self.assertEqual(ratio_from_rows(rows)['text'], '62/62')

    def test_three_token_split(self):
        rows = [
            row('62', (84, 645, 120, 687)),
            row('/', (122, 650, 132, 686)),
            row('62', (134, 645, 190, 687)),
        ]
        self.assertEqual(ratio_from_rows(rows)['text'], '62/62')

    def test_spaces_and_fullwidth_slash_are_folded(self):
        for text in ('62 / 62', ' 62 ／ 62 ', '62　／　62', '62/ 62'):
            with self.subTest(text=text):
                result = ratio_from_rows([row(text, (84, 645, 210, 687))])
                self.assertEqual(result['text'], '62/62')
                self.assertTrue(result['full'])

    # ------------------------------------------------------------ value rules
    def test_partial_counter_is_not_full(self):
        result = ratio_from_rows([row('61/62', (84, 645, 210, 687))])
        self.assertEqual((result['current'], result['total'], result['full']),
                         (61, 62, False))

    def test_zero_numerator_is_valid_and_not_full(self):
        result = ratio_from_rows([row('0/62', (84, 645, 210, 687))])
        self.assertEqual((result['current'], result['total'], result['full']),
                         (0, 62, False))

    def test_100_of_100_is_full(self):
        result = ratio_from_rows([row('100/100', (84, 645, 210, 687))])
        self.assertTrue(result['full'])

    def test_9999_of_9999_is_the_inclusive_cap(self):
        result = ratio_from_rows([row('9999/9999', (84, 645, 210, 687))])
        self.assertEqual((result['current'], result['total'], result['full']),
                         (9999, 9999, True))

    def test_zero_denominator_is_rejected(self):
        for text in ('0/0', '62/0'):
            with self.subTest(text=text):
                self.assertIsNone(
                    ratio_from_rows([row(text, (84, 645, 210, 687))]))

    def test_current_greater_than_total_is_rejected(self):
        self.assertIsNone(
            ratio_from_rows([row('63/62', (84, 645, 210, 687))]))

    def test_value_cap_is_strict(self):
        self.assertIsNone(
            ratio_from_rows([row('10000/10000', (84, 645, 210, 687))]))

    # ------------------------------------------------------------- fixed ROI
    def test_ratio_outside_roi_is_ignored(self):
        rows = [row('62/62', (300, 650, 420, 690))]
        self.assertIsNone(ratio_from_rows(rows))

    def test_partial_box_crossing_roi_is_ignored(self):
        for box in ((200, 645, 260, 687),      # crosses right edge
                    (84, 690, 210, 710)):      # crosses bottom edge
            with self.subTest(box=box):
                self.assertIsNone(ratio_from_rows([row('62/62', box)]))

    def test_box_on_roi_boundary_is_accepted(self):
        result = ratio_from_rows([row('62/62', (78, 630, 225, 704))])
        self.assertEqual(result['text'], '62/62')

    def test_one_outside_reading_does_not_hide_a_valid_roi_reading(self):
        rows = [
            row('62/62', (84, 645, 210, 687)),
            row('81/81', (300, 650, 420, 690)),
        ]
        self.assertEqual(ratio_from_rows(rows)['text'], '62/62')

    # ------------------------------------------------------------ confidence
    def test_low_numeric_score_is_rejected(self):
        self.assertIsNone(
            ratio_from_rows([row('62/62', (84, 645, 210, 687), score=.879)]))

    def test_minimum_numeric_score_is_inclusive(self):
        result = ratio_from_rows(
            [row('62/62', (84, 645, 210, 687), score=.88)])
        self.assertEqual(result['score'], .88)

    def test_low_slash_score_breaks_a_split(self):
        rows = [
            row('62', (84, 645, 120, 687), score=.99),
            row('/', (122, 650, 132, 686), score=.879),
            row('62', (134, 645, 190, 687), score=.99),
        ]
        self.assertIsNone(ratio_from_rows(rows))

    def test_score_above_one_and_bool_scores_are_rejected(self):
        for score in (1.01, True, float('nan'), float('inf')):
            with self.subTest(score=score):
                self.assertIsNone(ratio_from_rows(
                    [row('62/62', (84, 645, 210, 687), score=score)]))

    # ------------------------------------------------------- syntax strictness
    def test_missing_number_is_rejected(self):
        for rows in ([row('62', (84, 645, 139, 687))],
                     [row('62/', (84, 645, 139, 687))],
                     [row('/62', (127, 655, 184, 686))],
                     [row('62', (84, 645, 120, 687)),
                      row('/', (122, 650, 132, 686))]):
            with self.subTest(rows=rows):
                self.assertIsNone(ratio_from_rows(rows))

    def test_missing_slash_is_not_guessed(self):
        rows = [
            row('62', (84, 645, 120, 687)),
            row('62', (122, 645, 180, 687)),
        ]
        self.assertIsNone(ratio_from_rows(rows))

    def test_control_text_cannot_be_merged_into_a_ratio(self):
        for text in ('62/62 前往', 'OPERATION 62/62', '62/62%', '第62/62关'):
            with self.subTest(text=text):
                self.assertIsNone(
                    ratio_from_rows([row(text, (84, 645, 210, 687))]))

    def test_character_guesses_are_rejected(self):
        for text in ('6O/62', '62/6I', '62l62', '62\\62', '62,62',
                     '62//62', '62/62/'):
            with self.subTest(text=text):
                self.assertIsNone(
                    ratio_from_rows([row(text, (84, 645, 210, 687))]))

    def test_signs_percent_and_decimals_are_rejected(self):
        for text in ('-1/62', '62/-1', '+62/62', '62/62%',
                     '62.0/62', '62/62.0'):
            with self.subTest(text=text):
                self.assertIsNone(
                    ratio_from_rows([row(text, (84, 645, 210, 687))]))

    # ------------------------------------------------------------- line rules
    def test_vertical_overlap_not_equal_centres(self):
        # Centres are 660 and 670; the boxes still overlap vertically.
        rows = [
            row('62', (84, 640, 130, 680)),
            row('/62', (132, 650, 190, 690)),
        ]
        self.assertEqual(ratio_from_rows(rows)['text'], '62/62')

    def test_no_vertical_overlap_is_rejected(self):
        rows = [
            row('62', (84, 630, 130, 650)),
            row('/62', (132, 660, 190, 680)),
        ]
        self.assertIsNone(ratio_from_rows(rows))

    def test_eighteen_reference_pixel_gap_is_inclusive(self):
        rows = [
            row('62', (84, 645, 120, 687)),
            row('/62', (138, 655, 190, 686)),
        ]
        self.assertEqual(ratio_from_rows(rows)['text'], '62/62')

    def test_larger_than_eighteen_reference_pixel_gap_is_rejected(self):
        rows = [
            row('62', (84, 645, 120, 687)),
            row('/62', (138.1, 655, 190, 686)),
        ]
        self.assertIsNone(ratio_from_rows(rows))

    def test_gap_limit_scales_with_width(self):
        base = [
            row('62', (126, 967.5, 180, 1030.5)),
            row('/62', (207, 982.5, 270, 1029)),
        ]
        self.assertEqual(
            ratio_from_rows(base, width=1920, height=1080)['text'], '62/62')
        base[1] = row('/62', (207.1, 982.5, 270, 1029))
        self.assertIsNone(ratio_from_rows(base, width=1920, height=1080))

    # ------------------------------------------------------ control/ambiguity
    def test_control_text_inside_roi_is_ignored_when_not_between_tokens(self):
        rows = [
            row('62/62', (84, 645, 190, 687)),
            row('前往', (195, 645, 220, 687)),
        ]
        self.assertEqual(ratio_from_rows(rows)['text'], '62/62')

    def test_control_text_between_split_tokens_breaks_adjacency(self):
        rows = [
            row('62', (84, 645, 120, 687)),
            row('前往', (122, 645, 136, 687)),
            row('/62', (138, 655, 190, 686)),
        ]
        self.assertIsNone(ratio_from_rows(rows))

    def test_conflicting_ratios_on_one_line_are_rejected(self):
        rows = [
            row('62/62', (84, 635, 150, 660)),
            row('61/62', (155, 635, 220, 660)),
        ]
        self.assertIsNone(ratio_from_rows(rows))

    def test_conflicting_ratios_on_two_lines_are_rejected(self):
        rows = [
            row('62/62', (84, 635, 210, 655)),
            row('81/81', (84, 680, 210, 700)),
        ]
        self.assertIsNone(ratio_from_rows(rows))

    def test_same_ratio_on_two_lines_is_still_ambiguous(self):
        rows = [
            row('62/62', (84, 635, 210, 655)),
            row('62/62', (84, 680, 210, 700)),
        ]
        self.assertIsNone(ratio_from_rows(rows))

    def test_identical_duplicate_on_one_line_collapses(self):
        rows = [
            row('62/62', (84, 645, 150, 687), score=.99),
            row('62/62', (160, 645, 220, 687), score=.98),
        ]
        result = ratio_from_rows(rows)
        self.assertEqual(result['text'], '62/62')
        self.assertEqual(result['score'], .99)

    # -------------------------------------------------------- malformed input
    def test_missing_box_is_rejected(self):
        self.assertIsNone(ratio_from_rows([
            {'text': '62/62', 'score': .99, 'x': 147, 'y': 666}]))
        self.assertIsNone(ratio_from_rows([
            row('62/62', (84, 645, 210, 687)),
            {'text': '62/62', 'score': .99}]))  # fail closed, not silently skip

    def test_malformed_box_is_rejected(self):
        for box in ([84, 645, 210], '84,645,210,687',
                    [84, 645, 84, 687], [84, 687, 210, 645],
                    [84, 645, 210, 687, 1], None):
            with self.subTest(box=box):
                self.assertIsNone(ratio_from_rows([
                    {'text': '62/62', 'score': .99, 'box': box}]))

    def test_nan_inf_and_bool_coordinates_are_rejected(self):
        for box in ([84, 645, float('nan'), 687],
                    [84, float('inf'), 210, 687],
                    [True, 645, 210, 687],
                    [84, 645, 210, False]):
            with self.subTest(box=box):
                self.assertIsNone(ratio_from_rows([
                    {'text': '62/62', 'score': .99, 'box': box}]))

    def test_missing_or_wrong_text_is_rejected(self):
        for text in (None, 62, '', '   '):
            with self.subTest(text=text):
                self.assertIsNone(ratio_from_rows([
                    {'text': text, 'score': .99,
                     'box': [84, 645, 210, 687]}]))

    def test_optional_centre_coordinates_are_validated_when_present(self):
        good = {'text': '62/62', 'score': .99, 'box': [84, 645, 210, 687]}
        self.assertEqual(ratio_from_rows([good])['text'], '62/62')
        for extra in ({'x': float('nan')}, {'y': float('inf')},
                      {'x': True}, {'y': -1}, {'x': 1281}):
            with self.subTest(extra=extra):
                self.assertIsNone(ratio_from_rows([{**good, **extra}]))

    def test_non_mapping_rows_are_rejected(self):
        for rows in ([None], ['62/62'], [42], [['62/62']]):
            with self.subTest(rows=rows):
                self.assertIsNone(ratio_from_rows(rows))

    def test_invalid_dimensions_are_rejected(self):
        rows = [row('62/62', (84, 645, 210, 687))]
        for width, height in ((0, 720), (-1, 720), (1280, 0),
                              (float('nan'), 720), (1280, float('inf')),
                              (True, 720), ('1280', 720)):
            with self.subTest(width=width, height=height):
                self.assertIsNone(ratio_from_rows(rows, width, height))

    def test_invalid_rows_container_is_rejected(self):
        for rows in (None, '', b'', {}, 42):
            with self.subTest(rows=rows):
                self.assertIsNone(ratio_from_rows(rows))

    # ------------------------------------------------------------ purity/API
    def test_input_is_not_mutated(self):
        rows = [
            {'text': '62', 'score': .9987, 'x': 278.0, 'y': 1672.0,
             'box': [205.0, 1619.0, 351.0, 1725.0]},
            {'text': '/62', 'score': .974, 'x': 394.5, 'y': 1683.5,
             'box': [329.0, 1646.0, 460.0, 1721.0]},
        ]
        snapshot = copy.deepcopy(rows)
        ratio_from_rows(rows, width=3200, height=1808)
        self.assertEqual(rows, snapshot)
        self.assertIsInstance(rows[0]['box'], list)

    def test_generator_input_is_accepted(self):
        rows = [row('62/62', (84, 645, 210, 687))]
        self.assertEqual(
            ratio_from_rows(item for item in rows)['text'], '62/62')

    def test_calls_are_independent(self):
        bad = [row('62/62', (84, 635, 210, 655)),
               row('81/81', (84, 680, 210, 700))]
        good = [row('62/62', (84, 645, 210, 687))]
        self.assertIsNone(ratio_from_rows(bad))
        self.assertEqual(ratio_from_rows(good)['text'], '62/62')
        self.assertIsNone(ratio_from_rows(bad))


if __name__ == '__main__':
    unittest.main()
