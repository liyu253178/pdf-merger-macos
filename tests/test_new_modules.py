"""新增能力回归：去水印核心、页面模型、去水印 UI、页面管理 UI。

这些测试都用真实 PDF 和真实 Qt 控件，不打桩核心逻辑；但会拦掉
QFileDialog / QMessageBox / 自定义确认框，避免测试在无人值守时等待用户。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf  # noqa: E402
from PySide6.QtCore import QEventLoop, QTimer, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDialog, QFileDialog, QMessageBox,
)

from src.core.fonts import default_font, discover_fonts  # noqa: E402
from src.core.page_model import PageModel  # noqa: E402
from src.core.pdf_io import open_pdf  # noqa: E402
from src.core.watermark import WatermarkConfig, Watermarker  # noqa: E402
from src.core.watermark_remover import (  # noqa: E402
    WatermarkDetector, WatermarkObject, WatermarkRemover,
)
from src.ui.main_window import MainWindow  # noqa: E402
from src.ui.theme import APP_STYLE  # noqa: E402
import src.ui.page_module as page_ui  # noqa: E402
import src.ui.watermark_remove as remove_ui  # noqa: E402

FAILS: list[str] = []
OKS = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global OKS
    if cond:
        OKS += 1
        print(f"  ok   {label}")
    else:
        FAILS.append(label)
        print(f"  FAIL {label} {extra}")


def pump(ms: int = 250) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def make_pdf(path: str, pages: int, text: str = "正文",
             width: float = 595, height: float = 842) -> None:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=width, height=height)
        page.insert_text((60, 90), f"{text} {i + 1}", fontsize=20)
    doc.save(path)
    doc.close()


def make_image(path: str) -> None:
    img = QImage(120, 80, QImage.Format_RGB888)
    img.fill(QColor("#2563EB"))
    assert img.save(path)


def test_core_remover(tmp: str) -> None:
    print("[1] 去水印核心：文字 / 注释 / 小图")
    src = os.path.join(tmp, "core.pdf")
    wm = os.path.join(tmp, "core_wm.pdf")
    make_pdf(src, 2, "BODY")
    font = default_font(discover_fonts())
    Watermarker(WatermarkConfig(text="机密文件", angle=-45, font=font,
                                opacity=0.25)).apply_to_file(src, wm)
    doc = open_pdf(wm)
    try:
        objects = WatermarkDetector(query="机密文件", auto=False).detect(doc)
        check(bool(objects), f"检测到文字水印对象（{len(objects)} 个）")
        check(all(o.kind == "text" for o in objects), "对象类型为文字")
        stats = WatermarkRemover().remove(doc, objects)
        check(stats.text == len(objects), f"删除计数 {stats.text}/{len(objects)}")
        out = os.path.join(tmp, "core_removed.pdf")
        doc.save(out)
    finally:
        doc.close()
    with pymupdf.open(out) as d:
        text = " ".join(pg.get_text() for pg in d)
        check("机密文件" not in text, "导出后文字水印消失")
        check(text.count("BODY") >= 2, "导出后正文保留")
    with pymupdf.open(wm) as d:
        text = " ".join(pg.get_text() for pg in d)
        check("机密文件" in text, "原文件未被修改")

    # 没有 q…Q 包裹的文字对象：应回退到 BT…ET 范围，否则整行不可勾选。
    noq = os.path.join(tmp, "core_noq.pdf")
    noq_out = os.path.join(tmp, "core_noq_removed.pdf")
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 50), "x", fontsize=10)   # 先建立 /helv 字体资源
    xref = page.get_contents()[0]
    doc.update_stream(
        xref,
        b"BT /helv 36 Tf 1 0 0 1 100 500 Tm (CONFIDENTIAL) Tj ET\n"
        b"BT /helv 10 Tf 1 0 0 1 50 50 Tm (BODY) Tj ET\n")
    doc.save(noq)
    doc.close()
    doc = open_pdf(noq)
    try:
        objects = WatermarkDetector(query="CONFIDENTIAL", auto=False).detect(doc)
        check(len(objects) == 1 and objects[0].removable,
              "无 q/Q 包裹的文字水印可勾选")
        stats = WatermarkRemover().remove(doc, objects)
        check(stats.text == 1, "无 q/Q 文字水印已删除")
        doc.save(noq_out)
    finally:
        doc.close()
    with pymupdf.open(noq_out) as d:
        text = " ".join(pg.get_text() for pg in d)
        check("CONFIDENTIAL" not in text, "无 q/Q 导出后水印消失")
        check("BODY" in text, "无 q/Q 导出后正文保留")

    # 同一 BT…ET 里画了页眉 + 两处斜向水印 + 尺寸标注；旁边还有一张
    # 3D 轴测图。必须只删三次水印文字，尺寸和图片都保留。
    multi = os.path.join(tmp, "core_multi.pdf")
    multi_out = os.path.join(tmp, "core_multi_removed.pdf")
    stamp = os.path.join(tmp, "core_multi_image.png")
    make_image(stamp)
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text((50, 50), "x", fontsize=10)   # 建立 /helv
    xref = page.get_contents()[0]
    doc.update_stream(
        xref,
        b"BT /helv 1 Tf\n"
        b"1 0 0 rg\n11.811 0 0 11.811 754.88 796.73 Tm\n"
        b"<434F4E464944454E5449414C20444F43554D454E5453>Tj\n"
        b"0.72942 0.72942 0.72942 rg\n"
        b"69.92091 69.92091 -69.92093 69.92091 243.57 272.08 Tm\n"
        b"(confidential)Tj\n2.86225 -2.9585 Td\n(confidential)Tj\n"
        b"0 0 0 rg\n11.811 0 0 11.811 900 780 Tm\n(750.00)Tj\nET\n")
    page.insert_image(pymupdf.Rect(800, 400, 1100, 700), filename=stamp)
    doc.save(multi)
    doc.close()
    doc = open_pdf(multi)
    try:
        objects = WatermarkDetector(query="confidential", auto=False).detect(doc)
        check(len(objects) == 3 and all(o.removable for o in objects),
              "同一 BT 内三次水印分别可删")
        check(all(o.kind == "text" for o in objects),
              "关键词模式不再把 3D 图误判为水印")
        check(len(doc[0].get_image_info()) == 1, "检测阶段图片仍在")
        stats = WatermarkRemover().remove(doc, objects)
        check(stats.text == 3 and stats.image == 0, "只删文字，不删图片")
        doc.save(multi_out)
    finally:
        doc.close()
    with pymupdf.open(multi_out) as d:
        check(not d[0].search_for("confidential"), "同一 BT 水印全部消失")
        check(bool(d[0].search_for("750.00")), "尺寸标注保留")
        check(len(d[0].get_image_info()) == 1, "3D 轴测图保留")
    with pymupdf.open(multi) as d:
        check(len(d[0].search_for("confidential")) == 3, "多水印原文件未被修改")

    # 注释对象
    ann_src = os.path.join(tmp, "ann.pdf")
    make_pdf(ann_src, 1, "ANNOT")
    doc = pymupdf.open(ann_src)
    doc[0].add_text_annot((300, 300), "内部资料", icon="Note")
    doc.save(os.path.join(tmp, "ann2.pdf"))
    doc.close()
    doc = open_pdf(os.path.join(tmp, "ann2.pdf"))
    try:
        objects = WatermarkDetector(query="内部资料", auto=False).detect(doc)
        check(any(o.kind == "annot" for o in objects), "检测到注释水印")
        stats = WatermarkRemover().remove(doc, objects)
        check(stats.annot >= 1, "注释对象已删除")
        check(len(list(doc[0].annots())) == 0, "页面注释列表为空")
    finally:
        doc.close()

    # 小图对象
    img = os.path.join(tmp, "stamp.png")
    make_image(img)
    img_src = os.path.join(tmp, "img.pdf")
    make_pdf(img_src, 1, "IMAGE")
    doc = pymupdf.open(img_src)
    doc[0].insert_image(pymupdf.Rect(420, 520, 500, 560), filename=img)
    doc.save(os.path.join(tmp, "img2.pdf"))
    doc.close()
    doc = open_pdf(os.path.join(tmp, "img2.pdf"))
    try:
        objects = [o for o in WatermarkDetector(auto=True).detect(doc)
                   if o.kind == "image"]
        check(bool(objects), "检测到小图水印")
        stats = WatermarkRemover().remove(doc, objects)
        check(stats.image >= 1, "小图对象已删除")
        check(len(doc[0].get_image_info()) == 0, "页面不再放置小图")
    finally:
        doc.close()


def test_page_model(tmp: str) -> None:
    print("[2] 页面模型：插入 / 排序 / 旋转 / 复制 / 撤销 / 导出")
    a = os.path.join(tmp, "pm_a.pdf")
    b = os.path.join(tmp, "pm_b.pdf")
    make_pdf(a, 3, "A")
    make_pdf(b, 2, "B")
    model = PageModel()
    model.load(a)
    check(model.count == 3, "加载 3 页")
    added = model.insert_pdf(b, 1, "全部")
    check(len(added) == 2 and model.count == 5, "插入 2 页 PDF")
    check(model.rotate([0], 90), "旋转第 1 页")
    check(model.refs[0].rotation == 90, "旋转角度已记录")
    added = model.duplicate([1])
    check(len(added) == 1 and model.count == 6, "复制 1 页")
    check(model.move(0, 3), "移动页面")
    check(model.undo() and model.count == 6, "撤销可回退")
    check(model.redo(), "重做可用")
    check(model.remove([0]) and model.count == 5, "删除 1 页")
    blank = model.insert_blank(2, "A4", "横向", 2)
    check(len(blank) == 2 and model.count == 7, "插入 2 张横向空白页")
    image = os.path.join(tmp, "pm.png")
    make_image(image)
    at = model.insert_image(image, 3, "A4", "纵向")
    check(at == 3 and model.count == 8, "插入图片页")
    out = os.path.join(tmp, "pm_out.pdf")
    check(model.export(out) == 8, "导出 8 页")
    with pymupdf.open(out) as d:
        checks = [pg.rect.width > pg.rect.height for pg in d]
        check(checks[2] and checks[4], "空白页方向为横向")
        check(len(d[3].get_image_info()) == 1, "图片页包含图片")
        text = " ".join(pg.get_text() for pg in d)
        check("A 1" in text and "B 1" in text, "正文与插入 PDF 内容都在")
    check(model.exported and not model.changed, "导出状态已标记")
    with pymupdf.open(a) as d:
        check(d.page_count == 3, "源文件页数未变")


def test_page_ui(tmp: str, app: QApplication) -> None:
    print("[3] 页面管理 UI：网格 / 单页 / 编辑 / 导出")
    src = os.path.join(tmp, "ui_page.pdf")
    out = os.path.join(tmp, "ui_page_out.pdf")
    make_pdf(src, 4, "PAGE")
    win = MainWindow()
    win.tabs.setCurrentIndex(2)
    win.show()
    app.processEvents()
    mod = win.page_module
    mod._add_document(src)
    pump(350)
    check(mod.model is not None and mod.model.count == 4, "页面模型已加载")
    check(mod.workspace.grid_view.grid.count() == 4, "网格显示 4 张卡片")
    check(bool(mod._selected_indices()), "默认选中首页")
    check(mod.workspace.grid_view.actions.isVisible(), "右侧页面操作面板可见")

    mod.rotate_pages(90)
    check(mod.model.refs[0].rotation == 90, "UI 旋转生效")
    check(mod.model.changed, "未导出状态已标记")

    before = mod.model.count
    mod.copy_pages()
    check(mod.model.count == before + 1, "UI 复制新增一页")

    mod.open_single(1)
    pump(300)
    check(mod._view == "single", "切换到单页视图")
    check(not mod.workspace.single_view.canvas._pixmap.isNull(), "单页视图已渲染")
    check("第 2 页" in mod._lbl_current_page.text(), "侧栏当前页信息更新")
    mod.set_view("grid")

    page_ui.QFileDialog.getSaveFileName = staticmethod(
        lambda *a, **k: (out, "PDF 文件 (*.pdf)"))
    page_ui.ExportDoneDialog.exec = lambda self: QDialog.Accepted
    check(mod._export_pages(), "UI 导出成功")
    check(os.path.isfile(out), "导出文件存在")
    check(not mod.model.changed, "导出后未保存标记清除")
    check(mod.files.paths()[0] == out, "导出结果图层置顶")
    check(not mod.files.is_result(src), "源文件仍是普通图层")
    check(os.path.basename(mod.files.focus_path() or "") == "ui_page.pdf",
          "导出后焦点回到源文档")
    with pymupdf.open(out) as d:
        check(d.page_count == 5, "导出页数正确")
        check(d[0].rect.width > d[0].rect.height, "导出保留旋转方向")
    with pymupdf.open(src) as d:
        check(d.page_count == 4, "源 PDF 未被改写")
    win.close()


def test_remove_ui(tmp: str, app: QApplication) -> None:
    print("[4] 去水印 UI：检测 / 预览 / 导出")
    src = os.path.join(tmp, "ui_src.pdf")
    wm = os.path.join(tmp, "ui_wm.pdf")
    out = os.path.join(tmp, "ui_removed.pdf")
    make_pdf(src, 1, "BODY")
    font = default_font(discover_fonts())
    Watermarker(WatermarkConfig(text="机密文件", angle=-45, font=font,
                                opacity=0.25)).apply_to_file(src, wm)
    win = MainWindow()
    win.tabs.setCurrentIndex(1)
    win.watermark_subtabs.setCurrentIndex(1)
    win.show()
    app.processEvents()
    check(win.merge_module.spin_rows.value() == 3
          and win.merge_module.spin_cols.value() == 2,
          "合并默认 3 行 × 2 列")
    mod = win.watermark_remove_module
    mod.files.add_paths([wm])
    pump(350)
    mod.edit_query.setText("机密文件")
    mod._find_query()
    pump(400)
    check(bool(mod._objects), f"UI 检测到 {len(mod._objects)} 个对象")
    check(len(mod._selected_ids) > 0, "检测结果默认勾选")
    check(win.lbl_status_right.text().startswith("ui_wm.pdf"),
          "状态栏右侧显示当前文件")
    check(mod.preview.chk_overlays.isVisible(), "显示检测框开关可见")

    mod._preview_removal()
    pump(900)
    check(mod._preview_mode == "file", "预览切换到去除后")
    check(bool(mod._preview_path) and os.path.isfile(mod._preview_path),
          "生成临时预览 PDF")
    check(mod.files.is_result(mod._preview_path), "预览结果作为图层")

    remove_ui.ExportConfirmDialog.exec = lambda self: QDialog.Accepted
    remove_ui.QFileDialog.getSaveFileName = staticmethod(
        lambda *a, **k: (out, "PDF 文件 (*.pdf)"))
    mod._export_removal()
    for _ in range(30):
        pump(150)
        if os.path.isfile(out) and not mod._busy():
            break
    check(os.path.isfile(out), "去水印导出文件存在")
    check(mod.files.paths()[0] == out, "去水印结果图层置顶")
    with pymupdf.open(out) as d:
        text = " ".join(pg.get_text() for pg in d)
        check("机密文件" not in text, "导出后水印文字消失")
        check("BODY" in text, "导出后正文保留")
    with pymupdf.open(wm) as d:
        text = " ".join(pg.get_text() for pg in d)
        check("机密文件" in text, "去水印不改原文件")

    # 文字水印行要能整行点击勾选/取消，不必精确点 16px 方框。
    row = remove_ui.DetectionRow(WatermarkObject(
        oid="probe:text", page=0, kind="text", bbox=(0, 0, 10, 10),
        text="CONFIDENTIAL"))
    row.show()
    app.processEvents()
    before = row.is_checked()
    QTest.mouseClick(row, Qt.LeftButton, pos=row.rect().center())
    check(row.is_checked() != before, "点击文字水印行可切换勾选")
    row.close()
    win.close()


def test_close_quits() -> None:
    """关闭主窗口后事件循环必须返回，不能留下后台进程。"""
    print("[5] 退出：关闭主窗口即结束进程")
    code = r"""
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from src.ui.main_window import MainWindow
from src.ui.theme import APP_STYLE
app = QApplication([])
app.setStyle("Fusion")
app.setStyleSheet(APP_STYLE)
win = MainWindow()
win.show()
QTimer.singleShot(300, win.close)
rc = app.exec()
print("QUIT-OK", rc, flush=True)
"""
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code], cwd=PROJ, env=env,
            capture_output=True, text=True, timeout=8)
        ok = proc.returncode == 0 and "QUIT-OK 0" in proc.stdout
        extra = (proc.stdout or proc.stderr or "").strip().splitlines()[-1:]
        check(ok, "关闭主窗口后进程退出", " ".join(extra))
    except subprocess.TimeoutExpired:
        check(False, "关闭主窗口后进程退出", "8 秒内没有退出")


def main() -> int:
    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    # 所有弹窗都在测试里跳过；核心校验仍由断言完成。
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    tmp = tempfile.mkdtemp(prefix="pdftk-new-")
    test_core_remover(tmp)
    test_page_model(tmp)
    test_page_ui(tmp, app)
    test_remove_ui(tmp, app)
    test_close_quits()
    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for item in FAILS:
            print("  -", item)
        return 1
    print(f"ALL-OK ({OKS} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
