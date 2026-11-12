# MAA 自动推图助手 0.8.2 统一交付

日期：2026-10-31。用户要求统一版本，包含 GPU 和最新流程，只保留一个日常入口。

## 最终形态

- 正式程序：`App-0.8.2/MAA自动推图助手.exe`。界面和后台都运行这个 EXE，健康接口版本为 0.8.2。
- 桌面只保留“MAA 自动推图助手”，使用黍头像。“MAA GPU 试验版.lnk”移入本次部署备份目录；未删除旧包和历史记录。
- `active-release.json` 是兼容启动脚本的统一版本记录，包含版本、相对目录和 EXE SHA256。
- `启动助手.ps1` 核对记录与实际文件后启动同一个程序；`启动后台助手.vbs` 调用其后台模式。
- 旧 `启动GPU版本.ps1/.vbs` 仅保留为兼容别名，转向统一启动器，不再强制切换旧后台或改写 OCR 设置。
- 默认 GPU OCR 由现有 `settings.json` 管理。DirectML 不可用时保留已验证的 CPU 回退；用户不需要选择两套程序。

## 代码范围

先核对现有源文件与 0.8.0 合并包清单、0.8.1 UI 清单一致，再从完整源码快照使用 DirectML 环境重新打包。统一 `autostory.__version__` 与 Windows EXE 版本为 0.8.2；保留最新 UI、干员快照及详情回写、队伍失败记忆、演习先行、预算、换作业和操作后页面确认。

新包的 23 个项目模块与构建快照逐项核验。与 App-0.8.1 冻结包比较，22 个功能模块和桌面入口的代码对象一致，仅统一包版本标识。未改动游戏战斗策略、预算上限或账户记录。

构建完成后的 12:23 起，工作区 `desktop.py` 出现同期 WebView 界面开发改动。本次保留该源代码，不将未发布改动加入已验证的 0.8.2 包。交付基线明确为 `_tmp/unified082-source`；快照全部文件仍与发布清单一致，EXE 哈希未变。边界核对见 `runtime/unified082-source-boundary.json`。

构建：`packaging/prepare_unified082.py`、`packaging/build_unified082.py`。
快照：`_tmp/unified082-source`。
清单：`runtime/package-unified082-manifest.json`。
EXE SHA256：`492a85c4bf817d00a30a094edca015d52d712ccaffd0543cbf399600e0dddf45`。

## 本次验证

- 标准 Python 3.12 环境完整回归：268 项通过，`runtime/tests-unified082.log`。
- 从最终 EXE 加载真实冻结模块：141 项重点测试通过，`runtime/tests-frozen082.log`、`runtime/unified082-frozen-check.json`。
- 最终独立 EXE 自检：检测、方向分类、识别三个模型均使用 DirectML provider；已知截图 OCR 和 MAA v6.20.1 DLL/资源加载通过，`runtime/package-unified082-selftest.json`。
- 空账号夹具、PATH 仅含 System32：双启动单服务、冻结 worker plan、隐藏新版 UI 均通过，`runtime/package-unified082-integration.json`。
- 首次在 Windows venv 启动器下跑源码全测有两项 PID 等值断言失败。独立探针确认 venv 启动器 PID 与真正 Python 进程不同；标准 Python 下 268 项全过，冻结 EXE worker 集成也通过。保留原失败日志 `runtime/tests-unified082-venv-first.log` 与探针 `runtime/unified082-test-runtime-probe.json`，未放宽生产 PID 校验。
- 部署以与 worker 兼容的文件锁排除并行游戏任务，仅替换已核实身份的后台和本工具控制窗口。已独立验证 .NET 部署锁能排除 Python worker 锁。
- 桌面快捷方式、活动版本记录、后台路径与运行中的 UI 路径均确认指向 App-0.8.2，后台空闲。原账号数据、状态、设置与黍图标在部署前后哈希一致，见 `runtime/deployment-unified082.json`。

本轮未开始演习或正式作战。0.7.0 的 R8-10 演习成功、0.8.0 的导航循环属于各自此前的验证记录；不能作为本轮新增的通关证明。当前每轮 200 理智预算、3 次正式作战、12 次演习、单关 3 份候选的限制保留。

## 备份与独立核查

本次部署备份目录见 `runtime/deployment-unified082.json` 的 `Backup` 字段，包含替换前快捷方式和启动脚本。日常无需打开旧 App 目录。

DeepSeek 独立核查会话：`maa-unify-20261031-single-release`，结果 `evt_20261031T042702Z_b2f5ed1831`。核查未发现本次部署阻塞；Codex 已独立确认核心功能、包哈希和单入口。但该结果有编码降级，最终文字把标准 Python 复测误写成 venv 复测，整份报告继续标记 needs_validation，不作为完成依据。交付以 Codex 实际测试、冻结代码核验、部署与最终检查记录为准；`runtime/unified082-final-check.json` 保存最终检查。
