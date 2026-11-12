from __future__ import annotations
import copy
import json
import shutil
import sys
import unittest
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from autostory.attempts import (
    MAX_REASON, AttemptHistory, AttemptHistoryError, job_fingerprint,
    roster_fingerprint,
)

JOB_SHA = 'a' * 64
ROSTER_SHA = 'b' * 64


def sample_box():
    return {
        'done': True,
        'source': 'local',
        'syncTime': '2026-10-30T21:44:05+08:00',
        'own_opers': [
            {'id': 'char_a', 'name': 'Alpha', 'own': True, 'elite': 2,
             'level': 90, 'potential': 3, 'rarity': 6, 'skill': 3,
             'mastery': [3, 3, 3], 'module': 2, 'favor': 200},
            {'id': 'char_b', 'name': 'Beta', 'own': True, 'elite': 1,
             'level': 55, 'potential': 1, 'rarity': 5},
            {'id': 'char_c', 'name': 'Gamma', 'own': False, 'elite': 2,
             'level': 1, 'potential': 6, 'rarity': 6},
        ],
    }


def sample_job():
    return {
        'stage_name': 'main_04-10',
        'minimum_required': 'v4.0.0',
        'difficulty': 3,
        'version': 3,
        'doc': {'title': 'Narrative title', 'details': 'author notes'},
        'opers': [{'name': 'Alpha', 'skill': 2, 'skill_usage': 0}],
        'groups': [{'name': 'healers', 'opers': [
            {'name': 'Beta', 'skill': 1, 'skill_usage': 1}]}],
        'actions': [
            {'type': 'SpeedUp'},
            {'type': 'Deploy', 'name': 'Alpha', 'location': [4, 5],
             'direction': 'Right'},
            {'type': 'SkillDaemon'},
        ],
    }


class FingerprintTests(unittest.TestCase):
    def test_nested_unknown_training_fields_are_not_metadata(self):
        base=sample_box();changed=copy.deepcopy(base)
        base['own_opers'][0]['skills']=[{'time':1,'score':4}]
        changed['own_opers'][0]['skills']=[{'time':2,'score':4}]
        self.assertNotEqual(roster_fingerprint(base),roster_fingerprint(changed))
    def test_snapshot_metadata_and_order_do_not_change_roster(self):
        base = roster_fingerprint(sample_box())
        changed = sample_box()
        changed['syncTime'] = '2027-01-01T00:00:00+08:00'
        changed['source'] = 'maa'
        changed['done'] = False
        changed['own_opers'].reverse()
        changed['own_opers'][0]['confidence'] = 0.12
        changed['own_opers'][0]['bbox'] = [1, 2, 3, 4]
        changed['own_opers'].append(
            {'id': 'char_z', 'name': 'Zed', 'own': False, 'level': 99})
        self.assertEqual(base, roster_fingerprint(changed))

    def test_training_and_inventory_change_roster(self):
        base = sample_box()
        reference = roster_fingerprint(base)
        with_training = copy.deepcopy(base)
        with_training['training'] = {'char_a': {'mastery': [3, 3, 3]}}
        self.assertNotEqual(reference, roster_fingerprint(with_training))
        with_inventory = copy.deepcopy(with_training)
        with_inventory['inventory'] = {'module_blocks': 17}
        self.assertNotEqual(roster_fingerprint(with_training),
                            roster_fingerprint(with_inventory))

    def test_semantic_operator_fields_change_roster(self):
        reference = roster_fingerprint(sample_box())
        for field, value in (
                ('elite', 1), ('level', 89), ('potential', 2), ('rarity', 5),
                ('skill', 2), ('mastery', [3, 3, 0]), ('module', 1),
                ('favor', 199)):
            with self.subTest(field=field):
                box = sample_box()
                box['own_opers'][0][field] = value
                self.assertNotEqual(reference, roster_fingerprint(box))

    def test_unknown_field_and_absent_vs_null_are_preserved(self):
        reference = roster_fingerprint(sample_box())
        unknown = sample_box()
        unknown['own_opers'][0]['future_stat'] = 1
        self.assertNotEqual(reference, roster_fingerprint(unknown))
        nulled = sample_box()
        nulled['own_opers'][0]['favor'] = None
        self.assertNotEqual(reference, roster_fingerprint(nulled))
        self.assertNotEqual(roster_fingerprint(unknown),
                            roster_fingerprint(nulled))

    def test_invalid_roster_rejected(self):
        with self.assertRaises(TypeError):
            roster_fingerprint([])
        with self.assertRaises(ValueError):
            roster_fingerprint({'own_opers': {}})

    def test_narrative_and_metadata_do_not_change_job(self):
        base = job_fingerprint(sample_job())
        changed = sample_job()
        changed['doc'] = {'title': 'Another title', 'details': 'different'}
        changed['id'] = 25151
        changed['title'] = 'search result title'
        changed['url'] = 'https://example.invalid/job'
        changed['like'] = 999
        changed['available'] = False
        changed['timestamp'] = '2026-10-30T00:00:00+08:00'
        self.assertEqual(base, job_fingerprint(changed))
        # JSON formatting/key order must not matter either.
        reparsed = json.loads(json.dumps(sample_job(), ensure_ascii=False,
                                        indent=4, sort_keys=True))
        self.assertEqual(base, job_fingerprint(reparsed))

    def test_executable_job_fields_change_fingerprint(self):
        reference = job_fingerprint(sample_job())
        mutations = {
            'stage': lambda d: d.__setitem__('stage_name', 'main_04-11'),
            'difficulty': lambda d: d.__setitem__('difficulty', 2),
            'minimum_required': lambda d: d.__setitem__('minimum_required', 'v5.0.0'),
            'version': lambda d: d.__setitem__('version', 4),
            'assigned_oper': lambda d: d['opers'][0].__setitem__('name', 'Beta'),
            'group_oper': lambda d: d['groups'][0]['opers'][0].__setitem__('name', 'Alpha'),
            'action_location': lambda d: d['actions'][1]['location'].__setitem__(0, 5),
            'action_name': lambda d: d['actions'][1].__setitem__('name', 'Beta'),
            'action_added': lambda d: d['actions'].append({'type': 'Retreat'}),
            'unknown_key': lambda d: d.__setitem__('future_executable', True),
        }
        for name, mutate in mutations.items():
            with self.subTest(field=name):
                job = sample_job()
                mutate(job)
                self.assertNotEqual(reference, job_fingerprint(job))

    def test_invalid_job_rejected(self):
        with self.assertRaises(TypeError):
            job_fingerprint('not-a-document')


