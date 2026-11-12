# 0.7.0 逻辑与干员详情识别

日期：2026-10-31。

## 行为变化

- 失败记录改为关联实际选用的干员、技能/模组观测、作业执行内容与 MAA 版本。无关干员升级、招募和快照更新时间不会让同一失败组合重新进入演习。旧版 JT8-3 #70118 的失败证据已保留并转换；取得新的相关练度证据后会重新评估。
- 全目录检查点现在保存每关已尝试次数、该轮单关上限、作业及执行阶段。续跑保留本关额度；正式作战仍需本次演习成功，不能仅凭历史阶段绕过验证。MAA 的开始回调、预算预留、失败和地图核验分别记录；未确认消耗的预留不会自动退款。
- 候选干员可通过 MAA 定位详情页，读取技能等级、专精及当前装备模组。定位参数只含姓名，未提供升级、专精培养等目标；当前仅对已核对语义的 MAA 6.20.1 启用该路径。姓名/英文名、页面标志、精英化标志和两帧结果必须一致，无法确认的字段保持未知。
- 专精识别同时比较图标形状和灰度，防止深色零专精与亮色专三因形状相同而混淆。模组读取仅覆盖详情页当前装备、能明确识别种类与等级的一件，不代表已读取全部已解锁模组。
- 详情缓存有效期 30 分钟，并绑定干员基本属性；过期或基本属性变化后重新识别。确认的字段写回 `runtime/operators.json` 和 MAA `data/OperBoxData.json`。原快照先备份，不能确认的数值不按零填充。
- 全量干员刷新前先回到已确认的主页，再进入干员列表，通过 MAA 识别任务切换职业后返回 ALL，清除上一轮筛选和滚动位置；数量/覆盖异常仍拒绝覆盖旧快照。
- 作业搜索先查前三页；匹配不足时继续扩展，最多 12 页。成功响应按关卡/难度/页缓存 15 分钟。网络失败、搜索达到上限、站点已无下一页分别记录；缓存过期不会被当成最新数据。
- 全目录续跑使用增量地图扫描，复用已确认的普通/突袭通关记录。执行目标仍现场确认关卡与难度。
- 修复主页“终端”按钮被误当成已经到达终端的判断；必须离开主页并识别到终端内容后才启动章节导航。实机已进入 R8-10 并分别确认目标关卡和突袭未通关状态。
- 补齐演习失败提示之后的“行动结束”结算页恢复。只有已确认演习失败、结算关卡编号相符时才点击退出，最多两次，随后重新确认目标关卡与难度；不将失败结算当作通关。实机已从 R8-10 的失败结算恢复到突袭详情。

默认额度继续为每轮 200 理智预算、3 次正式作战、12 次演习尝试、每关 3 份候选。200 是程序预算，不是实时游戏理智；自动读取实际剩余理智/演习券并等待恢复，尚未在此版本实现。

## 验证

- 完整源码回归 233 项通过：`runtime/tests-logic070.log`。
- 直接加载最终 EXE 冻结模块的 85 项测试通过：`runtime/tests-frozen070.log`。
- 隔离安装包的双启动单服务、冻结 worker、隐藏界面加载通过：`runtime/package-integration.json`、`runtime/package070-test.log`。
- 实机定位“黍”并读取：技能等级 7，专精 3/0/3，装备 X 模组三级，已写回 MAA。证据：`runtime/profile070-live-check.json`，对应截图在 `runtime/screens/operator-details/`。
- 实机暴露并修复了详情读取后残留职业筛选的问题：第一次只识别到 14 名而被覆盖率检查拦截，修复后重新识别并写回 316 名。旧快照未被不完整结果覆盖。
- R8-10 突袭作业 #47401 的演习失败，有 `BattleStartExercise` 回调及失败截图，未调用正式作战。首次恢复停在行动结算页的记录保存在 `runtime/practice070-first-loss.json`；修复后的真实恢复验证在 `runtime/recovery070-live-check.json`。
- 最终包在 01:11:50 完成 R8-10 突袭作业 #23177 的演习验收：跳过已失败的 #47401，读取棘刺/桃金娘/泥岩详情，再编队并演习。开始回调为 `BattleStartExercise`，成功结算截图的关卡编号为 R8-10，突袭成功标志模板得分 0.97691；退出后返回地图。证据：`runtime/practice070-live-check.json` 与 `runtime/screens/settlements/1793380300-R8-10-raid-trial/01.png`。未调用正式作战，未将演习记为正式通关。
- 本次验证新增两次演习尝试：一失败、一通过。原整轮检查点现为演习 4/12、正式作战 1/3、剩余理智预算 179/200；R8-10 本关计数 2/3。理智是程序预算，不是游戏实时余额。
- 桌面快捷方式、后台启动脚本和监听 18741 端口的后台服务已更新到 App-0.7.0。升级前后账号进度、检查点、快照、失败记录、状态和设置的文件哈希一致，详见 `runtime/deployment-0.7.0.json`。旧包保留。

## 发布范围与证据

基线 App-0.6.0，更新 `controller`、`attempts`、`core`、`jobs`、`recovery`，加入 `operator_details`、`run_ledger`、`terminal_navigation`。导航只替换冻结基线的 `Navigator.terminal` 方法，其余方法保持基线实现。其余冻结模块与 1,078 个运行时文件保持基线内容。未将工作区同期其他视觉、OCR、导航和 UI 改动混入此包。

最终清单：`runtime/package-logic-070-manifest.json`；EXE SHA256：`7d50a432bc7342207ce45bf12e062e28ca4aff2e3e792800de3854e4eecdec53`。安装记录：`runtime/deployment-0.7.0.json`。构建：`packaging/build_logic070.py`；验证：`packaging/verify_frozen070.py`、`packaging/verify_bundle.py --app-name App-0.7.0`。

迁移记录：`runtime/migration070.json`。迁移前历史与检查点在 `runtime/backups/before-070-*.json`。原 4-10 与 JT8-3 两次演习分别归入单关计数，全局预算未重置。

作业搜索候选由 DeepSeek 会话 `maa-autostory-20261031-search-070` 提供，任务 `evt_20261030T161901Z_b31eb25994`，结果 `evt_20261030T162745Z_770851135e`。Codex 检查候选差异、集成、补充输入及练度判断，独立完成上述验证与交付。

详情定位语义参考 [MAA v6.20.1 OperProgressProcessTask](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/v6.20.1/src/MaaCore/Task/OperProgress/OperProgressProcessTask.cpp)，参数约束参考 [OperProgressTask](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/v6.20.1/src/MaaCore/Task/Interface/OperProgressTask.cpp)。读取到页面、识别正确字段、演习通过与正式通关是不同验证层级。
