import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from autostory import common


class AtomicJsonTests(unittest.TestCase):
    def test_concurrent_writers_keep_distinct_files_until_atomic_replace(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'report.json';barrier=threading.Barrier(4)
            original=common.os.replace;names=[];lock=threading.Lock()
            def replace(source,target):
                with lock:
                    first=source not in names
                    if first:names.append(source)
                if first:barrier.wait(timeout=5)
                original(source,target)
            values=[{'writer':i,'text':'内容'*10000} for i in range(4)]
            with patch.object(common.os,'replace',side_effect=replace),ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda value:common.write_json(path,value),values))
            self.assertEqual(len(set(names)),4)
            self.assertIn(json.loads(path.read_text('utf-8')),values)
            self.assertEqual(list(Path(folder).glob('*.tmp')),[])

    def test_windows_sharing_conflict_has_bounded_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'report.json';common.write_json(path,{'old':True})
            conflict=PermissionError('sharing');conflict.winerror=32
            with patch.object(common.os,'replace',side_effect=conflict) as replace, \
                 patch.object(common.time,'sleep') as sleep,self.assertRaises(PermissionError):
                common.write_json(path,{'new':True})
            self.assertEqual(replace.call_count,5)
            self.assertAlmostEqual(sum(c.args[0] for c in sleep.call_args_list),.2)
            self.assertEqual(common.read_json(path),{'old':True})
            self.assertEqual(list(Path(folder).glob('*.tmp')),[])

    def test_serialization_error_preserves_existing_record(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'record.json';common.write_json(path,{'valid':True})
            with self.assertRaises(TypeError):common.write_json(path,{'bad':object()})
            self.assertEqual(common.read_json(path),{'valid':True})
            self.assertEqual(list(Path(folder).glob('*.tmp')),[])

    def test_replace_error_preserves_old_record_and_cleans_only_own_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'record.json';common.write_json(path,{'old':True})
            other=Path(folder)/'record.json.other.tmp';other.write_text('other writer')
            with patch.object(common.os,'replace',side_effect=PermissionError('locked')),self.assertRaises(PermissionError):
                common.write_json(path,{'new':True})
            self.assertEqual(common.read_json(path),{'old':True})
            self.assertEqual(list(Path(folder).glob('*.tmp')),[other])

if __name__=='__main__':unittest.main()
