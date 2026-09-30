"""两个功能模块的实现：PDF 合并 与 PDF 水印。

这里把「预览、后台任务、进度、按钮态同步」这些每个模块都要写一遍的
重复逻辑收进 Module 基类，子类只保留自己的参数和作业本身。

基类还统一承担了三件新职责：
* 图层式文件管理 —— 结果 PDF 作为新图层插到列表最上方，原条目保留；
* 双击切换当前预览对象 —— 预览区在「效果预览 / 文件预览」间切换；
* 打印 —— 定位打印对象并调用系统打印对话框。
"""

from __future__ import annotations

import os

import pymupdf

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QProgressDialog, QSlider, QSpinBox,
    QVBoxLayout, QWidget,
)

from ..core.fonts import default_font, discover_fonts
from ..core.pdf_io import open_pdf, save_pdf
from ..core.pdf_merger import PDFMerger
from ..core.secure_export import DEFAULT_QUALITY, QUALITIES, export_secure, quality_by_key
from ..core.watermark import (
    DEFAULT_ANGLE, DEFAULT_OPACITY, DEFAULT_TEXT, WatermarkConfig, Watermarker,
)
from .print_pdf import ask_printer, print_pdf, selected_pages
from .ribbon import (
    RibbonBand, RibbonButton, button_group, control_row_group, form_group, row_widget,
)
from .widgets import MODE_EFFECT, MODE_FILE, ColorChip, LayerList, PreviewPane
from .workers import Debouncer, TaskWorker

PREVIEW_DEBOUNCE_MS = 260

PAPER_SIZES = ["A4", "A3", "A5", "Letter", "Legal"]


def _stem(path: str) -> str:
    """文件名（不含目录与扩展名），用于拼输出名。"""
    return os.path.splitext(os.path.basename(path))[0]


