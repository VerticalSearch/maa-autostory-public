import copy,json,unittest
from pathlib import Path
from unittest.mock import Mock
import cv2,numpy as np
from autostory.sn_sparse_map import regions
from autostory.motion import map_motion,map_signature,settle

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'runtime/screens/observation'
IDS=('1793692975325900100','1793692979822698600','1793692984251959300')
def frames():return [cv2.imdecode(np.fromfile(FOLDER/(name+'.png'),np.uint8),1) for name in IDS]
def labels():
    rows=json.loads((FOLDER/(IDS[-1]+'.json')).read_text('utf-8'))['texts']
    return rows+[dict(text='SN-4',score=.99,x=1159.5,y=550.5,box=[1143,543,1176,558])]

class SparseSNTests(unittest.TestCase):
    def test_exact_code_and_node_icon_are_required(self):
        image=frames()[-1];rows=labels()
        rois=regions(image,rows,{'SN-4'})
        self.assertEqual(len(rois),2)
        self.assertTrue(all(90<roi[1]<610 for roi in rois))
        self.assertFalse(regions(image,rows[:-1],{'SN-4'}))
        self.assertFalse(regions(image,rows,set()))
        damaged=image.copy();x,y,w,h=rois[1];damaged[y:y+h,x:x+w]=0
        self.assertFalse(regions(damaged,rows,{'SN-4'}))

    def test_other_region_overlay_and_duplicate_nodes_rejected(self):
        image=frames()[-1];rows=labels()
        for word in ('开始行动','开始演习','行动结束','是否花费','放弃行动'):
            self.assertFalse(regions(image,rows+[dict(text=word)],{'SN-4'}))
        changed=copy.deepcopy(rows)
        for row in changed:
            if row['text']=='格兰法洛':row['text']='其他区域'
        self.assertFalse(regions(image,changed,{'SN-4'}))
        second=copy.deepcopy(rows[-1]);second.update(text='SN-3',x=600,box=[583,543,616,558])
        self.assertFalse(regions(image,rows+[second],{'SN-3','SN-4'}))

    def test_both_map_anchors_stay_still_across_real_animation(self):
        images=frames();rois=regions(images[-1],labels(),{'SN-4'})
        self.assertEqual(len(rois),2)
        for roi in rois:
            for a,b in zip(images,images[1:]):
                self.assertTrue(map_motion(map_signature(a,roi),map_signature(b,roi))['stable'])
        _,report=settle(Mock(side_effect=images),Mock(),Mock(),clock=lambda:0,rois=rois,kind='map')
        self.assertTrue(report['confirmed']);self.assertEqual(report['frames'],3)

    def test_each_anchor_rejects_real_one_two_five_pixel_motion(self):
        image=frames()[-1];rois=regions(image,labels(),{'SN-4'})
        self.assertEqual(len(rois),2)
        for roi in rois:
            for axis in (0,1):
                for shift in (-5,-2,-1,1,2,5):
                    self.assertFalse(map_motion(map_signature(image,roi),map_signature(np.roll(image,shift,axis),roi))['stable'])

if __name__=='__main__':unittest.main()
