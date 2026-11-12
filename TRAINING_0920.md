# 0.9.20：按作业补技能、缺料自动合成

2026-10-31 已部署到原桌面黍图标和统一后台。真实验证与当前执行状态分别记录，当前状态以 `runtime/status.json` 为准。

## 已授权的执行范围

- 只处理已有三星、四星、五星干员，按实际候选作业要求补基础技能，最高7级。
- 缺少材料调用当前MAA 6.20.1的原生合成流程，按缺额递归合成。原料或配方不足时不把升级记为成功，换其他候选。
- 不下发干员等级、精英化或技能专精目标。精零最高4级，要求7级时先排除该干员；专精所需精英二也作为硬条件。
- 仅有任务完成回调不算升级成功：确认姓名、精英阶段和当前技能等级后才提交；完成后两次回读实际等级，再写回MAA与本地快照。
- 升级后重新匹配，仍先演习，演习通过才正式作战。正式/演习/单关尝试上限仍为 `null`，理智预算沿用原检查点。

## 实机证据

23:14:51，卡缇（精一55级）技能6→7成功，MAA返回 `OperProgressDetail: Success / Completed / skill_level7`。前后截图均双次读取，MAA与本地干员快照一致。本次库存足够，没有触发材料合成；因此合成分支为已接入、待自然缺料场景实测。

- [实测报告](runtime/training0920-live.json)
- [升级回执](runtime/training/1793459618270837100-char_209_ardign-rank7/receipt.json)
- [升级前](runtime/training/1793459618270837100-char_209_ardign-rank7/before/0.png)
- [升级后](runtime/training/1793459618270837100-char_209_ardign-rank7/after/0.png)
- [MAA原生任务事件](runtime/training/1793459618270837100-char_209_ardign-rank7/events.json)

同时完成上一轮R8-9 #60077编队失败的对账：真实回调中混入 `ScreencapCost` 连接信息，使旧版严格校验拒绝恢复。现在仅允许这一无任务标识的耗时统计，断线和任何战斗开始证据仍不能被过滤。冻结版已实机从选人页恢复到R8-9突袭未通关详情，写回失败记录，保留125理智预算、4次正式及14次累计尝试。

[编队恢复证据](runtime/campaign-unlimited-20261031-205049/R8-9-60077-formation-second/reconciled.json)

23:15:13通过本机API的 `resume-all` 续跑原任务。此版本验证没有宣称新增关卡通关，后续结果以结算和地图双核验记录为准。

## 验证与包

- 471项源码检查通过；365项从最终EXE读取字节码的检查通过；63项隔离浏览器检查通过，包括补技能状态与停止按钮。
- 验证了范围约束、精英阶段、缺料不能伪造成功、识别失败不升级、重复尝试保护、MAA快照同步、升级后仍演习及截图统计回调恢复。
- [包清单](runtime/package-training0920-manifest.json)、[部署记录](runtime/deployment-training0920.json)、[冻结检查](runtime/training0920-frozen-check.json)、[浏览器检查](runtime/web0920-browser-audit.json)。
- [部署后的状态快照](runtime/training0920-state.json)。不关闭既有控制窗口，不操作Windows桌面鼠标键盘，不改其他线程负责的 `release/`。

原生能力核对自[MAA v6.20.1 OperProgress实现](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/v6.20.1/src/MaaCore/Task/OperProgress/OperProgressProcessTask.cpp)。仅传 `name` 和 `skill_level`；合成由该原生任务在检测到缺料后触发，不额外提交精英化/专精目标。源文件与哈希保存在 `research/training0920/sources.json`。