class Module:
    """一个功能模块的骨架：Ribbon 带 + 左侧图层列表 + 右侧预览区。"""

    title: str = ""
    accept_images: bool = True
    empty_hint: str = "尚未添加文件"
    accept_filter: str = "支持的文件 (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp)"

    def __init__(self, status):
        self._emit_status = status
        self._worker: TaskWorker | None = None
        self._preview_worker: TaskWorker | None = None
        self._preview_kind = MODE_EFFECT
        self._preview_pending = False
        self._preview_mode = MODE_EFFECT
        self._page_index = 0
        self._page_count = 0
        self._last_output = ""
        self.band: RibbonBand | None = None
        # 由主窗口挂上：按钮态变化时同步「打印」入口的可用性
        self.on_state_change = None

        self.files = LayerList(accept_images=self.accept_images)
        self.files.files_changed.connect(self._on_files_changed)
        self.files.layer_activated.connect(self._on_layer_activated)

        self.lbl_count = QLabel(self.empty_hint)
        self.lbl_count.setObjectName("sideCount")

        self.preview = PreviewPane()
        self.preview.mode_changed.connect(self._on_preview_mode)
        self.preview.page_step.connect(self._on_page_step)
        # 缩放级别一变、或画布尺寸变化到需要更多像素时，按新倍率重渲染。
        # 死区判断在画布里做（见 PreviewCanvas._refresh_scale）。
        self.preview.scale_changed.connect(self._on_canvas_scale)
        self.preview.set_file_mode_available(False)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setFormat("就绪")

        self._preview_debounce = Debouncer(PREVIEW_DEBOUNCE_MS, self._render_preview)

        self.sidebar = self._build_sidebar()
        self.center = self._build_center()

    # ---------- 装配 ----------

    def set_band(self, band: RibbonBand) -> None:
        self.band = band

    def _build_sidebar(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("sidePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(9, 10, 9, 9)
        layout.setSpacing(5)

        title = QLabel("图层")
        title.setObjectName("sideTitle")
        layout.addWidget(title)
        layout.addWidget(self.files, 1)
        layout.addWidget(self.lbl_count)

        hint = QLabel("双击图层设为当前预览对象")
        hint.setObjectName("sideHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return panel

    def _build_center(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)
        layout.addWidget(self.preview, 1)
        layout.addWidget(self.progress)
        return panel

    # ---------- 通用状态 ----------

    def _update_count(self) -> None:
        count = self.files.count()
        if count == 0:
            self.lbl_count.setText(self.empty_hint)
            return
        pages = self.files.total_pages()
        suffix = f" · 共 {pages} 页" if pages else ""
        self.lbl_count.setText(f"{count} 个图层{suffix}")

    def _on_files_changed(self) -> None:
        self._update_count()
        if self._preview_mode == MODE_FILE and self.files.focus_path() is None:
            # 当前预览对象被移除或清空了，退回效果预览，别留一张过期的图
            self._set_preview_mode(MODE_EFFECT)
        self.preview.set_file_mode_available(self.files.count() > 0)
        self._sync_buttons()
        self._preview_debounce.trigger()

    def _busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _sync_buttons(self) -> None:
        """刷新按钮态，并把变化告诉主窗口（打印入口要用）。"""
        self._refresh_buttons()
        if self.on_state_change is not None:
            self.on_state_change()

    def _refresh_buttons(self) -> None:
        """子类实现：根据自身按钮调整启用态。"""

    def _load_results(self, paths: list[str]) -> list[str]:
        """把处理结果作为新图层插到列表最上方并设为当前预览对象。"""
        loaded = []
        for path in paths:
            if path and os.path.isfile(path) and self.files.add_result(path):
                loaded.append(path)
        return loaded

    # ---------- 预览 ----------

    def _schedule_preview(self) -> None:
        self._preview_debounce.trigger()

    def _on_canvas_scale(self, _scale: float) -> None:
        """显示所需的渲染倍率变了（窗口缩放 / 用户放大）→ 重渲染。

        倍率本身由画布算好，模块只管重跑一次渲染；是否需要重跑也已经在
        画布侧按 18% 死区筛过，这里不再做二次判断。
        """
        self._schedule_preview()

    def _render_scale(self) -> float:
        """当前该按多大倍率渲染：交给画布按显示尺寸与屏幕缩放比算。"""
        return self.preview.canvas.render_scale()

    def _on_params_changed(self) -> None:
        """参数一变就回到效果预览 —— 否则用户调完参数会以为「没反应」。"""
        if self._preview_mode != MODE_EFFECT:
            self._set_preview_mode(MODE_EFFECT)
        self._schedule_preview()

    def _set_preview_mode(self, mode: str) -> None:
        self._preview_mode = mode
        self.preview.set_mode(mode)
        self.preview.set_nav_visible(mode == MODE_FILE)
        if mode != MODE_FILE:
            self.preview.set_target("")
            self.preview.set_page_info("")

    def _on_preview_mode(self, mode: str) -> None:
        """预览区顶部的模式切换被点击。"""
        if mode == MODE_FILE:
            if self._focus_for_preview() is None:
                self._set_preview_mode(MODE_EFFECT)
                self._emit_status("请先双击左侧任一 PDF 图层，再切到文件预览。")
                return
        self._set_preview_mode(mode)
        self._schedule_preview()

    def _on_layer_activated(self, path: str) -> None:
        """双击图层：把它设为当前预览对象并实时刷新预览。"""
        self._page_index = 0
        self._page_count = 0
        self.preview.set_target(os.path.basename(path))
        self._set_preview_mode(MODE_FILE)
        self._sync_buttons()
        self._schedule_preview()

    def _focus_for_preview(self) -> str | None:
        """文件预览该显示哪个图层：当前预览对象 → 选中项 → 首个 PDF。"""
        current = self.files.focus_path()
        if current:
            return current
        for path in self.files.selected_paths():
            if path.lower().endswith(".pdf"):
                self.files.set_focus(path, notify=False)
                return path
        fallback = self.files.first_pdf()
        if fallback:
            self.files.set_focus(fallback, notify=False)
        return fallback

    def _on_page_step(self, step: int) -> None:
        if self._page_count <= 1:
            return
        index = min(max(self._page_index + step, 0), self._page_count - 1)
        if index == self._page_index:
            return
        self._page_index = index
        self._schedule_preview()

    def _render_preview(self) -> None:
        if self._preview_mode != MODE_FILE:
            self._render_effect_preview()
            return

        path = self._focus_for_preview()
        if path is None:
            self.preview.show_message("文件预览需要至少一个 PDF 图层")
            return
        if not path.lower().endswith(".pdf"):
            self.preview.show_message(
                f"「{os.path.basename(path)}」不是 PDF，无法文件预览"
            )
            self.preview.set_page_info("—")
            return
        self._render_file_preview(path)

    def _render_file_preview(self, path: str) -> None:
        """原样渲染某一页，作为「文件预览」显示。

        渲染倍率按预览区当前需要的显示像素来定，而不是写死的固定缩放 ——
        拼版发票里 3pt 出头的小字，固定 72 dpi 下只剩 3 个像素，怎么放大
        都是糊的；按显示尺寸渲染才是"看到多少像素就渲染多少像素"。
        """
        index = max(self._page_index, 0)
        scale = self._render_scale()
        self.preview.show_hint(f"正在读取 {os.path.basename(path)} …")

        def job(_progress):
            doc = open_pdf(path)
            try:
                page_index = min(index, doc.page_count - 1)
                page = doc[page_index]
                pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                                      alpha=False)
                return (pix.tobytes("png"), page_index, doc.page_count,
                        page.rect.width, page.rect.height)
            finally:
                doc.close()

        self._spawn_preview(job, MODE_FILE)

    def _render_effect_preview(self) -> None:
        """子类实现：按模块参数渲染效果预览。"""

    def _spawn_preview(self, job, kind: str) -> None:
        """在后台线程跑预览作业，上一帧没算完就先记下待办，避免任务堆积。"""
        if self._preview_worker is not None and self._preview_worker.isRunning():
            self._preview_pending = True
            return
        self._preview_kind = kind
        worker = TaskWorker(job, None)
        worker.succeeded.connect(self._on_preview_ok)
        worker.failed.connect(self._on_preview_failed)
        # deleteLater 之后 C++ 对象会失效，必须先清掉 Python 侧引用，
        # 否则下一帧再读 isRunning() 会抛 "already deleted"。
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda w=worker: self._forget_preview_worker(w))
        self._preview_worker = worker
        worker.start()

    def _forget_preview_worker(self, worker) -> None:
        if self._preview_worker is worker:
            self._preview_worker = None
        if self._preview_pending:
            # 之前被跳过的那次刷新补上，快速翻页/拖滑块才不会丢帧
            self._preview_pending = False
            self._schedule_preview()

    def _on_preview_failed(self, message: str) -> None:
        self.preview.show_hint(f"预览失败：{message}")

    def _on_preview_ok(self, payload) -> None:
        # 结果可能与当前模式已经对不上（切换瞬间上一帧才跑完），
        # 这种情况直接丢掉，否则会把过期的文件名/页码写回预览区。
        if self._preview_kind == MODE_FILE and self._preview_mode == MODE_FILE:
            data, index, count, page_w, page_h = payload
            self._page_index, self._page_count = index, count
            name = os.path.basename(self.files.focus_path() or "")
            self.preview.set_target(name)
            self.preview.set_page_info(f"{index + 1} / {count}")
            self.preview.show_png(
                data,
                f"文件预览 · {name} · 第 {index + 1}/{count} 页"
                "　|　滚轮或 ← → 翻页，双击左侧图层可切换文件",
                page_pt=(page_w, page_h),
            )
            return
        if self._preview_kind == MODE_EFFECT and self._preview_mode == MODE_EFFECT:
            self._on_effect_preview_ok(payload)

    def _on_effect_preview_ok(self, payload) -> None:
        """子类实现：把效果预览结果画出来。"""

    # ---------- 后台任务 ----------

    def _run_task(self, job, ok_title: str, report, output_path: str = "",
                  results=None) -> None:
        """跑一个后台作业。

        `results(result)` 负责从返回值里挑出落盘的 PDF 路径，作业成功后
        这些文件会作为新图层自动插到列表最上方。
        """
        self._last_output = output_path
        worker = TaskWorker(job, None)
        worker.progressed.connect(self._on_progress)
        worker.succeeded.connect(
            lambda result: self._on_done(ok_title, report(result), output_path,
                                         results(result) if results else [])
        )
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        self._sync_buttons()
        self.progress.setValue(0)
        worker.start()

    def _on_progress(self, done: int, total: int, text: str) -> None:
        if total > 0:
            self.progress.setValue(int(done / total * 100))
        self.progress.setFormat(text)

    def _on_done(self, title: str, message: str, output_path: str,
                 results: list[str]) -> None:
        self.progress.setValue(100)
        self.progress.setFormat("完成")
        detail = message
        if output_path and os.path.exists(output_path):
            detail += f"\n\n文件大小：{os.path.getsize(output_path) / 1024:.1f} KB"
        loaded = self._load_results(results)
        if loaded:
            detail += (f"\n\n已作为新图层插入到左侧列表最上方（{len(loaded)} 个），"
                       "双击即可预览，原有图层保持不变。")
        self._emit_status(detail.split("\n")[0])
        QMessageBox.information(self.center, title, detail)

    def _on_failed(self, message: str) -> None:
        self.progress.setValue(0)
        self.progress.setFormat("失败")
        self._emit_status(f"出错：{message}")
        QMessageBox.critical(self.center, "出错", message)

    def _on_finished(self) -> None:
        self._worker = None
        self._sync_buttons()

    # ---------- 打印 ----------

    def _print_target(self) -> str | None:
        """打印对象：当前预览对象 → 列表选中项 → 首个 PDF 图层。"""
        current = self.files.focus_path()
        if current and current.lower().endswith(".pdf"):
            return current
        for path in self.files.selected_paths():
            if path.lower().endswith(".pdf"):
                return path
        return self.files.first_pdf()

    def can_print(self) -> bool:
        return not self._busy() and self._print_target() is not None

    def print_hint(self) -> str:
        target = self._print_target()
        if target is None:
            return "打印（还没有可打印的 PDF 图层）"
        return f"打印：{os.path.basename(target)}\n先弹出系统打印对话框，确认后再输出"

    def print_current(self) -> None:
        """打印当前预览对象（无则用选中的/首个 PDF 图层）。"""
        if self._busy():
            QMessageBox.information(self.center, "提示", "正在处理任务，请稍候再打印。")
            return
        target = self._print_target()
        if target is None:
            QMessageBox.warning(self.center, "提示", "请先添加或生成一个 PDF 图层。")
            return
        try:
            with open_pdf(target) as doc:
                page_count = doc.page_count
        except ValueError as exc:
            QMessageBox.critical(self.center, "无法打印", str(exc))
            return

        printer = ask_printer(self.center, target, page_count)
        if printer is None:
            self._emit_status("已取消打印")
            return
        pages = selected_pages(printer, page_count)

        box = QProgressDialog("正在准备打印…", "", 0, len(pages), self.center)
        box.setWindowTitle("打印")
        box.setWindowModality(Qt.WindowModal)
        box.setCancelButton(None)
        box.setMinimumDuration(0)
        box.setAutoClose(False)
        box.setValue(0)

        def report(done: int, total: int) -> None:
            box.setLabelText(f"正在输出第 {done}/{total} 页…")
            box.setValue(done)
            QApplication.processEvents()

        try:
            printed = print_pdf(target, printer, pages, progress=report)
        except Exception as exc:  # noqa: BLE001 - 打印链路异常一律展示给用户
            box.close()
            box.deleteLater()
            self._emit_status(f"打印失败：{exc}")
            QMessageBox.critical(self.center, "打印失败", str(exc))
            return

        box.close()
        box.deleteLater()
        self.progress.setValue(100)
        self.progress.setFormat(f"已打印 {printed} 页")
        self._emit_status(f"已发送 {printed} 页到打印机")
        QMessageBox.information(
            self.center, "打印",
            f"已发送 {printed} 页到打印队列：\n{os.path.basename(target)}\n\n"
            "页码范围与份数以打印对话框中的设置为准。",
        )

    # ---------- 文件选择 ----------

    def add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self.center, "选择文件", "", self.accept_filter
        )
        if files:
            added = self.files.add_paths(files)
            if added < len(files):
                self._emit_status(f"已添加 {added} 个，其余为重复或不支持的格式")


