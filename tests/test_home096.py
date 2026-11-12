import unittest
from autostory.home_observation import home_regions

def label(text,box):
    return dict(text=text,box=box,score=.99,x=(box[0]+box[2])/2,y=(box[1]+box[3])/2)

class AlternateHomeAnchors(unittest.TestCase):
    def setUp(self):
        self.labels=[label('采购中心',[763,468,914,524]),label('公开招募',[943,490,1064,535]),label('千员寻访',[1102,499,1237,548])]

    def test_third_exact_independent_home_control_replaces_bad_glyph(self):
        for third in (label('编队',[697,308,784,363]),label('角色管理',[945,366,1041,396])):
            self.assertEqual(len(home_regions(self.labels+[third])),3)

    def test_bad_glyph_or_wrong_position_alone_is_not_enough(self):
        self.assertFalse(home_regions(self.labels))
        self.assertFalse(home_regions(self.labels+[label('编队',[100,100,180,150])]))

    def test_overlay_or_duplicate_control_rejects_alternative(self):
        third=label('编队',[697,308,784,363])
        self.assertFalse(home_regions(self.labels+[third,third]))
        self.assertFalse(home_regions(self.labels+[third,{'text':'放弃行动 继续行动'}]))
