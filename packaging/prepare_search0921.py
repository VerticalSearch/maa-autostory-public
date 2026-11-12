from pathlib import Path
root=Path(__file__).resolve().parent
text=(root/'build_training0920.py').read_text('utf-8-sig')
text=text.replace('training0920','search0921').replace('formation0919','training0920')
text=text.replace("BASE=ROOT/'App-0.9.19'","BASE=ROOT/'App-0.9.20'").replace("TARGET=ROOT/'App-0.9.20'","TARGET=ROOT/'App-0.9.21'")
text=text.replace('__version__ = "0.9.20"','__version__ = "0.9.21"').replace("'version':'0.9.20','baseline_version':'0.9.19'","'version':'0.9.21','baseline_version':'0.9.20'")
text=text.replace("'(0,9,20,0)'","'(0,9,21,0)'").replace("replace('0.8.1','0.9.20')","replace('0.8.1','0.9.21')")
start=text.index('CHANGED=');end=text.index('\n',start)
text=text[:start]+"CHANGED=('autostory','autostory.jobs','autostory.job_search','autostory.job_sources','autostory.controller','autostory.campaign','autostory.attempts')"+text[end:]
text=text.replace('adding authorized basic skill upgrades','expanding complete per-stage job discovery')
(root/'build_search0921.py').write_text(text,encoding='utf-8')
text=(root/'verify_training0920.py').read_text('utf-8-sig').replace('0.9.20','0.9.21')
text=text.replace('tests-frozen0920.log','tests-frozen0921.log').replace('training0920-frozen-check.json','search0921-frozen-check.json')
text=text.replace("'test_jobs_review'","'test_jobs_review','test_search0921'")
(root/'verify_search0921.py').write_text(text,encoding='utf-8')
text=(root/'install_training0920.ps1').read_text('utf-8-sig').replace('0.9.20','0.9.21').replace('training0920','search0921')
(root/'install_search0921.ps1').write_text(text,encoding='utf-8')
text=(root/'audit_web0920.py').read_text('utf-8-sig').replace('0.9.20','0.9.21').replace('web0920','web0921')
extra="""        target=next(r for r in current['coverage']['rows'] if r['code']=='R8-9' and r['mode']=='raid')
        target['job_search']={'at':'2026-10-31T23:59:00+08:00','complete':True,'exhausted':True,'candidates':23,'eligible':3,'sources':[{'name':'PRTS Plus'},{'name':'ntgmc历史归档'}]}
        target['job_count']=23
        page.locator('#navCatalog').click()
        page.evaluate('AutoStoryUI.resetFilters(); AutoStoryUI.setQuery("R8-9"); AutoStoryUI.setFilters({mode:"raid"}); AutoStoryUI.refresh()')
        page.wait_for_timeout(150);page.locator('#stageBody tr').first.click()
        check('search_coverage_and_sources_visible','23' in page.locator('#detailSearch').inner_text() and 'ntgmc历史归档' in page.locator('#detailSearch').inner_text())
        target['job_search']['complete']=False;target['job_search']['exhausted']=False
        page.evaluate('AutoStoryUI.refresh()');page.wait_for_timeout(150)
        check('partial_search_never_claims_complete','检索尚未完成' in page.locator('#detailSearch').inner_text())
        target['job_search']={};page.evaluate('AutoStoryUI.refresh()');page.wait_for_timeout(150)
        check('unsearched_distinct_from_no_job','尚未检索' in page.locator('#detailSearch').inner_text())
        page.screenshot(path=str(OUT/'web0921-search.png'))
"""
text=text.replace("        check('no_script_errors',not errors)",extra+"        check('no_script_errors',not errors)")
(root/'audit_web0921.py').write_text(text,encoding='utf-8')
print('Prepared build, frozen checks, idle backend installer and isolated browser audit.')
