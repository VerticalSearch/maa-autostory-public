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
            if '/api/health' in request.url:return route.fulfill(json={'version':'0.8.0','application':'MAA-AutoStory','pid':0})
            return route.fulfill(json=current)
        context.route('**/api/**',api)
        page.goto(URL)
        page.wait_for_function('window.AutoStoryUI && window.AutoStoryUI.getCoverage()')
        page.wait_for_timeout(80)
        check('initial_load_get_only',not posts)
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
            page.screenshot(path=str(OUT/('web090-'+str(width)+'.png')))
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
        page.screenshot(path=str(OUT/'web090-logs.png'))
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
        check('no_script_errors',not errors)
        browser.close()
    report={'ok':True,'passed':len(checks),'checks':checks,'layouts':layouts,'errors':errors,'post_test_scope':'All three POST requests intercepted in browser; no backend actions executed.'}
    (ROOT/'runtime/web090-browser-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'passed':len(checks),'errors':errors},ensure_ascii=False))

if __name__=='__main__':main()
