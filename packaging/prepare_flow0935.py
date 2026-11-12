"""Derive bounded release tooling from the installed 0.9.34 baseline."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def save(name,text):
    path=ROOT/'packaging'/name
    assert not path.exists(),name
    path.write_text(text,encoding='utf-8')
for name in ('build_flow0934.py','verify_flow0934.py','validate_flow0934.py','install_flow0934.ps1','resume_flow0934.py','probe_flow0934.py'):
    text=(ROOT/'packaging'/name).read_text('utf-8-sig')
    text=text.replace('0.9.34','0.9.35').replace('0.9.33','0.9.34').replace('0934','0935').replace('startup0933','flow0934')
    if name=='build_flow0934.py':
        start=text.index('CHANGED=');end=text.index('\ndef source_path',start)
        text=text[:start]+"CHANGED=('autostory','autostory.zone_icons','autostory.siracusa','autostory.navigation','autostory.story_zones','autostory.home_observation','autostory.stage_header')\nNEW_MODULES={'autostory.zone_icons','autostory.siracusa'}\n"+text[end:]
        text=text.replace('(0,9,34,0)','(0,9,35,0)')
        text=text.replace("{'assets/zone-he-ex.png':digest(ROOT/'assets/zone-he-ex.png')}",
            "{str(path.relative_to(ROOT)):digest(path) for path in (ROOT/'assets').glob('zone-*.png')}")
    if name=='verify_flow0934.py':
        text=text.replace('from autostory import startup, startup_screens, completion, story_zones, navigation, controller',
            'from autostory import startup, startup_screens, completion, story_zones, navigation, controller, siracusa, zone_icons, home_observation, stage_header')
        text=text.replace('completion,story_zones,navigation,controller):','completion,story_zones,navigation,controller,siracusa,zone_icons,home_observation,stage_header):')
    if name=='probe_flow0934.py':
        text=text.replace('PV-EX,PV-S,HE-EX','NL-EX,NL-S,MN-EX')
    save(name.replace('0934','0935'),text)
(ROOT/'autostory/__init__.py').write_text('"""Background-only Arknights story controller."""\n__version__ = "0.9.35"\n',encoding='utf-8')
print('Prepared release scripts for 0.9.35; no package installed')
