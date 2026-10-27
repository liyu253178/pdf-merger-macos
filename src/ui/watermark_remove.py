"""PDF 去水印模块：对象检测、预览点选与安全导出。

这个模块只负责界面和任务编排，真正的对象级删除在
``core.watermark_remover`` 里完成。界面分成三块：

* 左侧：统一图层栏（眼睛表示该文件的水印框是否显示）；
* 中间：原图预览，检测框直接画在页面上，点框即可勾选/取消；
* 右侧：按「文字 / 图片 / 注释」分组的检测结果，可勾选后预览或导出。

导出永远另存新文件，源 PDF 不会被写回。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

import pymupdf

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from ..core.pdf_io import open_pdf, save_pdf
from ..core.watermark_remover import (
    RemoveStats, WatermarkDetector, WatermarkObject, WatermarkRemover,
)
from .icons import svg_icon
from .modules import Module, _stem
from .ribbon import (
    RibbonBand, RibbonButton, button_group, control_row_group, stack_group,
)
from .widgets import (
    MODE_EFFECT, MODE_FILE, ChoiceField, EmptyState, PreviewPane,
)

_KIND_TITLES = {"text": "文字水印", "image": "图片水印", "annot": "注释水印"}
_KIND_ORDER = ("text", "image", "annot")


class _DetectCheck(QCheckBox):
    """14px 方形勾选框：自绘，确保选中时有清晰的白色对勾。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, _event):  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        if not self.isEnabled():
            fill, border, check = QColor("#F0F2F5"), QColor("#DCE1E8"), QColor("#B8C0CC")
        elif self.isChecked():
            fill, border, check = QColor("#2563EB"), QColor("#2563EB"), QColor("#FFFFFF")
        else:
            fill, border, check = QColor("#FFFFFF"), QColor("#C9D2E0"), QColor("#FFFFFF")
        painter.setPen(QPen(border, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 3, 3)
        if self.isChecked():
            painter.setPen(QPen(check, 1.8, Qt.SolidLine, Qt.RoundCap,
                                Qt.RoundJoin))
            painter.drawLine(rect.left() + 3, rect.center().y(),
                             rect.left() + 6, rect.bottom() - 3)
            painter.drawLine(rect.left() + 6, rect.bottom() - 3,
                             rect.right() - 3, rect.top() + 3)
        painter.end()


def _fmt_size(bbox) -> str:
    if not bbox:
        return ""
    w = max(0.0, bbox[2] - bbox[0])
    h = max(0.0, bbox[3] - bbox[1])
    return f"{w:.0f} × {h:.0f} pt"


def _short(text: str, limit: int = 18) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _output_stem(path: str) -> str:
    """给导出文件起一个不重复“水印”字样的基名。"""
    stem = _stem(path)
    for suffix in ("_水印", "-水印", " 水印", "_加水印", "_wm", "_WM"):
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    return f"{stem}_去除水印"


class DetectionRow(QFrame):
    """一条检测结果：勾选框 + 类型图标 + 标题/说明 + 页码。"""

    toggled = Signal(str, bool)
    activated = Signal(str)

    def __init__(self, obj: WatermarkObject, multi_doc: bool = False,
                 parent=None):
        super().__init__(parent)
        self.obj = obj
        self.setObjectName("detectRow")
        self.setProperty("kind", obj.kind)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(9, 6, 8, 6)
        row.setSpacing(7)

        self.check = _DetectCheck()
        self.check.setObjectName("detectCheck")
        self.check.setEnabled(obj.removable)
        self.check.setChecked(obj.removable and obj.confidence != "low")
        self.check.toggled.connect(
            lambda checked: self.toggled.emit(self.obj.oid, bool(checked)))
        row.addWidget(self.check)

        icon = QLabel()
        if obj.kind == "text":
            icon.setText("T")
            font = QFont()
            font.setPointSize(9)
            font.setWeight(QFont.Weight.Bold)
            icon.setFont(font)
            icon.setStyleSheet("color:#4B5563;")
        else:
            icon_name = {"image": "image", "annot": "stamp"}.get(obj.kind, "page")
            icon.setPixmap(svg_icon(icon_name).pixmap(QSize(16, 16)))
        icon.setFixedSize(18, 18)
        icon.setAlignment(Qt.AlignCenter)
        icon.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        row.addWidget(icon)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(1)
        if obj.kind == "text":
            title = _short(obj.text) or "文字水印"
            meta = f"{obj.size:.0f} pt · 不透明度 {obj.opacity:.0%}"
        elif obj.kind == "image":
            title = "图片水印"
            meta = f"{_fmt_size(obj.bbox)} · 小图对象"
        else:
            title = _short(obj.text) or "注释水印"
            meta = obj.note or "注释"
        if multi_doc and obj.doc_path:
            meta = f"{os.path.basename(obj.doc_path)} · {meta}"
        if not obj.removable:
            meta = obj.note or "该对象无法安全删除"
        self.lbl_title = QLabel(title)
        self.lbl_title.setObjectName("detectTitle")
        self.lbl_title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.lbl_meta = QLabel(meta)
        self.lbl_meta.setObjectName("detectMeta")
        self.lbl_meta.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.lbl_meta.setToolTip(meta)
        body.addWidget(self.lbl_title)
        body.addWidget(self.lbl_meta)
        row.addLayout(body, 1)

        page = QLabel(f"第 {obj.page_no} 页")
        page.setObjectName("detectPage")
        page.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        row.addWidget(page, 0, Qt.AlignTop)
        self.setToolTip(obj.note)

    def is_checked(self) -> bool:
        return self.check.isChecked()

    def set_checked(self, checked: bool) -> None:
        self.check.blockSignals(True)
        self.check.setChecked(bool(checked))
        self.check.blockSignals(False)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt 命名
        if event.button() == Qt.LeftButton:
            # 勾选框自己处理勾选；点行内任意其它位置也直接切换选中，
            # 不必精确点中 16px 的小方框。文字水印行较长，这一点尤其明显。
            if not self.check.geometry().contains(event.position().toPoint()):
                if self.obj.removable:
                    self.check.toggle()
                self.activated.emit(self.obj.oid)
        super().mouseReleaseEvent(event)


class DetectionPanel(QFrame):
    """右侧检测结果面板：分组、勾选、全选/全不选。"""

    selection_changed = Signal()
    object_activated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("detectPanel")
        self.setMinimumWidth(268)
        self.setMaximumWidth(340)
        self._objects: list[WatermarkObject] = []
        self._rows: dict[str, DetectionRow] = {}
        self._multi_doc = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("detectHeader")
        head = QHBoxLayout(header)
        head.setContentsMargins(14, 10, 12, 8)
        head.setSpacing(6)
        title = QLabel("检测结果")
        title.setObjectName("detectHeaderTitle")
        self.lbl_count = QLabel("0 个")
        self.lbl_count.setObjectName("detectCount")
        head.addWidget(title)
        head.addWidget(self.lbl_count)
        head.addStretch(1)
        layout.addWidget(header)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("detectScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.content.setObjectName("detectContent")
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(9, 2, 9, 8)
        self.content_layout.setSpacing(3)
        self.content_layout.addStretch(1)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)

        self.empty = QLabel(
            "尚未检测水印对象\n输入水印文字后点「查找」，\n或直接点「自动检测」。"
        )
        self.empty.setObjectName("detectEmpty")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty, 1)
        self.empty.setVisible(True)
        self.scroll.setVisible(False)

        footer = QWidget()
        footer.setObjectName("detectFooter")
        foot = QHBoxLayout(footer)
        foot.setContentsMargins(12, 6, 10, 8)
        foot.setSpacing(6)
        self.lbl_selected = QLabel("已选 0 / 0")
        self.lbl_selected.setObjectName("detectSelected")
        self.btn_all = QToolButton()
        self.btn_all.setText("全选")
        self.btn_all.setObjectName("detectLink")
        self.btn_all.clicked.connect(lambda: self.select_all(True))
        self.btn_none = QToolButton()
        self.btn_none.setText("全不选")
        self.btn_none.setObjectName("detectLink")
        self.btn_none.clicked.connect(lambda: self.select_all(False))
        foot.addWidget(self.lbl_selected)
        foot.addStretch(1)
        foot.addWidget(self.btn_all)
        foot.addWidget(self.btn_none)
        layout.addWidget(footer)

    # ---------- 数据 ----------

    def set_objects(self, objects: list[WatermarkObject],
                    multi_doc: bool = False) -> None:
        self._objects = list(objects)
        self._rows.clear()
        self._multi_doc = multi_doc
        self._clear_content()

        has_objects = bool(objects)
        self.empty.setVisible(not has_objects)
        self.scroll.setVisible(has_objects)
        self.lbl_count.setText(f"{len(objects)} 个")

        if not has_objects:
            self.update_summary()
            return

        groups: dict[str, list[WatermarkObject]] = {}
        for obj in objects:
            groups.setdefault(obj.kind, []).append(obj)

        for kind in _KIND_ORDER:
            items = groups.get(kind, [])
            if not items:
                continue
            group_title = QLabel(f"{_KIND_TITLES[kind]}  {len(items)}")
            group_title.setObjectName("detectGroupTitle")
            self.content_layout.insertWidget(
                self.content_layout.count() - 1, group_title)
            for obj in items:
                row = DetectionRow(obj, multi_doc=self._multi_doc)
                row.toggled.connect(self._on_row_toggled)
                row.activated.connect(self.object_activated)
                self.content_layout.insertWidget(
                    self.content_layout.count() - 1, row)
                self._rows[obj.oid] = row
        self.update_summary()

    def clear(self) -> None:
        self.set_objects([], False)

    def _clear_content(self) -> None:
        while self.content_layout.count() > 1:
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _on_row_toggled(self, _oid: str, _checked: bool) -> None:
        self.update_summary()
        self.selection_changed.emit()

    def selected_objects(self) -> list[WatermarkObject]:
        return [obj for obj in self._objects
                if self._rows.get(obj.oid) is not None
                and self._rows[obj.oid].is_checked()]

    def selected_ids(self) -> set[str]:
        return {obj.oid for obj in self.selected_objects()}

    def has_selection(self) -> bool:
        return any(row.is_checked() for row in self._rows.values())

    def toggle(self, oid: str) -> None:
        row = self._rows.get(oid)
        if row is None or not row.obj.removable:
            return
        row.set_checked(not row.is_checked())
        self.update_summary()
        self.selection_changed.emit()

    def select_all(self, checked: bool) -> None:
        for row in self._rows.values():
            if row.obj.removable:
                row.set_checked(checked)
        self.update_summary()
        self.selection_changed.emit()

    def set_checked(self, oid: str, checked: bool) -> None:
        row = self._rows.get(oid)
        if row is not None:
            row.set_checked(checked)
            self.update_summary()
            self.selection_changed.emit()

    def focus_row(self, oid: str) -> None:
        row = self._rows.get(oid)
        if row is not None:
            self.scroll.ensureWidgetVisible(row, 0, 80)

    def update_summary(self) -> None:
        total = len(self._objects)
        selected = len(self.selected_objects())
        self.lbl_selected.setText(f"已选 {selected} / {total}")
        self.btn_all.setEnabled(bool(self._rows))
        self.btn_none.setEnabled(bool(self._rows))


