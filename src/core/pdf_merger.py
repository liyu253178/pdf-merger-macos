"""PDF 合并与 N-up 拼版。

设计取舍（对照参考项目 pdf-merger-macos 的教训）
------------------------------------------------
1. **不使用临时文件**。参考实现为每个源文件生成临时 PDF 再清理，异常路径容易泄漏；
   这里直接持有源文档对象，拼版时实时引用。
2. **不使用 Pillow**。参考实现靠 Pillow 把图片转 PDF，多一份 14 MB 依赖；
   PyMuPDF 原生支持打开 png/jpg/tif/bmp，且 convert_to_pdf() 可在内存完成。
3. **不丢页**。参考实现用 insert_pdf(0, 0) 只取每个 PDF 的首页，多页文件后续页被静默丢弃。
   这里遍历每个源文档的全部页码。
4. **单页失败不拖垮整份任务**，按单元记录错误后继续。
"""

from __future__ import annotations

import pymupdf

from .pdf_io import save_pdf

# 图片格式需先转成 PDF 才能被 show_pdf_page 引用（该 API 要求源必须是 PDF）
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".jfif")

PAPER_SIZES = {
    "A4": "a4",
    "A3": "a3",
    "A5": "a5",
    "Letter": "letter",
    "Legal": "legal",
}


class MergeError(RuntimeError):
    """合并过程中的可预期错误，携带用户可读信息。"""


def _is_image(path: str) -> bool:
    return path.lower().endswith(IMAGE_SUFFIXES)


def expand_units(paths: list[str]) -> list[tuple[str, int]]:
    """把文件列表展开成 [(路径, 页码), ...]。

    这是修复「多页 PDF 丢页」的关键：逐个读取真实页数，而不是固定取第 0 页。
    """
    units: list[tuple[str, int]] = []
    errors: list[str] = []
    for path in paths:
        try:
            doc = pymupdf.open(path)
        except Exception as exc:
            errors.append(f"{path}：无法打开（{exc}）")
            continue
        try:
            if doc.page_count == 0:
                errors.append(f"{path}：文件为空")
                continue
            for pno in range(doc.page_count):
                units.append((path, pno))
        finally:
            doc.close()
    if not units and errors:
        raise MergeError("没有可处理的页面：\n" + "\n".join(errors))
    return units


class PDFMerger:
    """把多份 PDF / 图片按「行 × 列」拼版到纸张页面上。"""

    def __init__(self, rows: int = 3, cols: int = 2, orientation: str = "纵向",
                 paper: str = "A4", margin_ratio: float = 0.95, gap: float = 0.0):
        self.rows = max(1, rows)
        self.cols = max(1, cols)
        self.orientation = orientation
        self.paper = paper
        self.margin_ratio = margin_ratio
        self.gap = gap
        # 最近一次 build() 中被跳过或失败的单元说明，供界面提示用户
        self.last_failures: list[str] = []

    # ---------- 几何 ----------

    def page_size(self) -> tuple[float, float]:
        w, h = pymupdf.paper_size(PAPER_SIZES.get(self.paper, "a4"))
        return (h, w) if self.orientation == "横向" else (w, h)

    def _cell_rect(self, slot: int, page_rect: pymupdf.Rect,
                   src_rect: pymupdf.Rect) -> pymupdf.Rect:
        """第 slot 个单元中，源页等比缩放并居中后的落位矩形。

        坐标约定：PyMuPDF 是**左上原点、y 向下**，因此第 0 行直接取 y=0，
        即 `y = row * cell_h`。

        注意不要写成 `y = page_h - (row+1) * cell_h` —— 那是 PDF 左下原点
        的写法，在 PyMuPDF 里会让整个行序上下颠倒（实测第 1 行会跑到最下面）。
        参考项目 pdf-merger-macos 正是这么写的，因为它永远只渲染第 1 页而未被发现。
        """
        cell_w = page_rect.width / self.cols
        cell_h = page_rect.height / self.rows
        row, col = divmod(slot, self.cols)
        x = col * cell_w
        y = row * cell_h

        usable_w = cell_w * self.margin_ratio - self.gap
        usable_h = cell_h * self.margin_ratio - self.gap
        scale = min(usable_w / src_rect.width, usable_h / src_rect.height)
        w, h = src_rect.width * scale, src_rect.height * scale
        cx = x + (cell_w - w) / 2
        cy = y + (cell_h - h) / 2
        return pymupdf.Rect(cx, cy, cx + w, cy + h)

    # ---------- 拼版 ----------

    def build(self, paths: list[str], progress=None,
              first_page_only: bool = False) -> pymupdf.Document:
        """生成拼版后的文档。

        first_page_only 用于预览：只排满一页即可，避免为预览处理全部文件。
        """
        if not paths:
            raise MergeError("请先添加要合并的文件。")

        units = expand_units(paths)
        per_page = self.rows * self.cols
        page_w, page_h = self.page_size()

        out = pymupdf.open()
        page = out.new_page(width=page_w, height=page_h)
        failures: list[str] = []
        self.last_failures = failures
        placed = 0

        # 按路径缓存源文档，避免同一文件被反复打开
        cache: dict[str, pymupdf.Document] = {}
        try:
            for index, (path, pno) in enumerate(units):
                slot = index % per_page
                if slot == 0 and index > 0:
                    if first_page_only:
                        break
                    page = out.new_page(width=page_w, height=page_h)

                try:
                    src = cache.get(path)
                    if src is None:
                        src = pymupdf.open(path)
                        cache[path] = src

                    if _is_image(path):
                        # show_pdf_page 拒绝非 PDF 源，图片需先在内存中包成 PDF
                        wrapper = pymupdf.open("pdf", src.convert_to_pdf())
                        rect = self._cell_rect(slot, page.rect, wrapper[0].rect)
                        page.show_pdf_page(rect, wrapper, 0)
                        wrapper.close()
                    else:
                        rect = self._cell_rect(slot, page.rect, src[pno].rect)
                        page.show_pdf_page(rect, src, pno)

                    placed += 1
                except Exception as exc:
                    failures.append(f"{path} 第 {pno + 1} 页：{exc}")
                    continue

                if progress:
                    progress(placed, len(units), f"已排版 {placed} / {len(units)} 页")

            if placed == 0:
                raise MergeError("所有页面均处理失败：\n" + "\n".join(failures[:10]))
            return out
        except Exception:
            out.close()
            raise
        finally:
            for doc in cache.values():
                doc.close()

    def build_preview(self, paths: list[str], max_units: int | None = None) -> pymupdf.Document:
        """预览只渲染首页，交互才跟得上手。"""
        return self.build(paths, first_page_only=True)

    # ---------- 导出 ----------

    def merge_to_file(self, paths: list[str], output: str, progress=None) -> tuple[str, int, list[str]]:
        doc = self.build(paths, progress=progress)
        try:
            save_pdf(doc, output)
            pages = doc.page_count
        finally:
            doc.close()
        return output, pages, list(self.last_failures)
