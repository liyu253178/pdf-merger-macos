"""页面管理模块：缩略图网格、单页视图、插入/删除/排序/旋转/导出。

页面编辑是非破坏的：所有操作只改 ``PageModel`` 里的 PageRef 列表，
导出时才重建 PDF。侧栏列出的是「源文档图层」，同一时间只有一个文档
处于编辑态；切换到另一个文档前如果有未导出的改动，会先询问。
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

import pymupdf

from PySide6.QtCore import QMimeData, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QDrag, QFont, QFontMetrics, QIcon, QPainter, QPen, QPixmap,
    QShortcut, QKeySequence,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QStyle,
    QStyledItemDelegate, QStyleOptionViewItem, QToolButton, QVBoxLayout,
    QWidget,
)

from ..core.page_model import PageModel, PageRef, is_image, paper_size
from ..core.watermark import parse_page_spec
from .icons import svg_icon
from .modules import Module, _stem
from .ribbon import RibbonBand, RibbonButton, button_group
from .widgets import ChoiceField, EmptyState, Stepper

ROLE_REF = Qt.UserRole + 30
ROLE_THUMB = Qt.UserRole + 31
ROLE_NUM = Qt.UserRole + 32
ROLE_BADGE = Qt.UserRole + 33
ROLE_PAPER = Qt.UserRole + 34
ROLE_KIND = Qt.UserRole + 35


def _mm(pt: float) -> float:
    return pt * 25.4 / 72.0


def _page_dims(model: PageModel, index: int) -> tuple[float, float]:
    w, h = model.page_size(index)
    if model.refs[index].rotation % 180:
        w, h = h, w
    return w, h


def _paper_label(model: PageModel, index: int) -> str:
    ref = model.refs[index]
    if ref.kind in ("blank", "image") and ref.paper:
        orientation = ref.orientation
        if ref.rotation % 180:
            orientation = "横向" if orientation == "纵向" else "纵向"
        return f"{ref.paper} · {orientation}"
    try:
        w, h = _page_dims(model, index)
    except Exception:
        return "A4 · 纵向"
    # 常见纸张按短边/长边近似识别，识别不了就写「自定义」。
    short, long = sorted((w, h))
    paper = "A4"
    if short > 700:
        paper = "A3"
    elif short < 500:
        paper = "A5"
    elif abs(short - 612) < 20 and abs(long - 792) < 20:
        paper = "Letter"
    elif abs(short - 612) < 20 and abs(long - 1008) < 24:
        paper = "Legal"
    return f"{paper} · {'横向' if w > h else '纵向'}"


@dataclass
class _ItemData:
    index: int
    thumb: QPixmap
    badge: str
    paper: str
    kind: str


class PageCardDelegate(QStyledItemDelegate):
    """网格卡片：缩略图 + 来源标签 + 页码 + 纸张/方向 + 拖拽手柄。"""

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(188, 266)

    def paint(self, painter, option, index):  # noqa: N802
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        card = QRectF(option.rect).adjusted(5, 4, -5, -4)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        current = bool(opt.state & QStyle.StateFlag.State_HasFocus)
        if selected:
            fill = QColor("#F8FAFF")
            border = QColor("#2563EB")
            width = 2.0 if current else 1.6
        else:
            fill = QColor("#FFFFFF")
            border = QColor("#E1E6EE")
            width = 1.0
        painter.setPen(QPen(border, width))
        painter.setBrush(fill)
        painter.drawRoundedRect(card, 8, 8)

        data: _ItemData | None = index.data(ROLE_REF)
        badge = index.data(ROLE_BADGE) or "原件"
        num = index.data(ROLE_NUM) or (index.row() + 1)
        paper = index.data(ROLE_PAPER) or "A4 · 纵向"
        kind = index.data(ROLE_KIND) or "original"

        # 顶部来源标签 + 选择圆圈
        badge_rect = QRectF(card.left() + 9, card.top() + 8, 66, 18)
        badge_color = {
            "pdf": ("#EAF1FE", "#1D4ED8"),
            "image": ("#E8F7F4", "#0F766E"),
            "blank": ("#F1F4F8", "#6B7280"),
        }.get(kind, ("#F1F4F8", "#6B7280"))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(badge_color[0]))
        painter.drawRoundedRect(badge_rect, 4, 4)
        font = QFont(opt.font)
        font.setPointSize(7)
        painter.setFont(font)
        painter.setPen(QColor(badge_color[1]))
        icon = svg_icon({"pdf": "pdf", "image": "image", "blank": "blank"}
                        .get(kind, "pdf"))
        icon.paint(painter, QRect(int(badge_rect.left() + 4),
                                  int(badge_rect.top() + 2), 12, 12))
        painter.setFont(font)
        painter.setPen(QColor(badge_color[1]))
        painter.drawText(badge_rect.adjusted(19, 0, -2, 0),
                         Qt.AlignLeft | Qt.AlignVCenter, badge)

        circle = QRectF(card.right() - 26, card.top() + 9, 15, 15)
        painter.setBrush(QColor("#2563EB") if selected else QColor("#FFFFFF"))
        painter.setPen(QPen(QColor("#2563EB" if selected else "#C9D2E0"), 1.2))
        painter.drawEllipse(circle)
        if selected:
            painter.setPen(QPen(QColor("#FFFFFF"), 1.6))
            painter.drawLine(circle.left() + 4, circle.center().y(),
                             circle.left() + 7, circle.bottom() - 4)
            painter.drawLine(circle.left() + 7, circle.bottom() - 4,
                             circle.right() - 4, circle.top() + 4)

        # 纸页缩略图
        pix: QPixmap = index.data(ROLE_THUMB)
        thumb_area = QRectF(card.left() + 9, card.top() + 33,
                            card.width() - 18, card.height() - 76)
        painter.setPen(QPen(QColor("#E7EBF1"), 1))
        painter.setBrush(QColor("#F7F8FA"))
        painter.drawRoundedRect(thumb_area, 4, 4)
        if pix and not pix.isNull():
            pw, ph = pix.width(), pix.height()
            scale = min((thumb_area.width() - 12) / max(1, pw),
                        (thumb_area.height() - 12) / max(1, ph))
            dw, dh = pw * scale, ph * scale
            target = QRectF(thumb_area.center().x() - dw / 2,
                            thumb_area.center().y() - dh / 2, dw, dh)
            painter.setPen(QPen(QColor("#DCE2EA"), 1))
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawRect(target)
            painter.drawPixmap(target, pix, QRectF(pix.rect()))

        # 底部页码和纸张信息
        font.setPointSize(9)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor("#25303F"))
        painter.drawText(QRectF(card.left() + 10, card.bottom() - 37, 32, 18),
                         Qt.AlignLeft | Qt.AlignVCenter, str(num))
        font.setPointSize(8)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor("#8A93A3"))
        painter.drawText(QRectF(card.left() + 10, card.bottom() - 20,
                                card.width() - 38, 16),
                         Qt.AlignLeft | Qt.AlignVCenter, paper)
        svg_icon("drag").paint(
            painter,
            QRect(int(card.right() - 24), int(card.bottom() - 27), 14, 14))
        painter.restore()


class PageGrid(QListWidget):
    """缩略图网格；支持单选/多选、双击打开、拖拽排序。"""

    page_moved = Signal(int, int)
    page_activated = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pageGrid")
        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setSpacing(7)
        self.setGridSize(QSize(188, 266))
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setItemDelegate(PageCardDelegate(self))
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.itemDoubleClicked.connect(
            lambda item: self.page_activated.emit(self.row(item)))

    def startDrag(self, _actions):  # noqa: N802 - Qt 命名
        item = self.currentItem()
        if item is None:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(f"page:{self.row(item)}")
        drag.setMimeData(mime)
        pix = item.data(ROLE_THUMB)
        if isinstance(pix, QPixmap) and not pix.isNull():
            drag.setPixmap(pix.scaled(90, 120, Qt.KeepAspectRatio,
                                      Qt.SmoothTransformation))
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, event):  # noqa: N802
        if event.mimeData().hasText() and event.mimeData().text().startswith("page:"):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):  # noqa: N802
        if event.mimeData().hasText() and event.mimeData().text().startswith("page:"):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):  # noqa: N802
        text = event.mimeData().text()
        if not text.startswith("page:"):
            return super().dropEvent(event)
        try:
            source = int(text.split(":", 1)[1])
        except ValueError:
            return
        pos = event.position().toPoint()
        target_item = self.itemAt(pos)
        target = self.row(target_item) if target_item is not None else self.count()
        if target_item is not None:
            rect = self.visualItemRect(target_item)
            if pos.x() > rect.center().x():
                target += 1
        if target > source:
            target -= 1
        target = max(0, min(target, self.count() - 1))
        event.acceptProposedAction()
        if target != source:
            self.page_moved.emit(source, target)

    def mousePressEvent(self, event):  # noqa: N802
        # 右键不改变当前拖动，但要让右键页成为当前页，方便右侧操作面板。
        if event.button() == Qt.RightButton:
            item = self.itemAt(event.position().toPoint())
            if item is not None:
                self.setCurrentItem(item)
        super().mousePressEvent(event)


class PageActionPanel(QFrame):
    """网格右侧的当前页操作面板（原型 06）。"""

    def __init__(self, module, parent=None):
        super().__init__(parent)
        self.module = module
        self.setObjectName("pageActionPanel")
        self.setMinimumWidth(180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(2)
        self.lbl_title = QLabel("第 1 页")
        self.lbl_title.setObjectName("pageActionTitle")
        layout.addWidget(self.lbl_title)
        layout.addSpacing(6)

        def action(text, icon, slot, danger=False):
            btn = QToolButton()
            btn.setText(text)
            btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            btn.setIcon(svg_icon(icon))
            btn.setIconSize(QSize(14, 14))
            btn.setObjectName("pageActionRow")
            btn.setProperty("danger", "true" if danger else "false")
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setMinimumHeight(29)
            btn.clicked.connect(slot)
            layout.addWidget(btn)
            return btn

        self.btn_blank_before = action(
            "在此页前插入空白页", "blank", lambda: self.module._insert_blank("before"))
        self.btn_blank_after = action(
            "在此页后插入空白页", "blank", lambda: self.module._insert_blank("after"))
        self.btn_pdf = action("从 PDF 插入", "pdf", self.module._insert_pdf)
        self.btn_image = action("从图片插入", "image", self.module._insert_image)
        layout.addSpacing(3)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#EDF0F4;")
        layout.addWidget(line)
        self.btn_copy = action("复制此页", "copy", self.module.copy_pages)
        self.btn_left = action("左转 90°", "rotate_left",
                               lambda: self.module.rotate_pages(-90))
        self.btn_right = action("右转 90°", "rotate_right",
                                lambda: self.module.rotate_pages(90))
        layout.addSpacing(3)
        self.btn_delete = action("删除此页", "remove", self.module.delete_pages,
                                 danger=True)
        layout.addStretch(1)

    def set_context(self, index: int, selected_count: int) -> None:
        text = f"第 {index + 1} 页" if index >= 0 else "页面操作"
        if selected_count > 1:
            text += f" · 已选 {selected_count} 页"
        self.lbl_title.setText(text)


class GridView(QWidget):
    """缩略图网格视图：顶部工具条 + 网格 + 右侧操作面板。"""

    def __init__(self, module, parent=None):
        super().__init__(parent)
        self.module = module
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = QWidget()
        self.header.setObjectName("pageHeader")
        head = QHBoxLayout(self.header)
        head.setContentsMargins(12, 7, 12, 7)
        head.setSpacing(7)
        self.title = QLabel("页面")
        self.title.setObjectName("pageTitle")
        self.meta = QLabel("共 0 页")
        self.meta.setObjectName("pageMeta")
        self.dirty = QLabel("● 未导出的更改")
        self.dirty.setObjectName("pageDirty")
        self.dirty.setVisible(False)
        head.addWidget(self.title)
        head.addWidget(self.meta)
        head.addWidget(self.dirty)
        head.addStretch(1)
        self.chip_grid = QToolButton()
        self.chip_grid.setText("网格")
        self.chip_grid.setCheckable(True)
        self.chip_grid.setChecked(True)
        self.chip_grid.setProperty("chip", "true")
        self.chip_single = QToolButton()
        self.chip_single.setText("单页")
        self.chip_single.setCheckable(True)
        self.chip_single.setProperty("chip", "true")
        self.chip_grid.clicked.connect(lambda: module.set_view("grid"))
        self.chip_single.clicked.connect(lambda: module.set_view("single"))
        head.addWidget(self.chip_grid)
        head.addWidget(self.chip_single)
        head.addSpacing(8)
        self.btn_zoom_out = QToolButton()
        self.btn_zoom_out.setIcon(svg_icon("zoom_out"))
        self.btn_zoom_out.setIconSize(QSize(14, 14))
        self.btn_zoom_out.setProperty("pager", "true")
        self.btn_zoom_out.clicked.connect(lambda: module.grid_zoom(-1))
        self.lbl_zoom = QToolButton()
        self.lbl_zoom.setText("适应")
        self.lbl_zoom.setProperty("zoomLabel", "true")
        self.lbl_zoom.clicked.connect(lambda: module.grid_fit())
        self.btn_zoom_in = QToolButton()
        self.btn_zoom_in.setIcon(svg_icon("zoom_in"))
        self.btn_zoom_in.setIconSize(QSize(14, 14))
        self.btn_zoom_in.setProperty("pager", "true")
        self.btn_zoom_in.clicked.connect(lambda: module.grid_zoom(1))
        head.addWidget(self.btn_zoom_out)
        head.addWidget(self.lbl_zoom)
        head.addWidget(self.btn_zoom_in)
        layout.addWidget(self.header)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        self.grid = PageGrid()
        self.grid.page_moved.connect(module._on_page_moved)
        self.grid.page_activated.connect(module.open_single)
        self.grid.itemSelectionChanged.connect(module._on_grid_selection)
        self.grid.currentItemChanged.connect(
            lambda *_: module._on_grid_selection())
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(module._grid_context_menu)
        grid_wrap = QWidget()
        grid_layout = QVBoxLayout(grid_wrap)
        grid_layout.setContentsMargins(12, 10, 12, 10)
        grid_layout.addWidget(self.grid)
        splitter.addWidget(grid_wrap)
        self.actions = PageActionPanel(module)
        self.actions.setVisible(False)
        self.actions_wrap = QWidget()
        self.actions_wrap.setObjectName("pageActionWrap")
        aw = QVBoxLayout(self.actions_wrap)
        aw.setContentsMargins(12, 12, 12, 12)
        aw.setSpacing(0)
        aw.addWidget(self.actions)
        self.actions_wrap.setVisible(False)
        splitter.addWidget(self.actions_wrap)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([760, 224])
        layout.addWidget(splitter, 1)

    def set_actions_visible(self, visible: bool) -> None:
        self.actions.setVisible(visible)
        self.actions_wrap.setVisible(visible)


class SinglePageCanvas(QWidget):
    """单页视图里的纸页画布：灰色桌面 + 投影 + 可缩放页面。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 300)
        self._pixmap = QPixmap()
        self._zoom: float | None = None
        self._nav_prev: QToolButton | None = None
        self._nav_next: QToolButton | None = None

    def set_pixmap(self, pix: QPixmap) -> None:
        self._pixmap = pix
        self._place_nav()
        self.update()

    def set_zoom(self, zoom: float | None) -> None:
        self._zoom = zoom
        self._place_nav()
        self.update()

    def zoom_value(self) -> float | None:
        return self._zoom

    def set_nav_buttons(self, prev: QToolButton,
                        next_: QToolButton) -> None:
        """把上一页/下一页按钮做成画布上的浮层，贴近纸页两侧。"""
        self._nav_prev, self._nav_next = prev, next_
        for btn in (prev, next_):
            btn.setParent(self)
            btn.setFixedSize(32, 32)
            btn.setIconSize(QSize(14, 14))
            btn.setCursor(Qt.PointingHandCursor)
            btn.raise_()
            btn.show()
        self._place_nav()

    def _page_target(self) -> QRectF:
        if self._pixmap.isNull():
            return QRectF()
        area = self.rect().adjusted(28, 20, -28, -20)
        if self._zoom is None:
            scale = min(area.width() / max(1, self._pixmap.width()),
                        area.height() / max(1, self._pixmap.height()))
        else:
            scale = self._zoom
        w = self._pixmap.width() * scale
        h = self._pixmap.height() * scale
        return QRectF(area.center().x() - w / 2,
                      area.center().y() - h / 2, w, h)

    def _place_nav(self) -> None:
        if self._nav_prev is None or self._nav_next is None:
            return
        target = self._page_target()
        if target.isNull():
            self._nav_prev.move(8, self.height() // 2 - 16)
            self._nav_next.move(self.width() - 40, self.height() // 2 - 16)
            return
        y = int(target.center().y() - 16)
        left_x = int(target.left() - 46)
        right_x = int(target.right() + 14)
        left_x = max(8, min(left_x, self.width() - 80))
        right_x = min(self.width() - 40, max(right_x, 40))
        self._nav_prev.move(left_x, y)
        self._nav_next.move(right_x, y)
        self._nav_prev.raise_()
        self._nav_next.raise_()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._place_nav()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), QColor("#F6F8FB"))
        if self._pixmap.isNull():
            painter.setPen(QColor("#9AA3B2"))
            painter.drawText(self.rect(), Qt.AlignCenter, "正在渲染页面…")
            return
        target = self._page_target()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(15, 23, 42, 20))
        painter.drawRoundedRect(target.adjusted(2, 3, 4, 5), 2, 2)
        painter.setBrush(QColor("#FFFFFF"))
        painter.setPen(QPen(QColor("#E1E5EB"), 1))
        painter.drawRect(target)
        painter.save()
        painter.setClipRect(target.adjusted(1, 1, -1, -1))
        painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))
        painter.restore()


