# 黍桌面图标 v2

2026-10-31。使用内置 imagegen 重绘，旧 48×48 像素 `shu.png` 作为角色参考；生成透明背景高清源图，随后仅进行 Windows ICO 格式与尺寸编码。

- 高清源图：`shu-desktop-v2.png`，1254×1254。
- Windows 图标：`shu-desktop-v2.ico`，含 16、20、24、32、40、48、64、96、128、256 像素。
- 原桌面快捷方式已使用新版图标。应用目标、启动参数和工作目录均保持原值。
- `shu.ico`、旧界面使用的 `shu.png` 和现有网页内嵌头像同步更新。已经打开的窗口在下次加载时使用新资源。
- 界面修改仅包含头像图片数据，其余 HTML 经还原比较完全相同。EXE 哈希保持不变。
- 安装与备份记录：`../runtime/shu-icon-v2-install.json`。

## 完整生成提示词

Use case: style-transfer / app icon redesign. The provided image is the EXISTING Shu (黍, Arknights) desktop icon and the identity reference. Redesign this as ONE polished, high-resolution Windows desktop app icon, square composition, intended to remain recognizable at 32px and 48px. Keep the same adult female anime character identity, ash gray/taupe hair, distinctive golden branched horn/rice decorations, gentle calm face and warm golden eyes. Preserve the character design recognizable in the reference; do not invent a different girl. Art direction: refined clean anime vector-like cel illustration, elegant and warm, crisp deliberate contours, broad readable light and dark shapes, subtly simplified head-and-shoulders portrait, slight friendly smile, hair framing face. The face and distinctive horns should occupy most of the icon, not a tiny full body. Place portrait over a rich deep jade/ink-green rounded-square tile with very subtle tonal depth, restrained warm ivory and muted gold accents. Balanced uncluttered composition; portrait may slightly overlap the top of the tile but all horns must stay inside image canvas with safety padding. True transparent canvas outside the rounded-square silhouette and portrait. NO text, NO MAA lettering, NO lettering of any kind, NO watermark, NO extra symbols, NO gear, NO robot, NO busy background, NO thin decorative frame, NO neon, NO photorealism, NO app mockup, NO multiple variants. Output ONLY the final icon asset at 1024x1024 or higher, centered, crisp antialiased edges and real alpha.
