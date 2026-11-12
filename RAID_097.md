# 0.9.7：突袭编号位置与正式开打前的预留处理

2026-10-31。继续上轮任务时，R8-8 突袭作业 #103433 于 16:00:20 通过演习，16:00:32 在正式作战前的详情确认失败。任务尚未下发到 MAA，游戏没有开始这场正式战斗。

## 修复与核查

突袭页的短编号向左偏移，旧备用 OCR 仍裁剪普通模式的 x=915–1013，漏掉了 R8-8 左半段。现在根据已确认的普通/突袭模式分别使用对应位置；不同时尝试两种裁剪，避免将较长编号截成另一个短编号。真实 R8-8 误识别截图恢复成功，R8-9 负样本拒绝；真实 R8-10 不会被当成 R8-1。

正式预算仍在付费操作前预留。新代码明确标记任务是否已交给 MAA：仅当本地导航/入口核查失败，且尚未调用 Copilot 时释放预留。进入 MAA 后的异常或缺失回调继续保留预留，防止把实际已消耗的次数退回。演习已用次数不会退回。

这次 0.9.6 遗留预留经过独立核查：异常栈停在 `nav.find`，演习成功到退出期间无 Copilot 事件，最后一组 Copilot 事件仍为 16:00:10 的演习任务。保存原账本和完整核查记录后，释放未使用的 18 理智、1 次正式预留；账本由 164/2 次恢复为 **182/1 次**，演习仍为 **2 次**，R8-10 已通关状态保留。此操作没有重置本轮预算。

## 验证

- 346 项源码检查通过；233 项最终 EXE 冻结代码检查通过。
- 首次使用 `.venv-gpu` 跑全源码套件时，有两项测试因虚拟环境启动器派生子进程而 PID 断言失败；使用此前一直采用的实际 Python312 解释器复核全套通过。原失败日志保留于 `runtime/tests-raid097-venv-launcher.log`。
- 16:09:41–16:10:11，MuMu 连续三次定位 R8-8 突袭，均确认未通关并返回地图，未开始战斗。
- 统一桌面/后台更新为 0.9.7，黍图标保留。正式任务从原检查点继续，重新刷新干员并执行演习。后续通关结果应以账本和结算证据为准。

## 证据

- `runtime/campaign-continuation-20261031/header-probe.json`、`R8-8-raid-header-failure.png`。
- 同目录 `reservation-release.json`、`preflight-trace.txt`、`campaign-before-reservation-release.json`、`campaign-after-reservation-release.json`。
- `research/reconcile_raid097_reservation.py`：仅绑定这一次旧故障的核查与更正脚本。
- `runtime/raid097-live.json`、`runtime/tests-raid097-source.log`、`runtime/tests-frozen097.log`。
- `runtime/package-raid097-manifest.json`、`runtime/deployment-raid097.json`、`runtime/raid097-resume-request.json`。

EXE SHA-256：`329eb669e056abbfc2f775126e707e7bae1cf35b5ad47236e283ae6891c00484`。

## 后续真实战斗结果

16:23:47，R8-8 突袭作业 #103433 再次通过演习；16:24:15 正式开打，已越过原编号识别故障。16:26:47 实际正式作战失败，失败结算已保存，关卡仍标为未通关。具体失败原因尚未证实；演习通过不能保证正式作战必胜。

同一作业与实际阵容已经记入失败历史，不会通过刷新无关干员重新放行。16:30:38–16:30:58 在确认失败结算后人工监督退出并回读 R8-8 突袭仍未通关，整个预算账本原样保留，再从原轮次续跑。游戏失败结算显示理智返还，程序仍按原正式预留保守计算剩余预算 164；这里不代表实际净消耗了 36 理智。正式次数 2/3，演习次数 3/12。

证据位于 `runtime/campaign-continuation-20261031/formal-failure-recovery/` 和 `runtime/screens/settlements/1793435206-R8-8-raid-battle/`。随后发生的作业网站断连由 0.9.8 修复。
