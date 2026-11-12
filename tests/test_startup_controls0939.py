import copy
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

import cv2
import numpy as np

from autostory.startup_controls import start_button
from autostory.startup_screens import classify,GAME_PACKAGE
from autostory.vision import Vision

ROOT=Path(__file__).resolve().parents[1]

def row(text,x,y,score=.99):return dict(text=text,x=x,y=y,score=score)

class StartControlTests(unittest.TestCase):
    def test_resource_start_requires_all_anchors_and_high_template_score(self):
        rows=[row('清除缓存',85,48),row('网络检测',230,48),row('Ver:2.7.91',85,78)]
        vision=Mock();vision.match.return_value={'score':.99,'x':640,'y':681}
        self.assertEqual(start_button(None,rows,vision)['text'],'资源页开始')
        for index in range(3):
            bad=copy.deepcopy(rows);bad[index]['x']=900
            self.assertIsNone(start_button(None,bad,vision))
        vision.match.return_value={'score':.709765,'x':640,'y':665}
        self.assertIsNone(start_button(None,rows,vision))

    def test_title_wakeup_cannot_select_qr_or_account_controls(self):
        rows=[row('ARKNIGHTS',639,421),row('开始唤醒',642,512),row('扫码登录',607,683),row('账号管理',769,683)]
        self.assertEqual(classify(rows,GAME_PACKAGE)['state'],'login_start')
        self.assertEqual(start_button(None,rows,Mock())['text'],'开始唤醒')
        for bad in (rows[:1]+rows[2:],rows+[rows[1]],rows[1:]):
            self.assertIsNone(start_button(None,bad,Mock()))
        self.assertEqual(classify(rows+[row('请输入验证码',640,330)],GAME_PACKAGE)['state'],'attention')

    def test_retained_start_and_permission_popup(self):
        vision=Vision(ocr=Mock())
        for name,expected in [('autonomy0939/startup/5',True),('startup/1793686197378019900',False)]:
            base=ROOT/'runtime/screens'/name
            image=cv2.imdecode(np.fromfile(base.with_suffix('.png'),np.uint8),1)
            data=json.loads(base.with_suffix('.json').read_text('utf-8'))
            texts=data.get('observed',data)['texts']
            result=start_button(image,texts,vision) if classify(texts,GAME_PACKAGE)['state']=='login_start' else None
            self.assertEqual(result is not None,expected)

if __name__=='__main__':unittest.main()
