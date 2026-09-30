"""保密输出：把 PDF 栅格化成「只剩图片」的版本。

固定三步流程
------------
1. **先加水印** —— 按当前参数给源文件加印，写成一份临时 PDF（源文件不动）。
2. **逐页转图片** —— 把临时 PDF 按页拆分，每一页单独渲染成一张图片。
3. **再合回 PDF** —— 按原页面顺序把图片装进一个新 PDF，页面尺寸与原件一致。

成品里只剩像素：没有文本层、没有注释对象、没有表单域、没有超链接与书签、
元信息也被清空，内容既不可检索也不可复制。

为什么需要它
------------
加水印只是视觉标记 —— 原文仍在文本层里，别人照样能选中复制、提取表单
数据、甚至削掉水印那层。栅格化才是真正把内容"焊死"在像素上。代价不可逆：
文件变大、文字不再可搜。所以它是**独立的显式操作**，不混进普通导出。

画质为什么按档位给，而不是一个 dpi 数字
----------------------------------------
拼版后的发票类文档字号可能只有 3pt 出头，200 dpi 下不足 10 像素高，汉字
笔画会糊在一起 —— 而同一份文件在 300 dpi 下是 14 像素，清晰可读。可见
「够不够」取决于内容，不是取决于一个统一的数字。所以这里给的是带语义的
档位（标准 / 高清 / 无损 / 超清），而不是让用户去猜 dpi。

无损档用 PNG 直出：扫描件、线条图、印章这类内容重复编码一次就掉一层，
体积换画质；普通文字文档用 JPG 足够，且体积只有 PNG 的三分之一。

两个刻意的取舍
--------------
* 页面尺寸用**可见尺寸（page.rect，含 /Rotate）**重建，保证成品的页数、
  顺序、页面尺寸与原件完全一致。旋转页的 /Rotate 会被烘焙进像素，成品
  不保留旋转标记但可见长宽比逐像素相同（见 tests/test_e2e.py）。
* 页面上的可见注释由 PyMuPDF 一并画进像素，视觉与原件一致；这指的是
  「PDF 结构里不再有注释对象」，而不是把批注内容抹掉。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass

import pymupdf

from .pdf_io import open_pdf, save_pdf
from .watermark import WatermarkConfig, Watermarker

# JPEG 质量：92 是"看不出损失"的常见起点。再往上体积增长快而肉眼无感，
# 往下（88 及以下）细笔画汉字边缘会出振铃，正是"效果不好"的来源。
JPEG_QUALITY = 92

# 「无损」档的单页兜底阈值。
# 线条图 / 文字 / 印章这类内容用 PNG 又小又准（实测一页发票拼版 715 KB，
# 比同分辨率的 JPEG 还小）；但照片、彩色扫描件走 PNG 会到十几 MB 一页。
# 所以某一页的 PNG 超过这个大小就说明它是"照片型"，单独退回 JPEG ——
# 无损档不至于把一份扫描 PDF 变成几百 MB。
LOSSLESS_MAX_BYTES = 4_000_000


@dataclass(frozen=True)
class Quality:
    """一档输出画质。`lossless=True` 走 PNG 直出，忽略 jpeg_quality。"""

    key: str
    label: str
    dpi: int
    lossless: bool = False
    jpeg_quality: int = JPEG_QUALITY

    def describe(self) -> str:
        return f"{self.label}（{self.dpi} dpi，{'无损 PNG' if self.lossless else 'JPEG'}）"


QUALITIES: tuple[Quality, ...] = (
    Quality("standard", "标准", 200),
    Quality("high", "高清", 300),
    Quality("lossless", "无损", 300, lossless=True),
    Quality("ultra", "超清", 400),
)
# 默认走无损：文字与线条是 PNG 的主场，实测比 JPEG 更小也更锐利。
# 照片型页面会被 LOSSLESS_MAX_BYTES 自动挡回 JPEG，不会失控。
DEFAULT_QUALITY = "lossless"

# 兼容旧调用点：对外只暴露 dpi 的地方仍可用
DPI_CHOICES = tuple(q.dpi for q in QUALITIES)
DEFAULT_DPI = 300


def quality_by_key(key: str) -> Quality:
    for q in QUALITIES:
        if q.key == key:
            return q
    return next(q for q in QUALITIES if q.key == DEFAULT_QUALITY)

# 这些字段在成品里一律清空
_CLEARED_METADATA = {
    key: "" for key in (
        "title", "author", "subject", "keywords",
        "creator", "producer", "creationDate", "modDate",
    )
}

Progress = Callable[[int, int, str], None]


def export_secure(source: str, output: str, config: WatermarkConfig,
                  dpi: int = DEFAULT_DPI, progress: Progress | None = None,
                  lossless: bool = False,
                  jpeg_quality: int = JPEG_QUALITY) -> dict:
    """跑完「加水印 → 逐页转图片 → 合回 PDF」，输出到 output。

    原始文件不会被改动，中间产物全部落在临时目录里。返回
    {"pages": 页数, "bytes": 文件大小, "dpi": 出图分辨率,
     "lossless": 是否无损, "image_bytes": 中间图片合计大小}。
    """
    def report(percent: float, text: str) -> None:
        if progress:
            progress(int(percent), 100, text)

    tmpdir = tempfile.mkdtemp(prefix="pdf-secure-")
    try:
        # 第 1 步：加水印 → 临时 PDF（占 0-25%）
        watermarked = os.path.join(tmpdir, "watermarked.pdf")
        report(0, "正在加印水印…")
        doc = pymupdf.open(source)
        try:
            Watermarker(config).apply(
                doc,
                progress=lambda done, total, _t: report(
                    25 * done / max(total, 1), f"正在加印水印 {done}/{total} 页"
                ),
            )
            save_pdf(doc, watermarked)
        finally:
            doc.close()

        # 第 2 步：逐页拆分成图片（占 25-80%）
        report(25, "正在渲染页面…")
        wm_doc = pymupdf.open(watermarked)
        try:
            items, fell_back = _render_pages(
                wm_doc, tmpdir, dpi, lossless=lossless, jpeg_quality=jpeg_quality,
                on_page=lambda done, total: report(
                    25 + 55 * done / max(total, 1), f"正在渲染 {done}/{total} 页"
                ),
            )
        finally:
            wm_doc.close()

        # 第 3 步：按原顺序合回 PDF（占 80-100%）
        report(80, "正在合成图片 PDF…")
        pages = _build_image_pdf(items, output)
        report(100, "完成")
        return {
            "pages": pages,
            "bytes": os.path.getsize(output),
            "dpi": dpi,
            "lossless": lossless,
            "image_bytes": sum(os.path.getsize(p) for p, _w, _h in items),
            "jpeg_pages": fell_back,
        }
    finally:
        # 无论成功、失败还是异常，临时文件都不留在磁盘上
        shutil.rmtree(tmpdir, ignore_errors=True)


def _render_pages(doc: pymupdf.Document, tmpdir: str, dpi: int,
                  lossless: bool = False, jpeg_quality: int = JPEG_QUALITY,
                  on_page=None) -> tuple[list[tuple[str, float, float]], list[int]]:
    """把每一页拆成一张图片，返回 ([(图片路径, 页宽pt, 页高pt)], 退回 JPEG 的页码)。

    宽高取自 page.rect（可见尺寸，含 /Rotate）。get_pixmap() 同样施加旋转，
    因此像素的长宽比与 page.rect 一致，重建页面时不会错位。

    `alpha=False` 是必须的：带 alpha 的位图存 JPG 会被静默丢弃透明通道，
    存 PNG 则白白多出一层用不上的 alpha，体积翻倍。
    """
    zoom = dpi / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)
    suffix = ".png" if lossless else ".jpg"
    items: list[tuple[str, float, float]] = []
    fell_back: list[int] = []
    for index in range(doc.page_count):
        page = doc[index]
        pixmap = page.get_pixmap(matrix=matrix, alpha=False, colorspace=pymupdf.csRGB)
        path = os.path.join(tmpdir, f"page-{index + 1:05d}{suffix}")
        if lossless:
            pixmap.save(path)
            if os.path.getsize(path) > LOSSLESS_MAX_BYTES:
                # 照片/扫描型页面：换成 JPEG，并让扩展名与实际格式一致，
                # 否则 insert_image 会因为后缀与内容不符去猜格式
                os.remove(path)
                path = path[:-4] + ".jpg"
                pixmap.save(path, jpg_quality=jpeg_quality)
                fell_back.append(index + 1)
        else:
            pixmap.save(path, jpg_quality=jpeg_quality)
        items.append((path, page.rect.width, page.rect.height))
        if on_page:
            on_page(index + 1, doc.page_count)
    return items, fell_back


def _build_image_pdf(items: list[tuple[str, float, float]], output: str) -> int:
    """按给定顺序把图片装进新 PDF，每页尺寸与来源页一致。"""
    out = pymupdf.open()
    try:
        for path, width, height in items:
            page = out.new_page(width=width, height=height)
            page.insert_image(page.rect, filename=path)   # 原样嵌入 JPEG，不重编码
        _clear_metadata(out)
        save_pdf(out, output, subset_fonts=False)          # 无文字，无需子集化
        return out.page_count
    finally:
        out.close()


def _clear_metadata(doc: pymupdf.Document) -> None:
    """清空文档元信息（含 XMP）。清不掉不应阻断导出。"""
    try:
        doc.set_metadata(_CLEARED_METADATA)
    except Exception:
        pass
    try:
        doc.del_xml_metadata()
    except Exception:
        pass