# --------------------------------------------------------------------------- #
#  PDF 合并
# --------------------------------------------------------------------------- #

class MergeModule(Module):
    title = "PDF 合并"
    accept_images = True
    empty_hint = "尚未添加文件"

    def __init__(self, status):
        super().__init__(status)
        self._build_ribbon()
        self._connect()
        self._sync_buttons()

    # ---------- Ribbon ----------

    def _build_ribbon(self) -> None:
        self.cmb_paper = QComboBox()
        self.cmb_paper.addItems(PAPER_SIZES)
        self.cmb_paper.setFixedWidth(92)

        self.cmb_orientation = QComboBox()
        self.cmb_orientation.addItems(["纵向", "横向"])
        self.cmb_orientation.setFixedWidth(92)

        self.spin_rows = QSpinBox()
        self.spin_rows.setRange(1, 8)
        self.spin_rows.setValue(3)
        self.spin_rows.setFixedWidth(70)

        self.spin_cols = QSpinBox()
        self.spin_cols.setRange(1, 8)
        self.spin_cols.setValue(2)
        self.spin_cols.setFixedWidth(70)

        self.lbl_per_page = QLabel("6 份")
        self.lbl_per_page.setProperty("ribbonField", "true")

        self.btn_add = RibbonButton("添加文件", "open")
        self.btn_remove = RibbonButton("移除", "remove")
        self.btn_clear = RibbonButton("清空", "clear")
        self.btn_merge = RibbonButton("开始合并并导出", "layout", primary=True)

        self.btn_add.clicked.connect(self.add_files)
        self.btn_remove.clicked.connect(self.files.remove_selected)
        self.btn_clear.clicked.connect(self.files.clear_all)
        self.btn_merge.clicked.connect(self.merge)

        self.set_band(RibbonBand(
            [
                button_group("文件", [self.btn_add, self.btn_remove, self.btn_clear]),
                form_group("版面", [("纸张", self.cmb_paper),
                                    ("方向", self.cmb_orientation)]),
                form_group("拼版", [("行数", self.spin_rows),
                                    ("列数", self.spin_cols),
                                    ("每页", self.lbl_per_page)]),
            ],
            trailing=button_group("输出", [self.btn_merge]),
        ))

    def _connect(self) -> None:
        for widget in (self.cmb_paper, self.cmb_orientation):
            widget.currentIndexChanged.connect(self._on_params_changed)
        for widget in (self.spin_rows, self.spin_cols):
            widget.valueChanged.connect(self._update_per_page)
            widget.valueChanged.connect(self._on_params_changed)
        self._update_per_page()

    def _update_per_page(self) -> None:
        per = self.spin_rows.value() * self.spin_cols.value()
        self.lbl_per_page.setText(f"{per} 份")

    def _refresh_buttons(self) -> None:
        has_files = self.files.count() > 0
        busy = self._busy()
        self.btn_merge.setEnabled(has_files and not busy)
        self.btn_remove.setEnabled(has_files and not busy)
        self.btn_clear.setEnabled(has_files and not busy)
        self.btn_add.setEnabled(not busy)

    def _merger(self) -> PDFMerger:
        return PDFMerger(
            rows=self.spin_rows.value(),
            cols=self.spin_cols.value(),
            orientation=self.cmb_orientation.currentText(),
            paper=self.cmb_paper.currentText(),
        )

    # ---------- 预览 ----------

    def _render_effect_preview(self) -> None:
        paths = self.files.paths()
        if not paths:
            self.preview.show_message("预览将显示在这里")
            return
        merger = self._merger()
        scale = self._render_scale()
        self.preview.show_hint("正在生成预览…")

        def job(_progress):
            doc = merger.build_preview(paths)
            try:
                page = doc[0]
                pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                                      alpha=False)
                return pix.tobytes("png"), page.rect.width, page.rect.height
            finally:
                doc.close()

        self._spawn_preview(job, MODE_EFFECT)

    def _on_effect_preview_ok(self, payload) -> None:
        data, page_w, page_h = payload
        self.preview.show_png(
            data,
            f"{self.cmb_paper.currentText()} {self.cmb_orientation.currentText()} · "
            f"{self.spin_rows.value()} 行 × {self.spin_cols.value()} 列 · "
            f"共 {self.files.count()} 个图层"
            "（每个 PDF 的全部页面都会展开参与排版）",
            page_pt=(page_w, page_h),
        )

    # ---------- 动作 ----------

    def merge(self) -> None:
        paths = self.files.paths()
        if not paths:
            QMessageBox.warning(self.center, "提示", "请先添加要合并的文件。")
            return

        output, _ = QFileDialog.getSaveFileName(
            self.center, "导出合并后的 PDF", "合并结果.pdf", "PDF 文件 (*.pdf)"
        )
        if not output:
            return
        if not output.lower().endswith(".pdf"):
            output += ".pdf"

        merger = self._merger()
        self.progress.setFormat("正在排版…")

        def job(progress):
            doc = merger.build(paths, progress=progress)
            try:
                save_pdf(doc, output)
                return doc.page_count, list(merger.last_failures)
            finally:
                doc.close()

        self._run_task(job, "合并完成", self._merge_report, output,
                       results=lambda _payload: [output])

    def _merge_report(self, payload) -> str:
        pages, failures = payload
        message = f"已导出 {pages} 页。\n\n保存位置：{self._last_output}"
        if failures:
            # 不静默吞掉：哪一页没排进去必须让用户看到
            shown = "\n".join(failures[:8])
            more = f"\n…另有 {len(failures) - 8} 项" if len(failures) > 8 else ""
            message += f"\n\n⚠ 有 {len(failures)} 页未能处理：\n{shown}{more}"
        return message