class HistoryTests(unittest.TestCase):
    def setUp(self):
        # Path.mkdir inherits the sandbox ACL; tempfile's 0o700 dirs do not.
        self.temp_dir = ROOT / 'tests' / '_tmp' / uuid.uuid4().hex
        self.temp_dir.mkdir(parents=True)
        self.addCleanup(self.cleanup_directory,self.temp_dir)
        self.path = self.temp_dir / 'runtime' / 'attempt-history.json'

    @staticmethod
    def cleanup_directory(path):
        target=path.resolve()
        expected=(ROOT/'tests'/'_tmp').resolve()
        if target.parent!=expected or len(target.name)!=32:
            raise ValueError('Unexpected test cleanup directory')
        shutil.rmtree(target,ignore_errors=True)

    def record_failure(self, history, **overrides):
        values = {'code': '4-10', 'mode': 'normal', 'job_id': 25151,
                  'job_sha': JOB_SHA, 'roster_sha': ROSTER_SHA,
                  'outcome': 'practice_failed', 'reason': '演习失败'}
        values.update(overrides)
        return history.record(**values)

    def test_missing_is_empty_and_record_persists_across_instances(self):
        history = AttemptHistory(self.path)
        self.assertFalse(self.path.exists())
        self.assertIsNone(history.blocked(
            '4-10', 'normal', 25151, JOB_SHA, ROSTER_SHA))
        self.assertFalse(self.path.exists())

        entry = self.record_failure(history, evidence='runtime/screens/1.png')
        self.assertEqual(
            ['code', 'mode', 'job_id', 'job_sha', 'roster_sha', 'outcome',
             'reason', 'evidence', 'at'], list(entry))
        self.assertIsInstance(datetime.fromisoformat(entry['at']), datetime)

        reopened = AttemptHistory(self.path)
        blocked = reopened.blocked('4-10', 'normal', 25151, JOB_SHA, ROSTER_SHA)
        self.assertEqual(entry, blocked)
        stored = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(1, stored['schema'])
        self.assertEqual([entry], stored['attempts'])
        self.assertFalse(self.path.with_suffix('.json.tmp').exists())

    def test_mode_stage_job_and_digest_must_all_match(self):
        history = AttemptHistory(self.path)
        self.record_failure(history)
        exact = ('4-10', 'normal', 25151, JOB_SHA, ROSTER_SHA)
        self.assertIsNotNone(history.blocked(*exact))
        variants = {
            'mode': ('4-10', 'raid', 25151, JOB_SHA, ROSTER_SHA),
            'stage': ('4-11', 'normal', 25151, JOB_SHA, ROSTER_SHA),
            'job_id': ('4-10', 'normal', 70118, JOB_SHA, ROSTER_SHA),
            'job_sha': ('4-10', 'normal', 25151, 'c' * 64, ROSTER_SHA),
            'roster_sha': ('4-10', 'normal', 25151, JOB_SHA, 'd' * 64),
            'job_id_type': ('4-10', 'normal', '25151', JOB_SHA, ROSTER_SHA),
        }
        for name, args in variants.items():
            with self.subTest(field=name):
                self.assertIsNone(history.blocked(*args))

    def test_latest_failure_wins_and_exact_passed_unblocks(self):
        history = AttemptHistory(self.path)
        first = self.record_failure(history, reason='first')
        second = self.record_failure(history, outcome='formal_failed')
        self.assertEqual(second, history.blocked(
            '4-10', 'normal', 25151, JOB_SHA, ROSTER_SHA))
        passed = self.record_failure(history, outcome='passed', reason='')
        self.assertIsNone(history.blocked(
            '4-10', 'normal', 25151, JOB_SHA, ROSTER_SHA))
        self.assertEqual([first, second, passed],
                         json.loads(self.path.read_text(encoding='utf-8'))['attempts'])

    def test_passed_for_other_job_does_not_clear_failure(self):
        history = AttemptHistory(self.path)
        self.record_failure(history)
        self.record_failure(history, job_id=70118, outcome='passed', reason='')
        self.assertIsNotNone(history.blocked(
            '4-10', 'normal', 25151, JOB_SHA, ROSTER_SHA))

    def test_returned_records_are_independent_copies(self):
        history = AttemptHistory(self.path)
        entry = self.record_failure(
            history, evidence={'shots': ['00.png'], 'match': True})
        entry['reason'] = 'mutated'
        entry['evidence']['shots'].append('01.png')
        blocked = AttemptHistory(self.path).blocked(
            '4-10', 'normal', 25151, JOB_SHA, ROSTER_SHA)
        self.assertEqual('演习失败', blocked['reason'])
        self.assertEqual(['00.png'], blocked['evidence']['shots'])
        blocked['evidence']['shots'].append('02.png')
        again = AttemptHistory(self.path).blocked(
            '4-10', 'normal', 25151, JOB_SHA, ROSTER_SHA)
        self.assertEqual(['00.png'], again['evidence']['shots'])

    def test_reason_is_bounded_and_evidence_accepts_str_path_list(self):
        history = AttemptHistory(self.path)
        entry = self.record_failure(history, reason='x' * (MAX_REASON + 500),
                                    evidence=Path('runtime/screens/00.png'))
        self.assertEqual(MAX_REASON, len(entry['reason']))
        self.assertEqual(str(Path('runtime/screens/00.png')), entry['evidence'])
        listed = self.record_failure(
            history, evidence=['runtime/screens/00.png', {'ok': True}])
        self.assertEqual(['runtime/screens/00.png', {'ok': True}],
                         listed['evidence'])
        self.assertEqual(2, len(json.loads(
            self.path.read_text(encoding='utf-8'))['attempts']))

    def test_invalid_inputs_rejected_without_writing(self):
        history = AttemptHistory(self.path)
        base = {'code': '4-10', 'mode': 'normal', 'job_id': 1,
                'job_sha': JOB_SHA, 'roster_sha': ROSTER_SHA,
                'outcome': 'practice_failed'}
        cases = [
            (ValueError, {'outcome': 'error'}),
            (ValueError, {'outcome': 'RuntimeError'}),
            (ValueError, {'outcome': 'failed'}),
            (ValueError, {'mode': 'hard'}),
            (ValueError, {'code': ''}),
            (ValueError, {'job_sha': '   '}),
            (TypeError, {'roster_sha': 123}),
            (TypeError, {'job_id': 1.5}),
            (ValueError, {'job_id': -1}),
            (TypeError, {'reason': 5}),
            (TypeError, {'evidence': 5}),
            (TypeError, {'evidence': [object()]}),
        ]
        for error, override in cases:
            with self.subTest(override=override):
                values = dict(base)
                values.update(override)
                with self.assertRaises(error):
                    history.record(**values)
        self.assertFalse(self.path.exists())

    def test_malformed_database_fails_closed_and_is_not_overwritten(self):
        valid_record = {
            'code': '4-10', 'mode': 'normal', 'job_id': 1,
            'job_sha': JOB_SHA, 'roster_sha': ROSTER_SHA,
            'outcome': 'practice_failed', 'reason': '', 'evidence': None,
            'at': '2026-10-30T00:00:00+08:00',
        }
        variants = {
            'truncated': '{"schema":1,"attempts":[',
            'empty_file': '',
            'root_list': '[]',
            'root_null': 'null',
            'wrong_schema': json.dumps({'schema': 2, 'attempts': []}),
            'missing_schema': json.dumps({'attempts': []}),
            'attempts_object': json.dumps({'schema': 1, 'attempts': {}}),
            'entry_not_object': json.dumps({'schema': 1, 'attempts': [1]}),
            'entry_missing_key': json.dumps({'schema': 1, 'attempts': [
                {k: v for k, v in valid_record.items() if k != 'job_sha'}]}),
            'entry_bad_outcome': json.dumps({'schema': 1, 'attempts': [
                dict(valid_record, outcome='stopped')]}),
        }
        for name, text in variants.items():
            with self.subTest(variant=name):
                path = self.temp_dir / f'{name}.json'
                path.write_text(text, encoding='utf-8')
                before = path.read_bytes()
                with self.assertRaises(AttemptHistoryError):
                    AttemptHistory(path).blocked(
                        '4-10', 'normal', 1, JOB_SHA, ROSTER_SHA)
                with self.assertRaises(AttemptHistoryError):
                    self.record_failure(AttemptHistory(path))
                self.assertEqual(before, path.read_bytes())


if __name__ == '__main__':
    unittest.main()
