"""Prepare a reversible presentation-only refinement of the current UI."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '_tmp/ui-polish-20261031'
OUT.mkdir(parents=True, exist_ok=True)
source = (ROOT / 'web/index.html').read_text('utf-8')
(OUT / 'before.html').write_text(source, encoding='utf-8')
html = source
html = html.replace('min-width:1041px', 'min-width:1101px').replace('min-width: 1041px', 'min-width: 1101px')
for old, new in [
    ('查看账号进度、筛选作业并开始推图', '选择章节，自动匹配作业，先演习再正式作战'),
    ('>账号已确认</span>', '>已识别的关卡进度</span>'),
    ('>干员快照</span>', '>已记录干员</span>'),
    ('>本分区推图</span>', '>开始本章推图</span>'),
    ('>扫描分区</span>', '>识别本章进度</span>'),
    ('>更多操作</span>', '>辅助操作</span>'),
    ('“更多操作”', '“辅助操作”'),
    ('>作战章节</label>', '>选择作战章节</label>'),
    ('<span class="field-hint">仅影响本分区操作</span>', '<span class="field-hint">推图与进度识别将使用此章节</span>'),
    ("scan: '扫描分区'", "scan: '识别本章进度'"),
    ("run: '本分区推图'", "run: '开始本章推图'"),
]:
    html = html.replace(old, new)

# Low-frequency operations retain their existing event bindings and API values.
for action in ('plan', 'refresh'):
    start = html.index('          <button type="button" class="btn btn-secondary" data-action="'+action+'"')
    end = html.index('</button>', start) + len('</button>')
    button = html[start:end].strip().replace('class="btn btn-secondary" ', '')
    html = html[:start] + html[end:]
    label = '匹配适合当前干员的自动战斗脚本' if action == 'plan' else '从游戏重新识别干员与养成信息'
    button = button.replace('</span></button>', '</span><small>'+label+'</small></button>')
    html = html.replace('<div class="menu-panel">', '<div class="menu-panel">\n'+button, 1)

# Keep the live task and selected stage visible; diagnostics can be expanded on demand.
start = html.index('            <section class="rail-section" aria-label="快速检测结果">')
end = html.index('</section>', start)+len('</section>')
block = html[start:end].replace('<section ', '<details ', 1).replace('</section>', '</details>')
block = block.replace('<div class="section-label">最近一次快速检测</div>', '<summary class="section-label">连接与环境检测</summary>')
html = html[:start]+html[end:]
html = html.replace('            <section class="rail-section" aria-label="预算">', block+'\n\n            <section class="rail-section" aria-label="预算">', 1)
html = html.replace('<div class="section-label">预算</div>', '<div class="section-label">本次运行限制</div>')
html = html.replace('            <p class="fine-note">关闭窗口后', '            <p class="fine-note">全目录会依次处理多个章节。</p>\n            <p class="fine-note">关闭窗口后')
help_html = '''<details class="usage-help" id="usageHelp">
          <summary>使用说明</summary>
          <div class="usage-content">
            <strong>从一个章节开始</strong>
            <p>选好章节，点击“开始本章推图”。助手会匹配作业，先演习，通过后再正式作战。</p>
            <dl><dt>识别本章进度</dt><dd>只查看当前章节的通关情况。</dd>
            <dt>作业是什么？</dt><dd>社区提供的自动战斗脚本，包含编队、部署位置和技能时机。</dd>
            <dt>进度如何计数？</dt><dd>普通、突袭等难度分别计数；未识别不代表未通关。</dd></dl>
            <p>运行中可随时点击左下角“停止任务”。关闭窗口后，任务仍在后台继续。</p>
          </div>
        </details>
        '''
html = html.replace('<div class="head-actions">', '<div class="head-actions">\n        '+help_html, 1)
css = '''
/* Task-oriented reading and progressive disclosure. */
.usage-help{position:relative;font-size:12px;color:var(--text-2)}
.usage-help>summary{list-style:none;padding:8px 10px;cursor:pointer;border:1px solid var(--rule);border-radius:3px}
.usage-help>summary::-webkit-details-marker{display:none}
.usage-help[open]>summary{color:var(--accent);border-color:#596044}
.usage-content{position:absolute;top:42px;right:0;width:min(340px,65vw);z-index:70;padding:18px;background:#1b2023;border:1px solid #424a4c;box-shadow:0 16px 36px #0008;line-height:1.75;color:var(--text-2);max-height:65vh;overflow:auto}
.usage-content strong{font-size:15px;color:var(--text)}
.usage-content p{margin:10px 0 0}.usage-content dl{margin:14px 0;padding:12px 0;border-block:1px solid var(--rule)}
.usage-content dt{color:var(--text);margin-top:8px}.usage-content dt:first-child{margin-top:0}.usage-content dd{margin:2px 0 0}
.menu-panel{width:278px}.menu-panel button{height:auto;min-height:38px;flex-wrap:wrap;padding:10px;gap:5px 8px}
.menu-panel small{flex-basis:100%;padding-left:24px;color:var(--text-3);font-size:11px;font-weight:400}
.rail-section>summary.section-label{margin:0;cursor:pointer;list-style:none;display:flex;align-items:center;justify-content:space-between;padding:2px 0}
.rail-section>summary::after{content:'+';font-size:16px;color:var(--text-3)}
.rail-section[open]>summary::after{content:'−'}
.chapter-field{align-items:flex-start;gap:4px;flex-direction:column}
.chapter-field .field-hint{padding-left:0;font-size:11px;color:var(--text-2);font-weight:400}
@media(max-width:820px){.usage-help>summary{padding:7px}.head-actions{gap:4px}.page-title h1{font-size:22px}}
'''
candidate = Path(r'C:\Users\ECHO\Documents\Codex\2026-10-11\deepseek\work\autostory-polish-20261031-b\polish.css')
if candidate.exists():
    css = candidate.read_text('utf-8-sig') + '\n' + css
css += '''
/* Preserve readable Chinese type at the desktop's logical pixel scale. */
.stage-code{font-size:14px}.stage-name{font-size:12px}
.cell-muted,.cell-mode,.cell-reason{font-size:13px}
.badge{font-size:11px}.stage-table thead th{font-size:12px;letter-spacing:0;color:var(--text-2)}
.summary-label,.summary-meta,.section-label{font-size:12px;letter-spacing:0}
.page-title p{color:var(--text-2)}.brand-sub{font-size:10px}.nav-item{font-size:13px}
.stage-name,.empty-note{color:var(--text-2)}
@media(min-width:821px){.chapter-field .field-hint{display:block}}
'''
html = html.replace('</style>', css+'\n</style>', 1)
(OUT/'index.html').write_text(html, encoding='utf-8')
(OUT/'task.json').write_text(json.dumps({'conversation_id':'autostory-polish-20261031-b','source_sha256':hashlib.sha256(source.encode()).hexdigest(),'phase':'candidate'},indent=2),encoding='utf-8')
print(str(OUT/'index.html'))
