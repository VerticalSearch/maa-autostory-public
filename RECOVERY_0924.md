# 0.9.24 锚点校验与有界设备恢复

2026-11-01，当前统一安装入口：桌面「MAA 自动推图助手」，保持原壁纸版黍图标。后台仍只用 ADB 与 MaaCore 操作模拟器，只扫描不战斗。

## 这次解决的问题

### 1. 空白锚点让静止检查必然超时（真 bug）

0.9.22 的失败报告 `runtime/screens/unstable/1793466778634553100.json` 显示：地图静止检查选了 4 个"已知关卡标签"框，其中 **2 个 `textured:false`**（框里没有标签像素）。
`motion.map_motion` 对无纹理框只会返回 `stable:false`，因此那一轮静止检查**在数学上不可能通过**：必然烧满 `timeout=3.5 s`，再由 `observe()` 整体重做，才碰巧挑到可用标签。日志里 12 次「首次静止检查未通过」即由此产生，也是 10-31「连续 3 个分区阻塞」链路的起点。

现在：

- `motion.region_texture()` / `motion.textured_regions()` 在**选定锚点时就按原分辨率算灰度标准差**，低于 8 的框直接丢弃。
- `navigation.observe()` 只把**至少 2 个有纹理的框**交给地图门；不足 2 个时先重取一帧重选一次，仍不足则**退回整帧静止检查**并写日志（不再假装地图模式可用）。
- `motion.settle()` 增加 2 次连续空白锚点的**提前失败**（`blank_streak`），不再让一次错误选点吃满 3.5 秒窗口。

### 2. 模拟器瞬断仍会整轮中止

0.9.23 只在截图通道返回精确 `error: closed` 时重试一次。若第二次仍失败、或错误是 `device offline` / `not found`，整轮仍以 error 结束（01:13 那次损失 20 分钟）。

现在 `autostory.scanning` 增加**有界自动恢复**：

- 只对**只读扫描**（`scan-all` / `scan` / `scan-current`）生效，且只认瞬断类错误：`error: closed`、`device offline`、`device not found`、`no devices/emulators found`、`connection reset`、`protocol fault`、连接超时。
- 配置类/授权类错误（`指定模拟器…不在线`、`unauthorized`、`无法唯一确定模拟器`）**立即中止**，不重试。
- 每次等待用 `Device.ready()` 只读轮询同一 `serial` 的 `get-state`，不切换设备、不重启 ADB 服务、不重放点击/返回/滑动。
- 预算：默认 3 次，等待 10/20/30 秒，可在 `settings.json` 用
  `"device_recovery": {"enabled": true, "attempts": 3, "waits": [10, 20, 30]}` 调整或关闭。
- 恢复成功后先 best-effort 确认回到主页，再**继续同一分区**（逐关记录此前已落盘，`plan_groups` 会重算缺项）。
- 超限、用户停止、非瞬断错误都按原语义结束本轮并保留记录。

### 3. 失败横幅直接给出「继续扫描」

扫描类任务以 `state=error` 结束时，顶部错误横幅新增 **「继续扫描」** 按钮（右栏「关闭」左侧），点击即提交 `scan-all`。其它提示（成功/警告/普通错误）不显示该按钮，任务运行中也不显示。

## 验证

- **源码**：`568` 项测试通过（`python -m unittest discover -s tests -v`，系统 Python 3.12.10；日志 `runtime/tests-recovery0924-source.log`）。新增 `tests/test_recovery0924.py` 24 项，覆盖空白锚点、锚点数量不足回退、瞬断/致命错误分类、恢复预算、settings 开关、`Device.ready()` 与横幅接线。
- **冻结字节码**：`222` 项在最终 EXE 的 PYZ 上重跑通过（`runtime/recovery0924-frozen-check.json`、日志 `runtime/tests-frozen0924.log`）。
- **打包隔离检查**：`packaging/verify_bundle.py --app-name App-0.9.24` 通过 —— 并行启动退出码 `[0,0]`、`/api/health` 为 frozen、`plan` 任务 `done`、`--smoke-ui` WebView2 渲染 `ok`（`runtime/package-integration.json`）。
- **横幅实渲染**：`packaging/audit_notice0924.py` 在**隔离沙箱**（离线 serial `127.0.0.1:59999`、独立端口与目录，不接触真机）里制造一次真实设备错误，用 Edge headless 渲染页面并截图：横幅出现且带「继续扫描」按钮。证据 `runtime/notice0924-live-proof.json`、`runtime/notice0924-banner.png`。
- **安装**：`packaging/install_recovery0924.ps1` 在后台空闲时替换服务（App-0.9.23 → App-0.9.24），核验 EXE/页面哈希、账号数据前后一致；记录 `runtime/deployment-recovery0924.json`。
- **续扫**：00:53 起的只扫描任务在 02:12:15 优雅停止（526/1,646，已通关 510、待确认未通关 16），安装后 02:12:34 重启，`/api/health` = `0.9.24`，从第 17 章之后的缺项继续（见 `runtime/recovery0924-state.json`）。

**没有做的事（避免夸大）**：

- 本版没有再次遇到自然发生的 ADB 瞬断，恢复路径的"实机自然故障"未复现；它由源码测试 + 冻结字节码测试覆盖，加上 0.9.23 已做过的故障注入实机验证。
- 改动只涉及 `autostory/{__init__,common,device,motion,navigation,scanning}.py` 与 `web/index.html`；0.9.22 的 72 项浏览器检查**未重跑**，只做了上述一次定向横幅渲染检查。
- 扫描仍未完成：普通/突袭 1,646 项里还有 1,112 项未确认，231 项特殊难度未适配。

## 部署后的观察

- 02:12:34 重启后首分钟：8 条 cleared、0 次「静止检查未通过」、0 次设备恢复、0 次扫描未完整。
- 对照：01:22:41–02:12:15 那 49.5 分钟里只有 43 条记录，却有 **14 次「扫描未完整」**（多为往期活动分区进不去，单次约 2 分钟）与 4 次静止检查回退。**当前最大的时间黑洞已经变成"逐个分区翻「乐章收录」名录"**，下一步应做名录一次索引 + 按名录顺序扫描，而不是继续抠单帧耗时。

## 顺带确认的老问题（本版不修）

第 0 章的突袭详情编号 OCR 一直读不出来：`关卡详情编号未确认：0-5 / 0-6 / 0-8 / 0-9`（首次出现 2026-10-30 20:29，本版 02:14 复现），`1-6` 突袭同理。放大裁剪与 `stage_header.contrast_header` 两路备用都没通过，因此这 5 条突袭记录会长期停在"未确认"，且助手不会把它们谎报成未通关。下一版应针对第 0/1 章突袭标题的字形单独做模板或更强的对比度预处理。

