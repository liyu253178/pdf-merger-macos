"""界面里可复用的小控件：图层列表与预览区。

图层列表（LayerList）
--------------------
把「已打开的文件」和「处理产生的结果」统一当成图层来管理，参考 GIS 的图层
面板：结果图层插到最上方、原图层原封不动保留，双击任一图层即把它设为
「当前预览对象」。侧边栏只有 200 来像素宽，长文件名若换行会把列表撑得乱
七八糟，所以这里用委托统一画成单行中间省略，并在最左留出一条固定宽度的
状态列画「当前预览对象」的圆点 —— 状态列始终占位，切换时文字不会左右跳动。

预览区（PreviewPane）
--------------------
两种模式并存：
* 「效果预览」按当前模块的参数渲染（合并的拼版效果、水印的加印效果）；
* 「文件预览」原样显示某个图层的页面，用于多个 PDF 之间快速比对。
顶部一行放模式切换、目标文件名与翻页控件。
"""

from __future__ import annotations

import os

from PySide6.QtCore import (
    QElapsedTimer, QPoint, QPointF, QRect, QRectF, QSize, QSizeF, Qt, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QIcon, QPainter, QPalette, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMenu, QPushButton, QSizePolicy, QStyle,
    QStyledItemDelegate, QStyleOptionViewItem, QToolButton, QVBoxLayout,
    QWidget,
)

from .icons import svg_icon

SUPPORTED_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff",
                      ".bmp", ".webp", ".jfif")

# 图层条目上的自定义数据
ROLE_PATH = Qt.UserRole            # 文件绝对路径
ROLE_RESULT = Qt.UserRole + 1      # 是否为处理产生的结果图层
ROLE_FOCUS = Qt.UserRole + 2       # 是否为当前预览对象
ROLE_NOTE = Qt.UserRole + 3        # 第二行说明：页数 / 原件 / 结果
ROLE_KIND = Qt.UserRole + 4        # pdf / image / blank
ROLE_VISIBLE = Qt.UserRole + 5     # 眼睛状态
ROLE_TITLE = Qt.UserRole + 6       # 自定义显示名（临时预览文件用）

RESULT_COLOR = "#1d4ed8"           # 结果图层用强调色，和源文件区分开
FOCUS_COLOR = "#2563eb"
MARK_WIDTH = 14                    # 左侧状态列宽度，始终预留

MODE_EFFECT = "effect"             # 参数效果预览
MODE_FILE = "file"                 # 单个文件原样预览

# ---------- 预览渲染参数 ----------
# 预览按「显示需要的像素 × 超采样」渲染，而不是固定 72 dpi。
# 固定 72 dpi 时 A4 只有 595 px 宽：拼版发票里 3pt 出头的小字落到画布上
# 只剩 3 个像素，必然糊成一片 —— 用户说的"预览不清晰"就出在这里。
SUPERSAMPLE = 1.35            # 超采样系数：降采样显示远比放大显示锐利
MAX_RENDER_SCALE = 6.0        # 上限 ≈ 432 dpi，再高肉眼无感、只拖慢渲染
MIN_RENDER_SCALE = 0.15
ZOOM_STEPS = (1.0, 1.5, 2.0, 3.0)   # 绝对缩放：1.0 即 100%，1pt = 1 逻辑像素
PAN_STEP = 64                 # 放大后滚轮每次平移的逻辑像素
PAGE_STEP_COOLDOWN_MS = 90    # 翻页冷却：触控板一次轻扫会连发十几个滚轮事件
INNER_PAD = 8                 # 画布内边距


