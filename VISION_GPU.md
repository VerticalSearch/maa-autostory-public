> 2026-10-31 更新：GPU 能力已合入并部署为 0.8.0，主入口与 GPU 入口统一。以下 0.5.1 内容为上一轮试验记录；当前说明见 [STABILITY_080.md](STABILITY_080.md)。

# AutoStory 视觉识别、控制和 GPU 试验版

日期：2026-10-30。适用当前 MAA v6.20.1 核心与 AutoStory，安装文件夹名称 v6.9.5 不代表实际核心版本。

## 使用

桌面新增 **MAA GPU 试验版** 图标；也可双击本目录的 `启动GPU版本.vbs`。入口先确认本机服务归属；有游戏任务运行时拒绝切换，空闲时切换为 `App-0.5.1/MAA自动推图助手.exe` 后打开控制窗口。启动本身不会发起战斗。原桌面图标由并行的界面更新任务维护，本轮未改它。

本轮已完成 GPU 独立 EXE 的自检和隔离服务/worker 验证，未自动切换正式 HTTP 服务。正式后台是否正在使用 GPU 版，以 `/api/health` 的 executable 为准；当前 OCR 的实际 provider 记录在 `runtime/ocr-backend.json`。初始化 GPU 不可用时返回 CPU 并记录原因，不将 provider 回退伪装为 GPU。

默认配置新增 `ocr_backend: directml`、`ocr_device_id: 0`。本机 DXGI 设备 0 是 NVIDIA GeForce RTX 5060 Laptop GPU（约 8 GB）。可改为 `cpu` 或 `directml-detector`。无需修改 MAA DLL 或原资源任务。独立 Python 环境位于 `.venv-gpu`，没有替换系统 Python 的 CPU ONNX Runtime。

## 改进

- OCR 使用 DirectML，检测/方向分类/文字识别三个模型均启用 GPU provider；CPU provider 保留为算子后备。
- 最多 16 项的精确像素 OCR 缓存：ROI 像素、形状和数据类型共同参与摘要；只复用相同像素，结果坐标按本次 ROI 平移，返回独立对象。动画画面通常不会命中整帧缓存。
- 地图寻找目标编号失败时，使用有重叠的局部区域 2 倍放大 OCR，保留精确关卡目录核对。不会把相似字符强行改成目标编号。局部放大坐标与框均映射回原图。
- ADB 点击/滑动/返回后，导航等待连续两次稳定帧比较。同时检查平均变化和变化像素比例，黑屏/平坦加载帧不能通过。超时保存证据并停止；稳定只表示适合观察，不等于识别到了正确页面。
- 模板缺失和无效结果缓存；拒绝空 ROI、全部颜色通道均恒定的模板以及非有限匹配分数。结算默认仍是原单尺度阈值，必须同时核对关卡编号。

## 实测

同一独立环境 ONNX Runtime DirectML 1.24.4、相同 6 张原始截图，每种尺寸先预热，随后每图各测 3 次，未使用 OCR 缓存；指标为每图中位数再取中位数。

| 后端 | OCR 耗时 | 相对 CPU |
|---|---:|---:|
| CPU | 601.5 ms | 1.00× |
| 仅检测 GPU | 417.2 ms | 1.44× |
| 全 OCR GPU | 185.3 ms | 3.25× |

6 张图的筛选后文字序列都与 CPU 一致；没有据此推断所有截图识别精度相同。结果：`runtime/gpu-benchmark-device0.json`；重跑：`.venv-gpu/Scripts/python.exe research/benchmark_gpu.py --device 0 --rounds 3`。

同一张已知 84/84 截图，完整 GPU OCR 约 157.5 ms，精确缓存命中约 3.4 ms。该缓存结果不是动态画面的帧率。11 张历史结算截图的模板判断与保存的原记录一致，JT8-3 演习失败截图仍被拒绝。结果：`runtime/vision-replay.json`。

实机打开 JT8-3 普通详情、确认编号、关闭并返回地图成功；分别在 4 帧/约 2.06 秒和 3 帧/约 1.67 秒后稳定。全过程没有点击开始行动/演习，没有发起战斗。结果：`runtime/vision-live-control.json`。截图采集约 0.5 秒/帧，因此稳定检测会增加等待，不能把 3.25× OCR 加速说成完整推图加速。

4 张地图缩小再放大的小字探索样本没有出现基线漏字，不能据此宣称局部重识别提高了实际召回率。准确率和整轮推图耗时仍需更大真实样本验证。

## 验收和范围

- 最新源码完整回归：162 项通过，`runtime/tests-vision-gpu-final.log`。一次中途测试出现并行 UI 改版的选择事件失败，后续源码已修复并全量复测通过。本轮未修改 UI 文件。
- 冻结包在 PATH 仅保留 Windows System32 时完成已知截图 OCR、DirectML 三模型 provider、MAA v6.20.1 资源加载验证：`runtime/package-gpu-frozen-check.json`。
- 冻结服务的重复启动、隔离 worker plan 和隐藏 UI 加载：`runtime/package-gpu-integration.json`。测试使用空账号夹具，不连接设备。
- GPU 包使用独立源码快照与构建目录：`_tmp/gpu-source-01a18d2c`、`_build-gpu`。快照哈希：`runtime/gpu-source-manifest.json`。修改源码后需要重新打包；正在运行的旧 EXE 不会热更新算法。
- 尚未用新版进行完整演习或正式通关验证；原先演习先行、结算核对、预算和禁止吃药/源石等规则保持。

DeepSeek 会话 `maa-autostory-20261030-vision-01a18d2c` 在受限副本提供候选缓存/模板模块。桥返回码 124，最终候选与已集成版本有差异；Codex 独立检查实际文件，修正彩色模板常量判定，接入 GPU 和控制逻辑并完成以上验证。候选整体仍标记 needs_validation，不把其完成声明当作验收结果。

实现依据：[ONNX Runtime DirectML](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)、[OpenCV 模板匹配](https://docs.opencv.org/4.13.0/de/da9/tutorial_template_matching.html)、[MAA 任务协议](https://docs.maa.plus/zh-cn/protocol/task-schema.html)。DirectML 会话禁用 memory pattern 并使用顺序执行；没有将 MaaFramework 新字段混入此处旧 MAA 任务。
