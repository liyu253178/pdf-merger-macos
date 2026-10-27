"""页面管理的非破坏模型。

页面不会在源 PDF 上直接删改。模型只保存一串 ``PageRef``：

* ``source`` + ``page_index`` 指向源文档里的一页；
* ``rotation`` 是用户额外做的旋转；
* ``kind`` 区分原件、插入的 PDF、插入的图片与空白页。

导出时才按当前顺序重建一份新 PDF，源文件始终不动。撤销/重做保存的是
PageRef 列表快照，不复制 PDF 内容，因此很快。
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field

import pymupdf

from .pdf_io import open_pdf, save_pdf
from .watermark import parse_page_spec

PAPER_SIZES = {
    "A4": "a4",
    "A3": "a3",
    "A5": "a5",
    "Letter": "letter",
    "Legal": "legal",
}

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp",
                  ".webp", ".jfif")


def paper_size(paper: str = "A4", orientation: str = "纵向") -> tuple[float, float]:
    w, h = pymupdf.paper_size(PAPER_SIZES.get(paper, "a4"))
    return (h, w) if orientation == "横向" else (w, h)


def is_image(path: str) -> bool:
    return path.lower().endswith(IMAGE_SUFFIXES)


@dataclass
class PageRef:
    source: str
    page_index: int = 0
    kind: str = "original"       # original / pdf / image / blank
    rotation: int = 0            # 用户额外旋转，90 的整数倍
    badge: str = "原件"
    source_label: str = ""
    paper: str = "A4"
    orientation: str = "纵向"

    def clone(self) -> "PageRef":
        return dataclasses.replace(self)


class PageModel:
    """单文档页面整理模型。"""

    MAX_UNDO = 50

    def __init__(self):
        self.path = ""
        self._docs: dict[str, pymupdf.Document] = {}
        self.refs: list[PageRef] = []
        self._undo: list[list[PageRef]] = []
        self._redo: list[list[PageRef]] = []
        self._changed = False
        self._exported = False
        self._saved_snapshot: list[PageRef] = []

    # ---------- 打开/关闭 ----------

    def close(self) -> None:
        for doc in self._docs.values():
            try:
                doc.close()
            except Exception:
                pass
        self._docs.clear()
        self.refs = []
        self.path = ""
        self._undo.clear()
        self._redo.clear()
        self._changed = False
        self._exported = False

    def load(self, path: str) -> None:
        self.close()
        doc = open_pdf(path)
        self.path = path
        self._docs[path] = doc
        label = os.path.basename(path)
        self.refs = [
            PageRef(source=path, page_index=i, kind="original",
                    badge="原件", source_label=label)
            for i in range(doc.page_count)
        ]
        self._saved_snapshot = self.snapshot()
        self._changed = False
        self._exported = False

    def _doc(self, path: str) -> pymupdf.Document:
        doc = self._docs.get(path)
        if doc is not None:
            return doc
        if is_image(path):
            raise ValueError("图片不是 PDF，不能按页读取")
        doc = open_pdf(path)
        self._docs[path] = doc
        return doc

    # ---------- 基础属性 ----------

    @property
    def count(self) -> int:
        return len(self.refs)

    @property
    def changed(self) -> bool:
        return self._changed

    @property
    def exported(self) -> bool:
        return self._exported

    def set_changed(self, value: bool = True) -> None:
        self._changed = value

    def index_of(self, ref: PageRef) -> int:
        for i, item in enumerate(self.refs):
            if item is ref:
                return i
        return -1

    def page_size(self, index: int) -> tuple[float, float]:
        ref = self.refs[index]
        if ref.kind == "blank":
            return paper_size(ref.paper, ref.orientation)
        if ref.kind == "image":
            return paper_size(ref.paper, ref.orientation)
        page = self._doc(ref.source)[ref.page_index]
        return page.rect.width, page.rect.height

    def source_page(self, ref: PageRef) -> pymupdf.Page | None:
        if ref.kind in ("blank", "image"):
            return None
        return self._doc(ref.source)[ref.page_index]

    def source_label(self, ref: PageRef) -> str:
        if ref.kind == "blank":
            return "空白页"
        return os.path.basename(ref.source) or "页面"

    # ---------- 撤销 ----------

    def snapshot(self) -> list[PageRef]:
        return [ref.clone() for ref in self.refs]

    def _commit(self) -> None:
        self._undo.append(self._snapshot_before)
        if len(self._undo) > self.MAX_UNDO:
            self._undo.pop(0)
        self._redo.clear()
        self._changed = True

    @property
    def _snapshot_before(self) -> list[PageRef]:
        # 在修改前由 _begin() 写入
        return getattr(self, "_pending_snapshot", [])

    def _begin(self) -> None:
        self._pending_snapshot = self.snapshot()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self.snapshot())
        self.refs = self._undo.pop()
        self._changed = True
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self.snapshot())
        self.refs = self._redo.pop()
        self._changed = True
        return True

    # ---------- 编辑 ----------

    def move(self, from_index: int, to_index: int) -> bool:
        if from_index == to_index or not (0 <= from_index < self.count):
            return False
        to_index = max(0, min(to_index, self.count - 1))
        self._begin()
        ref = self.refs.pop(from_index)
        self.refs.insert(to_index, ref)
        self._commit()
        return True

    def remove(self, indices: list[int] | set[int]) -> bool:
        keep = [i for i in range(self.count) if i not in set(indices)]
        if not keep:
            return False
        self._begin()
        self.refs = [self.refs[i] for i in keep]
        self._commit()
        return True

    def rotate(self, indices: list[int] | set[int], degrees: int = 90) -> bool:
        if not indices:
            return False
        self._begin()
        for i in sorted(set(indices)):
            if 0 <= i < self.count:
                self.refs[i].rotation = (self.refs[i].rotation + degrees) % 360
        self._commit()
        return True

    def duplicate(self, indices: list[int] | set[int]) -> list[int]:
        """把选中的页复制到各自后面，返回新插入的页索引。"""
        if not indices:
            return []
        self._begin()
        inserted: list[int] = []
        # 从后往前插，避免前面的索引偏移
        for i in sorted(set(indices), reverse=True):
            if 0 <= i < self.count:
                self.refs.insert(i + 1, self.refs[i].clone())
                inserted.append(i + 1)
        self._commit()
        return sorted(inserted)

    def insert_pdf(self, path: str, at: int, pages: str = "全部") -> list[int]:
        doc = open_pdf(path)
        if doc.page_count == 0:
            raise ValueError(f"「{os.path.basename(path)}」没有页面。")
        picked = parse_page_spec(pages, doc.page_count)
        self._docs[path] = doc
        self._begin()
        at = max(0, min(at, self.count))
        label = os.path.basename(path)
        added = []
        for pno in picked:
            self.refs.insert(at + len(added), PageRef(
                source=path, page_index=pno, kind="pdf", badge="PDF 插入",
                source_label=label,
            ))
            added.append(at + len(added))
        self._commit()
        return added

    def insert_image(self, path: str, at: int, paper: str = "A4",
                     orientation: str = "纵向") -> int:
        if not is_image(path):
            raise ValueError("请选择 PNG / JPEG / TIFF 等图片文件。")
        self._begin()
        at = max(0, min(at, self.count))
        ref = PageRef(source=path, kind="image", badge="图片",
                      source_label=os.path.basename(path),
                      paper=paper, orientation=orientation)
        self.refs.insert(at, ref)
        self._commit()
        return at

    def insert_blank(self, at: int, paper: str = "A4",
                     orientation: str = "纵向", count: int = 1) -> list[int]:
        self._begin()
        at = max(0, min(at, self.count))
        added = []
        for i in range(max(1, count)):
            self.refs.insert(at + i, PageRef(
                source="", kind="blank", badge="空白",
                source_label="空白页", paper=paper, orientation=orientation,
            ))
            added.append(at + i)
        self._commit()
        return added

    # ---------- 缩略图 ----------

    def thumbnail(self, index: int, width_px: int = 180) -> tuple[bytes, float, float]:
        """渲染一页缩略图，返回 (PNG, 页宽pt, 页高pt)。"""
        ref = self.refs[index]
        if ref.kind == "blank":
            w, h = paper_size(ref.paper, ref.orientation)
            doc = pymupdf.open()
            try:
                page = doc.new_page(width=w, height=h)
                page.draw_rect(page.rect, color=(0.88, 0.9, 0.94), width=0.8)
                pix = page.get_pixmap(matrix=_fit_matrix(page.rect, width_px),
                                      alpha=False)
                return pix.tobytes("png"), page.rect.width, page.rect.height
            finally:
                doc.close()
        if ref.kind == "image":
            w, h = paper_size(ref.paper, ref.orientation)
            doc = pymupdf.open()
            try:
                page = doc.new_page(width=w, height=h)
                rect = _fit_rect(page.rect, ref.source, margin=18)
                page.insert_image(rect, filename=ref.source, keep_proportion=True)
                pix = page.get_pixmap(matrix=_fit_matrix(page.rect, width_px),
                                      alpha=False)
                return pix.tobytes("png"), page.rect.width, page.rect.height
            finally:
                doc.close()
        page = self._doc(ref.source)[ref.page_index]
        pix = page.get_pixmap(matrix=_fit_matrix(page.rect, width_px),
                              alpha=False)
        if ref.rotation:
            # 缩略图也反映用户额外旋转，但不动源页
            pix = _rotate_pixmap(page.get_pixmap(matrix=_fit_matrix(page.rect, width_px),
                                                 alpha=False), ref.rotation)
        return pix.tobytes("png"), page.rect.width, page.rect.height

    # ---------- 导出 ----------

    def export(self, output: str) -> int:
        if not self.refs:
            raise ValueError("至少保留一页后才能导出。")
        out = pymupdf.open()
        try:
            for ref in self.refs:
                if ref.kind == "blank":
                    w, h = paper_size(ref.paper, ref.orientation)
                    page = out.new_page(width=w, height=h)
                elif ref.kind == "image":
                    w, h = paper_size(ref.paper, ref.orientation)
                    page = out.new_page(width=w, height=h)
                    rect = _fit_rect(page.rect, ref.source, margin=18)
                    page.insert_image(rect, filename=ref.source, keep_proportion=True)
                else:
                    src = self._doc(ref.source)
                    out.insert_pdf(src, from_page=ref.page_index,
                                   to_page=ref.page_index, links=True,
                                   annots=True, widgets=True)
                    page = out[-1]
                if ref.rotation:
                    page.set_rotation((page.rotation + ref.rotation) % 360)
            save_pdf(out, output)
            pages = out.page_count
        finally:
            out.close()
        self._exported = True
        self._changed = False
        self._saved_snapshot = self.snapshot()
        return pages


def _fit_matrix(page_rect: pymupdf.Rect, width_px: int) -> pymupdf.Matrix:
    zoom = max(0.05, min(4.0, width_px / max(1.0, page_rect.width)))
    return pymupdf.Matrix(zoom, zoom)


def _fit_rect(page_rect: pymupdf.Rect, image_path: str, margin: float = 18) -> pymupdf.Rect:
    try:
        pix = pymupdf.Pixmap(image_path)
        iw, ih = pix.width, pix.height
        pix = None
    except Exception:
        return pymupdf.Rect(margin, margin,
                            page_rect.width - margin, page_rect.height - margin)
    avail_w = max(1.0, page_rect.width - 2 * margin)
    avail_h = max(1.0, page_rect.height - 2 * margin)
    scale = min(avail_w / max(1, iw), avail_h / max(1, ih))
    w, h = iw * scale, ih * scale
    x = (page_rect.width - w) / 2
    y = (page_rect.height - h) / 2
    return pymupdf.Rect(x, y, x + w, y + h)


def _rotate_pixmap(pix: pymupdf.Pixmap, degrees: int) -> pymupdf.Pixmap:
    """按 90 的整数倍旋转 Pixmap（页面缩略图用）。"""
    degrees = degrees % 360
    if degrees == 0:
        return pix
    # PyMuPDF Pixmap 没有直接旋转 API；借 Pillow 不存在，改用 QImage
    try:
        from PySide6.QtGui import QImage

        fmt = (QImage.Format_RGBA8888 if pix.alpha
               else QImage.Format_RGB888)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt)
        transform = None
        from PySide6.QtGui import QTransform
        transform = QTransform().rotate(degrees)
        rotated = img.transformed(transform)
        # 转回 PNG，再交给调用方；这里返回的 Pixmap 只用于缩略图
        from PySide6.QtCore import QBuffer, QIODevice
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        rotated.save(buf, "PNG")
        return pymupdf.Pixmap("png", bytes(buf.data()))
    except Exception:
        return pix
