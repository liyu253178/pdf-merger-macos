"""临时交互测试：图层列表、双击预览切换、分页、打印目标优先级。"""

import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf  # noqa: E402
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.ui.main_window import MainWindow  # noqa: E402
from src.ui.theme import APP_STYLE  # noqa: E402
from src.ui.widgets import MODE_EFFECT, MODE_FILE  # noqa: E402

FAILS = []


def check(cond, label, extra=""):
    if cond:
        print(f"  ok   {label}")
    else:
        FAILS.append(label)
        print(f"  FAIL {label} {extra}")


def pump(ms=700):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def make_pdf(path, pages, w=595, h=842, text="X"):
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page(width=w, height=h)
        page.insert_text((72, 100), f"{text} {n + 1}", fontsize=24)
    doc.save(path)
    doc.close()


def main():
    import tempfile

    tmp = tempfile.mkdtemp(prefix="pdftest-")
    s1 = os.path.join(tmp, "报告A.pdf")
    s2 = os.path.join(tmp, "报告B.pdf")
    res = os.path.join(tmp, "报告B_水印.pdf")
    make_pdf(s1, 3, text="A")
    make_pdf(s2, 2, w=400, h=600, text="B")
    make_pdf(res, 2, w=400, h=600, text="R")

    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    win = MainWindow()
    mm = win.merge_module
    wm = win.watermark_module
    win.tabs.setCurrentIndex(1)          # 打印入口作用于当前模块，先切到水印
    lst = wm.files

    print("[1] 初始状态")
    check(lst.count() == 0, "列表为空")
    check(wm.preview.mode() == MODE_EFFECT, "默认处于效果预览")
    check(not win.btn_print.isEnabled(), "无图层时打印入口禁用")

    print("[2] 添加图层")
    lst.add_paths([s1, s2])
    pump(500)
    check(lst.count() == 2, f"两个源图层，实为 {lst.count()}")
    check(lst.focus_path() is None, "尚未指定当前预览对象")
    check(win.btn_print.isEnabled(), "有图层后打印入口可用")
    check("报告A.pdf" in win.btn_print.toolTip(), "打印提示指向首个 PDF",
          win.btn_print.toolTip())

    print("[3] 双击切换预览对象")
    lst.itemDoubleClicked.emit(lst.item(1))
    check(lst.focus_path() == s2, "当前预览对象 = 报告B.pdf")
    check(wm.preview.mode() == MODE_FILE, "自动切到文件预览")
    pump(900)
    check(wm.preview.lbl_page.text() == "1 / 2", f"页码 1/2，实为 {wm.preview.lbl_page.text()}")
    check(not wm.preview.canvas._pixmap.isNull(), "文件预览已渲染出图像")
    first_pix = wm.preview.canvas._pixmap

    print("[4] 翻页与切换文件")
    wm.preview.page_step.emit(1)
    pump(900)
    check(wm.preview.lbl_page.text() == "2 / 2", f"翻到 2/2，实为 {wm.preview.lbl_page.text()}")
    wm.preview.page_step.emit(1)          # 已在末页，应无变化
    pump(400)
    check(wm.preview.lbl_page.text() == "2 / 2", "末页不越界")
    lst.itemDoubleClicked.emit(lst.item(0))
    pump(900)
    check(lst.focus_path() == s1, "切回报告A.pdf")
    check(wm.preview.lbl_page.text() == "1 / 3",
          f"页码应重置为 1/3，实为 {wm.preview.lbl_page.text()}")
    check(wm.preview.canvas._pixmap.cacheKey() != first_pix.cacheKey(), "预览图已刷新")

    print("[5] 结果图层自动置顶")
    wm._load_results([res])
    pump(700)
    order = lst.paths()
    check(order[0] == res, f"结果排在最上方：{order}")
    check(len(order) == 3, f"原有图层全部保留，共 3 个，实为 {len(order)}")
    check(lst.focus_path() == res, "结果自动成为当前预览对象")
    check("结果" in lst.item(0).text(), f"结果图层文案带标记：{lst.item(0).text()}")
    check(wm.preview.mode() == MODE_FILE, "结果加载后处于文件预览")
    check("报告B_水印.pdf" in win.btn_print.toolTip(), "打印对象跟着切到结果",
          win.btn_print.toolTip())

    print("[6] 参数变化回到效果预览")
    wm.edit_line1.setText("机密")
    check(wm.preview.mode() == MODE_EFFECT, "改参数自动回到效果预览")
    pump(900)
    check(not wm.preview.canvas._pixmap.isNull(), "效果预览已渲染")
    wm.preview.btn_file.click()
    check(wm.preview.mode() == MODE_FILE, "手动可切回文件预览")
    pump(900)
    check(wm.preview.lbl_page.text() == "1 / 2", "切回后仍显示当前对象")

    print("[7] 移除当前预览对象后回落")
    lst.setCurrentItem(lst.item(0))
    lst.remove_selected()
    pump(600)
    check(lst.count() == 2, f"剩两个图层，实为 {lst.count()}")
    check(lst.focus_path() is None, "focus 已清空")
    check(wm.preview.mode() == MODE_EFFECT, "自动回落到效果预览")

    print("[8] 清空")
    lst.clear_all()
    check(lst.count() == 0, "清空后无图层")
    check(not wm.preview.btn_file.isEnabled(), "无图层时文件预览禁用")
    check(not win.btn_print.isEnabled(), "无图层时打印禁用")

    print("[9] 合并模块同样具备图层能力")
    mm = win.merge_module
    mm.files.add_paths([s1, s2])
    pump(400)
    mm.files.itemDoubleClicked.emit(mm.files.item(0))
    check(mm.preview.mode() == MODE_FILE, "合并模块双击也切文件预览")
    pump(800)
    check(not mm.preview.canvas._pixmap.isNull(), "合并模块文件预览有图")
    check(mm._print_target() == s1, "合并模块打印对象正确")
    win.tabs.setCurrentIndex(0)
    win._sync_print()
    check(win.btn_print.isEnabled(), "切到合并模块后打印仍可用")

    # ---------------------------------------------------------------- 预览缩放
    print("[10] 预览缩放与渲染倍率")
    win.tabs.setCurrentIndex(1)
    win.show()                       # 布局要真正跑起来，控件尺寸才有意义
    pump(500)
    canvas = wm.preview.canvas
    fit = canvas.fit_zoom()
    check(canvas.is_fit(), "默认处于适应窗口")
    check(canvas.zoom_label() == "适应", f"标签为「适应」，实为 {canvas.zoom_label()}")
    base = canvas.render_scale()
    check(base >= fit * 1.2, f"渲染倍率覆盖显示所需（{base:.2f} ≥ {fit:.2f}×1.2）")

    canvas.zoom_in()
    check(not canvas.is_fit(), "放大后离开适应窗口")
    check(canvas.zoom() > fit, f"缩放比提高：{fit:.2f} → {canvas.zoom():.2f}")
    check(canvas.render_scale() > base,
          f"渲染倍率跟着提高：{base:.2f} → {canvas.render_scale():.2f}")
    check(canvas.zoom_label().endswith("%"), f"标签变成百分比：{canvas.zoom_label()}")

    wm.preview.lbl_zoom.click()      # 点比例标签 = 回适应窗口
    check(canvas.is_fit(), "点比例标签可回适应窗口")
    for _ in range(5):               # 一直缩小到最小档以下
        canvas.zoom_out()
    check(canvas.is_fit(), "缩到最小档以下自动回适应窗口")
    for _ in range(8):               # 一直放大到上限
        canvas.zoom_in()
    check(canvas.render_scale() <= 6.0 + 1e-6,
          f"渲染倍率不超过上限：{canvas.render_scale():.2f}")
    check(canvas._can_pan(), "放大到超出画布后具备平移能力")
    canvas.zoom_fit()

    # ------------------------------------------------------------ 窄窗口的 Ribbon
    print("[11] 窄窗口下功能带不被压扁")
    band_min = wm.band.minimumSizeHint().width()
    win.resize(1360, 860)
    pump(500)
    check(wm.band.width() >= band_min,
          f"常规窗口下功能带按自身宽度排开（{wm.band.width()} ≥ {band_min}）")

    win.resize(900, 760)             # 用户截图里的那种窄窗口
    pump(600)
    bar = win.ribbon_scroll.horizontalScrollBar()
    check(bar.maximum() > 0,
          f"功能带可横向滚动（可滚动 {bar.maximum()}px，视口 {win.ribbon_scroll.viewport().width()}px）")
    check(wm.band.width() >= band_min,
          f"窄窗口下功能带仍按最小宽度显示（{wm.band.width()} ≥ {band_min}）")
    for btn in (wm.btn_apply, wm.btn_secure, wm.btn_add):
        need = btn.fontMetrics().horizontalAdvance(btn.text())
        check(btn.width() >= need + 8,
              f"「{btn.text()}」文字未被压成省略号（{btn.width()} ≥ {need}+8）")
    check(wm.cmb_quality.width() == wm.cmb_quality.minimumWidth(),
          "清晰度下拉没被压窄，仍与相邻控件并排")
    check(wm.band.height() <= 118, f"功能带高度未被滚动条挤高：{wm.band.height()}")

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  -", f)
        return 1
    print("ALL-OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
