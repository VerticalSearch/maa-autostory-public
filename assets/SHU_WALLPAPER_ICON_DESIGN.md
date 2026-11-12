# 当前壁纸的黍：图标制作记录

2026-10-31。使用 Windows 当前壁纸登记路径读取原画，并保存参考副本 `shu-wallpaper-reference.jpg`。壁纸原文件与系统壁纸设置不作修改。

方式：内置 imagegen，按原壁纸重新构图并清理透明边缘；不是逐像素无损裁切。以原画的回眸表情、面容、金色树影和暖光为主要保留目标。

交付文件：`shu-wallpaper.png`（高清图标）、`shu-wallpaper.ico`（Windows 多尺寸图标）、`shu-wallpaper-preview.png`（256 像素预览）。安装证据为 `../runtime/shu-wallpaper-install.json`。原桌面快捷方式使用新图标，应用程序与启动参数保持原值。

## 主提示词

Use case: precise-object-edit / identity-preserve. Input image is the user's CURRENT WALLPAPER and the authoritative artwork. Create ONE Windows app icon by closely reframing this exact existing Shu portrait, keeping the original artwork's distinctive soft painterly style and the SAME face. This is an original-artwork-preserving crop/composition task. The user loves THIS specific face, not the previous generic anime redraw. Preserve the exact three-quarter backward glance to the viewer, gentle subtle closed-mouth smile, half-lidded gray-blue eyes with their small warm highlights, bangs and their dark indigo tips, the golden-olive hair under warm sunlight, original ear and original horn shapes. Retain the original golden leaves, soft cyan sky and dappled sunlight near her head. DO NOT change to a frontal view, gold eyes, a different smile, a sharper face, chibi, vector line art, 3D, glossy illustration or a new character design. Do not exaggerate or invent antlers, decorative jewelry or hair ornaments. Reframe the existing face and hair as a tight square head-and-upper-neck portrait so the face is prominent and readable at desktop icon size; keep some original hair and visible horn silhouette as space allows. Less shoulder and no chest. A clean rounded-square crop, corner radius approximately 14 percent, with genuine alpha transparency ONLY outside the rounded-square boundary. Inside the tile, keep the warm golden original background and soft original brushwork, not a dark-green replacement. No external frame, no border, no added symbols, no text, no watermark, no multiple panels or icon mockup. Preserve the facial identity and emotional character as faithfully as possible. Deliver one square asset of at least 1024x1024 pixels.

## 透明边缘修整提示词

Precise alpha-edge cleanup ONLY. This image is an already approved app icon composition derived from the user's wallpaper. Preserve ALL interior artwork, the exact face, expression, head position, hair, colors, lighting and crop. Do not redraw, repaint, restyle, reposition or zoom anything. Clean the outer rounded-square silhouette: remove every stray colored or white fragment outside it, especially the ragged white pixels along the lower edge and the right edge. Make the outside completely transparent RGBA alpha=0, with one smooth continuous antialiased rounded rectangle boundary. Keep the existing approximate rounded corner radius, no added border or shadow. Square output at least 1024x1024. ONLY edge alpha cleanup, no other changes, no text.
