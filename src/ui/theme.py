"""全局样式表。

集中在一处是为了让配色和结构保持单一来源； Ribbon 相关的规则依赖
objectName 而非类名选择器，这样在 PySide6 下行为稳定、不会被样式继承打乱。
"""

from __future__ import annotations

APP_STYLE = """
/* ---------- 基础 ---------- */
QMainWindow, QWidget            { background: #FFFFFF; color: #25303F; }
QWidget#moduleNav               { background: #FBFCFD; border-bottom: 1px solid #E4E8EE; }
QStatusBar                      { background: #F7F8FA; border-top: 1px solid #E4E8EE;
                                  color: #6B7280; font-size: 11px; }
QStatusBar::item                { border: none; }
QLabel#statusRight              { color: #6B7280; font-size: 11px; padding-right: 12px; }

/* ---------- 模块切换 ---------- */
QTabBar#moduleTabs              { background: transparent; }
QTabBar#moduleTabs::tab {
    background: transparent; color: #5C6570; padding: 7px 18px; margin: 0 3px;
    border: 1px solid transparent; border-radius: 7px; font-size: 13px;
}
QTabBar#moduleTabs::tab:hover   { color: #1D4ED8; background: #F0F4FB; }
QTabBar#moduleTabs::tab:selected {
    background: #E8F0FE; color: #1D4ED8; font-weight: 600;
    border-color: #D8E4FB;
}
QTabBar#moduleTabs QTabBar::tab { min-height: 20px; }
QLabel#navHint                   { color: #9AA3B2; font-size: 11px; padding-bottom: 3px; }

/* 二级切换（加水印 / 去水印） */
QTabBar#subTabs {
    background: #EEF1F5; border: 1px solid #E2E7EF; border-radius: 7px;
}
QTabBar#subTabs::tab {
    background: transparent; color: #6B7280; padding: 5px 13px;
    margin: 1px; border: 1px solid transparent; border-radius: 5px;
    font-size: 12px;
}
QTabBar#subTabs::tab:selected {
    background: #FFFFFF; color: #1D4ED8; font-weight: 600;
    border-color: #D8E0EC;
}
QTabBar#subTabs::tab:hover:!selected { color: #1D4ED8; }

/* ---------- Ribbon 功能带 ---------- */
QScrollArea#ribbonScroll        { background: #F6F8FB; border: none;
                                  border-bottom: 1px solid #E4E8EE; }
QScrollArea#ribbonScroll > QWidget,
QScrollArea#ribbonScroll > QWidget > QWidget { background: #F6F8FB; }
QStackedWidget#ribbonStack      { background: #F6F8FB; }
QWidget#ribbonBand              { background: #F6F8FB; }
QFrame#ribbonGroup              { background: transparent;
                                  border-right: 1px solid #E7EBF1; }
QLabel#ribbonGroupTitle         { background: transparent; color: #8A93A3;
                                  font-size: 10px; padding-bottom: 2px; }
QLabel[ribbonField="true"]      { background: transparent; color: #6B7280;
                                  font-size: 10px; }

QScrollBar:horizontal {
    background: transparent; height: 8px; margin: 1px 0 2px 0; border: none;
}
QScrollBar::handle:horizontal {
    background: #C9D2E0; border-radius: 4px; min-width: 40px;
}
QScrollBar::handle:horizontal:hover { background: #A9B6C9; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }

QToolButton[ribbon="true"] {
    background: transparent; color: #25303F; border: none;
    border-radius: 6px; padding: 4px 8px; font-size: 12px;
}
QToolButton[ribbon="true"]:hover    { background: #E8EEFB; }
QToolButton[ribbon="true"]:pressed  { background: #D8E2F6; }
QToolButton[ribbon="true"]:disabled { color: #B4BAC4; }

QToolButton[ribbonPrimary="true"] {
    background: #2563EB; color: #FFFFFF; font-weight: 600;
    padding: 0 16px; border-radius: 7px;
}
QToolButton[ribbonPrimary="true"]:hover    { background: #1D4ED8; }
QToolButton[ribbonPrimary="true"]:pressed  { background: #1E40AF; }
QToolButton[ribbonPrimary="true"]:disabled { background: #C8CFDA; color: #FFFFFF; }

QToolButton[ribbonCompact="true"] {
    background: transparent; color: #25303F; border: none;
    border-radius: 6px; padding: 4px 10px; font-size: 12px;
}
QToolButton[ribbonCompact="true"]:hover    { background: #E8EEFB; }
QToolButton[ribbonCompact="true"]:pressed  { background: #D8E2F6; }
QToolButton[ribbonCompact="true"]:disabled { color: #B4BAC4; }

/* ---------- 表单控件 ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: #FFFFFF; border: 1px solid #D8E0EA; border-radius: 5px;
    padding: 3px 7px; min-height: 22px; color: #25303F;
    selection-background-color: #DBE6FB;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: #7AA2F7;
}
QLineEdit:disabled, QComboBox:disabled { color: #A8B0BC; background: #F5F7FA; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox::down-arrow { image: none; border-left: 3px solid transparent;
    border-right: 3px solid transparent; border-top: 4px solid #7A8494;
    margin-right: 5px; }
QComboBox QAbstractItemView {
    background: #FFFFFF; border: 1px solid #D8E0EA; selection-background-color: #E8F0FE;
    selection-color: #1D4ED8; outline: none;
}
QSlider::groove:horizontal { height: 3px; background: #DCE3EC; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #8FB0F5; border-radius: 2px; }
QSlider::handle:horizontal {
    background: #FFFFFF; border: 1px solid #8FA8D8; width: 12px;
    margin: -5px 0; border-radius: 6px;
}

/* ---------- 左侧图层栏 ---------- */
QWidget#mergeEmptyWrap { background: transparent; }
QWidget#sidePanel     { background: #FBFCFD; border-right: 1px solid #E4E8EE; }
QWidget#sideHeader    { background: transparent; }
QLabel#sideTitle      { color: #4B5563; font-size: 12px; font-weight: 600; }
QLabel#sideCount      { color: #8A93A3; font-size: 10px; padding-left: 4px; }
QLabel#sideHint       { color: #9AA3B2; font-size: 10px; padding: 0 4px; }
QToolButton#sideIconBtn {
    background: transparent; border: none; border-radius: 4px; padding: 2px;
}
QToolButton#sideIconBtn:hover { background: #E8EEFB; }
QListWidget#fileList  {
    background: transparent; border: none; outline: none;
}
QListWidget#fileList::item { border: none; padding: 0; }
QListWidget#fileList::item:selected { background: transparent; }
QListWidget#fileList::item:alternate { background: transparent; }

/* ---------- 预览 ---------- */
QWidget#previewPane   { background: #F6F8FB; }
QWidget#previewHeader { background: transparent; }
QFrame#modeSwitch {
    background: #EEF1F5; border: 1px solid #E2E7EF; border-radius: 6px;
}
QToolButton[chip="true"] {
    background: transparent; color: #6B7280; border: 1px solid transparent;
    border-radius: 4px; padding: 3px 11px; font-size: 11px;
}
QToolButton[chip="true"]:hover          { color: #1D4ED8; }
QToolButton[chip="true"]:checked        {
    background: #FFFFFF; color: #1D4ED8; font-weight: 600;
    border-color: #D8E0EC;
}
QToolButton[chip="true"]:disabled       { color: #C0C6D0; }
QLabel#previewTarget  { color: #6B7280; font-size: 10px; }
QLabel#previewPage    { color: #25303F; font-size: 10px; }
QLabel#previewNote    { color: #8A93A3; font-size: 11px; }
QLabel#previewHint    { color: #6B7280; font-size: 11px; }
QLabel#previewFooterChip {
    background: #EEF4FF; border: 1px solid #D8E4FF; border-radius: 6px;
    color: #1D4ED8; font-size: 10px; padding: 4px 12px;
}

QToolButton[pager="true"] {
    background: transparent; border: 1px solid transparent;
    border-radius: 4px; padding: 0;
}
QToolButton[pager="true"]:hover     { background: #E8EEFB; }
QToolButton[pager="true"]:pressed   { background: #D8E2F6; }
QToolButton[pager="true"]:disabled  { background: transparent; }

QToolButton[zoomLabel="true"] {
    background: transparent; color: #4B5563; border: 1px solid transparent;
    border-radius: 4px; padding: 0 4px; font-size: 10px;
}
QToolButton[zoomLabel="true"]:hover { background: #E8EEFB; color: #1D4ED8; }

/* ---------- 步进器 / 选择器 ---------- */
QWidget#stepper {
    background: #FFFFFF; border: 1px solid #D8E0EA; border-radius: 5px;
}
QToolButton#stepperBtn {
    background: transparent; border: none; color: #6B7280;
    font-size: 14px; min-width: 18px; min-height: 22px;
}
QToolButton#stepperBtn:hover { background: #EEF3FB; color: #1D4ED8; }
QToolButton#stepperBtn:disabled { color: #C5CBD5; }
QLabel#stepperValue { color: #25303F; font-size: 12px; }
QToolButton#choiceField {
    background: #FFFFFF; border: 1px solid #D8E0EA; border-radius: 5px;
    color: #25303F; font-size: 11px; padding: 2px 7px; text-align: left;
}
QToolButton#choiceField:hover { border-color: #AFC4EA; background: #F8FAFE; }

/* ---------- 空态 ---------- */
QFrame#emptyCard {
    background: #FFFFFF; border: 1px solid #E4E8EE; border-radius: 12px;
}
QLabel#emptyTitle { color: #25303F; font-size: 14px; font-weight: 600; }
QLabel#emptySub   { color: #8A93A3; font-size: 11px; }
QPushButton#emptyPrimary {
    background: #2563EB; color: #FFFFFF; border: none; border-radius: 6px;
    padding: 8px 20px; font-size: 12px; font-weight: 600;
}
QPushButton#emptyPrimary:hover { background: #1D4ED8; }

/* ---------- 页面管理 ---------- */
QWidget#pageToolbar { background: #FFFFFF; }
QLabel#pageTitle    { color: #25303F; font-size: 13px; font-weight: 600; }
QLabel#pageMeta     { color: #8A93A3; font-size: 11px; }
QLabel#pageDirty    { color: #B45309; font-size: 11px; }
QFrame#pageCard     { background: #FFFFFF; border: 1px solid #E1E6EE;
                      border-radius: 8px; }
QFrame#pageCard[selected="true"] { border: 2px solid #2563EB;
                                   background: #F8FAFF; }
QLabel#pageThumb    { background: #F7F8FA; border-radius: 4px; }
QLabel#pageNumber   { color: #25303F; font-size: 11px; font-weight: 600; }
QLabel#pageBadge    { color: #6B7280; font-size: 10px; background: #F1F4F8;
                      border-radius: 4px; padding: 1px 6px; }
QLabel#pageBadgePdf { color: #1D4ED8; font-size: 10px; background: #EAF1FE;
                      border-radius: 4px; padding: 1px 6px; }
QLabel#pageBadgeImage { color: #0F766E; font-size: 10px; background: #E8F7F4;
                        border-radius: 4px; padding: 1px 6px; }
QFrame#pageActionBar {
    background: #FFFFFF; border: 1px solid #D8E0EA; border-radius: 10px;
}
QToolButton#pageAction {
    background: transparent; border: none; border-radius: 5px;
    padding: 5px 7px; color: #4B5563; font-size: 11px;
}
QToolButton#pageAction:hover { background: #E8EEFB; color: #1D4ED8; }
QToolButton#pageAction:disabled { color: #C5CBD5; }

/* ---------- 进度 ---------- */
QProgressBar {
    background: #EEF1F5; border: 1px solid #E0E4EA; border-radius: 5px;
    text-align: center; min-height: 16px; max-height: 18px;
    color: #25303F; font-size: 11px;
}
QProgressBar::chunk { background: #2563EB; border-radius: 4px; }
QProgressBar[state="success"]::chunk { background: #0F766E; }
QProgressBar[state="success"] { color: #FFFFFF; }
QProgressBar[state="error"]::chunk { background: #D9534F; }
QProgressBar[state="warning"]::chunk { background: #B45309; }

/* ---------- 弹窗 / 菜单 ---------- */
QDialog { background: #FFFFFF; }
QMenu {
    background: #FFFFFF; border: 1px solid #D8E0EA; border-radius: 6px;
    padding: 4px;
}
QMenu::item { padding: 6px 22px 6px 12px; border-radius: 4px; font-size: 12px; }
QMenu::item:selected { background: #E8F0FE; color: #1D4ED8; }
QMenu::separator { height: 1px; background: #EDF0F4; margin: 4px 8px; }
QMessageBox { background: #FFFFFF; }

/* ---------- 去水印：检测结果 ---------- */
QFrame#detectPanel       { background: #FFFFFF; border-left: 1px solid #E4E8EE; }
QWidget#detectHeader     { background: #FFFFFF; border-bottom: 1px solid #EDF0F4; }
QLabel#detectHeaderTitle { color: #25303F; font-size: 13px; font-weight: 600; }
QLabel#detectCount       { color: #8A93A3; font-size: 10px; background: #F1F4F8;
                           border-radius: 8px; padding: 1px 7px; }
QScrollArea#detectScroll { background: #FFFFFF; border: none; }
QWidget#detectContent    { background: #FFFFFF; }
QLabel#detectGroupTitle  { color: #6B7280; font-size: 10px; font-weight: 600;
                           padding: 8px 5px 2px 5px; }
QFrame#detectRow         { background: #FFFFFF; border: 1px solid transparent;
                           border-radius: 6px; }
QFrame#detectRow:hover   { background: #F7F9FD; border-color: #E5ECF7; }
QLabel#detectTitle       { color: #25303F; font-size: 11px; }
QLabel#detectMeta        { color: #8A93A3; font-size: 9px; }
QLabel#detectPage        { color: #6B7280; font-size: 9px; background: #F1F4F8;
                           border-radius: 4px; padding: 1px 5px; }
QLabel#detectEmpty       { color: #9AA3B2; font-size: 11px; padding: 20px; }
QWidget#detectFooter     { background: #FBFCFD; border-top: 1px solid #EDF0F4; }
QLabel#detectSelected    { color: #6B7280; font-size: 10px; }
QToolButton#detectLink   { background: transparent; border: none; color: #2563EB;
                           font-size: 10px; padding: 2px 4px; }
QToolButton#detectLink:hover { color: #1D4ED8; text-decoration: underline; }
QToolButton#detectLink:disabled { color: #C5CBD5; }

QCheckBox#previewCheck { color: #4B5563; font-size: 11px; spacing: 5px; }
QCheckBox#previewCheck::indicator { width: 14px; height: 14px; }
QCheckBox#confirmCheck { color: #4B5563; font-size: 11px; spacing: 6px; background: #F7F9FC; border: 1px solid #E5EAF2; border-radius: 6px; padding: 8px 10px; }
QCheckBox#confirmCheck::indicator { width: 14px; height: 14px; }
QCheckBox#ribbonCheck { color: #6B7280; font-size: 10px; spacing: 4px; }
QCheckBox#detectCheck::indicator { width: 14px; height: 14px; border-radius: 3px;
    border: 1px solid #C9D2E0; background: #FFFFFF; }
QCheckBox#detectCheck::indicator:checked { background: #2563EB; border-color: #2563EB; }
QCheckBox#detectCheck::indicator:disabled { background: #F0F2F5; border-color: #DCE1E8; }
QCheckBox#ribbonCheck::indicator { width: 13px; height: 14px; }

QToolButton[ribbonActive="true"] { background: #E8F0FE; color: #1D4ED8; }

/* ---------- 对话框 ---------- */
QLabel#dialogTitle   { color: #25303F; font-size: 16px; font-weight: 600; }
QLabel#dialogSub     { color: #6B7280; font-size: 11px; }
QFrame#statCard      { background: #F7F9FC; border: 1px solid #E5EAF2;
                        border-radius: 8px; }
QLabel#statValue     { color: #1D4ED8; font-size: 20px; font-weight: 600; }
QLabel#statLabel     { color: #6B7280; font-size: 10px; }
QFrame#warnBox       { background: #FFFBEB; border: 1px solid #FDE8B0;
                        border-radius: 7px; }
QLabel#warnText      { color: #92400E; font-size: 10px; }
QLabel#dialogKeep    { color: #0F766E; font-size: 10px; }
QPushButton#dialogPrimary { background: #2563EB; color: #FFFFFF; border: none;
    border-radius: 6px; padding: 8px 20px; font-size: 12px; font-weight: 600; }
QPushButton#dialogPrimary:hover { background: #1D4ED8; }
QPushButton#dialogPrimary:disabled { background: #BFD3FA; color: #FFFFFF; }
QPushButton#dialogCancel { background: #FFFFFF; color: #4B5563;
    border: 1px solid #D8E0EA; border-radius: 6px; padding: 7px 18px; font-size: 12px; }
QPushButton#dialogCancel:hover { background: #F5F7FA; }

/* ---------- 页面管理：网格 / 单页 ---------- */
QWidget#pageHeader  { background: #FFFFFF; border-bottom: 1px solid #E7EBF1; }
QWidget#pageEmptyWrap { background: #F7F8FA; }
QListWidget#pageGrid { background: #F7F8FA; border: none; outline: none; }
QListWidget#pageGrid::item { background: transparent; }
QToolButton[chip="true"] {
    background: transparent; color: #6B7280; border: 1px solid transparent;
    border-radius: 5px; padding: 3px 11px; font-size: 11px;
}
QToolButton[chip="true"]:checked {
    background: #FFFFFF; color: #1D4ED8; border-color: #D8E0EC;
    font-weight: 600;
}
QToolButton[chip="true"]:hover:!checked { color: #1D4ED8; background: #F0F4FB; }
QToolButton[pager="true"] {
    background: transparent; border: none; border-radius: 5px; padding: 3px 5px;
}
QToolButton[pager="true"]:hover { background: #EEF3FB; }
QToolButton[zoomLabel="true"] {
    background: transparent; border: none; color: #6B7280; font-size: 11px;
    padding: 2px 3px;
}
QToolButton[zoomLabel="true"]:hover { color: #1D4ED8; }
QToolButton#pageBack {
    background: transparent; border: none; color: #2563EB; font-size: 11px;
    padding: 3px 5px;
}
QToolButton#pageBack:hover { background: #E8F0FE; border-radius: 4px; }
QWidget#pageActionWrap { background: #F6F8FB; }
QFrame#pageActionPanel {
    background: #FFFFFF; border: 1px solid #E4EAF2; border-radius: 10px;
}
QLabel#pageActionTitle {
    color: #25303F; font-size: 12px; font-weight: 600; padding: 2px 4px;
}
QToolButton#pageActionRow {
    background: transparent; border: none; border-radius: 5px;
    color: #4B5563; font-size: 11px; padding: 5px 7px; text-align: left;
}
QToolButton#pageActionRow:hover { background: #EEF3FB; color: #1D4ED8; }
QToolButton#pageActionRow[danger="true"] { color: #B91C1C; }
QToolButton#pageActionRow[danger="true"]:hover { background: #FEF2F2; }
QToolButton#roundPageBtn {
    background: #FFFFFF; border: 1px solid #E1E6EE; border-radius: 15px;
    padding: 5px;
}
QToolButton#roundPageBtn:hover { border-color: #AFC4EA; background: #F8FAFE; }
QToolButton#roundPageBtn:disabled { background: #F5F7FA; border-color: #EDF0F4; }

/* 页面管理侧栏 */
QLabel#sideEmptyTitle { color: #6B7280; font-size: 11px; font-weight: 600; }
QLabel#sideEmptySub   { color: #9AA3B2; font-size: 10px; }
QWidget#sidePageInfo  { background: #F2F6FC; border: 1px solid #E4EBF5;
                        border-radius: 7px; }
QLabel#sidePageInfoTitle { color: #8A93A3; font-size: 9px; }
QLabel#sidePageInfoMain  { color: #25303F; font-size: 11px; font-weight: 600; }
QLabel#sidePageInfoSub   { color: #6B7280; font-size: 9px; }

/* 页面管理对话框 */
QFrame#unsavedCard { background: #F7F9FC; border: 1px solid #E5EAF2;
                     border-radius: 8px; }
QLabel#unsavedTag { color: #B45309; background: #FFF4E0; border-radius: 4px;
                    padding: 2px 7px; font-size: 9px; }
QLabel#deleteChip { color: #B91C1C; background: #FEF2F2; border: 1px solid #FAD4D4;
                    border-radius: 4px; padding: 2px 7px; font-size: 10px; }
QLabel#dialogPreview { background: #F7F8FA; border: 1px dashed #D8E0EA;
                       border-radius: 7px; color: #9AA3B2; font-size: 10px; }
QWidget#dialogPreviewStrip { background: #F7F8FA; border: 1px dashed #D8E0EA;
                             border-radius: 8px; }
QLabel#dialogThumb { background: #FFFFFF; border: 1px solid #E2E7EF; }
QLabel#dialogFile { color: #4B5563; font-size: 11px; }
QListWidget#dialogFileList { background: #FFFFFF; border: 1px solid #E1E6EE;
                             border-radius: 6px; padding: 3px; }
QPushButton#dialogDanger {
    background: #DC2626; color: #FFFFFF; border: none; border-radius: 6px;
    padding: 8px 20px; font-size: 12px; font-weight: 600;
}
QPushButton#dialogDanger:hover { background: #B91C1C; }
QPushButton#dialogDangerOutline {
    background: #FFFFFF; color: #B91C1C; border: 1px solid #F0BABA;
    border-radius: 6px; padding: 7px 16px; font-size: 12px;
}
QPushButton#dialogDangerOutline:hover { background: #FEF2F2; }
"""
