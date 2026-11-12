import http.client
import io
import ssl
import unittest
import urllib.error
from unittest.mock import Mock,patch
from autostory.jobs import JobStore,JobServiceUnavailable
from autostory.controller import Runner


class NetworkRetryTests(unittest.TestCase):
    def setUp(self):
        self.store=object.__new__(JobStore)
        self.stop=patch('autostory.jobs.check_stop').start()
        patch('autostory.jobs.time.sleep').start()
        patch('autostory.jobs.log').start()
        self.addCleanup(patch.stopall)

    def reply(self,raw=b'{"status_code":200,"data":{"ok":true}}'):
        response=Mock();response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False);response.read.return_value=raw
        return response

    def test_tls_disconnect_recovers_without_changing_request(self):
        error=urllib.error.URLError(ssl.SSLEOFError(8,'unexpected EOF'))
        with patch('autostory.jobs.urllib.request.urlopen',side_effect=[error,self.reply()]) as call:
            self.assertEqual(self.store.get('/copilot/query',{'document':'R8-8'}),{'ok':True})
        self.assertEqual(call.call_count,2)
        self.assertIs(call.call_args_list[0].args[0],call.call_args_list[1].args[0])

    def test_short_read_restarts_entire_response(self):
        bad=self.reply();bad.read.side_effect=http.client.IncompleteRead(b'partial')
        with patch('autostory.jobs.urllib.request.urlopen',side_effect=[bad,self.reply()]) as call:
            self.assertEqual(self.store.get('/copilot/get/1'),{'ok':True})
        self.assertEqual(call.call_count,2)

    def test_network_exhaustion_is_bounded_and_preserves_cause(self):
        error=TimeoutError('timeout')
        with patch('autostory.jobs.urllib.request.urlopen',side_effect=error) as call:
            with self.assertRaises(JobServiceUnavailable) as caught:self.store.get('/copilot/query')
        self.assertEqual(call.call_count,3);self.assertIs(caught.exception.__cause__,error)

    def test_permanent_http_or_certificate_errors_not_retried(self):
        errors=[urllib.error.HTTPError('https://test',404,'missing',{},io.BytesIO()),
                urllib.error.URLError(ssl.SSLCertVerificationError(1,'bad certificate'))]
        for error in errors:
            with self.subTest(error=error),patch('autostory.jobs.urllib.request.urlopen',side_effect=error) as call:
                with self.assertRaises(JobServiceUnavailable):self.store.get('/copilot/query')
                self.assertEqual(call.call_count,1)

    def test_temporary_http_recovers(self):
        error=urllib.error.HTTPError('https://test',503,'unavailable',{},io.BytesIO())
        with patch('autostory.jobs.urllib.request.urlopen',side_effect=[error,self.reply()]):
            self.assertEqual(self.store.get('/copilot/query'),{'ok':True})

    def test_single_deleted_job_is_candidate_error_not_service_failure(self):
        error=urllib.error.HTTPError('https://test',404,'missing',{},io.BytesIO())
        with patch('autostory.jobs.urllib.request.urlopen',side_effect=error) as call:
            with self.assertRaises(ValueError):self.store.get('/copilot/get/1')
        self.assertEqual(call.call_count,1)

    def test_auth_and_protocol_faults_stop_without_retry(self):
        for status in (401,403):
            error=urllib.error.HTTPError('https://test',status,'denied',{},io.BytesIO())
            with patch('autostory.jobs.urllib.request.urlopen',side_effect=error) as call:
                with self.assertRaises(JobServiceUnavailable):self.store.get('/copilot/query')
                self.assertEqual(call.call_count,1)
        for raw in (b'[]',b'{"status_code":500}',b'{"status_code":200}'):
            with patch('autostory.jobs.urllib.request.urlopen',return_value=self.reply(raw)) as call:
                with self.assertRaises(JobServiceUnavailable):self.store.get('/copilot/query')
                self.assertEqual(call.call_count,1)

    def test_stop_during_backoff_prevents_second_request(self):
        self.stop.side_effect=[None,InterruptedError('stop')]
        with patch('autostory.jobs.urllib.request.urlopen',side_effect=TimeoutError()) as call:
            with self.assertRaises(InterruptedError):self.store.get('/copilot/query')
        self.assertEqual(call.call_count,1)

    def test_invalid_detail_envelope_is_global_service_error(self):
        self.store.catalog=Mock();self.store.catalog.code.return_value='DV-1'
        for raw in (b'{"status_code":200,"data":null}',b'{"status_code":200,"data":{}}',
                    b'{"status_code":200,"data":{"content":null}}'):
            with patch('autostory.jobs.urllib.request.urlopen',return_value=self.reply(raw)) as call:
                with self.assertRaises(JobServiceUnavailable):self.store.download({'id':1},'DV-1')
                self.assertEqual(call.call_count,1)

    def test_invalid_job_document_remains_candidate_rejection(self):
        self.store.catalog=Mock();self.store.catalog.code.return_value='DV-1'
        import json
        for doc in ([],{'actions':[None]},{'actions':[{'type':[]}]},{'actions':[{}],'minimum_required':None}):
            with patch.object(self.store,'get',return_value={'content':json.dumps(doc)}):
                with self.assertRaises(ValueError):self.store.download({'id':1},'DV-1')

    def test_invalid_response_not_retried(self):
        with patch('autostory.jobs.urllib.request.urlopen',return_value=self.reply(b'bad json')) as call:
            with self.assertRaises(JobServiceUnavailable):self.store.get('/copilot/query')
        self.assertEqual(call.call_count,1)

    def test_invalid_timeout_rejects_before_writes_and_next_valid_job_downloads(self):
        import json,tempfile
        from pathlib import Path
        self.store.catalog=Mock();self.store.catalog.code.return_value='DV-1'
        with tempfile.TemporaryDirectory() as tmp:
            self.store.cache=Path(tmp)
            for value in (None,[],{},True,float('inf'),float('nan'),-1,-.5,1.5,'bad'):
                doc={'stage_name':'DV-1','difficulty':1,'actions':[{'type':'SkillDaemon','timeout':value}]}
                with patch.object(self.store,'get',return_value={'content':json.dumps(doc)}):
                    with self.assertRaises(ValueError):self.store.download({'id':1},'DV-1')
                self.assertEqual(list(self.store.cache.iterdir()),[])
            for action,expected in (({'type':'SkillDaemon'},180000),({'type':'SkillDaemon','timeout':500},500),({'type':'SkillDaemon','timeout':300000},180000)):
                doc={'stage_name':'DV-1','difficulty':1,'actions':[action]}
                with patch.object(self.store,'get',return_value={'content':json.dumps(doc)}):
                    path,saved,digest=self.store.download({'id':2},'DV-1')
                self.assertEqual(saved['actions'][0]['timeout'],expected);self.assertTrue(path.exists())

    def test_campaign_service_failure_keeps_budget_and_stops_before_next_map(self):
        runner=Runner(Mock(),Mock());seen=[];saved=[]
        def run(prefix,box,budget_state):
            seen.append(prefix);runner.phase='planning'
            raise JobServiceUnavailable('test outage')
        runner.run=run
        prior={'budget':{'sanity':164,'count':2,'limit':3,'practice_count':3,'practice_limit':12},'groups':[]}
        def read(path,default=None):return prior if path.name=='campaign-results.json' else {}
        with patch('autostory.controller.catalogue',return_value={'profiles':{'8':{},'16':{}},'stages':{}}), \
             patch('autostory.controller.settings',return_value={'max_battles_per_run':3}), \
             patch('autostory.controller.read_json',side_effect=read), \
             patch('autostory.controller.write_json',side_effect=lambda p,d:saved.append((p.name,d.copy()))), \
             patch('autostory.controller.box_is_stale',return_value=False), \
             patch('autostory.controller.workflow.update'),patch('autostory.controller.export_coverage'), \
             patch('autostory.controller.check_stop'),patch('autostory.controller.log'):
            with self.assertRaises(JobServiceUnavailable):runner.run_all(resume=True)
        self.assertEqual(seen,['8'])
        latest=[d for n,d in saved if n=='campaign-results.json'][-1]
        self.assertEqual(latest['state'],'error');self.assertEqual(latest['budget'],prior['budget'])

if __name__=='__main__':unittest.main()
