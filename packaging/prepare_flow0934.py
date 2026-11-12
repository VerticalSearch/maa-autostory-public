"""Derive the existing release harness for the next verified package."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def emit(name,text):
    target=ROOT/'packaging'/name
    if target.exists():raise RuntimeError(f'Already exists: {target}')
    target.write_text(text,encoding='utf-8')
build=(ROOT/'packaging/build_startup0933.py').read_text('utf-8')
build=build.replace('0.9.33','0.9.34').replace('0.9.32','0.9.33').replace('startup0933','flow0934').replace('startup0932','startup0933').replace('(0,9,33,0)','(0,9,34,0)')
build=build.replace("CHANGED=('autostory','autostory.startup','autostory.startup_screens','autostory.core','autostory.scanning','worker')",
    "CHANGED=('autostory','autostory.completion','autostory.story_zones','autostory.navigation','autostory.controller','autostory.scanning','autostory.workflow','autostory.dashboard','worker')")
build=build.replace('NEW_MODULES=set()',"NEW_MODULES={'autostory.completion','autostory.story_zones'}")
build=build.replace("'snapshot':str(work)","'asset_sha256':{'assets/zone-he-ex.png':digest(ROOT/'assets/zone-he-ex.png')},'snapshot':str(work)")
emit('build_flow0934.py',build)
verify=(ROOT/'packaging/verify_startup0933.py').read_text('utf-8').replace('0.9.33','0.9.34').replace('startup0933','flow0934')
verify=verify.replace('from autostory import startup, startup_screens','from autostory import startup, startup_screens, completion, story_zones, navigation, controller')
verify=verify.replace('(autostory,startup,startup_screens)', '(autostory,startup,startup_screens,completion,story_zones,navigation,controller)')
emit('verify_flow0934.py',verify)
install=(ROOT/'packaging/install_startup0933.ps1').read_text('utf-8-sig').replace('0.9.33','0.9.34').replace('startup0933','flow0934')
install=install.replace("$settings = Get-Content", "foreach ($p in $manifest.asset_sha256.PSObject.Properties) {\n    if ((Get-FileHash -LiteralPath (Join-Path $projectRoot $p.Name)).Hash -ne $p.Value) { throw ('Asset changed after build: '+$p.Name) }\n}\n$settings = Get-Content",1)
emit('install_flow0934.ps1',install)
audit=(ROOT/'packaging/audit_web0922.py').read_text('utf-8').replace('0.9.22','0.9.34').replace('web0922','web0934').replace("=='扫描仍有缺项'","=='部分完成'")
checks='''        for result_state,label in (('partial','部分完成'),('budget_exhausted','本轮预算已用完'),('done','本轮已完成')):
            run_id='completion-'+result_state
            message='普通/突袭范围完成。' if result_state=='done' else '剩余 2 项，进度已保存。'
            current['state'].update(running=False,status={'state':result_state,'run_id':run_id,'action':'run-all','chapter':'ALL','message':message},
                workflow={'run_id':run_id,'phase':'finished' if result_state=='done' else result_state,'message':message},
                campaign={'state':result_state,'active_group':'PV-EX','budget':{'sanity':0,'count':2,'limit':None,'practice_count':2,'practice_limit':None}},
                resume={'available':False,'reason':'本轮预算已用完' if result_state=='budget_exhausted' else 'fixture'})
            page.evaluate('AutoStoryUI.refresh()');page.wait_for_timeout(150)
            check('completion_label_'+result_state,page.locator('#statusText').inner_text()==label)
            check('completion_reason_'+result_state,message in page.locator('#workflowMessage').inner_text())
            check('completion_tone_'+result_state,('is-ok' if result_state=='done' else 'is-warn') in page.locator('#statusDot').get_attribute('class'))
            if result_state!='done':
                check('completion_not_claimed_'+result_state,'已完成' not in page.locator('#campaignBadge').inner_text())
            page.screenshot(path=str(OUT/('web0934-'+result_state+'.png')))
'''
audit=audit.replace("        check('no_script_errors',not errors)",checks+"        check('no_script_errors',not errors)")
emit('audit_web0934.py',audit)
