#!/usr/bin/env python3
"""把一张方图做成 macOS 应用图标（.icns）。

为什么不是「原图直接转 icns」
---------------------------
macOS 的应用图标不是一张方图，而是**圆角方形（squircle）+ 四周透明留白**：
Apple 的图标网格是 1024×1024 画布上放一块 824×824 的圆角方块。直接把方图
丢进 Dock，会显得比旁边的系统图标大一圈、而且四个直角很突兀。

圆角形状用超椭圆 |x/a|^n + |y/a|^n = 1（n≈5）采样得到，这是对 Apple
连续圆角（continuous corner）的常用逼近，比普通圆角矩形更接近原版观感。
图形一律开启抗锯齿，所以边缘不会有阶梯。

用法：
    python tools/make_icon.py <输入图> [--out-dir assets] [--name PDF工具箱]
                                [--padding 0.805] [--full-bleed]

    --padding    圆角方块边长占画布的比例，默认 0.805（Apple 网格）
    --full-bleed 不留白、不切圆角，整张图铺满画布（想要原图方角效果时用）
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
import sys
import tempfile

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QPainterPath

# iconutil 要求的文件清单：(文件名, 像素边长)
ICONSET = [
    ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
]

MASTER = 1024            # 画布边长
SQUIRCLE_N = 5.0         # 超椭圆指数：越大越接近方角
PATH_SAMPLES = 1440      # 轮廓采样点数（抗锯齿下足够平滑）


def squircle_path(side: float) -> QPainterPath:
    """生成边长 side 的超椭圆路径，中心在原点。"""
    a = side / 2.0
    path = QPainterPath()
    for i in range(PATH_SAMPLES + 1):
        t = 2.0 * math.pi * i / PATH_SAMPLES
        ct, st = math.cos(t), math.sin(t)
        # 超椭圆参数方程；用 |·|^(2/n) 保证符号与幂次都正确
        x = a * math.copysign(abs(ct) ** (2.0 / SQUIRCLE_N), ct)
        y = a * math.copysign(abs(st) ** (2.0 / SQUIRCLE_N), st)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.closeSubpath()
    return path


def build_master(src: QImage, padding: float, full_bleed: bool) -> QImage:
    """把源图合成到 1024×1024 的透明画布上。"""
    canvas = QImage(MASTER, MASTER, QImage.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

    side = MASTER * (1.0 if full_bleed else padding)
    left = (MASTER - side) / 2.0

    if not full_bleed:
        # 路径本身平移到画布中心，避免依赖 QTransform.map(QPainterPath) 的重载
        path = squircle_path(side)
        path.translate(MASTER / 2.0, MASTER / 2.0)
        painter.setClipPath(path)

    # 等比填满目标方块（短边允许溢出，由上面的 clip 裁掉），再居中抵消溢出，
    # 这样非方形源图也不会被挤扁或偏向一侧。
    scaled = src.scaled(int(round(side)), int(round(side)),
                        Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    dx = (scaled.width() - side) / 2.0
    dy = (scaled.height() - side) / 2.0
    painter.drawImage(QPointF(left - dx, left - dy), scaled)

    painter.end()
    return canvas


def write_iconset(master: QImage, iconset_dir: str) -> None:
    os.makedirs(iconset_dir, exist_ok=True)
    for name, size in ICONSET:
        # 小尺寸用平滑缩放；Qt 的 SmoothTransformation 是面积平均，缩小质量好
        out = master.scaled(size, size, Qt.IgnoreAspectRatio,
                            Qt.SmoothTransformation)
        path = os.path.join(iconset_dir, name)
        if not out.save(path, "PNG"):
            raise RuntimeError(f"写入失败：{path}")
    print(f"  iconset：{len(ICONSET)} 个尺寸 → {iconset_dir}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--out-dir", default="assets")
    ap.add_argument("--name", default="PDF工具箱")
    ap.add_argument("--padding", type=float, default=0.805)
    ap.add_argument("--full-bleed", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.source):
        print(f"找不到输入图：{args.source}")
        return 1
    if shutil.which("iconutil") is None:
        print("找不到 iconutil（macOS 自带工具，本机应存在）")
        return 1

    # QImage 需要 QGuiApplication 的插件环境；offscreen 足够，不需要窗口
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication([])          # noqa: F841 - 生命周期需覆盖整个函数

    src = QImage(args.source)
    if src.isNull():
        print(f"无法读取图片：{args.source}")
        return 1
    print(f"源图：{src.width()}×{src.height()}")

    master = build_master(src, args.padding, args.full_bleed)
    os.makedirs(args.out_dir, exist_ok=True)

    master_png = os.path.join(args.out_dir, "icon-1024.png")
    if not master.save(master_png, "PNG"):
        print(f"写入失败：{master_png}")
        return 1
    print(f"  主图：{master_png}")

    tmp = tempfile.mkdtemp(prefix="iconset-")
    try:
        iconset_dir = os.path.join(tmp, f"{args.name}.iconset")
        write_iconset(master, iconset_dir)
        icns = os.path.join(args.out_dir, f"{args.name}.icns")
        subprocess.run(["iconutil", "-c", "icns", iconset_dir,
                        "-o", icns], check=True)
        print(f"  icns：{icns}（{os.path.getsize(icns) / 1024:.0f} KB）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
