"""Complete search and archived JSON regressions; no live network or game input."""
import copy,hashlib,io,json,tempfile,time,unittest,zipfile
from pathlib import Path
from unittest.mock import Mock,patch
from autostory import job_search as search,job_sources as sources
from autostory.jobs import JobStore,JobServiceUnavailable
from autostory.attempts import AttemptHistory
from autostory.common import write_json,read_json
QUERY_SPECS=search.query_specs

def doc(**extra):return {'stage_name':'internal_8','difficulty':3,'opers':[],'groups':[],**extra}
def row(ident,**extra):return {'id':ident,'content':json.dumps(doc(**extra)),'available':True}
def page(rows=(),more=False):return {'data':list(rows),'has_next':more,'total':len(rows)}

class CompleteSearchTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.store=object.__new__(JobStore);self.store.cache=self.root/'jobs';self.store.cache.mkdir()
        self.store.catalog=Mock();self.store.catalog.maps=[]
        self.store.catalog.code.side_effect=lambda name:'R8-9' if name in ('internal_8','R8-9') else None
        self.store.get=Mock(return_value=page());self.store.query_sets=Mock(return_value=page())
        patches=[patch.object(search,'ROOT',self.root),patch.object(search,'check_stop'),patch.object(search.workflow,'update'),
                 patch.object(search.time,'sleep'),patch.object(search,'query_specs',return_value=[('text',{'document':'R8-9'})]),
                 patch.object(search,'additional_jobs',return_value=([],[]))]
        self.mocks=[]
        for target in patches:self.mocks.append(target.start());self.addCleanup(target.stop)
    def run_search(self,**kw):return self.store.search('R8-9',mode='raid',**kw)
    def coverage(self):return read_json(self.store.cache/'R8-9-raid-coverage.json')

    def test_all_301_rows_after_twelfth_page_and_three_eligible(self):
        def get(path,params):
            start=(params['page']-1)*20
            return page([row(i) for i in range(start+1,min(start+21,302))],start+20<301)
        self.store.get.side_effect=get
        rows=self.run_search(accept=lambda d:True)
        self.assertEqual(len(rows),301);self.assertEqual(self.store.get.call_count,16)
        self.assertTrue(self.coverage()['complete']);self.assertEqual(self.coverage()['eligible'],301)
        self.assertIsNone(self.coverage()['limit'])

    def test_preview_without_actions_retained_and_roster_is_not_identity(self):
        self.store.get.return_value=page([row(1),row(2)])
        self.assertEqual({r['id'] for r in self.run_search()},{1,2})

    def test_aliases_union_without_repeating_ids(self):
        self.mocks[4].return_value=[('text',{'document':'R8-9'}),('stage',{'levelKeyword':'internal_8'}),('text',{'document':'相逢'})]
        self.store.get.side_effect=[page([row(1)]),page([row(1),row(2)]),page([row(3)])]
        self.assertEqual({r['id'] for r in self.run_search()},{1,2,3})

    def test_wrong_mode_and_stage_excluded(self):
        self.store.get.return_value=page([row(1,difficulty=1),row(2,stage_name='other'),row(3)])
        self.assertEqual([r['id'] for r in self.run_search()],[3])

    def test_ignored_stage_filter_does_not_scan_global_database(self):
        self.mocks[4].return_value=[('stage',{'levelKeyword':'internal_8'}),('text',{'document':'R8-9'})]
        self.store.get.side_effect=[page([row(1,stage_name='other')],True),page([row(2)])]
        self.assertEqual([r['id'] for r in self.run_search()],[2]);self.assertEqual(self.store.get.call_count,2)
        self.assertFalse(self.coverage()['complete']);self.assertEqual(self.coverage()['stop_reason'],'source_incomplete')

    def test_repeat_empty_and_missing_terminal_marker_fail_explicitly(self):
        for responses in ([page([row(1)],True),page([row(1)],True)],[page([],True)],[{'data':[row(1)]}]):
            with self.subTest(responses=responses):
                self.store.get.side_effect=copy.deepcopy(responses)
                with self.assertRaises(JobServiceUnavailable):self.run_search(force=True)
                self.assertFalse(self.coverage()['complete']);self.assertEqual(self.coverage()['stop_reason'],'error')

    def test_bad_numeric_id_rejected(self):
        self.store.get.return_value=page([row('1')],True)
        with self.assertRaises(JobServiceUnavailable):self.run_search()
        self.assertEqual(self.store.get.call_count,1)

    def test_fresh_cache_copy_and_force(self):
        self.store.get.return_value=page([row(1)])
        self.run_search();self.store.get.reset_mock()
        rows=self.run_search();self.store.get.assert_not_called();self.assertEqual(self.coverage()['cache_hits'],1)
        rows[0]['preview']['opers'].append('mutated')
        self.assertEqual(self.run_search()[0]['preview']['opers'],[])
        self.run_search(force=True);self.assertEqual(self.store.get.call_count,1)

    def test_corrupt_future_expired_cache_refetched(self):
        self.run_search();cache=next((self.store.cache/'search-v2').glob('*.json'))
        for value in ('{',json.dumps({**read_json(cache),'at':time.time()+60})):
            cache.write_text(value);self.store.get.reset_mock();self.run_search();self.assertEqual(self.store.get.call_count,1)
        old=read_json(cache);old['at']=time.time()-901;write_json(cache,old)
        self.store.get.reset_mock();self.run_search();self.assertEqual(self.store.get.call_count,1)

    def test_excluded_ids_do_not_stop_search(self):
        self.store.get.return_value=page([row(1),row(2),row(3)])
        self.assertEqual([r['id'] for r in self.run_search(exclude_ids=[1,2])],[3]);self.assertTrue(self.coverage()['complete'])

    def test_explicit_limit_never_claims_complete(self):
        self.store.get.return_value=page([row(1),row(2)])
        self.assertEqual(len(self.run_search(limit=1)),1);self.assertFalse(self.coverage()['complete'])
        self.store.get.reset_mock();self.assertEqual(self.run_search(limit=0),[]);self.store.get.assert_not_called()

    def test_set_members_paged_and_batched(self):
        self.mocks[4].return_value=[('set',{'keyword':'第八章'})]
        self.store.query_sets.side_effect=[page([{'id':80,'copilot_ids':list(range(1,24))}],True),page([{'id':81,'copilot_ids':[1,24]}])]
        self.store.get.side_effect=lambda path,params:page([row(int(i)) for i in params['copilotIds'].split(',')])
        self.assertEqual(len(self.run_search()),24);self.assertEqual(self.store.get.call_count,2)
        self.assertEqual(self.coverage()['set_members'],24);self.assertTrue(self.coverage()['complete'])
        self.assertFalse(self.store.query_sets.call_args.args[0]['only_following'])

    def test_configured_set_and_deleted_member_accounted_for(self):
        write_json(self.root/'job-sources.json',{'prts_sets':[7]})
        def get(path,params):
            if path=='/set/get':return {'copilot_ids':[1,2]}
            return page([row(1)]) if 'copilotIds' in params else page()
        self.store.get.side_effect=get
        self.assertEqual(len(self.run_search()),1);self.assertEqual(self.coverage()['unavailable'],[2])

    def test_ignored_member_filter_rejected(self):
        self.mocks[4].return_value=[('set',{'keyword':'第八章'})]
        self.store.query_sets.return_value=page([{'id':80,'copilot_ids':[1]}]);self.store.get.return_value=page([row(2)])
        with self.assertRaises(JobServiceUnavailable):self.run_search()

    def test_external_full_content_dedup_keeps_provenance(self):
        full=doc(actions=[{'type':'SkillDaemon'}])
        items=[{'id':'ext-'+str(i),'preview':{**full,'doc':{'title':str(i)}},'source':'archive'+str(i)} for i in (1,2)]
        self.mocks[5].return_value=(items,[{'name':'archive','complete':True}])
        result=self.run_search();self.assertEqual(len(result),1);self.assertEqual(self.coverage()['duplicates'],1)
        self.assertEqual(len(result[0]['provenance']),2)

    def test_external_error_retains_primary_candidates_but_incomplete(self):
        self.store.get.return_value=page([row(1)])
        self.mocks[5].return_value=([],[{'name':'archive','complete':False,'errors':['offline']}])
        self.assertEqual(len(self.run_search()),1);self.assertFalse(self.coverage()['exhausted'])

    def test_stop_saved_and_propagated(self):
        self.mocks[1].side_effect=InterruptedError('stop')
        with self.assertRaises(InterruptedError):self.run_search()
        self.assertEqual(self.coverage()['stop_reason'],'stopped')

    def test_invalid_preview_skips_only_bad_job(self):
        bad=row(1);bad['content']='{';self.store.get.return_value=page([bad,row(2)])
        self.assertEqual(len(self.run_search()),1);self.assertEqual(self.coverage()['malformed'],1)

    def test_specs_internal_ids_and_chinese_chapter_aliases(self):
        with patch.object(search,'stage_info',return_value={'kind':'main','chapter':'8','zone':'怒号光明'}):
            catalog=Mock();catalog.maps=[{'code':'R8-9','stageId':'internal_8#f#','name':'相逢'}]
            specs=QUERY_SPECS(catalog,'R8-9','raid')
        self.assertIn(('stage',{'levelKeyword':'internal_8'}),specs)
        self.assertIn(('text',{'document':'相逢'}),specs);self.assertIn(('set',{'keyword':'第八章'}),specs)

class SourceAdapterTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        for name,value in [('ROOT',self.root),('MAA',self.root/'maa')]:
            p=patch.object(sources,name,value);p.start();self.addCleanup(p.stop)
        p=patch.object(sources,'check_stop');p.start();self.addCleanup(p.stop);self.cache=self.root/'cache'

    def zip_fixture(self,entries):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as bundle:
            for name,value in entries.items():bundle.writestr(name,json.dumps(value))
        payload=stream.getvalue();digest=hashlib.sha256(payload).hexdigest()
        path=self.cache/'archives'/(digest+'.zip');path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
        return {'name':'archive','url':'https://example.com/a.zip','sha256':digest}

    def test_zip_reads_json_without_extracting_paths(self):
        source=self.zip_fixture({'../../outside.json':doc(actions=[{'type':'SkillDaemon'}]),'unused.py':'not executed'})
        self.assertEqual(len(sources.archive_documents(source,self.cache)),1)
        self.assertFalse((self.root/'outside.json').exists())

    def test_zip_filters_by_actual_stage_and_counts_whole_catalogue(self):
        source=self.zip_fixture({'a.json':doc(actions=[{'type':'SkillDaemon'}]),'b.json':doc(stage_name='other',actions=[{'type':'SkillDaemon'}])})
        write_json(self.root/'job-sources.json',{'json_archives':[source]})
        catalog=Mock();catalog.code.side_effect=lambda value:'R8-9' if value=='internal_8' else 'R8-8'
        items,reports=sources.additional_jobs(self.cache,code='R8-9',catalog=catalog)
        self.assertEqual(len(items),1);self.assertEqual(reports[-1]['total_documents'],2);self.assertTrue(reports[-1]['complete'])

    def test_corrupt_zip_json_is_visible_source_failure(self):
        source=self.zip_fixture({'a.json':doc(actions=[{'type':'SkillDaemon'}])})
        write_json(self.root/'job-sources.json',{'json_archives':[source]})
        with patch.object(sources,'archive_documents',side_effect=ValueError('bad json')):
            items,reports=sources.additional_jobs(self.cache)
        self.assertEqual(items,[]);self.assertFalse(reports[-1]['complete']);self.assertTrue(reports[-1]['errors'])

    def test_zip_download_hash_must_match(self):
        source={'url':'https://example.com/b.zip','sha256':'a'*64}
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.read.return_value=b'wrong'
        with patch.object(sources.urllib.request,'urlopen',return_value=response):
            with self.assertRaisesRegex(ValueError,'摘要'):sources.archive_documents(source,self.cache)

    def test_numeric_and_external_candidate_ids_sort_together(self):
        from autostory.controller import candidate_priority
        rows=[{'id':'ext-abc','match':{}},{'id':8,'match':{}}]
        self.assertEqual(sorted(rows,key=candidate_priority)[0]['id'],8)

    def test_local_envelope_import_and_changed_hash_rejected(self):
        full=doc(actions=[{'type':'SkillDaemon'}]);write_json(self.root/'jobs-import/job.json',{'content':json.dumps(full)})
        items,reports=sources.additional_jobs(self.cache,code='R8-9')
        self.assertEqual(len(items),1);self.assertEqual(sources.external_document(self.cache,items[0]['id']),full)
        path=self.cache/'external'/(items[0]['id']+'.json');value=read_json(path);value['document']['opers']=['changed'];write_json(path,value)
        with self.assertRaises(ValueError):sources.external_document(self.cache,items[0]['id'])
        with self.assertRaises(ValueError):sources.external_document(self.cache,'../../x')

    def test_pinned_manifest_fetches_target_stage_only(self):
        revision='a'*40
        write_json(self.root/'data/index.json',{'repository':'a/b','sha':revision,'truncated':False,'tree':[
            {'type':'blob','path':'R8-9.json','code_hint':'R8-9'},{'type':'blob','path':'R8-8.json','code_hint':'R8-8'}]})
        write_json(self.root/'job-sources.json',{'github_repositories':[{'repository':'a/b','ref':revision,'manifest':'data/index.json'}]})
        with patch.object(sources,'cached_public',return_value=doc(actions=[{'type':'SkillDaemon'}])) as get:
            items,reports=sources.additional_jobs(self.cache,code='R8-9')
        self.assertEqual(len(items),1);self.assertEqual(get.call_count,1);self.assertTrue(reports[-1]['complete'])
        self.assertIn(revision,get.call_args.args[0])

    def test_truncated_directory_incomplete(self):
        write_json(self.root/'job-sources.json',{'github_repositories':[{'repository':'a/b'}]})
        with patch.object(sources,'cached_public',return_value={'tree':[],'truncated':True}):
            items,reports=sources.additional_jobs(self.cache)
        self.assertFalse(reports[-1]['complete']);self.assertTrue(reports[-1]['errors'])

    def test_external_download_rechecks_stage_and_never_calls_prts(self):
        full=doc(actions=[{'type':'SkillDaemon'}]);ident='ext-'+sources.sha(full)
        write_json(self.cache/'external'/(ident+'.json'),{'document':full})
        store=object.__new__(JobStore);store.cache=self.cache;store.catalog=Mock();store.catalog.code.return_value='R8-9'
        store.get=Mock(side_effect=AssertionError('local JSON expected'))
        path,loaded,digest=store.download({'id':ident},'R8-9','raid')
        self.assertTrue(path.exists());self.assertEqual(loaded['stage_name'],'internal_8');store.get.assert_not_called()
        with self.assertRaises(ValueError):store.download({'id':ident},'R8-8','raid')

    def test_equivalent_failure_only_invalidated_by_script_or_team_change(self):
        history=AttemptHistory(self.root/'history.json');history.record('R8-9','raid',1,'script','team','practice_failed')
        self.assertIsNotNone(history.blocked_equivalent('R8-9','raid','script','team'))
        self.assertIsNone(history.blocked_equivalent('R8-9','raid','other-script','team'))
        self.assertIsNone(history.blocked_equivalent('R8-9','raid','script','upgraded-team'))
        self.assertIsNone(history.blocked_equivalent('R8-9','normal','script','team'))
        history.record('R8-9','raid','ext-other','script','team','passed')
        self.assertIsNone(history.blocked_equivalent('R8-9','raid','script','team'))

if __name__=='__main__':unittest.main()