class SingleView(QWidget):
    """单页大图视图（原型 07）。"""

    def __init__(self, module, parent=None):
        super().__init__(parent)
        self.module = module
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("pageHeader")
        head = QHBoxLayout(header)
        head.setContentsMargins(12, 7, 12, 7)
        head.setSpacing(8)
        self.title = QLabel("单页视图")
        self.title.setObjectName("pageTitle")
        self.meta = QLabel("")
        self.meta.setObjectName("pageMeta")
        self.btn_back = QToolButton()
        self.btn_back.setText("返回网格")
        self.btn_back.setIcon(svg_icon("grid"))
        self.btn_back.setIconSize(QSize(14, 14))
        self.btn_back.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_back.setObjectName("pageBack")
        self.btn_back.clicked.connect(lambda: module.set_view("grid"))
        head.addWidget(self.title)
        head.addWidget(self.meta)
        head.addSpacing(6)
        head.addWidget(self.btn_back)
        head.addStretch(1)
        self.btn_zoom_out = QToolButton()
        self.btn_zoom_out.setIcon(svg_icon("zoom_out"))
        self.btn_zoom_out.setIconSize(QSize(14, 14))
        self.btn_zoom_out.setProperty("pager", "true")
        self.btn_zoom_out.clicked.connect(lambda: module.single_zoom(-1))
        self.lbl_zoom = QToolButton()
        self.lbl_zoom.setText("适应")
        self.lbl_zoom.setProperty("zoomLabel", "true")
        self.lbl_zoom.clicked.connect(lambda: module.single_fit())
        self.btn_zoom_in = QToolButton()
        self.btn_zoom_in.setIcon(svg_icon("zoom_in"))
        self.btn_zoom_in.setIconSize(QSize(14, 14))
        self.btn_zoom_in.setProperty("pager", "true")
        self.btn_zoom_in.clicked.connect(lambda: module.single_zoom(1))
        head.addWidget(self.btn_zoom_out)
        head.addWidget(self.lbl_zoom)
        head.addWidget(self.btn_zoom_in)
        layout.addWidget(header)

        body = QWidget()
        row = QHBoxLayout(body)
        row.setContentsMargins(0, 8, 0, 0)
        row.setSpacing(0)
        self.btn_prev = QToolButton()
        self.btn_prev.setObjectName("roundPageBtn")
        self.btn_prev.setIcon(svg_icon("prev"))
        self.btn_prev.setIconSize(QSize(14, 14))
        self.btn_prev.clicked.connect(lambda: module.single_step(-1))
        self.canvas = SinglePageCanvas()
        self.btn_next = QToolButton()
        self.btn_next.setObjectName("roundPageBtn")
        self.btn_next.setIcon(svg_icon("next"))
        self.btn_next.setIconSize(QSize(14, 14))
        self.btn_next.clicked.connect(lambda: module.single_step(1))
        row.addWidget(self.canvas, 1)
        self.canvas.set_nav_buttons(self.btn_prev, self.btn_next)
        layout.addWidget(body, 1)

        self.page_label = QLabel("")
        self.page_label.setObjectName("pageMeta")
        self.page_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.page_label)

        bar = QFrame()
        bar.setObjectName("pageActionBar")
        bar.setFixedHeight(42)
        b = QHBoxLayout(bar)
        b.setContentsMargins(8, 3, 8, 3)
        b.setSpacing(2)
        self.btn_left = self._action("左转", "rotate_left",
                                     lambda: module.rotate_pages(-90))
        self.btn_right = self._action("右转", "rotate_right",
                                      lambda: module.rotate_pages(90))
        self.btn_copy = self._action("复制", "copy", module.copy_pages)
        self.btn_delete = self._action("删除", "remove", module.delete_pages,
                                       danger=True)
        for btn in (self.btn_left, self.btn_right, self.btn_copy, self.btn_delete):
            b.addWidget(btn)
        wrap = QHBoxLayout()
        wrap.addStretch(1)
        wrap.addWidget(bar)
        wrap.addStretch(1)
        layout.addLayout(wrap)
        layout.addSpacing(10)

    def _action(self, text, icon, slot, danger=False):
        btn = QToolButton()
        btn.setText(text)
        btn.setIcon(svg_icon(icon))
        btn.setIconSize(QSize(14, 14))
        btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        btn.setObjectName("pageAction")
        btn.setProperty("danger", "true" if danger else "false")
        btn.clicked.connect(slot)
        return btn