class _LayerDelegate(QStyledItemDelegate):
    """统一图层条目：序号 + 文件图标 + 两行文字 + 右侧拖拽手柄/眼睛。"""

    def __init__(self, view: "LayerList"):
        super().__init__(view)
        self._view = view

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(180, 44)

    def paint(self, painter, option, index):  # noqa: N802 - Qt 命名
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        row_rect = QRectF(option.rect).adjusted(3, 2, -3, -2)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        focus = bool(index.data(ROLE_FOCUS))
        has_focus = any(
            self._view.item(i).data(ROLE_FOCUS)
            for i in range(self._view.count())
        ) if self._view.count() else False
        has_selection = bool(self._view.selectedItems())
        current = self._view.currentRow() == index.row()
        highlight = focus or (selected and not has_focus) or (
            current and not has_focus and not has_selection)
        if highlight:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#E8F0FE"))
            painter.drawRoundedRect(row_rect, 6, 6)
            painter.setBrush(QColor("#2563EB"))
            painter.drawRoundedRect(
                QRectF(row_rect.left(), row_rect.top() + 6, 3,
                       row_rect.height() - 12), 1.5, 1.5)

        show_index = self._view.show_index
        x = row_rect.left() + (9 if show_index else 7)
        if show_index:
            painter.setPen(QColor("#8A93A3"))
            font = QFont(opt.font)
            font.setPointSize(8)
            painter.setFont(font)
            painter.drawText(QRectF(x, row_rect.top(), 18, row_rect.height()),
                             Qt.AlignLeft | Qt.AlignVCenter, str(index.row() + 1))
            x += 22

        kind = index.data(ROLE_KIND) or "pdf"
        icon_name = {"image": "image", "blank": "blank"}.get(kind, "pdf")
        icon = svg_icon(icon_name)
        icon.paint(painter, QRect(int(x), int(row_rect.center().y() - 9), 18, 18))

        text_x = x + 25
        trailing_w = 25
        text_w = max(20, row_rect.right() - text_x - trailing_w)
        name = index.data(ROLE_TITLE) or index.data(Qt.DisplayRole) or ""
        note = index.data(ROLE_NOTE) or ""
        is_result = bool(index.data(ROLE_RESULT))

        font = QFont(opt.font)
        font.setPointSize(9)
        font.setWeight(QFont.Weight.DemiBold if highlight or is_result else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(RESULT_COLOR if is_result else "#25303F"))
        name_rect = QRectF(text_x, row_rect.top() + 5, text_w, 17)
        painter.drawText(
            name_rect, Qt.AlignLeft | Qt.AlignVCenter,
            QFontMetrics(font).elidedText(name, Qt.ElideMiddle, int(text_w)),
        )

        font2 = QFont(opt.font)
        font2.setPointSize(8)
        painter.setFont(font2)
        painter.setPen(QColor(RESULT_COLOR if is_result else "#8A93A3"))
        note_rect = QRectF(text_x, row_rect.top() + 21, text_w, 15)
        painter.drawText(
            note_rect, Qt.AlignLeft | Qt.AlignVCenter,
            QFontMetrics(font2).elidedText(note, Qt.ElideRight, int(text_w)),
        )

        # 右侧手柄 / 眼睛 / 编辑状态
        tx = row_rect.right() - 21
        ty = row_rect.center().y() - 8
        if self._view.trailing == "edit":
            svg_icon("edit" if focus else "eye_off").paint(
                painter, QRect(int(tx), int(ty), 16, 16))
        elif self._view.trailing == "eye":
            visible = bool(index.data(ROLE_VISIBLE))
            svg_icon("eye" if visible else "eye_off").paint(
                painter, QRect(int(tx), int(ty), 16, 16))
        else:
            svg_icon("drag").paint(painter, QRect(int(tx), int(ty), 16, 16))

        painter.setPen(QColor("#F0F2F5"))
        painter.drawLine(QPointF(row_rect.left() + 3, row_rect.bottom()),
                         QPointF(row_rect.right() - 3, row_rect.bottom()))
        painter.restore()


