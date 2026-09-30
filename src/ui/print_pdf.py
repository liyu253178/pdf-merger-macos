"""打印：把 PDF 页面光栅化后交给系统打印对话框输出。

为什么走「先弹系统对话框、再逐页光栅化」
--------------------------------------
macOS 上 PDF 的原生打印链路是 `PDFKit → CUPS`，而本项目要能在打包产物里
稳定工作，还得让「页码范围 / 份数」这些选项由系统对话框负责。所以这里
只用 QPrinter + QPrintDialog 拿到用户的选择，实际出图由 PyMuPDF 光栅化后
用 QPainter 画上去：

* 打印机、纸张、页码区间、份数 → 全部交给标准打印对话框（份数由 CUPS 处理，
  这里只画一遍，避免被重复乘一遍）；
* 每一页按打印区域的像素尺寸取缩放比，并封顶在 MAX_PRINT_DPI，
  既不会把 A4 拉糊，也不会在 600 dpi 打印机上生成上百 MB 的位图；
* 页面按原比例居中排进可打印区域，横竖混排的文档也不会变形。

`print_to_file()` 是给自检用的旁路：把同一套绘制流程写到 PDF 文件，
不弹对话框，从而能在打包产物里自动化验证这条链路。
"""

from __future__ import annotations

import os

import pymupdf
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QPainter
from PySide6.QtPrintSupport import QAbstractPrintDialog, QPrintDialog, QPrinter
from PySide6.QtWidgets import QDialog

from ..core.pdf_io import open_pdf

MAX_PRINT_DPI = 300          # 光栅化上限：再高对肉眼无意义，只会撑爆内存


def ask_printer(parent, path: str, page_count: int) -> QPrinter | None:
    """弹出标准打印设置对话框。确认则返回配置好的 QPrinter，取消返回 None。"""
    printer = QPrinter(QPrinter.HighResolution)
    printer.setDocName(os.path.basename(path))
    if page_count > 1:
        # 先告诉对话框本文档有几页，「页码范围」才会是可用的表单而不是灰的
        printer.setFromTo(1, page_count)

    dialog = QPrintDialog(printer, parent)
    dialog.setWindowTitle(f"打印 · {os.path.basename(path)}")
    dialog.setOption(QAbstractPrintDialog.PrintPageRange, True)
    if page_count > 1:
        dialog.setOption(QAbstractPrintDialog.PrintAllPages, True)
    if dialog.exec() != QDialog.Accepted:
        return None
    return printer


def selected_pages(printer: QPrinter, page_count: int) -> list[int]:
    """把对话框里的「页码范围」换算成 1 起的页号列表。"""
    first = printer.fromPage() or 1
    last = printer.toPage() or page_count
    first = max(1, min(first, page_count))
    last = max(first, min(last, page_count))
    return list(range(first, last + 1))


def _sources(printer: QPrinter) -> QRect:
    """可打印区域（设备像素）。取不到时退回整个页面。"""
    try:
        rect = printer.pageLayout().paintRectPixels(printer.resolution())
    except Exception:
        rect = QRect()
    if rect.isNull() or rect.isEmpty():
        size = printer.pageRect(QPrinter.DevicePixel).size()
        rect = QRect(0, 0, max(size.width(), 1), max(size.height(), 1))
    return rect


def _fit(area: QRect, width: int, height: int) -> QRect:
    """在 area 里等比居中放下一张 width×height 的图。"""
    if width <= 0 or height <= 0:
        return area
    scale = min(area.width() / width, area.height() / height)
    w = max(int(width * scale), 1)
    h = max(int(height * scale), 1)
    return QRect(area.x() + (area.width() - w) // 2,
                 area.y() + (area.height() - h) // 2, w, h)


def _render(page: pymupdf.Page, area: QRect, dpi_cap: int) -> QImage:
    """按目标区域的像素尺寸渲染一页，缩放比封顶在 dpi_cap。"""
    zoom = min(area.width() / max(page.rect.width, 1),
               area.height() / max(page.rect.height, 1))
    zoom = max(min(zoom, dpi_cap / 72.0), 0.05)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    # 用 PNG 中转而不是直接引用 pix.samples：QImage 不持有底层缓冲的所有权，
    # 走一次编解码最省心，也避免位图生命周期踩坑。
    return QImage.fromData(pix.tobytes("png"), "PNG")


def paint_pages(doc: pymupdf.Document, printer: QPrinter, pages: list[int],
                progress=None, dpi: int = MAX_PRINT_DPI) -> int:
    """把 doc 的指定页画到 printer 上。返回实际输出的页数。"""
    area = _sources(printer)
    painter = QPainter()
    if not painter.begin(printer):
        raise ValueError("无法启动打印任务：打印机不可用或任务已被取消。")
    try:
        for index, pno in enumerate(pages):
            if index:
                printer.newPage()
            image = _render(doc[pno - 1], area, dpi)
            if image.isNull():
                raise ValueError(f"第 {pno} 页渲染失败，无法打印。")
            painter.drawImage(_fit(area, image.width(), image.height()), image)
            if progress is not None:
                progress(index + 1, len(pages))
    finally:
        painter.end()
    return len(pages)


def print_pdf(path: str, printer: QPrinter, pages: list[int], progress=None) -> int:
    """按给定页号打印一个 PDF 文件。"""
    doc = open_pdf(path)
    try:
        return paint_pages(doc, printer, pages, progress=progress)
    finally:
        doc.close()


def print_to_file(path: str, output: str, pages: list[int] | None = None) -> int:
    """不弹对话框，把 PDF 走一遍同样的绘制流程写成文件。

    打印链路里最容易悄悄坏掉的是「QPainter 能不能在 QPrinter 上出图」，
    这一步让自检能在无人值守的环境里验证它。
    """
    doc = open_pdf(path)
    try:
        targets = pages or list(range(1, doc.page_count + 1))
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(output)
        printer.setDocName(os.path.basename(path))
        return paint_pages(doc, printer, targets)
    finally:
        doc.close()


__all__ = ["ask_printer", "selected_pages", "print_pdf", "print_to_file",
           "MAX_PRINT_DPI"]
