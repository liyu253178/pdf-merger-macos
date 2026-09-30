"""Ribbon（功能区）控件：带装功能组 + 主操作区。

Office 风格的 Ribbon 在这里不是为了好看，而是因为它恰好解决本项目的问题：
所有参数一眼可见、无需在折叠面板里翻找，同时把以前占三栏宽度的参数区
压缩成一条横向带，从而把空间让给预览。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QFormLayout, QFrame, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout,
    QWidget,
)

from .icons import svg_icon

BAND_HEIGHT = 118          # 功能带高度，够放四行紧凑表单
ICON_SIZE = 30
COMPACT_ICON_SIZE = 16     # 顶部工具条的矮版按钮


class RibbonButton(QToolButton):
    """图标在上、文字在下的功能区按钮。`primary` 表示主操作，用强调色填充。

    `compact=True` 换成「图标在左、文字在右」的矮版，用于顶部工具条这种
    高度受限的位置（功能带里放不下，也没必要那么高）。

    非紧凑按钮会把最小宽度**按文字实际宽度算出来**。QToolButton 默认的
    minimumSizeHint 不含完整文字宽度，窗口一窄，按钮就会被压成
    「应用…导出」这种半截省略号 —— 这是横向空间不足时最先暴露的难看点，
    所以宁可让按钮拒绝收缩，由功能带整体横向滚动去承担。
    """

    def __init__(self, text: str, icon_name: str | None = None,
                 primary: bool = False, compact: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setText(text)
        self.setToolTip(text)
        if compact:
            self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            self.setIconSize(QSize(COMPACT_ICON_SIZE, COMPACT_ICON_SIZE))
            self.setProperty("ribbonCompact", "true")
        else:
            self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
            self.setProperty("ribbon", "true")
            self.setProperty("ribbonPrimary", "true" if primary else "false")
            self.setMinimumWidth(self._needed_width(text, primary))
        if icon_name:
            self.setIcon(svg_icon(icon_name))

    def _needed_width(self, text: str, primary: bool) -> int:
        """文字整行放得下的宽度：主操作左右各留 14px（见 QSS 的 padding）。"""
        text_width = self.fontMetrics().horizontalAdvance(text)
        base = 96 if primary else 56
        return max(base, text_width + (34 if primary else 22))


class RibbonGroup(QFrame):
    """一个功能组：主体区域 + 底部居中的组名，右侧用竖线与邻组分隔。

    主体有「按钮行」和「紧凑表单」两种形态，由工厂函数创建。
    """

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ribbonGroup")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 2)
        outer.setSpacing(0)
        self.body = QWidget()
        outer.addWidget(self.body, 1)

        caption = QLabel(title)
        caption.setObjectName("ribbonGroupTitle")
        caption.setAlignment(Qt.AlignHCenter | Qt.AlignBottom)
        outer.addWidget(caption)

    def _use_layout(self, layout):
        self.body.setLayout(layout)
        return layout


def button_group(title: str, buttons: list[QToolButton]) -> RibbonGroup:
    """横向排列一组功能区按钮。"""
    group = RibbonGroup(title)
    row = group._use_layout(QHBoxLayout())
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(2)
    for btn in buttons:
        row.addWidget(btn)
    return group


def form_group(title: str, rows: list[tuple[str, QWidget]]) -> RibbonGroup:
    """标签在左、控件在右的紧凑表单。"""
    group = RibbonGroup(title)
    form = group._use_layout(QFormLayout())
    form.setContentsMargins(0, 0, 0, 0)
    form.setSpacing(3)
    form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    form.setFormAlignment(Qt.AlignVCenter | Qt.AlignLeft)
    for label, widget in rows:
        caption = QLabel(label)
        caption.setProperty("ribbonField", "true")
        form.addRow(caption, widget)
    return group


class RibbonBand(QWidget):
    """一条完整的功能带。

    `groups` 从左向右排列；`trailing` 是主操作组，会被推到最右侧，
    与主操作目视对齐而不是紧贴参数区。
    """

    def __init__(self, groups: list[RibbonGroup],
                 trailing: RibbonGroup | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ribbonBand")
        self.setFixedHeight(BAND_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 1, 3, 0)
        layout.setSpacing(1)
        for group in groups:
            layout.addWidget(group, 0)
        if trailing is not None:
            layout.addStretch(1)
            layout.addWidget(trailing, 0)
        else:
            layout.addStretch(1)


def control_row_group(title: str, entries: list) -> RibbonGroup:
    """一行里混合按钮与带小标题的控件，例如「主按钮 + 次按钮 + 清晰度下拉」。

    entries 里的元素可以是控件，也可以是 (小标题, 控件) 元组。
    """
    group = RibbonGroup(title)
    row = group._use_layout(QHBoxLayout())
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    for entry in entries:
        if isinstance(entry, tuple):
            caption = QLabel(entry[0])
            caption.setProperty("ribbonField", "true")
            row.addWidget(caption)
            row.addWidget(entry[1])
        else:
            row.addWidget(entry)
    return group


def row_widget(*widgets: QWidget, spacing: int = 4) -> QWidget:
    """把若干小控件并成一行，用于「滑块 + 数值框」这类组合。"""
    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(spacing)
    for widget in widgets:
        row.addWidget(widget)
    return host