class LayerList(QListWidget):
    """图层列表：源文件与处理结果并列，支持拖入、拖拽排序、双击切换预览对象。"""

    files_changed = Signal()
    layer_activated = Signal(str)       # 当前预览对象发生变化
    visibility_toggled = Signal(str)    # 点击右侧眼睛

    def __init__(self, parent=None, accept_images: bool = True):
        super().__init__(parent)
        self._accept_images = accept_images
        self.show_index = True
        self.show_title_pages = True
        self.trailing = "drag"
        self.source_note = ""
        self.setObjectName("fileList")
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setAcceptDrops(True)
        self.setAlternatingRowColors(False)
        self.setUniformItemSizes(True)
        self.setSpacing(0)
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setItemDelegate(_LayerDelegate(self))
        self.setToolTip("可直接拖入文件；双击任一图层即把它设为当前预览对象")
        self.model().rowsMoved.connect(self._on_rows_moved)
        self.itemDoubleClicked.connect(self._on_double_clicked)

    def set_trailing(self, kind: str) -> None:
        self.trailing = kind
        self.viewport().update()

    def set_show_index(self, show: bool) -> None:
        self.show_index = show
        self.viewport().update()

    def set_note(self, path: str, note: str) -> None:
        item = self._item_for(path)
        if item is None:
            return
        item.setData(ROLE_NOTE, note)
        self.viewport().update()

    def set_source_note(self, note: str) -> None:
        self.source_note = note
        for i in range(self.count()):
            item = self.item(i)
            if not item.data(ROLE_RESULT):
                item.setData(ROLE_NOTE, self._note_for(item.data(ROLE_PATH), ""))
        self.viewport().update()

    def _on_rows_moved(self, *_args) -> None:
        self.files_changed.emit()

    # ---------- 数据访问 ----------

    def paths(self) -> list[str]:
        return [self.item(i).data(ROLE_PATH) for i in range(self.count())]

    def selected_paths(self) -> list[str]:
        return [item.data(ROLE_PATH) for item in self.selectedItems()]

    def focus_path(self) -> str | None:
        """当前预览对象（被双击选中的图层）的路径。"""
        for i in range(self.count()):
            item = self.item(i)
            if item.data(ROLE_FOCUS):
                return item.data(ROLE_PATH)
        return None

    def _item_for(self, path: str) -> QListWidgetItem | None:
        for i in range(self.count()):
            item = self.item(i)
            if item.data(ROLE_PATH) == path:
                return item
        return None

    def _accepts(self, path: str) -> bool:
        if not os.path.isfile(path) or not path.lower().endswith(SUPPORTED_SUFFIXES):
            return False
        return self._accept_images or path.lower().endswith(".pdf")

    def _kind_for(self, path: str) -> str:
        return "pdf" if path.lower().endswith(".pdf") else "image"

    def _note_for(self, path: str, tag: str = "") -> str:
        if tag:
            return tag
        if not path.lower().endswith(".pdf"):
            return "图片 · 1 页"
        pages = 0
        try:
            import pymupdf
            with pymupdf.open(path) as doc:
                pages = doc.page_count
        except Exception:
            pages = 0
        note = f"{pages} 页" if pages else "PDF"
        if self.source_note:
            note += f" · {self.source_note}"
        return note

    def _make_item(self, path: str, tag: str = "", note: str = "") -> QListWidgetItem:
        item = QListWidgetItem(self._label_for(path, tag))
        item.setData(ROLE_PATH, path)
        item.setData(ROLE_RESULT, bool(tag))
        item.setData(ROLE_NOTE, note or self._note_for(path, tag))
        item.setData(ROLE_KIND, self._kind_for(path))
        item.setData(ROLE_VISIBLE, True)
        item.setSizeHint(QSize(180, 44))
        item.setToolTip(path)
        return item

    # ---------- 增删 ----------

    def add_paths(self, paths: list[str], to_top: bool = False) -> int:
        """添加文件图层，自动去重。返回实际新增数量。"""
        existing = set(self.paths())
        added = 0
        for path in paths:
            if path in existing or not self._accepts(path):
                continue
            item = self._make_item(path)
            if to_top:
                self.insertItem(added, item)
            else:
                self.addItem(item)
            existing.add(path)
            added += 1
        if added:
            if self.currentRow() < 0:
                self.setCurrentRow(0)
            self.clearSelection()
            self.viewport().update()
            self.files_changed.emit()
        return added

    def add_result(self, path: str, tag: str = "结果", note: str = "",
                   title: str = "") -> bool:
        """把处理结果作为新图层插到最上方，并设为当前预览对象。

        已有同名图层时只更新它的页数信息并挪到最上方，不会产生重复条目；
        其它图层一律保持原位置，不被覆盖也不被删除。
        """
        if not os.path.isfile(path):
            return False

        item = self._item_for(path)
        if item is None:
            item = self._make_item(path, tag, note)
            self.insertItem(0, item)
        else:
            row = self.row(item)
            item.setText(self._label_for(path, tag))
            item.setData(ROLE_NOTE, note or self._note_for(path, tag))
            item.setData(ROLE_KIND, self._kind_for(path))
            if row != 0:
                # takeItem 会让 Qt 顺手改当前项，这里先静音省掉无谓的信号
                self.blockSignals(True)
                try:
                    taken = self.takeItem(row)
                    self.insertItem(0, taken)
                finally:
                    self.blockSignals(False)

        item.setData(ROLE_RESULT, True)
        item.setData(ROLE_VISIBLE, True)
        item.setData(ROLE_TITLE, title or "")
        item.setForeground(QColor(RESULT_COLOR))
        self.setCurrentItem(item)
        self.clearSelection()
        self._apply_focus(item, notify=False)
        self.viewport().update()
        self.files_changed.emit()
        self.layer_activated.emit(path)
        return True

    def remove_selected(self) -> None:
        rows = sorted((self.row(i) for i in self.selectedItems()), reverse=True)
        if not rows:
            return
        focus = self.focus_path()
        for row in rows:
            self.takeItem(row)
        if focus is not None and focus not in set(self.paths()):
            self._clear_focus()
        self.files_changed.emit()

    def clear_all(self) -> None:
        if not self.count():
            return
        self.clear()
        self._clear_focus()
        self.files_changed.emit()

    # ---------- 当前预览对象 ----------

    def set_focus(self, path: str, notify: bool = True) -> bool:
        item = self._item_for(path)
        if item is None:
            return False
        self._apply_focus(item, notify=notify)
        return True

    def first_pdf(self) -> str | None:
        for path in self.paths():
            if path.lower().endswith(".pdf"):
                return path
        return None

    def source_paths(self) -> list[str]:
        """非结果图层（去掉预览/导出结果）。"""
        return [self.item(i).data(ROLE_PATH) for i in range(self.count())
                if not self.item(i).data(ROLE_RESULT)]

    def result_paths(self) -> list[str]:
        return [self.item(i).data(ROLE_PATH) for i in range(self.count())
                if self.item(i).data(ROLE_RESULT)]

    def is_result(self, path: str) -> bool:
        item = self._item_for(path)
        return bool(item.data(ROLE_RESULT)) if item else False

    def remove_path(self, path: str) -> None:
        item = self._item_for(path)
        if item is None:
            return
        row = self.row(item)
        was_focus = bool(item.data(ROLE_FOCUS))
        self.takeItem(row)
        if was_focus:
            self._clear_focus()
        self.files_changed.emit()

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        self.setCurrentItem(item)
        self._apply_focus(item)

    def _apply_focus(self, item: QListWidgetItem | None, notify: bool = True) -> None:
        for i in range(self.count()):
            current = self.item(i)
            current.setData(ROLE_FOCUS, item is not None and current is item)
        self.viewport().update()
        if notify and item is not None:
            self.layer_activated.emit(item.data(ROLE_PATH))

    def _clear_focus(self) -> None:
        self._apply_focus(None, notify=False)

    # ---------- 眼睛 ----------

    def is_visible(self, path: str) -> bool:
        item = self._item_for(path)
        return bool(item.data(ROLE_VISIBLE)) if item else True

    def set_visible(self, path: str, visible: bool) -> None:
        item = self._item_for(path)
        if item is None or bool(item.data(ROLE_VISIBLE)) == bool(visible):
            return
        item.setData(ROLE_VISIBLE, bool(visible))
        self.viewport().update()
        self.visibility_toggled.emit(path)

    def toggle_visibility(self, path: str) -> None:
        item = self._item_for(path)
        if item is None:
            return
        item.setData(ROLE_VISIBLE, not bool(item.data(ROLE_VISIBLE)))
        self.viewport().update()
        self.visibility_toggled.emit(path)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt 命名
        if self.trailing == "eye" and event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            if pos.x() >= self.viewport().width() - 36:
                item = self.itemAt(pos)
                if item is not None:
                    path = item.data(ROLE_PATH)
                    self.toggle_visibility(path)
                    event.accept()
                    return
        super().mouseReleaseEvent(event)

    # ---------- 标签 ----------

    def _label_for(self, path: str, tag: str = "") -> str:
        name = os.path.basename(path)
        suffix = f"  ● {tag}" if tag else ""
        if not path.lower().endswith(".pdf") or not self.show_title_pages:
            return name + suffix
        try:
            import pymupdf

            with pymupdf.open(path) as doc:
                return f"{name}   [{doc.page_count} 页]{suffix}"
        except Exception:
            return name + suffix

    def total_pages(self) -> int:
        """列表里 PDF 的总页数；图片按 1 页计。"""
        pages = 0
        try:
            import pymupdf
        except Exception:
            return 0
        for path in self.paths():
            if not path.lower().endswith(".pdf"):
                pages += 1
                continue
            try:
                with pymupdf.open(path) as doc:
                    pages += doc.page_count
            except Exception:
                continue
        return pages

    # ---------- 拖放 ----------

    def dragEnterEvent(self, event):  # noqa: N802 - Qt 命名
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):  # noqa: N802
        if event.mimeData().hasUrls():
            paths: list[str] = []
            for url in event.mimeData().urls():
                local = url.toLocalFile()
                if not local:
                    continue
                if os.path.isdir(local):
                    paths += [os.path.join(local, n) for n in sorted(os.listdir(local))
                              if os.path.isfile(os.path.join(local, n))]
                else:
                    paths.append(local)
            self.add_paths(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class ColorChip(QLabel):
    """可点击的色卡。比按钮更像一块颜料，也更适合塞进紧凑的表单里。"""

    clicked = Signal()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt 命名
        self.clicked.emit()
        super().mouseReleaseEvent(event)


class PreviewCanvas(QWidget):
    """预览画布：等比缩放绘制页面，支持缩放与平移。

    早先用 QLabel 直接塞原尺寸图，页面比可视区高时会被裁掉上下一截、
    还不出滚动条 —— 用户看到的是半张纸。改成整页自适应之后解决了裁切，
    但 4 合 1 的发票拼版缩到整页后小字仍然看不清。现在分两层处理：

    * **渲染**按显示所需的分辨率走（`render_scale()`）—— 像素够用，缩小
      显示时是漂亮的降采样，而不是把小图放大；
    * **查看**提供 −/＋ 与 Ctrl+滚轮缩放，放大后按住即可拖动平移。

    缩放以「100% = 1pt 对应 1 逻辑像素」为基准，与常见 PDF 阅读器一致；
    页面尺寸由渲染方回传（像素尺寸算不出真实比例）。
    """

    wheel_step = Signal(int)        # -1 / +1
    key_step = Signal(int)
    zoom_changed = Signal()         # 缩放级别变了，需要按新倍率重渲染
    scale_changed = Signal(float)   # 画布尺寸变了，可用渲染倍率随之变化
    object_clicked = Signal(int)    # 预览里的水印对象被点选

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("previewCanvas")
        self.setMinimumSize(320, 360)
        self.setFocusPolicy(Qt.StrongFocus)
        self._pixmap = QPixmap()
        self._message = "预览将显示在这里"
        self._page_pt = QSizeF(595.0, 842.0)
        self._abs_zoom: float | None = None      # None 表示「适应窗口」
        self._offset = QPointF(0.0, 0.0)
        self._drag_origin: QPointF | None = None
        self._last_scale = 0.0
        self._page_step_clock = QElapsedTimer()
        self._overlays: list[dict] = []

    # ---------- 几何 ----------

    def _inner(self) -> QRectF:
        return QRectF(self.rect().adjusted(INNER_PAD, INNER_PAD,
                                           -INNER_PAD, -INNER_PAD))

    def set_page_size(self, width_pt: float, height_pt: float) -> None:
        if width_pt > 0 and height_pt > 0:
            self._page_pt = QSizeF(width_pt, height_pt)

    def fit_zoom(self) -> float:
        """整页可见时的缩放比（逻辑像素 / pt）。"""
        inner = self._inner()
        pw = max(self._page_pt.width(), 1.0)
        ph = max(self._page_pt.height(), 1.0)
        if pw <= 0 or ph <= 0:
            return 1.0
        return min(inner.width() / pw, inner.height() / ph)

    def zoom(self) -> float:
        return self._abs_zoom if self._abs_zoom else self.fit_zoom()

    def is_fit(self) -> bool:
        return self._abs_zoom is None

    def zoom_label(self) -> str:
        return "适应" if self.is_fit() else f"{self.zoom() * 100:.0f}%"

    def render_scale(self) -> float:
        """当前应使用的渲染倍率（pt → 位图像素）。

        乘上屏幕缩放比（Retina 为 2）才是显示真正需要的像素；再乘一点
        超采样系数，缩放后画面才锐利。
        """
        try:
            dpr = float(self.devicePixelRatioF()) or 1.0
        except Exception:      # 极早期的控件可能还没挂到屏幕上
            dpr = 1.0
        scale = self.zoom() * dpr * SUPERSAMPLE
        return max(MIN_RENDER_SCALE, min(scale, MAX_RENDER_SCALE))

    def _draw_size(self) -> tuple[float, float]:
        zoom = self.zoom()
        return self._page_pt.width() * zoom, self._page_pt.height() * zoom

    def _can_pan(self) -> bool:
        inner = self._inner()
        w, h = self._draw_size()
        return w > inner.width() + 1 or h > inner.height() + 1

    def _clamp_offset(self) -> None:
        inner = self._inner()
        w, h = self._draw_size()
        max_x = max(0.0, w - inner.width())
        max_y = max(0.0, h - inner.height())
        self._offset = QPointF(min(max(self._offset.x(), 0.0), max_x),
                               min(max(self._offset.y(), 0.0), max_y))

    # ---------- 缩放 ----------

    def set_zoom(self, value: float | None) -> None:
        """`None` 表示回到适应窗口；否则按绝对比例（1.0 = 100%）。"""
        self._abs_zoom = value
        self._offset = QPointF(0.0, 0.0)
        self._refresh_scale()
        self.update()
        self.zoom_changed.emit()

    def zoom_in(self) -> None:
        current = self.zoom()
        nxt = next((z for z in ZOOM_STEPS if z > current + 1e-3), ZOOM_STEPS[-1])
        self.set_zoom(nxt)

    def zoom_out(self) -> None:
        current = self.zoom()
        prev = next((z for z in reversed(ZOOM_STEPS) if z < current - 1e-3), None)
        self.set_zoom(prev)          # 比最小档还小 → 回适应窗口

    def zoom_fit(self) -> None:
        self.set_zoom(None)

    def _refresh_scale(self) -> None:
        """画布几何变化后，把新的渲染倍率播出去。

        18% 的死区是必要的：窗口每拖动一像素都重算一遍预览会把手感拖垮，
        而这么小的倍率差异肉眼看不出来。倍率被 MAX_RENDER_SCALE 夹住时
        （再放大也不需要更多像素）这里会自然闭嘴。
        """
        scale = self.render_scale()
        previous, self._last_scale = self._last_scale, scale
        if previous <= 0 or abs(scale - previous) / previous >= 0.18:
            self.scale_changed.emit(scale)

    # ---------- 内容 ----------

    def set_pixmap(self, pixmap: QPixmap,
                   page_pt: tuple[float, float] | None = None) -> None:
        self._pixmap = pixmap
        if page_pt:
            self.set_page_size(*page_pt)
        self._message = ""
        self._clamp_offset()
        self.update()

    def set_message(self, text: str) -> None:
        self._pixmap = QPixmap()
        self._message = text
        self._abs_zoom = None
        self._offset = QPointF(0.0, 0.0)
        self._overlays = []
        self.update()
        self.zoom_changed.emit()      # 空态已回到适应窗口，比例标签要跟上

    def set_overlays(self, overlays: list[dict]) -> None:
        """设置页面坐标系里的检测框；bbox 单位是 pt。"""
        self._overlays = list(overlays or [])
        self.update()

    def _page_draw_rect(self) -> QRectF:
        inner = self._inner()
        w, h = self._draw_size()
        if w <= inner.width():
            x = inner.x() + (inner.width() - w) / 2.0
        else:
            x = inner.x() - self._offset.x()
        if h <= inner.height():
            y = inner.y() + (inner.height() - h) / 2.0
        else:
            y = inner.y() - self._offset.y()
        return QRectF(x, y, w, h)

    def _paint_overlays(self, painter: QPainter, page_rect: QRectF) -> None:
        if not self._overlays or self._page_pt.width() <= 0:
            return
        sx = page_rect.width() / self._page_pt.width()
        sy = page_rect.height() / self._page_pt.height()
        painter.save()
        painter.setClipRect(page_rect)
        for item in self._overlays:
            bbox = item.get("bbox")
            if not bbox:
                continue
            x0, y0, x1, y1 = bbox
            rect = QRectF(page_rect.x() + x0 * sx, page_rect.y() + y0 * sy,
                          max(2.0, (x1 - x0) * sx),
                          max(2.0, (y1 - y0) * sy))
            selected = bool(item.get("selected", True))
            kind = item.get("kind", "text")
            if selected:
                fill = QColor(37, 99, 235, 32)
                pen = QPen(QColor("#2563EB"), 1.4, Qt.SolidLine)
            else:
                fill = QColor(148, 163, 184, 18)
                pen = QPen(QColor("#94A3B8"), 1.2, Qt.DashLine)
            if kind == "image":
                fill = QColor(15, 118, 110, 22) if selected else QColor(148, 163, 184, 12)
                pen = QPen(QColor("#0F766E" if selected else "#94A3B8"),
                           1.2, Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(fill)
            painter.drawRect(rect)
        painter.restore()

    # ---------- 交互 ----------

    def wheelEvent(self, event):  # noqa: N802 - Qt 命名
        delta = event.angleDelta().y()
        if not delta:
            super().wheelEvent(event)
            return
        if event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            self.zoom_in() if delta > 0 else self.zoom_out()
            event.accept()
            return
        if self._can_pan():
            # 放大之后滚轮用于平移，否则看不到被裁掉的部分
            step = -PAN_STEP if delta > 0 else PAN_STEP
            self._offset = QPointF(self._offset.x(), self._offset.y() + step)
            self._clamp_offset()
            self.update()
            event.accept()
            return
        if self._page_step_clock.isValid() and self._page_step_clock.elapsed() < PAGE_STEP_COOLDOWN_MS:
            # 触控板一次轻扫会连发十几个滚轮事件，不设冷却就会一路翻到底
            event.accept()
            return
        self._page_step_clock.restart()
        self.wheel_step.emit(-1 if delta > 0 else 1)
        event.accept()

    def keyPressEvent(self, event):  # noqa: N802
        key = event.key()
        if key == Qt.Key_Left:
            self.key_step.emit(-1)
        elif key == Qt.Key_Right:
            self.key_step.emit(1)
        elif key in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_in()
        elif key in (Qt.Key_Minus, Qt.Key_Underscore):
            self.zoom_out()
        elif key == Qt.Key_0:
            self.zoom_fit()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton and self._overlays:
            page_rect = self._page_draw_rect()
            pos = event.position()
            if page_rect.contains(pos):
                sx = self._page_pt.width() / max(1.0, page_rect.width())
                sy = self._page_pt.height() / max(1.0, page_rect.height())
                px = (pos.x() - page_rect.x()) * sx
                py = (pos.y() - page_rect.y()) * sy
                for idx in range(len(self._overlays) - 1, -1, -1):
                    bbox = self._overlays[idx].get("bbox")
                    if bbox and QRectF(*bbox).contains(QPointF(px, py)):
                        self.object_clicked.emit(idx)
                        event.accept()
                        return
        if event.button() == Qt.LeftButton and self._can_pan():
            self._drag_origin = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag_origin is None:
            super().mouseMoveEvent(event)
            return
        delta = event.position() - self._drag_origin
        self._drag_origin = event.position()
        self._offset = QPointF(self._offset.x() - delta.x(),
                               self._offset.y() - delta.y())
        self._clamp_offset()
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._drag_origin is not None:
            self._drag_origin = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._clamp_offset()
        self._refresh_scale()

    def paintEvent(self, event):  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), QColor("#F6F8FB"))

        inner = self._inner()
        if self._pixmap.isNull():
            painter.setPen(QColor("#9AA3B2"))
            painter.drawText(inner, Qt.AlignCenter, self._message)
        else:
            page_rect = self._page_draw_rect()
            # 纸页投影 + 细边框，让预览区有“桌面上的纸”的层次
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(15, 23, 42, 18))
            painter.drawRoundedRect(page_rect.adjusted(2, 3, 3, 4), 2, 2)
            painter.setBrush(QColor("#FFFFFF"))
            painter.setPen(QPen(QColor("#E1E5EB"), 1))
            painter.drawRect(page_rect)
            painter.drawPixmap(page_rect, self._pixmap,
                               QRectF(self._pixmap.rect()))
            self._paint_overlays(painter, page_rect)

        painter.end()


