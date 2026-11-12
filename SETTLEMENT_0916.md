# 0.9.16：演习中断后的结算页恢复

2026-10-31，已更新原桌面入口和后台服务。

本次真实停止发生在21:03:35：R8-9突袭作业88653已收到演习开始回调，随后MAA报告 `BattlePauseCancel / ExceededLimit` 并结束Copilot。助手截到的画面为 `OPERATION R8-9 / 行动结束 / MISSION RESULTS`。旧处理只允许已返回地图的中断，故在这张结算页上停止整轮。没有启动新的正式作战，剩余程序预算146理智、正式计数3、演习计数8/12。

修复只扩展有完整演习中断证据的恢复路径：首帧是确切目标结算页时，交给已有的有界恢复处理。恢复逐帧核查关卡、限制点击次数、等待加载，最后重新读取目标关卡及难度；暂停菜单、错误关卡和无法确认的画面不会盲点。中断作业仍标记为 `game_result=unverified`，禁止以此进入正式作战，随后排除该作业并继续候选。

实机已在21:14:06核对R8-9当前突袭仍未通过，把原来悬置的 `practice_started` 对账为带原事件和地图证据的 `practice_failed / execution_aborted`。全部次数与预算保留，21:14:35已恢复原全目录任务。没有把演习或恢复当作真实通关。

验证与证据：

- 原截图：`runtime/screens/practice-aborts/1793451814223726400-R8-9-raid/returned-map.png`。
- 原截图离线OCR确认结算页、精确R8-9标题，R8-8标题检查为假：`runtime/abort0916-offline-frame.json`。
- 427项源码、321项最终EXE字节码检查通过：`runtime/tests-settlement0916-source.log`、`runtime/settlement0916-frozen-check.json`。新增7项覆盖真实场景、错误关卡、暂停菜单、加载等待/上限、难度状态核验、非演习回调。
- 实机对账：`runtime/campaign-unlimited-20261031-205049/R8-9-execution-abort/reconciled.json`。只回读关卡、记录中断结果，没有启动战斗。
- 安装与快捷方式：`runtime/deployment-settlement0916.json`。保留黍图标、200理智预算、正式次数不限。
- 控制页未变，沿用0.9.15已通过的58项浏览器检查；本次不重复声明新的浏览器测试。

最终EXE SHA-256：`c8ac1e81ef27a3a514e006f3d39f64a97f85f91311eb192389a95c6aa32dcace`。

本次结算页恢复分支已通过截图回放和隔离流程测试；当前实机恢复时已在地图，因此实机验证的是精确关卡回读与旧尝试对账。新分支尚待后续实际中断结算场景再次触发。完整未知画面、首帧持续加载及不完整中断回调属于另行评估范围。
