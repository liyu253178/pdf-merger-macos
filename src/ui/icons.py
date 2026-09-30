"""Ribbon 与界面用到的内联 SVG 图标。

刻意不用图片资源文件：一是省体积，二是免去打包时配置 data_files，
三是矢量在任意分辨率下都清晰。所有图形都是手工绘制的极简线条，与
Ribbon 的轻量风格一致。
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_STROKE = "#5a6373"      # 主线条
_ACCENT = "#2563eb"      # 强调色
_DANGER = "#d9534f"      # 删除类语义

_VIEWBOX = 'viewBox="0 0 24 24"'

_BODIES: dict[str, str] = {
    # 打开文件夹 —— 添加文件
    "open": (
        f'<path d="M3 7.6A1.6 1.6 0 0 1 4.6 6h3.3l1.8 2.2h8.7A1.6 1.6 0 0 1 20 9.8v7.6'
        f'a1.6 1.6 0 0 1-1.6 1.6H4.6A1.6 1.6 0 0 1 3 17.4z" fill="none" '
        f'stroke="{_STROKE}" stroke-width="1.6" stroke-linejoin="round"/>'
        f'<path d="M3 10.2h17v3H3z" fill="{_ACCENT}" opacity="0.16"/>'
    ),
    # 文档 + 加号 —— 添加 PDF / 图片
    "add": (
        f'<path d="M6.2 3.6h6.6l5 5v11.8H6.2z" fill="none" stroke="{_STROKE}" '
        f'stroke-width="1.5" stroke-linejoin="round"/>'
        f'<path d="M12.8 3.6v5h5" fill="none" stroke="{_STROKE}" stroke-width="1.5"/>'
        f'<path d="M14.6 12.2v5.2M12 14.8h5.2" stroke="{_ACCENT}" stroke-width="2" '
        f'stroke-linecap="round"/>'
    ),
    # 文档 + 减号 —— 移除
    "remove": (
        f'<path d="M6.2 3.6h6.6l5 5v11.8H6.2z" fill="none" stroke="{_STROKE}" '
        f'stroke-width="1.5" stroke-linejoin="round"/>'
        f'<path d="M12.8 3.6v5h5" fill="none" stroke="{_STROKE}" stroke-width="1.5"/>'
        f'<path d="M12 14.8h5.2" stroke="{_DANGER}" stroke-width="2" stroke-linecap="round"/>'
    ),
    # 垃圾桶 —— 清空
    "clear": (
        f'<path d="M4.6 7h14.8M9.6 7V5.3a1.3 1.3 0 0 1 1.3-1.3h2.2a1.3 1.3 0 0 1 1.3 1.3V7" '
        f'fill="none" stroke="{_STROKE}" stroke-width="1.5" stroke-linecap="round"/>'
        f'<path d="M6.4 7l.9 12.2A1.5 1.5 0 0 0 8.8 20.5h6.4a1.5 1.5 0 0 0 1.5-1.3L17.6 7" '
        f'fill="none" stroke="{_STROKE}" stroke-width="1.5" stroke-linejoin="round"/>'
        f'<path d="M10.4 11v6M13.6 11v6" stroke="{_STROKE}" stroke-width="1.3" '
        f'stroke-linecap="round"/>'
    ),
    # 导出 —— 主操作
    "export": (
        f'<path d="M12 3.8v9.4" stroke="{_STROKE}" stroke-width="1.7" stroke-linecap="round"/>'
        f'<path d="M8.4 9.6L12 13.2l3.6-3.6" fill="none" stroke="{_STROKE}" '
        f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<path d="M5 16.2v3.4a.8.8 0 0 0 .8.8h12.4a.8.8 0 0 0 .8-.8v-3.4" fill="none" '
        f'stroke="{_STROKE}" stroke-width="1.6" stroke-linecap="round"/>'
    ),
    # N-up 拼版 —— 合并
    "layout": (
        f'<rect x="3.2" y="4.2" width="17.6" height="15.6" rx="2" fill="none" '
        f'stroke="{_STROKE}" stroke-width="1.5"/>'
        f'<path d="M3.2 12h17.6M12 4.2v15.6" stroke="{_ACCENT}" stroke-width="1.4" '
        f'opacity="0.75"/>'
    ),
    # 三行文本 —— 多行水印
    "lines": (
        f'<path d="M4.6 6.6h14.8M4.6 11h14.8M4.6 15.4h9" stroke="{_STROKE}" '
        f'stroke-width="1.7" stroke-linecap="round"/>'
    ),
    # 旋转 —— 角度
    "rotate": (
        f'<path d="M18.4 12a6.4 6.4 0 1 1-1.9-4.6" fill="none" stroke="{_STROKE}" '
        f'stroke-width="1.6" stroke-linecap="round"/>'
        f'<path d="M12 5.2v4.1l3-2z" fill="{_STROKE}"/>'
        f'<circle cx="12" cy="12" r="2.1" fill="none" stroke="{_STROKE}" stroke-width="1.4"/>'
    ),
    # 半透明圆 —— 不透明度
    "opacity": (
        f'<circle cx="12" cy="12" r="7.4" fill="none" stroke="{_STROKE}" stroke-width="1.5"/>'
        f'<path d="M12 4.6a7.4 7.4 0 0 1 0 14.8z" fill="{_STROKE}" opacity="0.32"/>'
    ),
    # 调色板 —— 颜色
    "color": (
        f'<path d="M12 4.2a7.8 7.8 0 0 0 0 15.6c1.1 0 1.6-.7 1.6-1.5 0-1.3-1.1-1.5-1.1-2.5 '
        f'0-.8.7-1.3 1.5-1.3h1.6a4.2 4.2 0 0 0 4.2-4.2c0-3.1-3.5-6.1-7.8-6.1z" fill="none" '
        f'stroke="{_STROKE}" stroke-width="1.5" stroke-linejoin="round"/>'
        f'<circle cx="8.3" cy="10.3" r="1.15" fill="{_STROKE}"/>'
        f'<circle cx="11.1" cy="7.5" r="1.15" fill="{_STROKE}"/>'
        f'<circle cx="14.8" cy="8.4" r="1.15" fill="{_STROKE}"/>'
    ),
    # 水印 —— 主操作
    "stamp": (
        f'<path d="M12 4.4c3.6 4.1 5.6 6.9 5.6 9.5a5.6 5.6 0 0 1-11.2 0'
        f'c0-2.6 2-5.4 5.6-9.5z" fill="none" stroke="{_STROKE}" stroke-width="1.6" '
        f'stroke-linejoin="round"/>'
        f'<path d="M8.8 14.4h6.4" stroke="{_STROKE}" stroke-width="1.5" stroke-linecap="round"/>'
    ),
    # 盾牌 —— 保密输出
    "shield": (
        f'<path d="M12 3.4l7 2.6v5.3c0 4.3-2.9 7.8-7 9.3-4.1-1.5-7-5-7-9.3V6z" fill="none" '
        f'stroke="{_STROKE}" stroke-width="1.5" stroke-linejoin="round"/>'
        f'<path d="M9.1 12.1l2.1 2.1 4-4.3" fill="none" stroke="{_ACCENT}" '
        f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    # 页面 —— 目标 PDF
    "page": (
        f'<path d="M6.2 3.4h7.4l4.8 4.8v12.4H6.2z" fill="none" stroke="{_STROKE}" '
        f'stroke-width="1.5" stroke-linejoin="round"/>'
        f'<path d="M13.6 3.4v4.8h4.8" fill="none" stroke="{_STROKE}" stroke-width="1.5"/>'
        f'<path d="M9 12.6h6M9 16h4" stroke="{_STROKE}" stroke-width="1.3" '
        f'stroke-linecap="round"/>'
    ),
    # 打印机 —— 打印当前预览
    "print": (
        f'<path d="M7 9.4V4.6h10v4.8" fill="none" stroke="{_STROKE}" '
        f'stroke-width="1.5" stroke-linejoin="round"/>'
        f'<path d="M5 9.4h14a1.4 1.4 0 0 1 1.4 1.4v5.2A1.4 1.4 0 0 1 19 17.4H5'
        f'a1.4 1.4 0 0 1-1.4-1.4v-5.2A1.4 1.4 0 0 1 5 9.4z" fill="none" '
        f'stroke="{_STROKE}" stroke-width="1.5" stroke-linejoin="round"/>'
        f'<path d="M7 14.6h10v5.2H7z" fill="none" stroke="{_ACCENT}" '
        f'stroke-width="1.5" stroke-linejoin="round"/>'
        f'<path d="M16.6 12h1.9" stroke="{_ACCENT}" stroke-width="1.7" '
        f'stroke-linecap="round"/>'
    ),
    # 左尖角 —— 上一页
    "prev": (
        f'<path d="M14.6 5.6L8.2 12l6.4 6.4" fill="none" stroke="{_STROKE}" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    # 右尖角 —— 下一页
    "next": (
        f'<path d="M9.4 5.6L15.8 12l-6.4 6.4" fill="none" stroke="{_STROKE}" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    # 放大镜加号 —— 放大预览
    "zoom_in": (
        f'<circle cx="10.4" cy="10.4" r="6.1" fill="none" stroke="{_STROKE}" '
        f'stroke-width="1.7"/>'
        f'<path d="M7.5 10.4h5.8M10.4 7.5v5.8" stroke="{_STROKE}" stroke-width="1.7" '
        f'stroke-linecap="round"/>'
        f'<path d="M15 15l4.6 4.6" stroke="{_STROKE}" stroke-width="1.9" '
        f'stroke-linecap="round"/>'
    ),
    # 放大镜减号 —— 缩小预览
    "zoom_out": (
        f'<circle cx="10.4" cy="10.4" r="6.1" fill="none" stroke="{_STROKE}" '
        f'stroke-width="1.7"/>'
        f'<path d="M7.5 10.4h5.8" stroke="{_STROKE}" stroke-width="1.7" '
        f'stroke-linecap="round"/>'
        f'<path d="M15 15l4.6 4.6" stroke="{_STROKE}" stroke-width="1.9" '
        f'stroke-linecap="round"/>'
    ),
}

_RENDER_SIZE = 96
_cache: dict[str, QIcon] = {}


def svg_icon(name: str) -> QIcon:
    """把内联 SVG 渲染成图标。结果按名字缓存，避免重复渲染。"""
    cached = _cache.get(name)
    if cached is not None:
        return cached

    body = _BODIES.get(name, "")
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" {_VIEWBOX} '
        f'width="{_RENDER_SIZE}" height="{_RENDER_SIZE}">{body}</svg>'
    )
    pixmap = QPixmap(_RENDER_SIZE, _RENDER_SIZE)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        renderer.render(painter, QRectF(0, 0, _RENDER_SIZE, _RENDER_SIZE))
    finally:
        painter.end()

    icon = QIcon(pixmap)
    _cache[name] = icon
    return icon
