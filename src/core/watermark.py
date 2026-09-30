"""PDF 水印：多行文本、任意角度、真实透明度。

关键实现要点（均为实测结论，不是推测）
--------------------------------------
1. **必须用 TextWriter，不能用 page.insert_text**。
   insert_text 不支持透明度，且它的 rotate 参数只接受 90 的整数倍，
   传 -45 会直接抛 `ValueError: bad rotate value`。
   TextWriter.write_text(page, color=..., opacity=..., morph=...) 三者齐全。

2. **任意角度靠 morph 旋转矩阵**，不能靠 rotate 参数。
   morph=(pivot, matrix) 会把整块文字绕 pivot 做刚体旋转 —— 已用
   「基线方向 与 行间位移 的夹角恒为 90°、位移长度恒等于行距」验证过。

3. **字体必须同时给 fontfile 和 fontname**，只给 fontfile 会静默回退到
   Helvetica，中文全部丢失且不报错。详见 core/fonts.py。

4. **坐标系是左上原点、y 向下**（PyMuPDF 约定）。所以行 anchor 取
   `pivot.y + i * line_height` 才能让第二行落在第一行下方；写成减号会导致
   多行文字上下颠倒。

角度约定：与 PyMuPDF 一致，正值逆时针、负值顺时针。默认 -45° 即常见的
「左上到右下」斜向水印（形如 \\）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pymupdf

from .fonts import FontOption, default_font, discover_fonts
from .pdf_io import save_pdf

DEFAULT_ANGLE = -45.0
DEFAULT_FONT_SIZE = 30.0
DEFAULT_OPACITY = 0.18
DEFAULT_LINE_SPACING = 1.4
DEFAULT_COLOR = (0.35, 0.35, 0.40)   # 中性灰，压住彩色文档也不抢眼
DEFAULT_TEXT = "机密文件 请勿外传"


@dataclass
class WatermarkConfig:
    """一次水印的全部参数。"""

    text: str = DEFAULT_TEXT
    angle: float = DEFAULT_ANGLE
    font_size: float = DEFAULT_FONT_SIZE
    opacity: float = DEFAULT_OPACITY
    color: tuple[float, float, float] = DEFAULT_COLOR
    font: FontOption | None = None
    line_spacing: float = DEFAULT_LINE_SPACING
    tile_cols: int = 2          # 横向平铺个数；1 表示只放一个
    tile_rows: int = 3          # 纵向平铺个数
    pages: str = "全部"          # "全部" 或 "1-3,5" 形式

    def lines(self) -> list[str]:
        """按行拆分，忽略空行两侧空白，但保留中间的空行。"""
        raw = self.text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        return [ln for ln in (s.rstrip() for s in raw) if ln.strip()] or [DEFAULT_TEXT]

    def resolved_font(self) -> FontOption:
        if self.font is not None:
            return self.font
        return default_font(discover_fonts())


def parse_page_spec(spec: str, total: int) -> list[int]:
    """把 "1-3,5" 解析成 0 基页号列表；"全部"/空 表示所有页。"""
    spec = (spec or "").strip()
    if not spec or spec in {"全部", "所有", "all", "*"}:
        return list(range(total))

    pages: list[int] = []
    for chunk in spec.replace("，", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            lo, _, hi = chunk.partition("-")
            try:
                start, end = int(lo), int(hi)
            except ValueError:
                continue
            for p in range(min(start, end), max(start, end) + 1):
                if 1 <= p <= total:
                    pages.append(p - 1)
        else:
            try:
                p = int(chunk)
            except ValueError:
                continue
            if 1 <= p <= total:
                pages.append(p - 1)
    # 去重并按顺序返回
    seen, ordered = set(), []
    for p in pages:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return ordered or list(range(total))


def _rotation_matrix(deg: float) -> pymupdf.Matrix:
    r = math.radians(deg)
    return pymupdf.Matrix(math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0, 0)


class Watermarker:
    """在 PDF 页面上绘制多行、可旋转、带透明度的文字水印。"""

    def __init__(self, config: WatermarkConfig):
        self.cfg = config

    # ---------- 布局计算 ----------

    def _line_height(self) -> float:
        """单行占用的高度。"""
        return self.cfg.font_size * self.cfg.line_spacing

    def _tile_pivots(self, page_rect: pymupdf.Rect) -> list[pymupdf.Point]:
        """生成平铺锚点。

        锚点取每格中心；并向外多铺一圈，避免旋转后页面边缘出现空缺。
        1×1 时退化为页面正中 —— 即「单个居中水印」。
        """
        cols = max(1, self.cfg.tile_cols)
        rows = max(1, self.cfg.tile_rows)
        step_x = page_rect.width / cols
        step_y = page_rect.height / rows

        pivots: list[pymupdf.Point] = []
        for j in range(-1, rows + 1):
            for i in range(-1, cols + 1):
                pivots.append(pymupdf.Point((i + 0.5) * step_x,
                                            (j + 0.5) * step_y))
        return pivots

    # ---------- 绘制 ----------

    def _draw_block(self, page: pymupdf.Page, font: pymupdf.Font,
                    lines: list[str], pivot: pymupdf.Point, line_h: float) -> None:
        """在 pivot 处绘制整块多行文字，并绕 pivot 刚体旋转。"""
        writer = pymupdf.TextWriter(page.rect)
        for i, line in enumerate(lines):
            # y 向下为正，故用加法让后续行依次下移
            anchor = pymupdf.Point(pivot.x, pivot.y + i * line_h)
            writer.append(anchor, line, font=font, fontsize=self.cfg.font_size)
        writer.write_text(
            page,
            color=tuple(self.cfg.color),
            opacity=max(0.0, min(1.0, self.cfg.opacity)),
            morph=(pivot, _rotation_matrix(self.cfg.angle)),
        )

    def apply(self, doc: pymupdf.Document, progress=None) -> int:
        """就地给文档加印水印，返回处理页数。"""
        cfg = self.cfg
        lines = cfg.lines()
        if not lines:
            raise ValueError("水印文字不能为空。")

        option = cfg.resolved_font()
        # Font() 同样必须带 fontname，否则中文会静默丢失
        if option.fontfile:
            font = pymupdf.Font(fontfile=option.fontfile, fontname=option.fontname)
        else:
            font = pymupdf.Font(fontname=option.fontname)

        targets = parse_page_spec(cfg.pages, doc.page_count)
        line_h = self._line_height()

        for done, pno in enumerate(targets, start=1):
            page = doc[pno]
            for pivot in self._tile_pivots(page.rect):
                self._draw_block(page, font, lines, pivot, line_h)
            if progress:
                progress(done, len(targets), f"已加印 {done} / {len(targets)} 页")
        return len(targets)

    # ---------- 便捷入口 ----------

    def apply_to_file(self, source: str, output: str, progress=None) -> tuple[str, int]:
        doc = pymupdf.open(source)
        try:
            count = self.apply(doc, progress=progress)
            save_pdf(doc, output)      # 必须走 save_pdf：中文字体需子集化，否则体积暴涨
        finally:
            doc.close()
        return output, count

    def render_preview(self, zoom: float = 1.0, background: str | None = None,
                       page_w: float = 595.0, page_h: float = 842.0) -> tuple[bytes, float, float]:
        """渲染一张预览图，返回 (PNG 字节, 页宽pt, 页高pt)。

        页宽高回传是为了让预览区知道这张图的"纸"有多大 —— 缩放到 100%
        或按比例平移都要用到它，光靠像素尺寸算不出真实比例。

        给了 background（某个 PDF 路径）就以它的首页为底，用户看到的是水印
        压在自己文件上的真实效果；否则退化为带参考线的空白 A4。
        """
        doc = pymupdf.open()
        try:
            if not self._insert_background(doc, background):
                page = doc.new_page(width=page_w, height=page_h)
                # 参考线便于判断倾斜方向与平铺密度
                shape = page.new_shape()
                shape.draw_rect(page.rect)
                mid = page_h / 2
                shape.draw_line(pymupdf.Point(0, mid), pymupdf.Point(page_w, mid))
                shape.finish(color=(0.85, 0.86, 0.9), width=0.7)
                shape.commit()

            self.apply(doc)
            page = doc[0]
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom),
                                     alpha=False)
            return pixmap.tobytes("png"), page.rect.width, page.rect.height
        finally:
            doc.close()

    @staticmethod
    def _insert_background(doc: pymupdf.Document, path: str | None) -> bool:
        """把 path 的首屏复制进预览文档。文件不可读时静默返回 False。"""
        if not path:
            return False
        try:
            src = pymupdf.open(path)
        except Exception:
            return False
        try:
            if not src.page_count:
                return False
            doc.insert_pdf(src, from_page=0, to_page=0)
            return True
        except Exception:
            return False
        finally:
            src.close()
