"""全局样式表。

集中在一处是为了让配色和结构保持单一来源； Ribbon 相关的规则依赖
objectName 而非类名选择器，这样在 PySide6 下行为稳定、不会被样式继承打乱。
"""

from __future__ import annotations

APP_STYLE = """
/* ---------- 窗口框架 ---------- */
QMainWindow            { background: #ffffff; }
QStatusBar             { background: #f7f8fa; border-top: 1px solid #e4e8ee; }

/* ---------- 模块切换（左上角） ---------- */
QWidget#moduleNav      { background: #eef1f5; border-bottom: 1px solid #e0e4ea; }
QTabBar#moduleTabs     { background: transparent; }
QTabBar#moduleTabs::tab {
    background: #eef1f5; color: #5c6570; padding: 5px 20px; margin-right: 2px;
    border: 1px solid transparent;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
}
QTabBar#moduleTabs::tab:hover     { color: #1d4ed8; }
QTabBar#moduleTabs::tab:selected  {
    background: #f7f8fa; color: #1d4ed8; font-weight: 600;
    border-color: #e0e4ea; border-bottom-color: #f7f8fa;
}
QLabel#navHint         { color: #8a93a3; font-size: 11px; padding-bottom: 4px; }

/* ---------- Ribbon 功能带 ---------- */
QScrollArea#ribbonScroll      { background: #f7f8fa; border: none;
                                border-bottom: 1px solid #e0e4ea; }
QScrollArea#ribbonScroll > QWidget > QWidget { background: #f7f8fa; }
QWidget#ribbonBand        { background: #f7f8fa; }
QFrame#ribbonGroup        { background: transparent; border-right: 1px solid #e4e8ee; }
QLabel#ribbonGroupTitle   { background: transparent; color: #8a93a3; font-size: 11px; }
QLabel[ribbonField="true"]{ background: transparent; color: #6b7280; font-size: 11px; }

/* 功能带的横向滚动条：压到 8px，不用 macOS 默认的那种粗条，
   否则它会把 118px 高的功能带吃掉一大块 */
QScrollBar:horizontal {
    background: transparent; height: 8px; margin: 1px 0 2px 0; border: none;
}
QScrollBar::handle:horizontal {
    background: #c9d2e0; border-radius: 4px; min-width: 40px;
}
QScrollBar::handle:horizontal:hover { background: #a9b6c9; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }

QToolButton[ribbon="true"] {
    background: transparent; color: #25303f; border: none;
    border-radius: 6px; padding: 4px 8px; font-size: 12px;
}
QToolButton[ribbon="true"]:hover    { background: #e8eefb; }
QToolButton[ribbon="true"]:pressed  { background: #d8e2f6; }
QToolButton[ribbon="true"]:disabled { color: #b4bac4; }

QToolButton[ribbonPrimary="true"] {
    background: #2563eb; color: #ffffff; font-weight: 600; padding: 6px 14px;
}
QToolButton[ribbonPrimary="true"]:hover    { background: #1d4ed8; }
QToolButton[ribbonPrimary="true"]:disabled { background: #c8cfda; color: #ffffff; }

/* 顶部工具条上的矮版按钮（打印） */
QToolButton[ribbonCompact="true"] {
    background: transparent; color: #25303f; border: none;
    border-radius: 6px; padding: 3px 10px; font-size: 12px;
}
QToolButton[ribbonCompact="true"]:hover    { background: #e8eefb; }
QToolButton[ribbonCompact="true"]:pressed  { background: #d8e2f6; }
QToolButton[ribbonCompact="true"]:disabled { color: #b4bac4; }

/* ---------- 左侧窄图层栏 ---------- */
QWidget#sidePanel     { background: #fbfcfd; border-right: 1px solid #e4e8ee; }
QLabel#sideTitle      { color: #6b7280; font-size: 12px; font-weight: 600; }
QLabel#sideCount      { color: #8a93a3; font-size: 11px; }
QLabel#sideHint       { color: #9aa3b2; font-size: 11px; }
QListWidget#fileList  {
    background: #ffffff; border: 1px solid #e4e8ee; border-radius: 6px; outline: none;
}
QListWidget#fileList::item            { padding: 5px 6px; }
QListWidget#fileList::item:selected   { background: #dbe6fb; color: #1f2937; }
QListWidget#fileList::item:alternate  { background: #fafbfc; }

/* ---------- 预览与进度 ---------- */
QWidget#previewHeader { background: transparent; }
QLabel#previewTarget  { color: #6b7280; font-size: 11px; }
QLabel#previewPage    { color: #25303f; font-size: 11px; }
QLabel#previewHint  { color: #6b7280; font-size: 12px; }

QToolButton[chip="true"] {
    background: transparent; color: #6b7280; border: 1px solid transparent;
    border-radius: 5px; padding: 2px 10px; font-size: 12px;
}
QToolButton[chip="true"]:hover          { background: #eef2f9; color: #1d4ed8; }
QToolButton[chip="true"]:checked        {
    background: #dbe6fb; color: #1d4ed8; font-weight: 600;
}
QToolButton[chip="true"]:disabled       { color: #c0c6d0; }

QToolButton[pager="true"] {
    background: transparent; border: 1px solid transparent;
    border-radius: 5px; padding: 0;
}
QToolButton[pager="true"]:hover     { background: #eef2f9; }
QToolButton[pager="true"]:pressed   { background: #d8e2f6; }
QToolButton[pager="true"]:disabled  { background: transparent; }

/* 缩放比例（点击回到「适应窗口」） */
QToolButton[zoomLabel="true"] {
    background: transparent; color: #4b5563; border: 1px solid transparent;
    border-radius: 5px; padding: 0 4px; font-size: 11px;
}
QToolButton[zoomLabel="true"]:hover { background: #eef2f9; color: #1d4ed8; }

QProgressBar {
    border: 1px solid #e0e4ea; border-radius: 5px;
    text-align: center; min-height: 16px; max-height: 18px;
}
QProgressBar::chunk { background: #3b82f6; border-radius: 4px; }
"""
