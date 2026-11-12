from pathlib import Path

root=Path(__file__).resolve().parent
for src,dst in [('build_formation0919.py','build_training0920.py'),
                ('verify_formation0919.py','verify_training0920.py'),
                ('install_formation0919.ps1','install_training0920.ps1'),
                ('audit_web0919.py','audit_web0920.py')]:
    text=(root/src).read_text('utf-8-sig')
    text=text.replace('0.9.19','0.9.20').replace('0.9.18','0.9.19')
    text=text.replace('formation0919','training0920').replace('continuous0918','formation0919')
    text=text.replace('frozen0919','frozen0920').replace('web0919','web0920').replace('(0,9,19,0)','(0,9,20,0)')
    if src.startswith('build_'):
        text=text.replace("CHANGED=('autostory','autostory.controller','autostory.core','autostory.practice_abort','autostory.formation_recovery')",
            "CHANGED=('autostory','autostory.controller','autostory.jobs','autostory.operator_details','autostory.skill_training','autostory.formation_recovery','autostory.workflow')")
        text=text.replace('Preserve the desktop bundle while removing the formal battle cap.',
                          'Preserve the unified bundle while adding authorized basic skill upgrades.')
    if src.startswith('verify_'):
        text=text.replace("'test_training0920'","'test_formation0919','test_training0920'")
        text=text.replace("'test_formation0919','test_formation0919'","'test_continuous0918','test_formation0919'")
    (root/dst).write_text(text,encoding='utf-8')
