"""临时截图脚本：把窗口渲染成 PNG 以便肉眼检查布局。"""

import os
import sys
import tempfile

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf  # noqa: E402
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.ui.main_window import MainWindow  # noqa: E402
from src.ui.theme import APP_STYLE  # noqa: E402

OUT = os.environ.get("SHOTS_OUT", os.path.join(tempfile.gettempdir(), "pdftk-shots"))


def pump(ms=800):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def shoot(win, name):
    """QPixmap.save() 在目录不存在时是**静默失败**的（返回 False 不抛异常），
    所以这里先建目录，并检查返回值 —— 否则会误以为截图成功。"""
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    if not win.grab().save(path):
        raise RuntimeError(f"截图保存失败：{path}")
    print(f"saved {path}")


def make_pdf(path, pages, w, h, text, small=False):
    """`small=True` 时再叠一行极小的字，用来检查预览清晰度。

    真实场景里这就是 4 合 1 发票拼版后的效果：正文字号只剩 3pt 出头。
    """
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page(width=w, height=h)
        page.insert_text((60, 90), f"{text}", fontsize=26)
        page.insert_text((60, 150), f"第 {n + 1} / {pages} 页", fontsize=16)
        page.draw_rect(pymupdf.Rect(50, 60, w - 50, h - 60), color=(0.7, 0.75, 0.85))
        if small:
            y = 220
            for i in range(14):
                page.insert_text((60, y), "购买方名称:珠海金华威数码科技有限公司  小字清晰度测试 1234567890",
                                 fontsize=3.4)
                y += 6
    doc.save(path)
    doc.close()


def main():
    tmp = tempfile.mkdtemp(prefix="pdfshot-")
    s1 = os.path.join(tmp, "2026年9月发票汇总.pdf")
    s2 = os.path.join(tmp, "合同扫描件-第二版.pdf")
    res = os.path.join(tmp, "2026年9月发票汇总_水印.pdf")
    res2 = os.path.join(tmp, "保密输出_合并结果.pdf")
    make_pdf(s1, 2, 595, 842, "发票汇总", small=True)
    make_pdf(s2, 3, 595, 842, "合同扫描件")
    make_pdf(res, 2, 595, 842, "已加水印")
    make_pdf(res2, 2, 595, 842, "保密输出")

    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    win = MainWindow()
    win.resize(1360, 860)
    win.show()
    win.tabs.setCurrentIndex(1)          # 先看水印模块
    pump(300)

    wm = win.watermark_module
    wm.files.add_paths([s1, s2])
    pump(500)
    wm._load_results([res, res2])
    wm.files.itemDoubleClicked.emit(wm.files.item(1))
    pump(1200)
    shoot(win, "shot_watermark.png")

    # 效果预览
    wm.preview.btn_effect.click()
    pump(1500)
    shoot(win, "shot_effect.png")

    # 合并模块
    win.tabs.setCurrentIndex(0)
    mm = win.merge_module
    mm.files.add_paths([s1, s2])
    pump(400)
    mm.files.itemDoubleClicked.emit(mm.files.item(0))
    pump(1400)
    shoot(win, "shot_merge.png")

    # 放大看小字：预览清晰度的关键场景
    canvas = mm.preview.canvas
    canvas.zoom_in()
    canvas.zoom_in()
    pump(1600)
    shoot(win, "shot_merge_zoom.png")
    canvas.zoom_fit()

    # 窄窗口：功能带应该横向滚动而不是把按钮压成省略号
    win.resize(900, 760)
    win.tabs.setCurrentIndex(1)
    pump(900)
    shoot(win, "shot_narrow.png")

    # 滚到最右，确认导出区在窄窗口下依然完整可读
    bar = win.ribbon_scroll.horizontalScrollBar()
    bar.setValue(bar.maximum())
    pump(400)
    shoot(win, "shot_narrow_export.png")

    # 无文件的空状态
    win.resize(1360, 860)
    wm.files.clear_all()
    pump(600)
    shoot(win, "shot_empty.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