class PageWorkspace(QWidget):
    """页面管理的中心区：网格 / 单页两种视图。"""

    def __init__(self, module, parent=None):
        super().__init__(parent)
        self.module = module
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.grid_view = GridView(module)
        self.single_view = SingleView(module)
        self.empty = EmptyState(
            "page",
            "打开一个 PDF，开始整理页面",
            "支持删除、插入、拖拽排序、旋转和复制页面；原 PDF 不会被修改。",
            "打开 PDF",
        )
        self.empty.setMinimumSize(520, 320)
        self.empty.action.connect(module.add_files)
        empty_wrap = QWidget()
        empty_wrap.setObjectName("pageEmptyWrap")
        ev = QVBoxLayout(empty_wrap)
        ev.setContentsMargins(24, 24, 24, 24)
        ev.addStretch(1)
        ev.addWidget(self.empty, 0, Qt.AlignHCenter)
        ev.addStretch(1)
        self.stack = QStackedWidget()
        self.stack.addWidget(empty_wrap)
        self.stack.addWidget(self.grid_view)
        self.stack.addWidget(self.single_view)
        layout.addWidget(self.stack)

    def show_empty(self) -> None:
        self.stack.setCurrentWidget(self.stack.widget(0))

    def show_grid(self) -> None:
        self.stack.setCurrentWidget(self.grid_view)

    def show_single(self) -> None:
        self.stack.setCurrentWidget(self.single_view)


# --------------------------------------------------------------------------- #
#  对话框
# --------------------------------------------------------------------------- #

def _dialog_buttons(cancel_text="取消", ok_text="确定", danger=False):
    row = QHBoxLayout()
    row.addStretch(1)
    cancel = QPushButton(cancel_text)
    cancel.setObjectName("dialogCancel")
    ok = QPushButton(ok_text)
    ok.setObjectName("dialogDanger" if danger else "dialogPrimary")
    row.addWidget(cancel)
    row.addWidget(ok)
    return row, cancel, ok


class InsertBlankDialog(QDialog):
    def __init__(self, page_index: int, page_count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("插入空白页")
        self.setModal(True)
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        title = QLabel("插入空白页")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        form = QHBoxLayout()
        form.setSpacing(12)
        self.count = Stepper(1, 20, 1)
        self.paper = ChoiceField("纸张", ["A4", "A3", "A5", "Letter", "Legal"], "A4")
        self.orientation = ChoiceField("方向", ["纵向", "横向"], "纵向")
        self.position = ChoiceField("插入位置", self._positions(page_index, page_count),
                                    self._positions(page_index, page_count)[1])
        form.addWidget(self._field("张数", self.count))
        form.addWidget(self.paper)
        form.addWidget(self.orientation)
        form.addWidget(self.position, 1)
        layout.addLayout(form)

        self.preview = QLabel()
        self.preview.setObjectName("dialogPreview")
        self.preview.setMinimumSize(260, 150)
        self.preview.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.preview)
        buttons, cancel, ok = _dialog_buttons(ok_text="插入")
        self._buttons = (cancel, ok)
        self._ok = ok
        layout.addLayout(buttons)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        self.count.valueChanged.connect(lambda _v: self._update_preview())
        self.paper.changed.connect(lambda _v: self._update_preview())
        self.orientation.changed.connect(lambda _v: self._update_preview())
        self._update_preview()

    def _update_preview(self) -> None:
        width, height = paper_size(self.paper.currentText(),
                                   self.orientation.currentText())
        pix = QPixmap(280, 160)
        pix.fill(QColor("#F7F8FA"))
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing, True)
        scale = min(100 / max(1.0, width), 128 / max(1.0, height))
        pw, ph = width * scale, height * scale
        target = QRectF((pix.width() - pw) / 2, (pix.height() - ph) / 2,
                        pw, ph)
        painter.setPen(QPen(QColor("#DCE2EA"), 1))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRect(target)
        painter.setPen(QColor("#9AA3B2"))
        painter.drawText(target, Qt.AlignCenter, "空白页")
        painter.end()
        self.preview.setPixmap(pix)
        self._ok.setText(f"插入 {self.count.value()} 页")

    @staticmethod
    def _positions(page_index: int, page_count: int) -> list[str]:
        n = min(max(page_index + 1, 1), max(page_count, 1))
        return [f"第 {n} 页之前", f"第 {n} 页之后", "文档末尾"]

    @staticmethod
    def _field(label: str, widget: QWidget) -> QWidget:
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)
        lbl = QLabel(label)
        lbl.setProperty("ribbonField", "true")
        column.addWidget(lbl)
        column.addWidget(widget)
        return box

    def values(self):
        pos = self.position.currentText()
        return self.count.value(), self.paper.currentText(), \
            self.orientation.currentText(), pos


