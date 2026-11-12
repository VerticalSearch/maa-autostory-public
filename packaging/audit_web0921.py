"""Browser acceptance against intercepted API fixtures. Never reaches real POST."""
import copy
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/playwright'
URL='http://127.0.0.1:18924/'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    fixture=json.loads((ROOT/'runtime/web090-preview/dashboard.json').read_text('utf-8'))
    current=copy.deepcopy(fixture)
    # The preview file is also used by visual work and may contain a live run.
    # This action regression explicitly starts from an isolated idle fixture.
    current['state'].update(running=False,status={'state':'idle'},workflow={})
    errors=[];posts=[];held=[];checks=[];layouts=[]
    mode={'hold':False,'failure':False}
    def check(name,condition):
        assert condition,name
        checks.append(name)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':1152,'height':768},permissions=['clipboard-read','clipboard-write'])
        page=context.new_page()
        page.on('pageerror',lambda e:errors.append(str(e)))
        def api(route):
            request=route.request
            if request.method=='POST':
                posts.append({'path':request.url.split('/api/')[1],'data':request.post_data_json,'token':request.headers.get('x-autostory-token')})
                if mode['hold'] and request.url.endswith('/api/action'):
                    held.append(route);return
                if mode['failure']:
                    return route.fulfill(status=409,json={'error':'已有后台任务正在运行（隔离测试）'})
                return route.fulfill(json={'message':'停止已接收（隔离测试）' if request.url.endswith('/api/stop') else '请求已接收（隔离测试）'})
            if '/api/health' in request.url:return route.fulfill(json={'version':'0.9.21','application':'MAA-AutoStory','pid':0})
            return route.fulfill(json=current)
        context.route('**/api/**',api)
        context.route(URL,lambda route:route.fulfill(body=(ROOT/'web/index.html').read_text('utf-8').replace('__TOKEN__','inert-preview-token'),content_type='text/html'))
        page.goto(URL)
        page.wait_for_function('window.AutoStoryUI && window.AutoStoryUI.getCoverage()')
        page.wait_for_timeout(80)
        check('initial_load_get_only',not posts)
        check('version_matches_backend',page.locator('#appVersion').inner_text()=='AutoStory 0.9.21')
        check('all_1877_records_loaded',page.evaluate('AutoStoryUI.getVisibleRows().length')==len(fixture['coverage']['rows']))
        check('first_page_200',page.locator('#stageBody tr').count()==200)
        page.locator('#nextPage').click()
        check('next_page',page.evaluate('AutoStoryUI.getPage()')==1)
        page.locator('#stageBody tr').first.click()
        selected=page.evaluate('AutoStoryUI.getSelectedKey()')
        page.evaluate('AutoStoryUI.refresh()')
        page.wait_for_timeout(80)
        check('poll_keeps_page_and_selection',page.evaluate('AutoStoryUI.getPage()')==1 and page.evaluate('AutoStoryUI.getSelectedKey()')==selected)
        page.evaluate('AutoStoryUI.goPage(9)')
        check('last_page_77',page.locator('#stageBody tr').count()==77)
        page.locator('#searchInput').fill('CW-EX-8')
        page.wait_for_timeout(200)
        check('search_and_page_clamp',page.evaluate('AutoStoryUI.getPage()')==0 and 0<page.locator('#stageBody tr').count()<10)
        page.locator('#modeFilter').select_option('raid')
        check('mode_filter',page.evaluate('AutoStoryUI.getVisibleRows().every(r=>r.mode==="raid")'))
        page.locator('#stageBody tr').first.click()
        check('complete_details',page.locator('#detailCode').inner_text()=='CW-EX-8' and page.locator('#detailMode').inner_text()=='突袭')
        page.locator('#copyDetails').click()
        check('copy_details', 'CW-EX-8' in page.evaluate('navigator.clipboard.readText()'))
        page.locator('#searchInput').fill('no-such-stage-zzzz')
        page.wait_for_timeout(200)
        check('empty_filter_clears_details',page.locator('#stageBody tr').count()==0 and page.locator('#copyDetails').is_disabled())
        page.locator('#resetFilters').click()
        page.locator('#stateFilter').select_option('pending')
        check('pending_excludes_unknown',page.evaluate('AutoStoryUI.getVisibleRows().every(r=>["partial","uncleared"].includes(r.state))'))
        page.locator('#resetFilters').click()
        for width,height in [(1152,768),(1050,720),(980,680)]:
            page.set_viewport_size({'width':width,'height':height})
            page.wait_for_timeout(80)
            if page.locator('#noticeClose').is_visible():page.locator('#noticeClose').click()
            bounds=page.evaluate('''() => ({width:innerWidth,scroll:document.body.scrollWidth,stop:getComputedStyle(document.querySelector('#stopBtn')).display,table:document.querySelector('#tableScroll').getBoundingClientRect().toJSON()})''')
            check('layout_no_horizontal_overflow_'+str(width),bounds['scroll']<=width)
            check('table_usable_'+str(width),bounds['table']['height']>=240)
            if width<=1100:
                page.locator('#taskDrawerBtn').click()
                check('drawer_stop_visible',page.locator('#stopBtn').is_visible())
                page.keyboard.press('Escape')
            page.screenshot(path=str(OUT/('web0921-'+str(width)+'.png')))
            layouts.append(bounds)
        page.set_viewport_size({'width':1152,'height':768})
        current['state']['logs']='\n'.join('隔离日志 '+str(i) for i in range(200))
        page.evaluate('AutoStoryUI.refresh()');page.wait_for_timeout(80)
        page.locator('#navLogs').click()
        check('stop_available_in_logs',page.locator('#stopBtn').is_visible())
        check('logs_follow_on_open',page.evaluate('(()=>{const e=document.querySelector("#logOutput");return e.scrollHeight-e.scrollTop-e.clientHeight<4})()'))
        page.locator('#followLogs').uncheck()
        page.locator('#logOutput').evaluate('(e)=>e.scrollTop=0')
        current['state']['logs']+='\n追加日志'
        page.evaluate('AutoStoryUI.refresh()');page.wait_for_timeout(80)
        check('logs_hold_position',page.locator('#logOutput').evaluate('(e)=>e.scrollTop')==0)
        page.locator('#jumpLatest').click()
        check('logs_jump_latest',page.evaluate('(()=>{const e=document.querySelector("#logOutput");return e.scrollHeight-e.scrollTop-e.clientHeight<4})()'))
        old_top=page.locator('#logOutput').evaluate('(e)=>e.scrollTop')
        current['state']['logs']+='\n跟随关闭后追加'
        page.evaluate('AutoStoryUI.refresh()');page.wait_for_timeout(80)
        check('logs_follow_off_holds_even_at_bottom',page.locator('#logOutput').evaluate('(e)=>e.scrollTop')==old_top)
        page.screenshot(path=str(OUT/'web0921-logs.png'))
        page.keyboard.press('Control+f')
        check('search_shortcut',page.locator('#searchInput').evaluate('(e)=>e===document.activeElement'))
        check('navigation_get_only',not posts)
        saved_coverage=current['coverage']
        current['coverage']=None
        page.evaluate('AutoStoryUI.refresh()');page.wait_for_timeout(80)
        check('revision_null_retains_catalogue',page.locator('#stageBody tr').count()==200)
        current['coverage']=saved_coverage
        # Simulate a slow action: stop must remain independently operable.
        mode['hold']=True
        page.locator('#runAllBtn').click()
        page.wait_for_timeout(60)
        check('global_action_scope',len(posts)==1 and posts[0]['data']=={'action':'run-all','chapter':'ALL'})
        check('csrf_header',posts[0]['token']=='inert-preview-token')
        check('busy_disables_actions_keeps_stop',page.locator('#runBtn').is_disabled() and page.locator('#stopBtn').is_enabled())
        page.locator('#stopBtn').click()
        page.wait_for_timeout(80)
        check('stop_independent',len(posts)==2 and posts[-1]['path']=='stop' and len(held)==1)
        held.pop().fulfill(json={'message':'过期启动响应'})
        mode['hold']=False
        page.wait_for_timeout(100)
        check('stale_action_response_ignored','过期启动响应' not in page.locator('#noticeText').inner_text())
        mode['failure']=True
        page.locator('#runBtn').click()
        page.wait_for_timeout(100)
        check('http_error_visible','已有后台任务正在运行' in page.locator('#noticeText').inner_text())
        check('no_post_retry',len(posts)==3)
        page.reload();page.wait_for_function('window.AutoStoryUI && window.AutoStoryUI.getCoverage()')
        check('reload_get_only',len(posts)==3)
        mode['failure']=False
        page.locator('#runBtn').click()
        page.wait_for_timeout(200)
        check('worker_request_accepted',len(posts)==4 and '请求已接收' in page.locator('#noticeText').inner_text())
        current['state']['status']={'state':'error','run_id':'fixture-092','finished':'2026-10-31T13:30:00+08:00','action':'scan','chapter':'CW-EX','message':'检测到源石兑换弹窗，请先取消弹窗再开始扫描或推图；尚未执行任务'}
        page.evaluate('AutoStoryUI.refresh()');page.wait_for_timeout(200)
        check('worker_failure_visible_after_accept','先取消弹窗' in page.locator('#noticeText').inner_text())
        check('failure_is_not_labelled_paused',page.locator('#statusText').inner_text()=='执行失败')
        check('old_workflow_cannot_claim_completion','本轮完成' not in page.locator('#workflowHeadline').inner_text() and '先取消弹窗' in page.locator('#workflowMessage').inner_text())
        check('old_campaign_labelled_separately','上轮全目录' in page.locator('#campaignBadge').inner_text())
        page.wait_for_timeout(8300)
        check('worker_failure_is_persistent',page.locator('#noticeText').is_visible() and '先取消弹窗' in page.locator('#noticeText').inner_text())
        page.screenshot(path=str(OUT/'web0921-worker-error.png'))
        page.locator('#noticeClose').click()
        page.evaluate('AutoStoryUI.refresh()');page.wait_for_timeout(100)
        check('dismissed_failure_not_repeated',not page.locator('#noticeText').is_visible())
        current['state']['status']['run_id']='fixture-092-next'
        page.evaluate('AutoStoryUI.refresh()');page.wait_for_timeout(100)
        check('new_failure_is_shown','先取消弹窗' in page.locator('#noticeText').inner_text() and page.locator('#noticeText').is_visible())
        check('worker_failure_never_retries_post',len(posts)==4)
        page.locator('details[aria-label="快速检测结果"] > summary').click()
        current['state']['diagnostics']={'state':'error','summary':'指定模拟器不在线','at':'2026-10-31T17:00:00+08:00','checks':[{'name':'设备连接','state':'error','message':'请核对 device 地址 <img src=x>'}]}
        page.evaluate('AutoStoryUI.refresh()');page.wait_for_timeout(150)
        page.wait_for_function("document.querySelector('#diagnosticSummary').textContent.includes('指定模拟器不在线')")
        check('diagnostic_failure_visible','指定模拟器不在线' in page.locator('#diagnosticSummary').inner_text())
        check('diagnostic_remedy_visible','请核对 device' in page.locator('#diagnosticChecks').inner_text())
        check('diagnostic_html_is_text',page.locator('#diagnosticChecks img').count()==0)
        current['state']['diagnostics']['summary']='连接与截图正常'
        current['state']['diagnostics']['checks']=[{'name':'当前截图','state':'ok','message':'1280×720，截图可读取'}]
        page.evaluate('AutoStoryUI.refresh()');page.wait_for_timeout(150)
        check('diagnostic_success_replaces_old_error','连接与截图正常' in page.locator('#diagnosticSummary').inner_text() and '核对 device' not in page.locator('#diagnosticChecks').inner_text())
        page.locator('#moreMenu summary').click();page.locator('[data-action="diagnose"]').click();page.wait_for_timeout(150)
        check('diagnostic_request_only',len(posts)==5 and posts[-1]['data']['action']=='diagnose')
        page.screenshot(path=str(OUT/'web0921-diagnostics.png'))
        current['state']['settings']['max_battles_per_run']=None
        current['state']['settings']['max_sanity_per_run']=200
        current['state']['campaign']={'budget':{'sanity':146,'limit':None,'count':3},'active_group':'8'}
        page.evaluate('AutoStoryUI.refresh()');page.wait_for_timeout(150)
        check('unlimited_setting_is_explicit','不限次数' in page.locator('#budgetSettings').inner_text())
        check('unlimited_campaign_keeps_used_count','3 次 · 不限次数' in page.locator('#budgetCampaign').inner_text())
        check('sanity_not_reset','146' in page.locator('#budgetCampaign').inner_text() and '200' in page.locator('#budgetSettings').inner_text())
        current['state']['campaign']['budget']['count']=7
        page.evaluate('AutoStoryUI.refresh()');page.wait_for_timeout(150)
        check('above_three_renders_cleanly','7 次 · 不限次数' in page.locator('#budgetCampaign').inner_text() and 'NaN' not in page.locator('#budgetCampaign').inner_text())
        page.locator('#navCatalog').click()
        page.locator('#budgetSettings').scroll_into_view_if_needed()
        page.screenshot(path=str(OUT/'web0921-unlimited.png'))
        current['state']['settings']['max_battles_per_run']=3
        current['state']['campaign']['budget'].update(limit=3,count=2)
        page.evaluate('AutoStoryUI.refresh()');page.wait_for_timeout(150)
        check('finite_setting_still_visible','3 次' in page.locator('#budgetSettings').inner_text())
        check('finite_campaign_still_visible','2 / 3 次' in page.locator('#budgetCampaign').inner_text())
        current['state']['settings'].update(max_practice_attempts_per_run=None,max_candidates_per_stage=None)
        current['state']['campaign']['budget'].update(practice_count=13,practice_limit=None)
        page.evaluate('AutoStoryUI.refresh()');page.wait_for_timeout(150)
        check('both_attempt_limits_visible',page.locator('#budgetSettings').inner_text().count('不限次数')==2)
        check('practice_above_old_cap_visible','13 次 · 不限次数' in page.locator('#budgetCampaign').inner_text())
        current['state']['settings'].update(max_practice_attempts_per_run=12,max_candidates_per_stage=3)
        current['state']['campaign']['budget'].update(practice_count=5,practice_limit=12)
        page.evaluate('AutoStoryUI.refresh()');page.wait_for_timeout(150)
        check('finite_practice_still_visible','5 / 12 次' in page.locator('#budgetCampaign').inner_text())
        current['state'].update(running=True,status={'state':'running','run_id':'training-proof','action':'resume-all','chapter':'ALL'},
            workflow={'run_id':'training-proof','phase':'training','message':'正在为 卡缇 补技能至7级，缺料时按需合成','chapter':'8','code':'R8-9','mode':'raid'})
        page.evaluate('AutoStoryUI.refresh()')
        page.wait_for_function("document.querySelector('#workflowHeadline').textContent.includes('补技能与合成')")
        check('training_progress_is_readable','卡缇' in page.locator('#workflowMessage').inner_text() and '按需合成' in page.locator('#workflowMessage').inner_text())
        check('stop_available_during_training',page.locator('#stopBtn').is_enabled())
        target=next(r for r in current['coverage']['rows'] if r['code']=='R8-9' and r['mode']=='raid')
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
        check('no_script_errors',not errors)
        browser.close()
    report={'ok':True,'passed':len(checks),'checks':checks,'layouts':layouts,'errors':errors,'post_test_scope':'All five POST requests intercepted in browser; no backend actions executed.'}
    (ROOT/'runtime/web0921-browser-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'passed':len(checks),'errors':errors},ensure_ascii=False))

if __name__=='__main__':main()
