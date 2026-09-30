"""主窗口外壳：Ribbon 布局的装配。

窗口本身不含业务逻辑，只负责四件事：
* 左上角切换模块；
* 把当前模块的功能带、图层栏、预览区显示出来；
* 右上角提供「打印」入口，作用于当前模块的预览对象；
* 转发状态栏消息。

功能带放在一条可横向滚动的窄槽里：水印模块功能带完整排开要 1300px 往上，
窗口一收窄，Qt 就会把按钮压成省略号、下拉框叠在一起 —— 与其让控件变形，
不如让它整体滚动。滚动条只在需要时出现，且专门留了一条 11px 的槽位。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMainWindow, QScrollArea, QSplitter,
    QStackedWidget, QTabBar, QVBoxLayout, QWidget,
)

from .modules import MergeModule, WatermarkModule
from .ribbon import BAND_HEIGHT, RibbonButton

SIDE_WIDTH = 208          # 窄图层栏宽度：够看文件名，又不挤压预览
RIBBON_SCROLLBAR_H = 11   # 功能带下方预留给横向滚动条的高度


class MainWindow(QMainWindow):
    """左上角切换模块，下方是各自的 Ribbon 功能区与工作区。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF 工具箱")
        self.resize(1360, 860)
        # 比功能带的完整宽度小得多，但功能带现在会横向滚动，
        # 所以这个下限只由「图层栏 + 能用的预览区」决定。
        self.setMinimumWidth(880)

        self.status = self.statusBar()
        self.status.showMessage("就绪")

        self.merge_module = MergeModule(self._show_status)
        self.watermark_module = WatermarkModule(self._show_status)
        self._modules = [self.merge_module, self.watermark_module]
        # 图层/任务状态一变，打印入口的可用性与提示文字要跟着走
        for module in self._modules:
            module.on_state_change = self._sync_print

        root = QWidget()
        column = QVBoxLayout(root)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        column.addWidget(self._build_nav())
        column.addWidget(self._build_band_stack())
        column.addWidget(self._build_workspace(), 1)

        self.setCentralWidget(root)
        self._switch_module(0)
        self._sync_print()

    # ---------- 装配 ----------

    def _build_nav(self) -> QWidget:
        nav = QWidget()
        nav.setObjectName("moduleNav")
        layout = QHBoxLayout(nav)
        layout.setContentsMargins(8, 5, 8, 0)
        layout.setSpacing(6)

        self.tabs = QTabBar()
        self.tabs.setObjectName("moduleTabs")
        self.tabs.setExpanding(False)
        self.tabs.setDocumentMode(True)
        for module in self._modules:
            self.tabs.addTab(module.title)
        self.tabs.currentChanged.connect(self._switch_module)
        layout.addWidget(self.tabs)

        hint = QLabel("拖文件到左侧图层列表即可添加")
        hint.setObjectName("navHint")
        layout.addStretch(1)
        layout.addWidget(hint)

        self.btn_print = RibbonButton("打印", "print", compact=True)
        self.btn_print.clicked.connect(self._print_current)
        layout.addWidget(self.btn_print)
        return nav

    def _build_band_stack(self) -> QScrollArea:
        self._band_stack = QStackedWidget()
        for module in self._modules:
            self._band_stack.addWidget(module.band)

        scroll = QScrollArea()
        scroll.setObjectName("ribbonScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.NoFocus)
        scroll.setWidget(self._band_stack)
        # 功能带是定高的，滚动条要另占一条窄槽：不留高度的话，窗口一窄
        # 滚动条就会压在组名上。不需要滚动时把这条槽收掉，免得功能带
        # 下面永远挂着一条空带。
        bar = scroll.horizontalScrollBar()
        bar.rangeChanged.connect(
            lambda lo, hi: scroll.setFixedHeight(
                BAND_HEIGHT + (RIBBON_SCROLLBAR_H if hi > lo else 0))
        )
        scroll.setFixedHeight(BAND_HEIGHT)
        self.ribbon_scroll = scroll
        return scroll

    def _build_workspace(self) -> QSplitter:
        self._side_stack = QStackedWidget()
        self._center_stack = QStackedWidget()
        self._side_stack.setMinimumWidth(178)

        for module in self._modules:
            self._side_stack.addWidget(module.sidebar)
            self._center_stack.addWidget(module.center)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(4)
        splitter.addWidget(self._side_stack)
        splitter.addWidget(self._center_stack)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([SIDE_WIDTH, self.width() - SIDE_WIDTH])
        return splitter

    # ---------- 切换 ----------

    def _active_module(self):
        index = self.tabs.currentIndex()
        if not 0 <= index < len(self._modules):
            return self._modules[0]
        return self._modules[index]

    def _switch_module(self, index: int) -> None:
        if not 0 <= index < len(self._modules):
            return
        self._band_stack.setCurrentIndex(index)
        self._side_stack.setCurrentIndex(index)
        self._center_stack.setCurrentIndex(index)
        self._sync_print()
        self._show_status(f"当前模块：{self._modules[index].title}")

    # ---------- 打印 ----------

    def _sync_print(self) -> None:
        module = self._active_module()
        self.btn_print.setEnabled(module.can_print())
        self.btn_print.setToolTip(module.print_hint())

    def _print_current(self) -> None:
        self._active_module().print_current()
        self._sync_print()

    # ---------- 状态 ----------

    def _show_status(self, message: str) -> None:
        self.status.showMessage(message.split("\n")[0])