class InsertPdfDialog(QDialog):
    def __init__(self, path: str, page_index: int, page_count: int, parent=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle("从 PDF 插入页面")
        self.setModal(True)
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(9)
        title = QLabel("从 PDF 插入页面")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        self.lbl_file = QLabel(os.path.basename(path))
        self.lbl_file.setObjectName("dialogFile")
        layout.addWidget(self.lbl_file)
        row = QHBoxLayout()
        row.addWidget(QLabel("页面范围"))
        self.edit_range = QLineEdit("全部")
        self.edit_range.setPlaceholderText("例如 1-3,5")
        row.addWidget(self.edit_range, 1)
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("pageMeta")
        row.addWidget(self.lbl_count)
        layout.addLayout(row)
        self.preview_strip = QWidget()
        self.preview_strip.setObjectName("dialogPreviewStrip")
        self.preview_layout = QHBoxLayout(self.preview_strip)
        self.preview_layout.setContentsMargins(10, 9, 10, 9)
        self.preview_layout.setSpacing(8)
        self.preview_layout.addStretch(1)
        layout.addWidget(self.preview_strip)
        self.position = ChoiceField("插入位置",
                                    InsertBlankDialog._positions(page_index, page_count),
                                    InsertBlankDialog._positions(page_index, page_count)[1])
        layout.addWidget(self.position)
        buttons, cancel, ok = _dialog_buttons(ok_text="插入")
        self._ok = ok
        layout.addLayout(buttons)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        self.edit_range.textChanged.connect(self._update_count)
        self._update_count()

    def _update_count(self):
        try:
            with pymupdf.open(self.path) as doc:
                picked = parse_page_spec(self.edit_range.text(), doc.page_count)
            self.lbl_count.setText(f"将插入 {len(picked)} 页")
            self.lbl_count.setStyleSheet("color:#2563EB;font-size:11px;")
            self._ok.setText(f"插入 {len(picked)} 页")
            self._render_thumbnails(picked)
        except Exception:
            self.lbl_count.setText("页面范围无效")
            self._ok.setText("插入")
            self._render_thumbnails([])

    def _render_thumbnails(self, picked: list[int]) -> None:
        while self.preview_layout.count():
            item = self.preview_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not picked:
            empty = QLabel("没有可插入的页面")
            empty.setObjectName("dialogSub")
            self.preview_layout.addWidget(empty, 0, Qt.AlignCenter)
            self.preview_layout.addStretch(1)
            return
        doc = pymupdf.open(self.path)
        try:
            for page_no in picked[:4]:
                page = doc[page_no]
                pix = page.get_pixmap(matrix=pymupdf.Matrix(0.16, 0.16),
                                      alpha=False)
                cell = QWidget()
                cell.setObjectName("dialogThumbCell")
                box = QVBoxLayout(cell)
                box.setContentsMargins(0, 0, 0, 0)
                box.setSpacing(3)
                thumb = QLabel()
                thumb.setObjectName("dialogThumb")
                pm = QPixmap()
                pm.loadFromData(pix.tobytes("png"))
                thumb.setPixmap(pm)
                thumb.setAlignment(Qt.AlignCenter)
                box.addWidget(thumb)
                num = QLabel(str(page_no + 1))
                num.setObjectName("pageMeta")
                num.setAlignment(Qt.AlignCenter)
                box.addWidget(num)
                self.preview_layout.addWidget(cell)
        finally:
            doc.close()
        if len(picked) > 4:
            more = QLabel(f"+{len(picked) - 4}")
            more.setObjectName("dialogSub")
            self.preview_layout.addWidget(more, 0, Qt.AlignCenter)
        self.preview_layout.addStretch(1)

    def values(self):
        return self.edit_range.text().strip() or "全部", self.position.currentText()


class InsertImageDialog(QDialog):
    def __init__(self, paths: list[str], page_index: int, page_count: int,
                 parent=None):
        super().__init__(parent)
        self.paths = list(paths)
        self.setWindowTitle("从图片插入页面")
        self.setModal(True)
        self.setMinimumWidth(660)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(9)
        head = QHBoxLayout()
        title = QLabel("从图片插入页面")
        title.setObjectName("dialogTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.count_label = QLabel()
        self.count_label.setObjectName("dialogSub")
        head.addWidget(self.count_label)
        layout.addLayout(head)
        self.list = QListWidget()
        self.list.setObjectName("dialogFileList")
        self.list.setMinimumHeight(180)
        self.list.setMaximumHeight(190)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(6)
        left.addWidget(self.list)
        add_row = QHBoxLayout()
        add_row.addStretch(1)
        self.btn_add = QPushButton("+ 添加")
        self.btn_add.setObjectName("dialogCancel")
        self.btn_add.clicked.connect(self._add_files)
        add_row.addWidget(self.btn_add)
        left.addLayout(add_row)
        left_wrap = QWidget()
        left_wrap.setLayout(left)
        content = QHBoxLayout()
        content.setSpacing(12)
        content.addWidget(left_wrap, 1)
        self.preview = QLabel("选择图片预览")
        self.preview.setObjectName("dialogPreview")
        self.preview.setMinimumSize(230, 210)
        self.preview.setMaximumWidth(240)
        self.preview.setAlignment(Qt.AlignCenter)
        content.addWidget(self.preview)
        layout.addLayout(content)
        self.list.currentRowChanged.connect(self._update_preview)
        form = QHBoxLayout()
        self.paper = ChoiceField("纸张", ["A4", "A3", "A5", "Letter", "Legal"], "A4")
        self.orientation = ChoiceField("方向", ["纵向", "横向"], "纵向")
        self.position = ChoiceField("插入位置",
                                    InsertBlankDialog._positions(page_index, page_count),
                                    InsertBlankDialog._positions(page_index, page_count)[1])
        form.addWidget(self.paper)
        form.addWidget(self.orientation)
        form.addWidget(self.position, 1)
        layout.addLayout(form)
        self.chk_fit = QCheckBox("适应页面（保持比例）")
        self.chk_fit.setChecked(True)
        layout.addWidget(self.chk_fit)
        buttons, cancel, ok = _dialog_buttons(ok_text="插入")
        self._ok = ok
        layout.addLayout(buttons)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        self._rebuild_list()

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "添加图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp *.jfif)")
        changed = False
        for path in paths:
            if path and path not in self.paths:
                self.paths.append(path)
                changed = True
        if changed:
            self._rebuild_list()

    def _rebuild_list(self) -> None:
        current = self.list.currentRow()
        self.list.blockSignals(True)
        self.list.clear()
        for path in self.paths:
            pm = QPixmap(path)
            if pm.isNull():
                meta = f"{os.path.splitext(path)[1][1:].upper()} · 无法预览"
                icon = QIcon()
            else:
                meta = f"{pm.width()} × {pm.height()} · {os.path.splitext(path)[1][1:].upper()}"
                icon = QIcon(pm.scaled(32, 32, Qt.KeepAspectRatio,
                                       Qt.SmoothTransformation))
            item = QListWidgetItem(
                icon, f"{os.path.basename(path)}\n{meta}")
            item.setData(Qt.UserRole, path)
            item.setSizeHint(QSize(0, 46))
            self.list.addItem(item)
        self.list.blockSignals(False)
        if self.paths:
            self.list.setCurrentRow(max(0, min(current, len(self.paths) - 1)))
        self._refresh_count()

    def _refresh_count(self) -> None:
        self.count_label.setText(f"{len(self.paths)} 张图片")
        self._ok.setText(f"插入 {len(self.paths)} 页")
        self._ok.setEnabled(bool(self.paths))

    def _update_preview(self, row: int) -> None:
        if not (0 <= row < len(self.paths)):
            self.preview.setText("选择图片预览")
            return
        pm = QPixmap(self.paths[row])
        if pm.isNull():
            self.preview.setText("图片预览")
        else:
            self.preview.setPixmap(pm.scaled(220, 200, Qt.KeepAspectRatio,
                                             Qt.SmoothTransformation))

    def values(self):
        return [self.list.item(i).data(Qt.UserRole)
                for i in range(self.list.count())], \
            self.paper.currentText(), self.orientation.currentText(), \
            self.position.currentText()


class DeleteConfirmDialog(QDialog):
    def __init__(self, indices: list[int], parent=None):
        super().__init__(parent)
        self.setWindowTitle("删除页面")
        self.setModal(True)
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(11)
        head = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(svg_icon("warning").pixmap(QSize(26, 26)))
        head.addWidget(icon, 0, Qt.AlignTop)
        title = QLabel(f"删除 {len(indices)} 页？")
        title.setObjectName("dialogTitle")
        head.addWidget(title, 1)
        layout.addLayout(head)
        note = QFrame()
        note.setObjectName("unsavedCard")
        nv = QVBoxLayout(note)
        nv.setContentsMargins(12, 9, 12, 9)
        nv.setSpacing(6)
        for icon_name, text in (
            ("undo", "删除后可以在导出前用 ⌘Z 撤销。"),
            ("shield", "原文件不会被修改。"),
        ):
            row = QHBoxLayout()
            ico = QLabel()
            ico.setPixmap(svg_icon(icon_name).pixmap(QSize(14, 14)))
            row.addWidget(ico)
            row.addWidget(QLabel(text), 1)
            nv.addLayout(row)
        layout.addWidget(note)
        chips = QHBoxLayout()
        chips.addWidget(QLabel("将删除"))
        for i in indices[:8]:
            chip = QLabel(f"第 {i + 1} 页")
            chip.setObjectName("deleteChip")
            chips.addWidget(chip)
        chips.addStretch(1)
        layout.addLayout(chips)
        buttons, cancel, ok = _dialog_buttons(ok_text="删除", danger=True)
        layout.addLayout(buttons)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)