# --------------------------------------------------------------------------- #
#  PDF 水印
# --------------------------------------------------------------------------- #

class WatermarkModule(Module):
    title = "PDF 水印"
    accept_images = False
    empty_hint = "尚未添加 PDF"
    accept_filter = "PDF 文件 (*.pdf)"

    def __init__(self, status):
        self._fonts = discover_fonts()
        self._color = (0.35, 0.35, 0.40)
        self._preview_source: str | None = None
        super().__init__(status)
        self._build_ribbon()
        self._connect()
        self._sync_buttons()
        if not self._fonts:
            self._emit_status("警告：未找到可用的中文字体，水印中文可能无法显示。")
        self._preview_debounce.trigger()

    # ---------- Ribbon ----------

    def _build_ribbon(self) -> None:
        self.edit_line1 = QLineEdit(DEFAULT_TEXT)
        self.edit_line1.setPlaceholderText("例如：机密文件 请勿外传")
        self.edit_line1.setFixedWidth(134)
        self.edit_line2 = QLineEdit("")
        self.edit_line2.setPlaceholderText("例如：张三 · 2026-09-21")
        self.edit_line2.setFixedWidth(134)

        self.cmb_font = QComboBox()
        for opt in self._fonts:
            self.cmb_font.addItem(opt.name, opt)
        if self._fonts:
            self.cmb_font.setCurrentIndex(self._fonts.index(default_font(self._fonts)))
        self.cmb_font.setFixedWidth(136)

        self.spin_size = QDoubleSpinBox()
        self.spin_size.setRange(8, 160)
        self.spin_size.setDecimals(0)
        self.spin_size.setValue(34)
        self.spin_size.setSuffix(" pt")
        self.spin_size.setFixedWidth(78)

        self.btn_color = ColorChip()
        self.btn_color.setFixedSize(72, 20)
        self.btn_color.setToolTip("点击选择水印颜色")
        self.btn_color.setCursor(Qt.PointingHandCursor)
        self.btn_color.setAlignment(Qt.AlignCenter)

        self.slider_angle = QSlider(Qt.Horizontal)
        self.slider_angle.setRange(-180, 180)
        self.slider_angle.setValue(int(DEFAULT_ANGLE))
        self.slider_angle.setFixedWidth(112)

        self.spin_angle = QDoubleSpinBox()
        self.spin_angle.setRange(-180, 180)
        self.spin_angle.setDecimals(0)
        self.spin_angle.setValue(DEFAULT_ANGLE)
        self.spin_angle.setSuffix(" °")
        self.spin_angle.setFixedWidth(74)

        self.slider_opacity = QSlider(Qt.Horizontal)
        self.slider_opacity.setRange(1, 100)
        self.slider_opacity.setValue(int(DEFAULT_OPACITY * 100))
        self.slider_opacity.setFixedWidth(112)

        self.lbl_opacity = QLabel(f"{self.slider_opacity.value()}%")
        self.lbl_opacity.setProperty("ribbonField", "true")

        self.spin_cols = QSpinBox()
        self.spin_cols.setRange(1, 6)
        self.spin_cols.setValue(2)
        self.spin_cols.setFixedWidth(70)

        self.spin_rows = QSpinBox()
        self.spin_rows.setRange(1, 6)
        self.spin_rows.setValue(3)
        self.spin_rows.setFixedWidth(70)

        self.edit_pages = QLineEdit("全部")
        self.edit_pages.setPlaceholderText("全部，或 1-3,5")
        self.edit_pages.setFixedWidth(84)

        self.cmb_quality = QComboBox()
        for q in QUALITIES:
            # 文案必须短：功能带横向已经排满，下拉框一宽就会把左边的按钮挤到省略号
            self.cmb_quality.addItem(
                f"{q.label} {q.dpi}dpi" + ("★" if q.key == DEFAULT_QUALITY else ""),
                q.key,
            )
        self.cmb_quality.setCurrentIndex(
            next(i for i, q in enumerate(QUALITIES) if q.key == DEFAULT_QUALITY)
        )
        # 宽度按最长条目算：写死过 104/112，结果「无损 300dpi ★」末尾的星号
        # 被下拉箭头盖住了 —— 这类"差几个像素"的截断肉眼最难发现。
        metrics = self.cmb_quality.fontMetrics()
        widest = max(metrics.horizontalAdvance(self.cmb_quality.itemText(i))
                     for i in range(self.cmb_quality.count()))
        self.cmb_quality.setFixedWidth(widest + 36)      # 下拉箭头 + 左右内边距
        self.cmb_quality.setToolTip(
            "保密输出的画质档位：\n"
            "· 标准 200dpi —— 体积最小，适合只看个大概\n"
            "· 高清 300dpi —— JPEG，照片/扫描件推荐\n"
            "· 无损 300dpi —— PNG 直出，文字线条最锐利\n"
            "  （内容为照片且单页超过 4 MB 时该页自动改用 JPEG）\n"
            "· 超清 400dpi —— 需要再放大看细节时用\n"
            "拼版后的小字建议不低于 300 dpi。"
        )

        self.btn_add = RibbonButton("添加 PDF", "add")
        self.btn_remove = RibbonButton("移除", "remove")
        self.btn_clear = RibbonButton("清空", "clear")
        self.btn_apply = RibbonButton("应用水印并导出", "stamp", primary=True)
        self.btn_secure = RibbonButton("保密输出", "shield")
        self.btn_secure.setToolTip(
            "加水印 → 逐页转成图片 → 按原顺序合回 PDF：\n"
            "成品只保留图片，文本层、注释、表单、链接与元信息全部移除。\n"
            "（文件会变大，文字不再可搜索）\n"
            "画质由右侧「清晰度」档位决定，建议 300 dpi 及以上。"
        )

        self.btn_add.clicked.connect(self.add_files)
        self.btn_remove.clicked.connect(self.files.remove_selected)
        self.btn_clear.clicked.connect(self.files.clear_all)
        self.btn_apply.clicked.connect(self.apply)
        self.btn_secure.clicked.connect(self.secure_export)
        self.btn_color.clicked.connect(self.pick_color)

        self.set_band(RibbonBand(
            [
                button_group("图层", [self.btn_add, self.btn_remove, self.btn_clear]),
                form_group("水印文字", [("第一行", self.edit_line1),
                                        ("第二行", self.edit_line2)]),
                form_group("字体样式", [("字体", self.cmb_font),
                                        ("字号", self.spin_size),
                                        ("颜色", self.btn_color)]),
                form_group("角度与透明", [("角度", row_widget(self.slider_angle,
                                                              self.spin_angle)),
                                          ("不透明度", row_widget(self.slider_opacity,
                                                                  self.lbl_opacity))]),
                form_group("平铺与范围", [("横向", self.spin_cols),
                                          ("纵向", self.spin_rows),
                                          ("应用页", self.edit_pages)]),
            ],
            trailing=control_row_group("导出", [
                self.btn_apply,
                self.btn_secure,
                ("清晰度", self.cmb_quality),
            ]),
        ))
        self._refresh_color()

    def _connect(self) -> None:
        self.edit_line1.textChanged.connect(self._on_params_changed)
        self.edit_line2.textChanged.connect(self._on_params_changed)
        self.cmb_font.currentIndexChanged.connect(self._on_params_changed)
        self.spin_size.valueChanged.connect(self._on_params_changed)
        self.slider_angle.valueChanged.connect(self._on_angle_slider)
        self.spin_angle.valueChanged.connect(self._on_angle_spin)
        self.slider_opacity.valueChanged.connect(self._on_opacity)
        for widget in (self.spin_cols, self.spin_rows):
            widget.valueChanged.connect(self._on_params_changed)

    def _refresh_buttons(self) -> None:
        has_files = self.files.count() > 0
        busy = self._busy()
        self.btn_apply.setEnabled(has_files and not busy)
        self.btn_secure.setEnabled(has_files and not busy)
        self.btn_remove.setEnabled(has_files and not busy)
        self.btn_clear.setEnabled(has_files and not busy)
        self.btn_add.setEnabled(not busy)

    # ---------- 颜色（色卡比按钮更贴合紧凑表单） ----------

    def _refresh_color(self) -> None:
        r, g, b = (int(c * 255) for c in self._color)
        self.btn_color.setText(f"#{r:02X}{g:02X}{b:02X}")
        self.btn_color.setStyleSheet(
            f"QLabel {{ background:#{r:02X}{g:02X}{b:02X}; border-radius:4px; "
            f"border:1px solid #c9cfda; font-size:10px; color:"
            f"{'#ffffff' if (r * 299 + g * 587 + b * 114) / 1000 < 140 else '#111827'}; }}"
        )

    def pick_color(self) -> None:
        r, g, b = (int(c * 255) for c in self._color)
        chosen = QColorDialog.getColor(QColor(r, g, b), self.center, "选择水印颜色")
        if chosen.isValid():
            self._color = (chosen.redF(), chosen.greenF(), chosen.blueF())
            self._refresh_color()
            self._on_params_changed()

    # ---------- 参数 ----------

    def _on_angle_slider(self, value: int) -> None:
        self.spin_angle.blockSignals(True)
        self.spin_angle.setValue(float(value))
        self.spin_angle.blockSignals(False)
        self._on_params_changed()

    def _on_angle_spin(self, value: float) -> None:
        self.slider_angle.blockSignals(True)
        self.slider_angle.setValue(int(round(value)))
        self.slider_angle.blockSignals(False)
        self._on_params_changed()

    def _on_opacity(self, value: int) -> None:
        self.lbl_opacity.setText(f"{value}%")
        self._on_params_changed()

    def _text(self) -> str:
        lines = [self.edit_line1.text().strip(), self.edit_line2.text().strip()]
        return "\n".join(ln for ln in lines if ln) or DEFAULT_TEXT

    def _config(self) -> WatermarkConfig:
        font = self.cmb_font.currentData() or (self._fonts[0] if self._fonts else None)
        return WatermarkConfig(
            text=self._text(),
            angle=self.spin_angle.value(),
            font_size=self.spin_size.value(),
            opacity=self.slider_opacity.value() / 100.0,
            color=self._color,
            font=font,
            tile_cols=self.spin_cols.value(),
            tile_rows=self.spin_rows.value(),
            pages=self.edit_pages.text().strip() or "全部",
        )

    # ---------- 预览 ----------

    def _render_effect_preview(self) -> None:
        cfg = self._config()
        # 有 PDF 就拿它的首页当底，预览才是「真效果」而不是空白示意图；
        # 双击指定过当前预览对象就用它，方便逐个文件比对加印效果。
        current = self.files.focus_path()
        self._preview_source = (current if current and current.lower().endswith(".pdf")
                                else self.files.first_pdf())
        background = self._preview_source
        scale = self._render_scale()
        self._spawn_preview(
            lambda _progress: Watermarker(cfg).render_preview(zoom=scale,
                                                              background=background),
            MODE_EFFECT,
        )

    def _on_effect_preview_ok(self, payload) -> None:
        data, page_w, page_h = payload
        cfg = self._config()
        font_name = cfg.resolved_font().name if cfg.font is None else cfg.font.name
        if self._preview_source:
            base = f"底图：{os.path.basename(self._preview_source)} 第 1 页"
        else:
            base = "底图：空白页示意（添加 PDF 后可看真实效果）"
        self.preview.show_png(
            data,
            f"{font_name} · {cfg.font_size:.0f}pt · {cfg.angle:g}° · "
            f"不透明度 {cfg.opacity * 100:.0f}% · 平铺 {cfg.tile_cols}×{cfg.tile_rows}"
            f"　|　{base}",
            page_pt=(page_w, page_h),
        )

    # ---------- 动作 ----------

    def _validated_paths(self) -> list[str] | None:
        paths = self.files.paths()
        if not paths:
            QMessageBox.warning(self.center, "提示", "请先添加要加印水印的 PDF。")
            return None
        if not self._config().lines():
            QMessageBox.warning(self.center, "提示", "水印文字不能为空。")
            return None
        return paths

    def _pick_target(self, paths: list[str], suffix: str,
                     title: str) -> tuple[str, bool] | None:
        """选输出目标。返回 (路径或目录, 是否多文件模式)，取消则返回 None。"""
        if len(paths) > 1:
            directory = QFileDialog.getExistingDirectory(self.center, "选择导出目录")
            return (directory, True) if directory else None

        target, _ = QFileDialog.getSaveFileName(
            self.center, title, _stem(paths[0]) + suffix, "PDF 文件 (*.pdf)"
        )
        if not target:
            return None
        if not target.lower().endswith(".pdf"):
            target += ".pdf"
        return target, False

    def apply(self) -> None:
        """导出带水印的 PDF（文本层、可选文字等原样保留）。"""
        paths = self._validated_paths()
        if paths is None:
            return
        picked = self._pick_target(paths, "_水印.pdf", "导出带水印的 PDF")
        if picked is None:
            return
        target, multi = picked
        cfg = self._config()
        count = len(paths)

        def job(progress):
            outputs = []
            for index, path in enumerate(paths):
                out_path = (os.path.join(target, f"{_stem(path)}_水印.pdf")
                            if multi else target)

                def per_page(done, total, text, _i=index):
                    overall = int(((_i + done / max(total, 1)) / count) * 100)
                    progress(overall, 100, f"[{_i + 1}/{count}] {text}")

                with open_pdf(path) as doc:
                    Watermarker(cfg).apply(doc, progress=per_page)
                    save_pdf(doc, out_path)
                outputs.append(out_path)
            return outputs

        self.progress.setFormat("正在加印…")
        self._run_task(job, "水印完成", self._watermark_report,
                       results=lambda outputs: outputs)

    def secure_export(self) -> None:
        """保密输出：加水印 → 逐页转图片 → 合回 PDF。

        源文件不会被改动；中间的水印 PDF 与逐页图片都落在临时目录里，
        结束（含出错）时一并清理，只留下最终的图片版 PDF。
        """
        paths = self._validated_paths()
        if paths is None:
            return
        picked = self._pick_target(paths, "_保密.pdf", "导出保密 PDF")
        if picked is None:
            return
        target, multi = picked
        cfg = self._config()
        quality = quality_by_key(self.cmb_quality.currentData() or DEFAULT_QUALITY)
        count = len(paths)

        def job(progress):
            results = []
            for index, path in enumerate(paths):
                out_path = (os.path.join(target, f"{_stem(path)}_保密.pdf")
                            if multi else target)

                def sub(done, total, text, _i=index):
                    overall = int(((_i + done / max(total, 1)) / count) * 100)
                    progress(overall, 100, f"[{_i + 1}/{count}] {text}")

                results.append((out_path, export_secure(
                    path, out_path, cfg, dpi=quality.dpi, progress=sub,
                    lossless=quality.lossless, jpeg_quality=quality.jpeg_quality,
                )))
            return results

        self.progress.setFormat("正在准备…")
        self._run_task(job, "保密输出完成", self._secure_report,
                       results=lambda results: [path for path, _info in results])

    def _watermark_report(self, outputs: list[str]) -> str:
        lines = []
        for path in outputs:
            size = os.path.getsize(path) / 1024 if os.path.exists(path) else 0
            lines.append(f"{os.path.basename(path)}（{size:.1f} KB）")
        return ("已导出：\n" + "\n".join(lines)
                + "\n\n提示：中文字体已自动子集化，不会把整份字体塞进 PDF。")

    def _secure_report(self, results: list[tuple[str, dict]]) -> str:
        quality = quality_by_key(self.cmb_quality.currentData() or DEFAULT_QUALITY)
        lines, pages, total_kb, fell_back = [], 0, 0.0, 0
        for path, info in results:
            kb = info["bytes"] / 1024
            pages += info["pages"]
            total_kb += kb
            fell_back += len(info.get("jpeg_pages") or ())
            lines.append(f"{os.path.basename(path)}（{info['pages']} 页，{kb:.1f} KB）")
        message = (
            f"已输出 {len(results)} 个文件，共 {pages} 页，合计 {total_kb:.1f} KB\n"
            f"画质：{quality.describe()}　|　临时文件已清理，原文件未被改动\n\n"
            + "\n".join(lines)
            + "\n\n成品只保留图片：先加水印，再逐页转成图片，最后按原顺序合回 PDF —— "
              "文本层、注释对象、表单域、超链接与元信息均已移除，"
              "内容不可检索、不可复制。"
        )
        if fell_back:
            message += (
                f"\n\n注：有 {fell_back} 页内容为照片/扫描型，PNG 会过大，"
                "这些页已自动改用 JPEG（其余页仍为无损）。"
            )
        return message
