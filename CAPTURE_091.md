# 0.9.1 截图加速与停止响应

2026-10-31。基于已部署的 0.9.0 界面与 GPU 运行库，修改截图、ADB 等待、画面超时和原子文件写入。保留 0.9.0 桌面入口、WebView、GPU 模型、候选选择、演习先行、预算和通关判定。

## 实现

- 本机模拟器连接默认使用原始像素截图，远程/USB 连接默认 PNG；`capture_transport` 可配置 `auto`、`raw`、`png`，现有设置不必修改。
- 生产路径固定解析现代 16 字节头，验证尺寸、像素格式、sRGB 色彩空间和完整长度，再转成 BGR。拒绝猜测头长度，避免截断 4 字节后整幅像素错位。格式不适配时丢弃该数据，重新截图并在本次连接中保持 PNG；连接异常和停止不走回退重试。
- 像素头与逐行无 stride 填充的布局核对了 [AOSP screencap 源码](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/cmds/screencap/screencap.cpp)。本机实际头为 `[1280,720,1,1]`，长度 `16+1280*720*4`。非 sRGB 和旧版头使用 PNG，不声称覆盖所有 Android 输出格式。
- ADB 客户端每 0.1 秒检查停止，采用命令总超时；异常时只终止并回收自己启动的客户端。不能撤回已经送达 Android 的输入，但不继续发后续输入。用户停止独立传播，不再错误归类为设备断连。
- 在发送输入之前撤销旧画面的稳定标记；稳定截图不得在超过截止时间后获得确认。保留原连续两次稳定比较和操作后关卡/模式确认。
- 等待间隔也检查后台写入的 STOP 文件，而不只检查当前 Python 进程内的事件。
- JSON 写入使用每个写入者独立的临时文件，再原子替换。Windows 短暂文件共享冲突最多重试 5 次、总等待 0.2 秒；序列化/替换失败保留原记录。该修改不替代已有任务锁或业务读改写锁。

## 验收证据

- `runtime/tests-capture091.log`：源码全测 316 项通过。
- `runtime/tests-frozen091.log`：最终 EXE 的实际冻结模块 203 项通过，包括格式损坏、停止/超时、迟到帧、重复输入防护和并发写入。
- `runtime/capture091-benchmark.json`：同一地图交替采集各 6 帧，PNG 截图中位约 453 ms，raw 约 221 ms。只读截图，无游戏输入。
- `runtime/capture091-readonly-check.json`：两种截图 OCR 文本序列一致，7 个已知地图关卡均命中；实际 STOP 文件中断无害测试子进程，记录响应耗时。此测试不故意挂死模拟器，也不代表真实驱动故障恢复。
- `runtime/capture091-baseline-gpu-live.json` 与 `runtime/capture091-release-gpu-live.json`：旧版/新版相同的 6 个循环、24 次动作，DirectML 同一适配器，在 R8-9/R8-10/R8-11 依次打开详情、切突袭、切普通、关闭；每次重新截图核对状态。最终统计见 `runtime/capture091-summary.json`。
- `runtime/package-capture091-selftest.json`：独立 EXE、PATH 仅 System32，GPU 三个 OCR 模型、已知截图和 MAA DLL/资源自检。
- `runtime/package-capture091-integration.json`：隔离空账号环境，双启动单服务、冻结 worker plan、WebView 隐藏界面检查。
- `runtime/package-capture091-manifest.json`：与 0.9.0 逐模块比较；仅包版本、device、motion、common 和两个新增辅助模块变化。其余冻结代码、桌面入口、运行库和 WebView 文件逐字节保持一致。

这轮没有启动演习或正式作战。导航循环通过不代表所有章节、特殊地图和长时间无人值守均已验证。实测时间是本机当前场景的样本，不是所有设备的速度保证。

最终发布样本：旧版 24 次动作共 91.018 秒，新版 68.358 秒，减少 24.90%；单次动作中位 3.766→2.8355 秒。最终 24/24 通过，6 个循环，全部使用 raw 截图和 DirectML；STOP 文件在 0.312 秒内中断无害子进程，截图 OCR 文本序列一致。统计与包哈希见 `runtime/capture091-summary.json`。

## 部署

已部署 `App-0.9.1/MAA自动推图助手.exe`，统一后台 PID 54768、健康接口 0.9.1；主桌面快捷方式及 `active-release.json` 同步。EXE SHA256：`394b66e436661fb2a60e9f6aa5ddaf2fc5cdb88d8039c2f1d17f528388d786b5`。

`runtime/deployment-capture091.json` 保存部署前后账号记录、状态、设置和黍图标的相同哈希。旧正式版本仍在。清理本轮 `_tmp/capture091-*` 中间构建运行库的命令被自动审批以 `blocked by policy` 拒绝，未提供细分原因，因此保留了这些临时构建文件，不影响当前入口。

## 发现并解决的问题

验收发现用户停止异常继承 OSError，被旧异常包装逻辑误当成设备故障；已增加独立传播及回归。并行诊断复现固定临时文件名冲突，后续并发替换测试又复现 Windows 短暂拒绝访问；分别用唯一临时文件和有界重试修复。

旧 Tk 界面测试在 0.9.0 加入 WebView 后会启动真实测试窗口并阻塞，已在该测试明确隔离 WebView 路径，生产 UI 未修改。最初构建的快照换行格式导致哈希不一致，已改成复制原始文件字节后重新打包。失败测试日志与未部署候选记录保留，可与最终通过记录区分。

## 协作

DeepSeek 独立目录：`C:\Users\ECHO\Documents\Codex\2026-10-11\deepseek\work\maa-capture-20261031-091`。
会话 `maa-capture-20261031-091`，任务 `evt_20261031T043711Z_1e75fe415a`，结果 `evt_20261031T044723Z_78e820ef9c`。桥调用到达 600 秒限制，返回码 124，虽然文件、31 项候选测试日志和最终答复已经落盘，不能将桥运行标成成功。本次交付依据 Codex 对实际文件、接入修改、源码/冻结测试和实机记录的独立验收。候选辅助模块以外的接入、停止异常修复、总超时完成检查、并发文件写入与打包均由 Codex 完成。