class UnsavedDialog(QDialog):
    """切换文档时的未导出确认（原型 09）。"""

    def __init__(self, path: str, page_count: int, changed_pages: int,
                 rotations: int, parent=None):
        super().__init__(parent)
        self.choice = "cancel"
        self.setWindowTitle("当前整理结果尚未导出")
        self.setModal(True)
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        head = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(svg_icon("warning").pixmap(QSize(30, 30)))
        head.addWidget(icon, 0, Qt.AlignTop)
        title = QLabel("当前整理结果尚未导出")
        title.setObjectName("dialogTitle")
        head.addWidget(title, 1)
        layout.addLayout(head)
        text = QLabel(
            f"{os.path.basename(path)} 的页面增删、排序或旋转改动还没有导出。"
            "打开其他文件后，这些改动将丢失。"
        )
        text.setObjectName("dialogSub")
        text.setWordWrap(True)
        layout.addWidget(text)
        card = QFrame()
        card.setObjectName("unsavedCard")
        row = QHBoxLayout(card)
        row.setContentsMargins(12, 9, 12, 9)
        ico = QLabel()
        ico.setPixmap(svg_icon("pdf").pixmap(QSize(22, 22)))
        row.addWidget(ico)
        info = QVBoxLayout()
        info.setSpacing(1)
        name = QLabel(os.path.basename(path))
        name.setObjectName("detectTitle")
        meta = QLabel(f"{page_count} 页 · 含 {rotations} 次旋转")
        meta.setObjectName("detectMeta")
        info.addWidget(name)
        info.addWidget(meta)
        row.addLayout(info, 1)
        tag = QLabel("未导出")
        tag.setObjectName("unsavedTag")
        row.addWidget(tag)
        layout.addWidget(card)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("dialogCancel")
        discard = QPushButton("放弃更改")
        discard.setObjectName("dialogDangerOutline")
        export = QPushButton("先导出再打开")
        export.setObjectName("dialogPrimary")
        cancel.clicked.connect(lambda: self._done("cancel"))
        discard.clicked.connect(lambda: self._done("discard"))
        export.clicked.connect(lambda: self._done("export"))
        buttons.addWidget(cancel)
        buttons.addWidget(discard)
        buttons.addWidget(export)
        layout.addLayout(buttons)

    def _done(self, choice: str):
        self.choice = choice
        self.accept()