def _chip(text: str) -> QToolButton:
    """预览模式切换用的小标签按钮。"""
    btn = QToolButton()
    btn.setText(text)
    btn.setCheckable(True)
    btn.setAutoExclusive(True)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setProperty("chip", "true")
    return btn


def _pager(icon_name: str, tip: str) -> QToolButton:
    btn = QToolButton()
    btn.setIcon(svg_icon(icon_name))
    btn.setIconSize(QSize(13, 13))
    btn.setFixedSize(24, 20)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setToolTip(tip)
    btn.setProperty("pager", "true")
    return btn


class PreviewPane(QWidget):
    """预览展示区：模式切换行 + 缩放控件 + 画布 + 一行说明。"""

    mode_changed = Signal(str)
    page_step = Signal(int)
    scale_changed = Signal(float)      # 转发画布的渲染倍率需求
    overlay_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("previewPane")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 画布先于表头创建：表头里的缩放按钮是直接连到画布上的
        self.canvas = PreviewCanvas()
        layout.addWidget(self._build_header())

        self.canvas.wheel_step.connect(self._on_step)
        self.canvas.key_step.connect(self._on_step)
        self.canvas.zoom_changed.connect(self._sync_zoom_label)
        self.canvas.scale_changed.connect(self.scale_changed)
        layout.addWidget(self.canvas, 1)

        self.footer_chip = QLabel("")
        self.footer_chip.setObjectName("previewFooterChip")
        self.footer_chip.setAlignment(Qt.AlignCenter)
        self.footer_chip.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.footer_chip.setVisible(False)
        layout.addWidget(self.footer_chip, 0, Qt.AlignHCenter)

        self.hint = QLabel("")
        self.hint.setObjectName("previewHint")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self._base_hint = ""
        self._always_step = False

        self.set_mode(MODE_EFFECT)
        self.set_nav_visible(False)

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("previewHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self.btn_effect = _chip("效果预览")
        self.btn_file = _chip("文件预览")
        self.btn_effect.setToolTip("按当前参数渲染：合并看拼版效果，水印看加印效果")
        self.btn_file.setToolTip("原样显示当前图层（双击左侧任一图层即可切换）")
        self.btn_effect.clicked.connect(lambda: self._emit_mode(MODE_EFFECT))
        self.btn_file.clicked.connect(lambda: self._emit_mode(MODE_FILE))

        self.mode_box = QFrame()
        self.mode_box.setObjectName("modeSwitch")
        mode_layout = QHBoxLayout(self.mode_box)
        mode_layout.setContentsMargins(2, 2, 2, 2)
        mode_layout.setSpacing(1)
        mode_layout.addWidget(self.btn_effect)
        mode_layout.addWidget(self.btn_file)

        self.lbl_note = QLabel("")
        self.lbl_note.setObjectName("previewNote")

        self.chk_overlays = QCheckBox("显示检测框")
        self.chk_overlays.setObjectName("previewCheck")
        self.chk_overlays.setChecked(True)
        self.chk_overlays.setVisible(False)
        self.chk_overlays.toggled.connect(self.overlay_toggled)

        self.btn_zoom_out = _pager("zoom_out", "缩小（Ctrl+滚轮）")
        self.btn_zoom_in = _pager("zoom_in", "放大（Ctrl+滚轮）；放大后按住拖动可平移")
        self.lbl_zoom = QToolButton()
        self.lbl_zoom.setText("适应")
        self.lbl_zoom.setToolTip("当前缩放比例；点一下回到「适应窗口」")
        self.lbl_zoom.setCursor(Qt.PointingHandCursor)
        self.lbl_zoom.setProperty("zoomLabel", "true")
        self.lbl_zoom.setMinimumWidth(46)
        self.btn_zoom_out.clicked.connect(self.canvas.zoom_out)
        self.btn_zoom_in.clicked.connect(self.canvas.zoom_in)
        self.lbl_zoom.clicked.connect(self.canvas.zoom_fit)

        self.lbl_target = QLabel("")
        self.lbl_target.setObjectName("previewTarget")

        self.btn_prev = _pager("prev", "上一页")
        self.btn_next = _pager("next", "下一页")
        self.lbl_page = QLabel("")
        self.lbl_page.setObjectName("previewPage")
        self.lbl_page.setMinimumWidth(52)
        self.lbl_page.setAlignment(Qt.AlignCenter)
        self.btn_prev.clicked.connect(lambda: self._on_step(-1))
        self.btn_next.clicked.connect(lambda: self._on_step(1))

        row.addWidget(self.mode_box)
        row.addSpacing(10)
        row.addWidget(self.chk_overlays)
        row.addWidget(self.lbl_note)
        row.addStretch(1)
        row.addWidget(self.btn_zoom_out)
        row.addWidget(self.lbl_zoom)
        row.addWidget(self.btn_zoom_in)
        row.addWidget(self.lbl_target)
        row.addWidget(self.btn_prev)
        row.addWidget(self.lbl_page)
        row.addWidget(self.btn_next)
        return header

    def _sync_zoom_label(self) -> None:
        self.lbl_zoom.setText(self.canvas.zoom_label())
        self._apply_hint()

    # ---------- 模式 ----------

    def _emit_mode(self, mode: str) -> None:
        self.mode_changed.emit(mode)

    def mode(self) -> str:
        return MODE_FILE if self.btn_file.isChecked() else MODE_EFFECT

    def set_mode(self, mode: str) -> None:
        """程序内部切换模式。

        只连了 `clicked`（由用户操作触发），程序里 setChecked 不会回声，
        所以这里不需要额外屏蔽信号。
        """
        target = self.btn_file if mode == MODE_FILE else self.btn_effect
        target.setChecked(True)

    def set_file_mode_available(self, available: bool) -> None:
        self.btn_file.setEnabled(available)

    def set_mode_labels(self, effect: str, file: str) -> None:
        self.btn_effect.setText(effect)
        self.btn_file.setText(file)

    def set_always_step(self, enabled: bool) -> None:
        """是否允许在「效果预览」里也用滚轮/←→ 翻页。"""
        self._always_step = enabled

    def set_overlay_toggle_visible(self, visible: bool) -> None:
        self.chk_overlays.setVisible(visible)

    def set_overlay_toggle_checked(self, checked: bool) -> None:
        self.chk_overlays.blockSignals(True)
        self.chk_overlays.setChecked(checked)
        self.chk_overlays.blockSignals(False)

    # ---------- 翻页 ----------

    def set_nav_visible(self, visible: bool) -> None:
        for widget in (self.btn_prev, self.btn_next, self.lbl_page,
                       self.lbl_target):
            widget.setVisible(visible)

    def set_target(self, name: str) -> None:
        self.lbl_target.setText(name)

    def set_page_info(self, text: str) -> None:
        self.lbl_page.setText(text)

    def set_header_note(self, text: str) -> None:
        self.lbl_note.setText(text)

    def set_footer_chip(self, text: str) -> None:
        self.footer_chip.setText(text)
        self.footer_chip.setVisible(bool(text))

    def _on_step(self, step: int) -> None:
        if self.mode() == MODE_FILE or self._always_step:
            self.page_step.emit(step)

    # ---------- 内容 ----------

    def show_png(self, data: bytes, hint: str = "",
                 page_pt: tuple[float, float] | None = None) -> None:
        pix = QPixmap()
        if pix.loadFromData(data):
            self.canvas.set_pixmap(pix, page_pt)
        self._base_hint = hint
        self._apply_hint()

    def show_hint(self, text: str) -> None:
        """只改说明文字，不动画面（用于"正在读取…"这类瞬时提示）。"""
        self._base_hint = text
        self._apply_hint()

    def show_message(self, text: str) -> None:
        self.canvas.set_message(text)
        self._base_hint = ""
        self.hint.setText("")

    def _apply_hint(self) -> None:
        """缩放会改变滚轮的含义，说明文字必须跟着一起变。"""
        if not self._base_hint:
            self.hint.setText("")
            return
        if self.canvas._pixmap.isNull():
            suffix = ""
        elif self.canvas._can_pan():
            suffix = "　|　已放大：滚轮平移，按住拖动可移动，Ctrl+滚轮缩放"
        else:
            suffix = "　|　Ctrl+滚轮或 −/＋ 可放大看细节"
        self.hint.setText(self._base_hint + suffix)


class Stepper(QWidget):
    """紧凑的「− 2 +」步进器，比原生 QSpinBox 更贴近原型。"""

    valueChanged = Signal(int)

    def __init__(self, minimum: int = 1, maximum: int = 9,
                 value: int = 1, parent=None):
        super().__init__(parent)
        self._min, self._max, self._value = minimum, maximum, value
        self.setObjectName("stepper")
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(0)

        self.btn_minus = QToolButton()
        self.btn_minus.setText("−")
        self.btn_minus.setObjectName("stepperBtn")
        self.btn_minus.setCursor(Qt.PointingHandCursor)
        self.btn_minus.clicked.connect(lambda: self.setValue(self._value - 1))

        self.lbl_value = QLabel(str(value))
        self.lbl_value.setObjectName("stepperValue")
        self.lbl_value.setAlignment(Qt.AlignCenter)
        self.lbl_value.setMinimumWidth(24)

        self.btn_plus = QToolButton()
        self.btn_plus.setText("+")
        self.btn_plus.setObjectName("stepperBtn")
        self.btn_plus.setCursor(Qt.PointingHandCursor)
        self.btn_plus.clicked.connect(lambda: self.setValue(self._value + 1))

        row.addWidget(self.btn_minus)
        row.addWidget(self.lbl_value, 1)
        row.addWidget(self.btn_plus)
        self.setFixedHeight(30)
        self._sync()

    def value(self) -> int:
        return self._value

    def setValue(self, value: int) -> None:  # noqa: N802 - Qt 命名
        value = max(self._min, min(self._max, int(value)))
        if value == self._value:
            return
        self._value = value
        self.lbl_value.setText(str(value))
        self._sync()
        self.valueChanged.emit(value)

    def setRange(self, minimum: int, maximum: int) -> None:  # noqa: N802
        self._min, self._max = minimum, maximum
        self.setValue(self._value)
        self._sync()

    def _sync(self) -> None:
        self.btn_minus.setEnabled(self._value > self._min)
        self.btn_plus.setEnabled(self._value < self._max)


class ChoiceField(QWidget):
    """小标题在上、当前值在下、右侧带箭头的选择器。"""

    changed = Signal(str)

    def __init__(self, label: str, items: list[str], value: str = "",
                 parent=None):
        super().__init__(parent)
        self._items = list(items)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(1)
        self.lbl = QLabel(label)
        self.lbl.setProperty("ribbonField", "true")
        self.lbl.setVisible(bool(label))
        self.btn = QToolButton()
        self.btn.setObjectName("choiceField")
        self.btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn.setIcon(svg_icon("chevron_down"))
        self.btn.setIconSize(QSize(10, 10))
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setMinimumWidth(86)
        self.btn.setFixedHeight(28)
        self.btn.clicked.connect(self._popup)
        self._menu = QMenu(self.btn)
        for item in self._items:
            action = self._menu.addAction(item)
            action.triggered.connect(lambda _=False, t=item: self.setValue(t))
        self._value = value or (self._items[0] if self._items else "")
        self.btn.setText(self._value)
        if label:
            box.addWidget(self.lbl)
        box.addWidget(self.btn)

    def value(self) -> str:
        return self._value

    def currentText(self) -> str:  # noqa: N802 - 与 QComboBox 对齐
        return self._value

    def setValue(self, value: str) -> None:  # noqa: N802
        if value not in self._items:
            return
        if value == self._value:
            return
        self._value = value
        self.btn.setText(value)
        self.changed.emit(value)

    def setItems(self, items: list[str]) -> None:  # noqa: N802
        self._items = list(items)
        self._menu.clear()
        for item in self._items:
            action = self._menu.addAction(item)
            action.triggered.connect(lambda _=False, t=item: self.setValue(t))
        self.setValue(self._items[0] if self._items else "")

    def _popup(self) -> None:
        self._menu.setMinimumWidth(max(86, self.btn.width()))
        self._menu.exec(self.btn.mapToGlobal(QPoint(0, self.btn.height())))


class EmptyState(QFrame):
    """空态卡片：图标 + 标题 + 说明 + 主按钮。"""

    action = Signal()

    def __init__(self, icon_name: str, title: str, subtitle: str,
                 button_text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("emptyCard")
        self.setMaximumWidth(520)
        box = QVBoxLayout(self)
        box.setContentsMargins(36, 30, 36, 30)
        box.setSpacing(9)
        box.setAlignment(Qt.AlignCenter)

        icon = QLabel()
        icon.setPixmap(svg_icon(icon_name).pixmap(QSize(46, 46)))
        icon.setAlignment(Qt.AlignCenter)
        self.title = QLabel(title)
        self.title.setObjectName("emptyTitle")
        self.title.setAlignment(Qt.AlignCenter)
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("emptySub")
        self.subtitle.setAlignment(Qt.AlignCenter)
        self.subtitle.setWordWrap(True)
        self.button = QPushButton(button_text)
        self.button.setObjectName("emptyPrimary")
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.clicked.connect(self.action.emit)

        box.addWidget(icon)
        box.addWidget(self.title)
        box.addWidget(self.subtitle)
        box.addSpacing(4)
        box.addWidget(self.button, 0, Qt.AlignHCenter)