class ExportConfirmDialog(QDialog):
    """导出前的汇总确认，风格对齐原型 04。"""

    def __init__(self, objects: list[WatermarkObject], doc_count: int,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("导出前确认")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setMaximumWidth(640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        header = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(svg_icon("remove_watermark").pixmap(QSize(44, 44)))
        header.addWidget(icon, 0, Qt.AlignTop)
        head_text = QVBoxLayout()
        head_text.setSpacing(3)
        title = QLabel(f"去除 {len(objects)} 个水印对象？")
        title.setObjectName("dialogTitle")
        sub = QLabel(
            f"将从 {doc_count} 个 PDF 中移除 {len(objects)} 个水印对象。"
        )
        sub.setObjectName("dialogSub")
        sub.setWordWrap(True)
        head_text.addWidget(title)
        head_text.addWidget(sub)
        header.addLayout(head_text, 1)
        layout.addLayout(header)

        stats = QHBoxLayout()
        stats.setSpacing(8)
        counts = {
            "text": sum(1 for o in objects if o.kind == "text"),
            "image": sum(1 for o in objects if o.kind == "image"),
            "annot": sum(1 for o in objects if o.kind == "annot"),
        }
        for kind in _KIND_ORDER:
            stats.addWidget(self._stat_card(_KIND_TITLES[kind], counts[kind]))
        layout.addLayout(stats)

        warn = QFrame()
        warn.setObjectName("warnBox")
        wrow = QHBoxLayout(warn)
        wrow.setContentsMargins(10, 8, 10, 8)
        wrow.setSpacing(7)
        wicon = QLabel()
        wicon.setPixmap(svg_icon("warning").pixmap(QSize(16, 16)))
        wrow.addWidget(wicon, 0, Qt.AlignTop)
        text = QLabel(
            "导出会另存为新文件，原 PDF 不会被修改。\n"
            "扫描件中已经烧录进像素的水印无法用本功能去除；"
            "重叠在正文上的文字水印会被安全跳过。"
        )
        text.setObjectName("warnText")
        text.setWordWrap(True)
        wrow.addWidget(text, 1)
        layout.addWidget(warn)

        keep = QLabel("✓  导出结果作为新图层置顶，原图层继续保留")
        keep.setObjectName("dialogKeep")
        layout.addWidget(keep)

        self.confirm = QCheckBox("我确认有权移除该水印，并自行承担相关责任")
        self.confirm.setObjectName("confirmCheck")
        layout.addWidget(self.confirm)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("dialogCancel")
        cancel.clicked.connect(self.reject)
        self.btn_export = QPushButton("去除并导出")
        self.btn_export.setObjectName("dialogPrimary")
        self.btn_export.setIcon(svg_icon("export"))
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self.accept)
        self.confirm.toggled.connect(self.btn_export.setEnabled)
        buttons.addWidget(cancel)
        buttons.addWidget(self.btn_export)
        layout.addLayout(buttons)

    @staticmethod
    def _stat_card(label: str, count: int) -> QFrame:
        card = QFrame()
        card.setObjectName("statCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(12, 8, 12, 8)
        box.setSpacing(1)
        value = QLabel(str(count))
        value.setObjectName("statValue")
        name = QLabel(label)
        name.setObjectName("statLabel")
        box.addWidget(value)
        box.addWidget(name)
        return card


class WatermarkRemoveModule(Module):
    """PDF 去水印。"""

    title = "PDF 去水印"
    accept_images = False
    empty_hint = "尚未添加 PDF"
    accept_filter = "PDF 文件 (*.pdf)"
    layer_show_index = False
    layer_trailing = "eye"
    layer_source_note = "原件"

    def __init__(self, status):
        super().__init__(status)
        self._objects: list[WatermarkObject] = []
        self._selected_ids: set[str] = set()
        self._eye_signature: tuple[str, ...] = ()
        self.files.show_title_pages = False
        self._current_source: str | None = None
        self._preview_path: str | None = None
        self._preview_page_count = 0
        self._pending_add_preview = False
        self._pick_mode = False
        self._overlay_oids: list[str] = []
        self._overlay_visible = True
        self._doc_page_cache: dict[str, int] = {}

        self._build_ribbon()
        self._build_detection_panel()
        self._wrap_center()
        self._connect_extra()
        self.preview.set_mode_labels("原图", "去除后")
        self.preview.set_always_step(True)
        self.preview.set_nav_visible(True)
        self.preview.set_file_mode_available(True)
        self.preview.set_overlay_toggle_visible(True)
        self.preview.overlay_toggled.connect(self._on_overlay_toggle)
        self.preview.canvas.object_clicked.connect(self._on_overlay_clicked)
        self.preview.show_message(
            "添加 PDF 后，在功能区输入水印文字或点「自动检测」。"
        )
        self._sync_buttons()
        self._update_empty_state()

    # ---------- Ribbon ----------

    def _build_ribbon(self) -> None:
        self.edit_query = QLineEdit()
        self.edit_query.setPlaceholderText("输入水印文字，例如：机密文件")
        self.edit_query.setFixedWidth(158)
        self.edit_query.setMinimumHeight(28)

        self.btn_find = RibbonButton("查找", "zoom_in")
        self.btn_auto = RibbonButton("自动检测", "remove_watermark")
        self.btn_pick = RibbonButton("在预览中点选", "single")
        self.btn_find.clicked.connect(self._find_query)
        self.btn_auto.clicked.connect(self._auto_detect)
        self.btn_pick.clicked.connect(self._toggle_pick_mode)
        self.edit_query.returnPressed.connect(self._find_query)

        self.cmb_range = ChoiceField("", ["全部 0 页"], "全部 0 页")
        self.cmb_range.setMinimumWidth(96)
        self.chk_all = QCheckBox("应用到所有 PDF")
        self.chk_all.setObjectName("ribbonCheck")
        self.chk_all.setChecked(True)
        self.chk_all.toggled.connect(self._on_scope_changed)
        range_cell = QWidget()
        range_box = QVBoxLayout(range_cell)
        range_box.setContentsMargins(0, 0, 0, 0)
        range_box.setSpacing(1)
        range_box.addWidget(self.cmb_range)
        range_box.addWidget(self.chk_all)

        self.btn_preview = RibbonButton("预览去除效果", "eye")
        self.btn_export = RibbonButton("去除并导出", "remove_watermark", primary=True)
        self.btn_preview.clicked.connect(self._preview_removal)
        self.btn_export.clicked.connect(self._export_removal)

        self.btn_remove_source = RibbonButton("移除", "remove")
        self.btn_remove_source.clicked.connect(self.files.remove_selected)
        self.btn_clear = RibbonButton("清空", "clear")
        self.btn_clear.clicked.connect(self.files.clear_all)
        self.set_band(RibbonBand(
            [
                button_group("文件", [self._ribbon_add_button(),
                                      self.btn_remove_source, self.btn_clear]),
                control_row_group("水印对象", [
                    self.edit_query, self.btn_find, self.btn_auto, self.btn_pick,
                ]),
                stack_group("范围", [("页范围", range_cell)]),
            ],
            trailing=button_group("导出", [self.btn_preview, self.btn_export]),
        ))

    def _ribbon_add_button(self) -> RibbonButton:
        # 侧栏已经有一个「+」，功能区仍保留完整按钮，便于大屏快速操作。
        self.btn_add = RibbonButton("添加 PDF", "open")
        self.btn_add.clicked.connect(self.add_files)
        return self.btn_add

    def _build_detection_panel(self) -> None:
        self.detect_panel = DetectionPanel()
        self.detect_panel.selection_changed.connect(self._on_selection_changed)
        self.detect_panel.object_activated.connect(self._focus_object)

    def _wrap_center(self) -> None:
        self._inner_center = self.center
        self._left_stack = QStackedWidget()

        self._empty = EmptyState(
            "remove_watermark",
            "打开 PDF 开始去除水印",
            "按文字、图片或注释对象删除，不做整页栅格化；\n"
            "扫描件里烧录进像素的水印不会被误删。",
            "选择 PDF",
        )
        self._empty.action.connect(self.add_files)
        empty_wrap = QWidget()
        box = QVBoxLayout(empty_wrap)
        box.setContentsMargins(24, 24, 24, 24)
        box.addStretch(1)
        box.addWidget(self._empty, 0, Qt.AlignHCenter)
        box.addStretch(1)
        self._left_stack.addWidget(empty_wrap)
        self._left_stack.addWidget(self._inner_center)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.addWidget(self._left_stack)
        splitter.addWidget(self.detect_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([940, 300])
        self.center = splitter

    def _connect_extra(self) -> None:
        # layer_activated / page_step 已由基类连接到本类覆盖后的方法，
        # 这里只补文件列表变化后的范围刷新。
        self.files.files_changed.connect(self._refresh_source_info)

    # ---------- 文件与范围 ----------

    def _source_pdfs(self) -> list[str]:
        return [p for p in self.files.source_paths()
                if p.lower().endswith(".pdf")]

    def _scope_paths(self) -> list[str]:
        sources = self._source_pdfs()
        if self.chk_all.isChecked():
            return sources
        if self._current_source in sources:
            return [self._current_source]
        return sources[:1]

    def _refresh_source_info(self) -> None:
        sources = self._source_pdfs()
        old = self._current_source
        if self._current_source not in sources:
            self._current_source = sources[0] if sources else None
            self._page_index = 0
            self._preview_path = None
            if old and old not in sources:
                self._objects = []
                self._selected_ids = set()
                self.detect_panel.clear()
        if self._current_source:
            self._overlay_visible = self.files.is_visible(self._current_source)
        else:
            self._overlay_visible = True
        self.preview.set_overlay_toggle_checked(self._overlay_visible)
        self._refresh_range()
        self._sync_buttons()

    def _refresh_range(self) -> None:
        path = self._current_source
        count = 0
        if path:
            count = self._doc_page_cache.get(path, 0)
            if not count:
                try:
                    with open_pdf(path) as doc:
                        count = doc.page_count
                except Exception:
                    count = 0
            self._doc_page_cache[path] = count
        items = [f"全部 {count} 页"] if count else ["全部 0 页"]
        if self.cmb_range._items != items:
            self.cmb_range.setItems(items)

    def _on_scope_changed(self, _checked: bool) -> None:
        self._refresh_buttons()

    def _update_empty_state(self) -> None:
        if not hasattr(self, "_left_stack"):
            return
        has = bool(self._source_pdfs())
        self._left_stack.setCurrentIndex(1 if has else 0)
        if not has:
            self.detect_panel.clear()
            self.preview.show_message("添加 PDF 后开始检测水印")
        self._refresh_range()

    def _on_files_changed(self) -> None:
        super()._on_files_changed()
        sources = tuple(self._source_pdfs())
        if sources != self._eye_signature:
            self._eye_signature = sources
            active = self._current_source if self._current_source in sources else (
                sources[0] if sources else None)
            self._sync_source_eyes(active)
        self._refresh_source_info()
        if self._current_source:
            self.preview.set_file_mode_available(True)
        self._update_status_text()

    def _sync_source_eyes(self, active: str | None) -> None:
        """只有当前源文档显示检测框；其余图层默认闭眼，和原型一致。"""
        for path in self._source_pdfs():
            self.files.set_visible(path, path == active)

    # ---------- 检测 ----------

    def _find_query(self) -> None:
        query = self.edit_query.text().strip()
        if not query:
            self._auto_detect()
            return
        if len(query) < 2:
            QMessageBox.information(self.center, "提示", "请至少输入两个字的水印文字。")
            return
        self._detect(query=query, auto=False)

    def _auto_detect(self) -> None:
        self.edit_query.clear()
        self._detect(query="", auto=True)

    def _detect(self, query: str, auto: bool) -> None:
        paths = self._scope_paths()
        if not paths:
            QMessageBox.warning(self.center, "提示", "请先添加一个 PDF 文件。")
            return
        objects: list[WatermarkObject] = []
        failed: list[str] = []
        for path in paths:
            try:
                doc = open_pdf(path)
                try:
                    found = WatermarkDetector(
                        query=query, auto=auto, pages=None
                    ).detect(doc)
                    for obj in found:
                        obj.doc_path = path
                    objects.extend(found)
                finally:
                    doc.close()
            except Exception as exc:  # noqa: BLE001 - 展示给用户
                failed.append(f"{os.path.basename(path)}：{exc}")

        self._objects = objects
        multi = len(paths) > 1
        self.detect_panel.set_objects(objects, multi_doc=multi)
        self._selected_ids = self.detect_panel.selected_ids()
        self._preview_path = None
        self._pending_add_preview = False
        if self._preview_mode == MODE_FILE:
            self._set_preview_mode(MODE_EFFECT)
        self._update_overlays()
        self._sync_buttons()
        self._update_status_text()
        if objects:
            self._emit_status(
                f"检测到 {len(objects)} 个水印对象，已选 {len(self._selected_ids)} 个"
            )
        else:
            self._emit_status("未检测到匹配的水印对象")
        if failed:
            QMessageBox.warning(self.center, "部分文件检测失败", "\n".join(failed))

    def _on_selection_changed(self) -> None:
        self._selected_ids = self.detect_panel.selected_ids()
        self._update_overlays()
        self._sync_buttons()
        self._update_status_text()
        if self._preview_mode == MODE_FILE and self._preview_path:
            # 已有预览时，勾选变化后让「去除后」重新生成，避免看到旧结果。
            self._preview_debounce.trigger()

    def _toggle_pick_mode(self) -> None:
        if not self._objects:
            self._auto_detect()
        self._pick_mode = not self._pick_mode
        self.btn_pick.setProperty("ribbonActive", "true" if self._pick_mode else "false")
        self.btn_pick.style().unpolish(self.btn_pick)
        self.btn_pick.style().polish(self.btn_pick)
        if self._pick_mode:
            self._emit_status("点选模式：在中间预览里点击蓝色框，切换是否移除该对象")
        else:
            self._emit_status("已退出点选模式")

    def _on_overlay_clicked(self, index: int) -> None:
        if not (0 <= index < len(self._overlay_oids)):
            return
        oid = self._overlay_oids[index]
        self.detect_panel.toggle(oid)
        self.detect_panel.focus_row(oid)
        self._emit_status(
            "已取消该对象" if oid not in self._selected_ids else "已选中该对象"
        )

    def _focus_object(self, oid: str) -> None:
        """点击右侧结果时跳到对象所在 PDF/页，并把检测框显示出来。"""
        obj = next((o for o in self._objects if o.oid == oid), None)
        if obj is None:
            return
        if obj.doc_path and obj.doc_path != self._current_source:
            self._current_source = obj.doc_path
            self._preview_path = None
            self._set_preview_mode(MODE_EFFECT)
            self._refresh_range()
            self._sync_buttons()
        self._page_index = max(0, obj.page)
        if self._preview_mode != MODE_EFFECT:
            self._set_preview_mode(MODE_EFFECT)
        self.detect_panel.focus_row(oid)
        self._update_status_text()
        self._schedule_preview()
        self._emit_status(
            f"已定位到第 {obj.page_no} 页的{_KIND_TITLES.get(obj.kind, '水印')}"
        )

    # ---------- 预览 ----------

    def _render_preview(self) -> None:
        if self._preview_mode == MODE_FILE:
            self._render_removed_preview()
        else:
            self._render_original_preview()

    def _render_original_preview(self) -> None:
        path = self._current_source
        if not path or not os.path.isfile(path):
            self.preview.show_message("添加 PDF 后开始检测水印")
            return
        index = max(0, self._page_index)
        scale = self._render_scale()
        self.preview.show_hint(f"正在读取 {os.path.basename(path)} …")

        def job(_progress):
            doc = open_pdf(path)
            try:
                idx = min(index, doc.page_count - 1)
                page = doc[idx]
                pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                                      alpha=False)
                return (pix.tobytes("png"), idx, doc.page_count,
                        page.rect.width, page.rect.height)
            finally:
                doc.close()

        self._spawn_preview(job, MODE_EFFECT)

    def _on_original_ok(self, payload) -> None:
        data, index, count, page_w, page_h = payload
        self._page_index, self._page_count = index, count
        name = os.path.basename(self._current_source or "")
        self.preview.set_target(name)
        self.preview.set_page_info(f"{index + 1} / {count}")
        self.preview.show_png(data, "", page_pt=(page_w, page_h))
        if any(o.doc_path == self._current_source for o in self._objects):
            self.preview.set_footer_chip("原图 · 蓝色框为已选对象，灰色为保留")
        else:
            self.preview.set_footer_chip("")
        self._update_overlays()

    def _render_removed_preview(self) -> None:
        source = self._current_source
        if not source or not os.path.isfile(source):
            self.preview.show_message("添加 PDF 后开始检测水印")
            return
        selected = self._selected_for_doc(source)
        if not selected:
            self.preview.show_message(
                "还没有勾选要移除的对象\n先在右侧勾选，或点「自动检测」。"
            )
            return
        index = max(0, self._page_index)
        scale = self._render_scale()
        add_layer = self._pending_add_preview
        stem = _stem(source)
        self.preview.show_hint("正在生成去除后的预览…")

        def job(progress):
            doc = open_pdf(source)
            temp_path = ""
            try:
                stats = WatermarkRemover().remove(doc, selected)
                idx = min(index, doc.page_count - 1)
                page = doc[idx]
                pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                                      alpha=False)
                if add_layer:
                    fd, temp_path = tempfile.mkstemp(
                        prefix=f"{stem}_预览_", suffix=".pdf")
                    os.close(fd)
                    save_pdf(doc, temp_path)
                return (pix.tobytes("png"), idx, doc.page_count,
                        page.rect.width, page.rect.height,
                        stats.as_tuple(), temp_path)
            finally:
                doc.close()

        self._spawn_preview(job, MODE_FILE)

    def _on_removed_ok(self, payload) -> None:
        (data, index, count, page_w, page_h,
         stats_tuple, temp_path) = payload
        self._page_index, self._page_count = index, count
        self._preview_page_count = count
        text_n, image_n, annot_n, skipped_n, _pages = stats_tuple
        removed = text_n + image_n + annot_n
        source = self._current_source or ""
        name = f"{_output_stem(source)}.pdf" if source else "去除后预览"
        self.preview.set_target(name)
        self.preview.set_page_info(f"{index + 1} / {count}")
        self.preview.show_png(data, "", page_pt=(page_w, page_h))
        self.preview.set_header_note(f"去除后预览 · 已移除 {removed} 个对象")
        self.preview.set_footer_chip("")
        self.preview.canvas.set_overlays([])
        self._pending_add_preview = False
        if temp_path and os.path.isfile(temp_path):
            if self._preview_path and self._preview_path != temp_path:
                self.files.remove_path(self._preview_path)
            self._preview_path = temp_path
            self.files.add_result(
                temp_path, "预览", "预览中 · 未导出",
                title=f"{_output_stem(source)}.pdf")
            self._sync_source_eyes(None)
        if skipped_n:
            self._emit_status(
                f"预览已移除 {removed} 个对象，{skipped_n} 个对象因安全原因跳过"
            )
        else:
            self._emit_status(
                f"这是预览，导出后原文件不会被修改 · 已移除 {removed} 个对象"
            )

    def _preview_removal(self) -> None:
        if not self._current_source:
            return
        if not self._selected_ids:
            QMessageBox.information(self.center, "提示", "请先勾选要移除的水印对象。")
            return
        self._pending_add_preview = True
        self._set_preview_mode(MODE_FILE)
        self._page_index = 0
        self._preview_debounce.trigger()

    def _on_preview_mode(self, mode: str) -> None:
        if mode == MODE_FILE and not self._source_pdfs():
            self._set_preview_mode(MODE_EFFECT)
            return
        if mode == MODE_FILE and not self._selected_ids:
            QMessageBox.information(
                self.center, "提示", "请先在右侧勾选要移除的水印对象。")
            self._set_preview_mode(MODE_EFFECT)
            return
        self._set_preview_mode(mode)
        self._schedule_preview()

    def _set_preview_mode(self, mode: str) -> None:
        super()._set_preview_mode(mode)
        # 去水印模块在原图模式也要能翻页，所以导航条始终显示。
        self.preview.set_nav_visible(True)
        if mode == MODE_EFFECT:
            name = os.path.basename(self._current_source or "")
            self.preview.set_target(name)
        else:
            self.preview.set_target(
                f"{_output_stem(self._current_source or '')}.pdf")
        if self._page_count:
            self.preview.set_page_info(f"{self._page_index + 1} / {self._page_count}")

    def _on_preview_ok(self, payload) -> None:
        if self._preview_kind == MODE_EFFECT and self._preview_mode == MODE_EFFECT:
            self._on_original_ok(payload)
        elif self._preview_kind == MODE_FILE and self._preview_mode == MODE_FILE:
            self._on_removed_ok(payload)

    def _on_layer_activated(self, path: str) -> None:
        if self.files.is_result(path):
            self._preview_path = path
            self._pending_add_preview = False
            self._page_index = 0
            self._sync_source_eyes(None)
            self._set_preview_mode(MODE_FILE)
            self._sync_buttons()
            self._schedule_preview()
            return
        sources = self._source_pdfs()
        if path not in sources:
            return
        self._current_source = path
        self._preview_path = None
        self._page_index = 0
        self._sync_source_eyes(path)
        self._overlay_visible = True
        self.preview.set_overlay_toggle_checked(True)
        if not self.chk_all.isChecked():
            self._objects = []
            self.detect_panel.clear()
            self._selected_ids = set()
        self._set_preview_mode(MODE_EFFECT)
        self._update_empty_state()
        self._sync_buttons()
        self._schedule_preview()

    def _on_page_step(self, step: int) -> None:
        if self._page_count <= 1:
            return
        index = min(max(self._page_index + step, 0), self._page_count - 1)
        if index == self._page_index:
            return
        self._page_index = index
        self._schedule_preview()

    def _update_overlays(self) -> None:
        if self._preview_mode != MODE_EFFECT:
            self._overlay_oids = []
            self.preview.canvas.set_overlays([])
            return
        path = self._current_source
        if not path:
            self._overlay_oids = []
            self.preview.canvas.set_overlays([])
            return
        self._overlay_oids = []
        overlays = []
        for obj in self._objects:
            if obj.doc_path != path or obj.page != self._page_index:
                continue
            self._overlay_oids.append(obj.oid)
            overlays.append({
                "bbox": obj.bbox,
                "kind": obj.kind,
                "selected": obj.oid in self._selected_ids,
            })
        self.preview.canvas.set_overlays(overlays if self._overlay_visible else [])
        self._update_status_text()

    def _on_visibility_toggled(self, path: str) -> None:
        if path == self._current_source:
            self._overlay_visible = self.files.is_visible(path)
            self.preview.set_overlay_toggle_checked(self._overlay_visible)
            self._update_overlays()

    def _on_overlay_toggle(self, checked: bool) -> None:
        self._overlay_visible = bool(checked)
        if self._current_source:
            self.files.set_visible(self._current_source, checked)
        self._update_overlays()

    # ---------- 选择与导出 ----------

    def _selected_for_doc(self, path: str) -> list[WatermarkObject]:
        return [o for o in self._objects
                if o.doc_path == path and o.oid in self._selected_ids and o.removable]

    def _selected_by_doc(self) -> dict[str, list[WatermarkObject]]:
        out: dict[str, list[WatermarkObject]] = {}
        for obj in self._objects:
            if obj.oid in self._selected_ids and obj.removable:
                out.setdefault(obj.doc_path, []).append(obj)
        return out

    def _export_removal(self) -> None:
        by_doc = self._selected_by_doc()
        if not by_doc:
            QMessageBox.information(self.center, "提示", "请先勾选要移除的水印对象。")
            return
        objects = [o for items in by_doc.values() for o in items]
        dialog = ExportConfirmDialog(objects, len(by_doc), self.center)
        if dialog.exec() != QDialog.Accepted:
            return

        outputs: list[str] = []
        if len(by_doc) == 1:
            source = next(iter(by_doc))
            default = os.path.join(
                os.path.dirname(source), f"{_output_stem(source)}.pdf")
            path, _ = QFileDialog.getSaveFileName(
                self.center, "导出去除水印后的 PDF", default, "PDF 文件 (*.pdf)")
            if not path:
                return
            if os.path.abspath(path) == os.path.abspath(source):
                QMessageBox.warning(self.center, "不能覆盖原文件",
                                    "请选择一个新文件名，原文件不会被修改。")
                return
            outputs = [path]
        else:
            folder = QFileDialog.getExistingDirectory(
                self.center, "选择导出文件夹")
            if not folder:
                return
            used: set[str] = set()
            for source in by_doc:
                base = _output_stem(source)
                candidate = os.path.join(folder, base + ".pdf")
                n = 2
                while candidate in used or os.path.exists(candidate):
                    candidate = os.path.join(folder, f"{base}_{n}.pdf")
                    n += 1
                used.add(candidate)
                outputs.append(candidate)

        def job(progress):
            total = len(by_doc)
            sum_text = sum_image = sum_annot = 0
            for i, (source, items) in enumerate(by_doc.items()):
                progress(i, total, f"正在处理 {os.path.basename(source)} …")
                doc = open_pdf(source)
                try:
                    stats = WatermarkRemover().remove(doc, items)
                    save_pdf(doc, outputs[i])
                    sum_text += stats.text
                    sum_image += stats.image
                    sum_annot += stats.annot
                finally:
                    doc.close()
                progress(i + 1, total, f"已完成 {i + 1}/{total}")
            return outputs, (sum_text, sum_image, sum_annot)

        def report(result):
            _files, (text_n, image_n, annot_n) = result
            return (
                f"已移除 {text_n + image_n + annot_n} 个水印对象"
                f"（文字 {text_n} / 图片 {image_n} / 注释 {annot_n}），"
                f"共导出 {len(outputs)} 个 PDF。"
            )

        self._run_task(
            job, "去除水印完成", report, output_path=outputs[0] if outputs else "",
            results=lambda result: result[0],
        )

    def _load_results(self, paths: list[str]) -> list[str]:
        # 导出成功后，临时预览图层没有保留价值，先移除再放正式结果。
        if self._preview_path and os.path.isfile(self._preview_path):
            if self._preview_path not in paths:
                self.files.remove_path(self._preview_path)
            self._preview_path = None
        loaded = []
        for path in paths:
            if path and os.path.isfile(path):
                if self.files.add_result(path, "结果", "已导出 · 原文件未改动"):
                    loaded.append(path)
        return loaded

    # ---------- 状态 ----------

    def _refresh_buttons(self) -> None:
        has_docs = bool(self._source_pdfs())
        busy = self._busy()
        has_sel = bool(self._selected_ids)
        for btn in (self.btn_find, self.btn_auto, self.btn_pick):
            btn.setEnabled(has_docs and not busy)
        self.btn_remove_source.setEnabled(has_docs and not busy)
        self.btn_clear.setEnabled(has_docs and not busy)
        self.btn_preview.setEnabled(has_docs and has_sel and not busy)
        self.btn_export.setEnabled(has_docs and has_sel and not busy)
        if hasattr(self, "btn_add"):
            self.btn_add.setEnabled(not busy)
        if hasattr(self, "btn_side_add"):
            self.btn_side_add.setEnabled(not busy)
        if hasattr(self, "chk_all"):
            self.chk_all.setEnabled(has_docs and not busy)

    def _update_status_text(self) -> None:
        if not hasattr(self, "preview"):
            return
        path = self._current_source
        if not path:
            self.preview.set_header_note("")
            self.preview.set_footer_chip("")
            return
        objects = [o for o in self._objects if o.doc_path == path]
        sel = sum(1 for o in objects if o.oid in self._selected_ids)
        if self._preview_mode == MODE_EFFECT:
            if objects:
                self.preview.set_header_note(
                    f"检测到 {len(objects)} 个对象 · 已选 {sel} 个")
            else:
                self.preview.set_header_note("点击「自动检测」查找水印对象")

    def status_text(self) -> str:
        if not self._current_source:
            return "尚未添加 PDF"
        page = f"第 {self._page_index + 1} 页" if self._page_count else "第 1 页"
        mode = "原图" if self._preview_mode == MODE_EFFECT else "去除后"
        return f"{os.path.basename(self._current_source)} · {page} · {mode}"