class ExportDoneDialog(QDialog):
    def __init__(self, path: str, page_count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导出成功")
        self.setModal(True)
        self.setMinimumWidth(470)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(11)
        icon = QLabel()
        icon.setFixedSize(48, 48)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("background:#E8F7F1; border-radius:24px;")
        icon.setPixmap(svg_icon("check_green").pixmap(QSize(28, 28)))
        layout.addWidget(icon)
        title = QLabel(f"已导出 {page_count} 页")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        card = QFrame()
        card.setObjectName("unsavedCard")
        row = QHBoxLayout(card)
        row.setContentsMargins(12, 10, 12, 10)
        ico = QLabel()
        ico.setPixmap(svg_icon("pdf").pixmap(QSize(24, 24)))
        row.addWidget(ico)
        info = QVBoxLayout()
        info.setSpacing(1)
        name = QLabel(os.path.basename(path))
        name.setObjectName("detectTitle")
        try:
            size = os.path.getsize(path) / 1024 / 1024
            meta_text = f"{page_count} 页 · {size:.1f} MB · 已保存到「{os.path.dirname(path)}」"
        except OSError:
            meta_text = f"{page_count} 页"
        meta = QLabel(meta_text)
        meta.setObjectName("detectMeta")
        meta.setWordWrap(True)
        meta.setMaximumWidth(360)
        info.addWidget(name)
        info.addWidget(meta)
        row.addLayout(info, 1)
        layout.addWidget(card)
        note = QLabel("原文件没有被修改，导出结果是另一个新文件。")
        note.setObjectName("dialogSub")
        layout.addWidget(note)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        reveal = QPushButton("在访达中显示")
        reveal.setObjectName("dialogCancel")
        reveal.clicked.connect(lambda: self._reveal(path))
        ok = QPushButton("好")
        ok.setObjectName("dialogPrimary")
        ok.clicked.connect(self.accept)
        buttons.addWidget(reveal)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

    @staticmethod
    def _reveal(path: str):
        try:
            subprocess.Popen(["open", "-R", path])
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  页面管理模块
# --------------------------------------------------------------------------- #

class PageModule(Module):
    title = "页面管理"
    accept_images = False
    empty_hint = "尚未添加 PDF"
    accept_filter = "PDF 文件 (*.pdf)"
    layer_show_index = False
    layer_trailing = "eye"
    layer_source_note = ""

    def __init__(self, status):
        self._models: dict[str, PageModel] = {}
        self._active_path: str | None = None
        self._view = "grid"
        self._grid_scale = 1.0
        self._single_zoom: float | None = None
        self._suppress_sidebar = False
        super().__init__(status)
        self.files.show_title_pages = False
        self.files.set_trailing("edit")
        self._build_ribbon()
        self.workspace = self.center
        self.files.itemClicked.connect(self._on_sidebar_clicked)
        self._build_page_sidebar_extras()
        self._build_shortcuts()
        self._update_empty_state()
        self._sync_buttons()
        self._emit_status("添加 PDF 后可以删除、插入、拖拽排序、旋转和复制页面")

    # ---------- 侧栏 ----------

    def _build_page_sidebar_extras(self) -> None:
        layout = self.sidebar.layout()
        if layout is None:
            return
        header_widget = layout.itemAt(0).widget()
        if header_widget is not None:
            header_widget.setFixedHeight(24)
            header_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.removeWidget(self.files)
        layout.removeWidget(self.lbl_count)
        self.lbl_count.hide()
        self._side_stack = QStackedWidget()
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.setContentsMargins(8, 0, 8, 0)
        ev.addStretch(1)
        icon = QLabel()
        icon.setPixmap(svg_icon("page").pixmap(QSize(30, 30)))
        icon.setAlignment(Qt.AlignCenter)
        title = QLabel("尚未添加 PDF")
        title.setObjectName("sideEmptyTitle")
        title.setAlignment(Qt.AlignCenter)
        sub = QLabel("点上方「添加 PDF」导入")
        sub.setObjectName("sideEmptySub")
        sub.setAlignment(Qt.AlignCenter)
        ev.addWidget(icon)
        ev.addWidget(title)
        ev.addWidget(sub)
        ev.addStretch(1)
        self._side_stack.addWidget(empty)
        self._side_stack.addWidget(self.files)
        layout.insertWidget(1, self._side_stack, 1)

        self._page_info = QWidget()
        self._page_info.setObjectName("sidePageInfo")
        pv = QVBoxLayout(self._page_info)
        pv.setContentsMargins(7, 7, 7, 7)
        pv.setSpacing(2)
        t = QLabel("当前页")
        t.setObjectName("sidePageInfoTitle")
        self._lbl_current_page = QLabel("")
        self._lbl_current_page.setObjectName("sidePageInfoMain")
        self._lbl_current_size = QLabel("")
        self._lbl_current_size.setObjectName("sidePageInfoSub")
        pv.addWidget(t)
        pv.addWidget(self._lbl_current_page)
        pv.addWidget(self._lbl_current_size)
        self._side_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._page_info.setFixedHeight(62)
        self._page_info.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._page_info.setVisible(False)
        layout.insertWidget(2, self._page_info)
        layout.setStretch(1, 0)
        layout.addStretch(1)
        self._side_spacer = layout.itemAt(layout.count() - 1).spacerItem()
        self.lbl_count.setVisible(False)

    def set_view(self, view: str) -> None:
        self._view = "single" if view == "single" else "grid"
        if self.model is None:
            self.workspace.show_empty()
            self._page_info.setVisible(False)
            return
        if self._view == "single":
            self.workspace.show_single()
            self._page_info.setVisible(True)
            self._render_single()
        else:
            self.workspace.show_grid()
            self._page_info.setVisible(False)
        self._update_view()
        self._sync_buttons()

    # ---------- 文档 ----------

    @property
    def model(self) -> PageModel | None:
        if self._active_path:
            return self._models.get(self._active_path)
        return None

    def _source_items(self) -> list[str]:
        return [p for p in self.files.paths() if not self.files.is_result(p)]

    def _add_document(self, path: str) -> None:
        if path in self._models:
            self._activate_path(path)
            return
        try:
            model = PageModel()
            model.load(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self.center, "无法打开 PDF", str(exc))
            return
        self._models[path] = model
        self.files.add_paths([path])
        self._activate_path(path)

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self.center, "打开 PDF", "", "PDF 文件 (*.pdf)")
        if not paths:
            return
        for path in paths:
            self._add_document(path)

    def _activate_path(self, path: str, force: bool = False) -> bool:
        if path == self._active_path:
            return True
        if not force and self.model is not None and self.model.changed:
            dlg = UnsavedDialog(
                self._active_path,
                self.model.count,
                self.model.count,
                sum(1 for r in self.model.refs if r.rotation),
                self.center,
            )
            dlg.exec()
            if dlg.choice == "cancel":
                self._restore_sidebar_focus()
                return False
            if dlg.choice == "export":
                if not self._export_pages():
                    self._restore_sidebar_focus()
                    return False
            elif dlg.choice == "discard":
                # 真正丢弃：关掉旧模型，下次切回来从源文件重新加载。
                if self._active_path and self._active_path in self._models:
                    self._models[self._active_path].close()
                    del self._models[self._active_path]
        if path not in self._models:
            try:
                model = PageModel()
                model.load(path)
                self._models[path] = model
            except Exception as exc:  # noqa: BLE001
                QMessageBox.critical(self.center, "无法打开 PDF", str(exc))
                self._restore_sidebar_focus()
                return False
        self._active_path = path
        self._suppress_sidebar = True
        self.files.set_focus(path, notify=False)
        item = self.files._item_for(path)
        if item is not None:
            self.files.setCurrentItem(item)
        self._suppress_sidebar = False
        self._page_index = 0
        self._view = "grid"
        self.workspace.show_grid()
        self._page_info.setVisible(False)
        # 新文档默认选中第一页
        self._refresh_grid(select=[0])
        self._update_empty_state()
        self._sync_buttons()
        self._emit_status(
            f"正在整理 {os.path.basename(path)} · 原文件不会被修改")
        return True

    def _restore_sidebar_focus(self) -> None:
        if not self._active_path:
            return
        self._suppress_sidebar = True
        item = self.files._item_for(self._active_path)
        if item is not None:
            self.files.setCurrentItem(item)
        self.files.set_focus(self._active_path, notify=False)
        self._suppress_sidebar = False

    def _on_sidebar_clicked(self, item: QListWidgetItem) -> None:
        if self._suppress_sidebar:
            return
        path = item.data(Qt.UserRole)
        if not path or self.files.is_result(path):
            return
        if path != self._active_path:
            self._activate_path(path)

    def _on_layer_activated(self, path: str) -> None:
        if self.files.is_result(path):
            self._emit_status("导出的结果图层已置顶；双击源文档继续整理页面")
            return
        if path != self._active_path:
            self._activate_path(path)

    def _on_files_changed(self) -> None:
        self._update_empty_state()
        self._update_layer_notes()
        self._refresh_buttons()

    def _update_layer_notes(self) -> None:
        for path in self._source_items():
            if path == self._active_path and self.model is not None:
                note = f"{self.model.count} 页 · 编辑中"
            else:
                note = self.files._note_for(path)
            self.files.set_note(path, note)

    def _update_empty_state(self) -> None:
        if not hasattr(self, "_side_stack"):
            return
        sources = self._source_items()
        has = bool(sources)
        self._side_stack.setCurrentIndex(1 if has else 0)
        layout = self.sidebar.layout()
        if layout is not None and layout.count():
            layout.setStretch(layout.count() - 1, 1 if has else 0)
        if getattr(self, "_side_spacer", None) is not None:
            self._side_spacer.changeSize(
                0, 0, QSizePolicy.Minimum,
                QSizePolicy.Expanding if has else QSizePolicy.Fixed)
        if has:
            self._side_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self._side_stack.setFixedHeight(min(420, 8 + len(sources) * 44))
        else:
            self._side_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self._side_stack.setMinimumHeight(0)
            self._side_stack.setMaximumHeight(16777215)
        if hasattr(self, "_side_empty"):
            self._side_empty.setVisible(not has)
        self.lbl_count.setText(
            f"{len(self._source_items())} 个文档" if has else self.empty_hint)
        if not has:
            self.workspace.grid_view.grid.clear()
            self.workspace.grid_view.meta.setText("共 0 页")
            self.workspace.grid_view.dirty.setVisible(False)
            self.workspace.show_empty()
            self._page_info.setVisible(False)
        else:
            self.lbl_count.setText(
                f"{len(self._source_items())} 个文档"
                + (f" · {self.model.count} 页" if self.model else ""))
            if self._view == "single":
                self.workspace.show_single()
            else:
                self.workspace.show_grid()

    def _remove_document_layer(self) -> None:
        selected = [p for p in self.files.selected_paths()
                    if not self.files.is_result(p)]
        if not selected:
            QMessageBox.information(self.center, "提示", "请先在左侧选中要移除的文档。")
            return
        if self._active_path in selected and self.model is not None and self.model.changed:
            dlg = UnsavedDialog(
                self._active_path, self.model.count,
                self.model.count,
                sum(1 for r in self.model.refs if r.rotation), self.center)
            dlg.exec()
            if dlg.choice == "cancel":
                return
            if dlg.choice == "export" and not self._export_pages():
                return
        for path in selected:
            if path in self._models:
                self._models[path].close()
                del self._models[path]
            self.files.remove_path(path)
        if self._active_path in selected:
            remaining = self._source_items()
            self._active_path = None
            self.workspace.grid_view.grid.clear()
            if remaining:
                self._activate_path(remaining[0], force=True)
            else:
                self._update_empty_state()
        self._sync_buttons()

    def can_close(self) -> bool:
        """关闭主窗口前确认未导出的页面改动。"""
        if self.model is None or not self.model.changed:
            return True
        dlg = UnsavedDialog(
            self._active_path, self.model.count, self.model.count,
            sum(1 for r in self.model.refs if r.rotation), self.center)
        dlg.exec()
        if dlg.choice == "cancel":
            return False
        if dlg.choice == "export":
            return bool(self._export_pages())
        return True

    def shutdown(self) -> None:
        super().shutdown()
        for model in list(self._models.values()):
            try:
                model.close()
            except Exception:
                pass
        self._models.clear()

    def _clear_documents(self) -> None:
        if not self._source_items():
            return
        if self.model is not None and self.model.changed:
            dlg = UnsavedDialog(
                self._active_path, self.model.count, self.model.count,
                sum(1 for r in self.model.refs if r.rotation), self.center)
            dlg.exec()
            if dlg.choice == "cancel":
                return
            if dlg.choice == "export" and not self._export_pages():
                return
        for model in self._models.values():
            model.close()
        self._models.clear()
        self._active_path = None
        self.files.clear_all()
        self.workspace.grid_view.grid.clear()
        self._update_empty_state()
        self._sync_buttons()

    # ---------- 缩略图 ----------

    def _refresh_grid(self, select: list[int] | None = None,
                      current: int | None = None) -> None:
        model = self.model
        grid = self.workspace.grid_view.grid
        if model is None:
            grid.clear()
            self._update_header()
            return
        old_selection = set(self._selected_indices())
        if select is None:
            select = sorted(old_selection)
        grid.blockSignals(True)
        grid.clear()
        for i in range(model.count):
            try:
                data, _w, _h = model.thumbnail(i, 176)
                pm = QPixmap()
                pm.loadFromData(data)
            except Exception:
                pm = QPixmap()
            ref = model.refs[i]
            item = QListWidgetItem()
            item.setData(ROLE_THUMB, pm)
            item.setData(ROLE_NUM, i + 1)
            item.setData(ROLE_BADGE, ref.badge)
            item.setData(ROLE_PAPER, _paper_label(model, i))
            item.setData(ROLE_KIND, ref.kind)
            item.setSizeHint(QSize(188, 266))
            item.setToolTip(f"第 {i + 1} 页 · {_paper_label(model, i)}")
            grid.addItem(item)
        grid.blockSignals(False)
        idx = current if current is not None else (select[0] if select else 0)
        if 0 <= idx < grid.count():
            grid.setCurrentRow(idx)
        grid.clearSelection()
        for i in select:
            if 0 <= i < grid.count():
                grid.item(i).setSelected(True)
        self._update_header()
        self._update_layer_notes()
        self._update_action_panel()
        self._update_view()
        if self._view == "single":
            self._render_single()
        self._sync_buttons()

    def _selected_indices(self) -> list[int]:
        grid = self.workspace.grid_view.grid
        return sorted(self.workspace.grid_view.grid.row(i)
                      for i in grid.selectedItems())

    def _current_index(self) -> int:
        grid = self.workspace.grid_view.grid
        row = grid.currentRow()
        if row < 0 and grid.count():
            row = 0
        return row

    def _update_header(self) -> None:
        model = self.model
        grid_view = self.workspace.grid_view
        if model is None:
            grid_view.meta.setText("共 0 页")
            grid_view.dirty.setVisible(False)
            return
        selected = len(self._selected_indices())
        text = f"共 {model.count} 页"
        if selected:
            text += f"  |  已选 {selected} 页"
        grid_view.meta.setText(text)
        grid_view.dirty.setVisible(model.changed)
        single_meta = self.workspace.single_view.meta
        idx = self._current_index()
        single_meta.setText(f"第 {idx + 1} 页 / 共 {model.count} 页")
        self.workspace.single_view.page_label.setText(
            f"{idx + 1} / {model.count}")
        self.workspace.single_view.btn_prev.setEnabled(idx > 0)
        self.workspace.single_view.btn_next.setEnabled(idx < model.count - 1)
        if self._page_info.isVisible() and model.count:
            ref = model.refs[idx]
            w, h = _page_dims(model, idx)
            paper = _paper_label(model, idx)
            self._lbl_current_page.setText(f"第 {idx + 1} 页 · {paper}")
            self._lbl_current_size.setText(
                f"{_mm(w):.0f} × {_mm(h):.0f} mm · {ref.rotation}°")

    def _update_action_panel(self) -> None:
        if self._view != "grid" or self.model is None:
            self.workspace.grid_view.set_actions_visible(False)
            return
        selected = self._selected_indices()
        idx = self._current_index()
        self.workspace.grid_view.set_actions_visible(bool(selected))
        if selected:
            self.workspace.grid_view.actions.set_context(idx, len(selected))

    def _update_view(self) -> None:
        self.workspace.grid_view.chip_grid.setChecked(self._view == "grid")
        self.workspace.grid_view.chip_single.setChecked(self._view == "single")
        if self._view == "grid":
            self._update_action_panel()
        else:
            self.workspace.grid_view.set_actions_visible(False)

    def _on_grid_selection(self) -> None:
        if self._suppress_sidebar:
            return
        self._update_header()
        self._update_action_panel()
        self._sync_buttons()
        if self._view == "single":
            self._render_single()

    def _on_page_moved(self, source: int, target: int) -> None:
        model = self.model
        if model is None:
            return
        if model.move(source, target):
            self._refresh_grid(select=[target], current=target)
            self._emit_status(f"已把第 {source + 1} 页移动到第 {target + 1} 页")
            self._sync_buttons()

    def _grid_context_menu(self, pos: QPoint) -> None:
        if self.model is None:
            return
        item = self.workspace.grid_view.grid.itemAt(pos)
        if item is not None:
            self.workspace.grid_view.grid.setCurrentItem(item)
        menu = QMenu(self.center)
        act_insert = menu.addAction("插入页面…")
        act_copy = menu.addAction("复制此页")
        act_left = menu.addAction("左转 90°")
        act_right = menu.addAction("右转 90°")
        menu.addSeparator()
        act_delete = menu.addAction("删除此页")
        act_insert.triggered.connect(self._insert_menu)
        act_copy.triggered.connect(self.copy_pages)
        act_left.triggered.connect(lambda: self.rotate_pages(-90))
        act_right.triggered.connect(lambda: self.rotate_pages(90))
        act_delete.triggered.connect(self.delete_pages)
        menu.exec(self.workspace.grid_view.grid.mapToGlobal(pos))

    # ---------- 单页视图 ----------

    def open_single(self, index: int | None = None) -> None:
        if self.model is None:
            return
        if index is not None:
            self.workspace.grid_view.grid.setCurrentRow(index)
        self.set_view("single")

    def single_step(self, step: int) -> None:
        model = self.model
        if model is None:
            return
        idx = min(max(self._current_index() + step, 0), model.count - 1)
        self.workspace.grid_view.grid.setCurrentRow(idx)
        self._update_header()
        self._update_page_info()
        self._render_single()

    def _update_page_info(self) -> None:
        model = self.model
        if model is None or not self._page_info.isVisible():
            return
        idx = self._current_index()
        w, h = _page_dims(model, idx)
        self._lbl_current_page.setText(f"第 {idx + 1} 页 · {_paper_label(model, idx)}")
        self._lbl_current_size.setText(
            f"{_mm(w):.0f} × {_mm(h):.0f} mm · {model.refs[idx].rotation}°")

    def _render_single(self) -> None:
        model = self.model
        if model is None:
            return
        idx = self._current_index()
        try:
            data, _w, _h = model.thumbnail(idx, 1000)
            pm = QPixmap()
            pm.loadFromData(data)
        except Exception:
            pm = QPixmap()
        self.workspace.single_view.canvas.set_pixmap(pm)
        self.workspace.single_view.canvas.set_zoom(self._single_zoom)
        self.workspace.single_view.lbl_zoom.setText(
            "适应" if self._single_zoom is None
            else f"{self._single_zoom * 100:.0f}%")
        self._update_header()
        self._update_page_info()
        self._sync_buttons()

    def single_zoom(self, step: int) -> None:
        current = self._single_zoom
        if current is None:
            current = 1.0
        current = min(3.0, max(0.2, current + step * 0.15))
        self._single_zoom = current if current != 1.0 else None
        self.workspace.single_view.canvas.set_zoom(self._single_zoom)
        self.workspace.single_view.lbl_zoom.setText(
            "适应" if self._single_zoom is None
            else f"{self._single_zoom * 100:.0f}%")

    def single_fit(self) -> None:
        self._single_zoom = None
        self.workspace.single_view.canvas.set_zoom(None)
        self.workspace.single_view.lbl_zoom.setText("适应")

    def grid_zoom(self, step: int) -> None:
        self._grid_scale = min(1.35, max(0.8, self._grid_scale + step * 0.1))
        size = int(158 * self._grid_scale)
        self.workspace.grid_view.grid.setGridSize(QSize(size, int(232 * self._grid_scale)))
        for i in range(self.workspace.grid_view.grid.count()):
            item = self.workspace.grid_view.grid.item(i)
            item.setSizeHint(QSize(size, int(232 * self._grid_scale)))
        self.workspace.grid_view.lbl_zoom.setText(
            "适应" if abs(self._grid_scale - 1.0) < 0.01
            else f"{self._grid_scale * 100:.0f}%")

    def grid_fit(self) -> None:
        self._grid_scale = 1.0
        self.grid_zoom(0)

    # ---------- 编辑 ----------

    def _apply_change(self, keep: list[int] | None = None) -> None:
        self._refresh_grid(select=keep or [self._current_index()],
                           current=(keep[0] if keep else self._current_index()))
        self._update_header()
        self._sync_buttons()

    def delete_pages(self) -> None:
        model = self.model
        if model is None:
            return
        selected = self._selected_indices()
        if not selected:
            QMessageBox.information(self.center, "提示", "请先选中要删除的页面。")
            return
        if len(selected) >= model.count:
            QMessageBox.warning(self.center, "至少保留一页",
                                "页面管理至少需要保留一页；可以删除部分页面后再导出。")
            return
        dlg = DeleteConfirmDialog(selected, self.center)
        if dlg.exec() != QDialog.Accepted:
            return
        idx = selected[0]
        if model.remove(selected):
            next_idx = min(idx, model.count - 1)
            self._refresh_grid(select=[next_idx], current=next_idx)
            self._emit_status(f"已删除 {len(selected)} 页；⌘Z 可撤销")
            self._sync_buttons()

    def copy_pages(self) -> None:
        model = self.model
        if model is None:
            return
        selected = self._selected_indices()
        if not selected:
            selected = [self._current_index()]
        added = model.duplicate(selected)
        if added:
            self._refresh_grid(select=[added[0]], current=added[0])
            self._emit_status(f"已复制 {len(added)} 页")
            self._sync_buttons()

    def rotate_pages(self, degrees: int) -> None:
        model = self.model
        if model is None:
            return
        selected = self._selected_indices() or [self._current_index()]
        if model.rotate(selected, degrees):
            self._refresh_grid(select=selected,
                               current=self._current_index())
            self._emit_status(f"已旋转 {len(selected)} 页 {abs(degrees)}°")
            self._sync_buttons()

    def select_all_pages(self) -> None:
        self.workspace.grid_view.grid.selectAll()
        self._update_header()
        self._update_action_panel()
        self._sync_buttons()

    def clear_selection(self) -> None:
        self.workspace.grid_view.grid.clearSelection()
        self._update_header()
        self._update_action_panel()
        self._sync_buttons()

    def select_range(self) -> None:
        model = self.model
        if model is None:
            return
        text, ok = QInputDialog.getText(
            self.center, "选择范围", "输入页码范围（例如 1-3,5）：",
            QLineEdit.Normal, "")
        if not ok or not text.strip():
            return
        try:
            indices = parse_page_spec(text, model.count)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self.center, "页码无效", str(exc))
            return
        grid = self.workspace.grid_view.grid
        grid.clearSelection()
        if indices:
            grid.setCurrentRow(indices[0])
        for i in indices:
            grid.item(i).setSelected(True)
        self._update_header()
        self._update_action_panel()
        self._sync_buttons()

    def undo(self) -> None:
        model = self.model
        if model is None or not model.undo():
            return
        self._refresh_grid(select=[min(self._current_index(), model.count - 1)])
        self._emit_status("已撤销")
        self._sync_buttons()

    def redo(self) -> None:
        model = self.model
        if model is None or not model.redo():
            return
        self._refresh_grid(select=[min(self._current_index(), model.count - 1)])
        self._emit_status("已重做")
        self._sync_buttons()

    # ---------- 插入 ----------

    def _insert_pos(self, position: str) -> int:
        model = self.model
        if model is None:
            return 0
        idx = self._current_index()
        if position.startswith("第 ") and "之前" in position:
            return idx
        if position.startswith("第 ") and "之后" in position:
            return idx + 1
        return model.count

    def _insert_menu(self) -> None:
        menu = QMenu(self.center)
        act_pdf = menu.addAction("从 PDF 插入…")
        act_image = menu.addAction("从图片插入…")
        act_blank = menu.addAction("插入空白页…")
        act_pdf.triggered.connect(self._insert_pdf)
        act_image.triggered.connect(self._insert_image)
        act_blank.triggered.connect(lambda: self._insert_blank("after"))
        menu.exec(self.center.mapToGlobal(
            self.center.rect().center() - QPoint(70, 20)))

    def _insert_pdf(self, _checked=False) -> None:
        model = self.model
        if model is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self.center, "选择要插入的 PDF", "", "PDF 文件 (*.pdf)")
        if not path:
            return
        dlg = InsertPdfDialog(path, self._current_index(), model.count, self.center)
        if dlg.exec() != QDialog.Accepted:
            return
        pages, position = dlg.values()
        try:
            added = model.insert_pdf(path, self._insert_pos(position), pages)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self.center, "插入失败", str(exc))
            return
        if added:
            self._refresh_grid(select=added, current=added[0])
            self._emit_status(f"已从 PDF 插入 {len(added)} 页")
            self._sync_buttons()

    def _insert_image(self, _checked=False) -> None:
        model = self.model
        if model is None:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self.center, "选择图片", "",
            "图片 (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp)")
        if not paths:
            return
        dlg = InsertImageDialog(paths, self._current_index(), model.count, self.center)
        if dlg.exec() != QDialog.Accepted:
            return
        files, paper, orientation, position = dlg.values()
        at = self._insert_pos(position)
        added = []
        for i, path in enumerate(files):
            try:
                added.append(model.insert_image(path, at + i, paper, orientation))
            except Exception as exc:  # noqa: BLE001
                QMessageBox.critical(self.center, "插入失败", str(exc))
                break
        if added:
            self._refresh_grid(select=added, current=added[0])
            self._emit_status(f"已插入 {len(added)} 张图片页")
            self._sync_buttons()

    def _insert_blank(self, where: str = "after", _checked=False) -> None:
        model = self.model
        if model is None:
            return
        dlg = InsertBlankDialog(self._current_index(), model.count, self.center)
        if where == "before":
            dlg.position.setValue(InsertBlankDialog._positions(
                self._current_index(), model.count)[0])
        if dlg.exec() != QDialog.Accepted:
            return
        count, paper, orientation, position = dlg.values()
        added = model.insert_blank(self._insert_pos(position), paper,
                                   orientation, count)
        if added:
            self._refresh_grid(select=added, current=added[0])
            self._emit_status(f"已插入 {len(added)} 张空白页")
            self._sync_buttons()

    # ---------- 导出 ----------

    def _export_pages(self) -> bool:
        model = self.model
        if model is None:
            return False
        source = self._active_path or "页面整理"
        default = os.path.join(os.path.dirname(source),
                               f"{_stem(source)}_页面整理.pdf")
        path, _ = QFileDialog.getSaveFileName(
            self.center, "导出新 PDF", default, "PDF 文件 (*.pdf)")
        if not path:
            return False
        if self._active_path and os.path.abspath(path) == os.path.abspath(self._active_path):
            QMessageBox.warning(self.center, "不能覆盖原文件",
                                "请选择一个新文件名，原文件不会被修改。")
            return False
        try:
            pages = model.export(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self.center, "导出失败", str(exc))
            return False
        self.files.add_result(path, "结果", f"已导出 · {pages} 页")
        if self._active_path:
            self._suppress_sidebar = True
            item = self.files._item_for(self._active_path)
            if item is not None:
                self.files.setCurrentItem(item)
            self.files.set_focus(self._active_path, notify=False)
            self._suppress_sidebar = False
        self._refresh_grid(select=[min(self._current_index(), model.count - 1)])
        ExportDoneDialog(path, pages, self.center).exec()
        self._emit_status(f"已导出 {pages} 页：{os.path.basename(path)}")
        self._sync_buttons()
        return True

    # ---------- 视图/状态 ----------

    def _build_shortcuts(self) -> None:
        parent = self.center
        self._sc_undo = QShortcut(QKeySequence.StandardKey.Undo, parent)
        self._sc_undo.activated.connect(self.undo)
        self._sc_redo = QShortcut(QKeySequence.StandardKey.Redo, parent)
        self._sc_redo.activated.connect(self.redo)
        self._sc_delete = QShortcut(QKeySequence(Qt.Key_Delete), parent)
        self._sc_delete.activated.connect(self.delete_pages)
        self._sc_backspace = QShortcut(QKeySequence(Qt.Key_Backspace), parent)
        self._sc_backspace.activated.connect(self.delete_pages)
        self._sc_all = QShortcut(QKeySequence.StandardKey.SelectAll, parent)
        self._sc_all.activated.connect(self.select_all_pages)

    def _refresh_buttons(self) -> None:
        model = self.model
        has = model is not None and model.count > 0
        sel = bool(self._selected_indices()) if has else False
        for btn in (self.btn_remove_layer, self.btn_clear_doc):
            btn.setEnabled(bool(self._source_items()))
        for btn in (self.btn_delete, self.btn_copy, self.btn_rotate_left,
                    self.btn_rotate_right, self.btn_insert):
            btn.setEnabled(has)
        self.btn_export.setEnabled(has)
        self.btn_select_all.setEnabled(has)
        self.btn_clear_sel.setEnabled(has)
        self.btn_select_range.setEnabled(has)
        self.btn_undo.setEnabled(bool(model and model.can_undo()))
        self.btn_redo.setEnabled(bool(model and model.can_redo()))
        self._update_view()
        if self.on_state_change is not None:
            self.on_state_change()

    def _build_ribbon(self) -> None:
        self.btn_add = RibbonButton("添加 PDF", "open")
        self.btn_remove_layer = RibbonButton("移除", "remove")
        self.btn_clear_doc = RibbonButton("清空", "clear")
        self.btn_insert = RibbonButton("插入页面", "add")
        self.btn_delete = RibbonButton("删除", "remove")
        self.btn_copy = RibbonButton("复制", "copy")
        self.btn_rotate_left = RibbonButton("旋转左", "rotate_left")
        self.btn_rotate_right = RibbonButton("旋转右", "rotate_right")
        self.btn_undo = RibbonButton("撤销", "undo")
        self.btn_redo = RibbonButton("重做", "redo")
        self.btn_select_all = RibbonButton("全选", "grid")
        self.btn_clear_sel = RibbonButton("清除", "clear")
        self.btn_select_range = RibbonButton("选择范围", "single")
        self.btn_export = RibbonButton("导出新 PDF", "export", primary=True)

        self.btn_add.clicked.connect(self.add_files)
        self.btn_remove_layer.clicked.connect(self._remove_document_layer)
        self.btn_clear_doc.clicked.connect(self._clear_documents)
        self.btn_insert.clicked.connect(self._insert_menu)
        self.btn_delete.clicked.connect(self.delete_pages)
        self.btn_copy.clicked.connect(self.copy_pages)
        self.btn_rotate_left.clicked.connect(lambda: self.rotate_pages(-90))
        self.btn_rotate_right.clicked.connect(lambda: self.rotate_pages(90))
        self.btn_undo.clicked.connect(self.undo)
        self.btn_redo.clicked.connect(self.redo)
        self.btn_select_all.clicked.connect(self.select_all_pages)
        self.btn_clear_sel.clicked.connect(self.clear_selection)
        self.btn_select_range.clicked.connect(self.select_range)
        self.btn_export.clicked.connect(self._export_pages)

        self.set_band(RibbonBand(
            [
                button_group("文件", [self.btn_add, self.btn_remove_layer,
                                      self.btn_clear_doc]),
                button_group("页面", [self.btn_insert, self.btn_delete,
                                      self.btn_copy, self.btn_rotate_left,
                                      self.btn_rotate_right]),
                button_group("编辑", [self.btn_undo, self.btn_redo,
                                      self.btn_select_all, self.btn_clear_sel,
                                      self.btn_select_range]),
            ],
            trailing=button_group("导出", [self.btn_export]),
        ))

    def _build_center(self) -> QWidget:
        self.workspace = PageWorkspace(self)
        return self.workspace

    def _build_sidebar(self) -> QWidget:
        panel = super()._build_sidebar()
        # 当前页信息是页面管理特有的；空态由 _build_page_sidebar_extras 插入。
        self._side_empty = QLabel()
        return panel

    def status_text(self) -> str:
        if self.model is None:
            return "页面管理 · 0 个文档"
        if self._view == "single":
            idx = self._current_index()
            return (f"{os.path.basename(self._active_path or '')} · "
                    f"第 {idx + 1} 页 / 共 {self.model.count} 页 · 适应窗口")
        selected = len(self._selected_indices())
        return (f"{os.path.basename(self._active_path or '')} · "
                f"共 {self.model.count} 页"
                + (f" · 已选 {selected} 页" if selected else ""))

    def add_files_from_paths(self, paths: list[str]) -> None:
        for path in paths:
            if path.lower().endswith(".pdf"):
                self._add_document(path)

    def can_print(self) -> bool:
        return self._active_path is not None and not self._busy()

    def print_hint(self) -> str:
        if not self._active_path:
            return "打印（尚未打开 PDF）"
        return f"打印：{os.path.basename(self._active_path)}"

    def print_current(self) -> None:
        if not self._active_path:
            QMessageBox.warning(self.center, "提示", "请先打开一个 PDF。")
            return
        # 页面整理结果需要先导出；打印源文件时给出明确提示。
        if self.model is not None and self.model.changed:
            QMessageBox.information(
                self.center, "请先导出",
                "当前页面整理结果尚未导出。请先导出新 PDF，再打印导出结果。")
            return
        super().print_current()
