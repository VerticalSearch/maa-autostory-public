import copy,unittest
from unittest.mock import Mock,patch
from autostory.jobs import match_job
from autostory.practice_abort import reject_aborted_practice
from test_practice_abort0914 import events


class SkillRankTests(unittest.TestCase):
    def match(self,rank,required,selected=1,elite=1):
        row={'id':'cardigan','name':'卡缇','own':True,'elite':elite,'level':55,
             'mainSkillLevel':rank,'observedSkillLevels':{}}
        operator={'name':'卡缇','requirements':{'elite':1,'level':55,'skill_level':required}}
        if selected is not None:operator['skill']=selected
        with patch('autostory.jobs.read_json',return_value={'chars':{}}):
            return match_job({'opers':[operator]},{'own_opers':[row]})

    def test_cardigan_rank_six_cannot_enter_rank_seven_job_without_mastery_metadata(self):
        result=self.match(6,7)
        self.assertEqual(result['status'],'blocked');self.assertIn('技能等级不足',';'.join(result['reasons']))

    def test_rank_six_cannot_satisfy_mastery_requirement(self):
        self.assertEqual(self.match(6,10)['status'],'blocked')

    def test_omitted_selected_skill_still_rejects_known_low_base_rank(self):
        self.assertEqual(self.match(6,7,None)['status'],'blocked')

    def test_base_only_requirement_is_confirmed_without_mastery_icons(self):
        for rank,required in ((6,6),(7,7)):
            with self.subTest(rank=rank):
                result=self.match(rank,required)
                self.assertEqual(result['status'],'trial_required')
                self.assertEqual(result['assignment'][0]['unknown'],[])

    def test_rank_seven_does_not_fabricate_mastery(self):
        self.assertEqual(self.match(7,10)['status'],'blocked')
        result=self.match(7,10,elite=2)
        self.assertEqual(result['status'],'trial_required')
        self.assertIn('技能/专精待实测',result['assignment'][0]['unknown'])

    def test_missing_base_rank_remains_unknown(self):
        result=self.match(None,7)
        self.assertEqual(result['status'],'trial_required')
        self.assertIn('技能/专精待实测',result['assignment'][0]['unknown'])


class PartialAbortTests(unittest.TestCase):
    def test_partial_abort_always_stops_before_result_observation(self):
        for kind in ('duplicate_start','missing_complete','wrong_id','missing_chain','invalid_count'):
            rows=copy.deepcopy(events())
            if kind=='duplicate_start':rows.insert(1,copy.deepcopy(rows[0]))
            elif kind=='missing_complete':rows.pop()
            elif kind=='wrong_id':rows[1]['taskid']=12
            elif kind=='missing_chain':rows[0].pop('taskchain')
            else:rows[1]['details']['exec_times']=9
            nav=Mock()
            with self.subTest(kind=kind),self.assertRaisesRegex(RuntimeError,'回调不完整'):
                reject_aborted_practice(nav,'8','R8-9','raid',rows)
            nav.observe.assert_not_called();nav.d.tap.assert_not_called();nav.find.assert_not_called()


if __name__=='__main__':unittest.main()
