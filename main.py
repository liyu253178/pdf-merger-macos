"""PDF 工具箱 —— 应用入口。

功能：PDF 合并（含图片，N-up 拼版）、PDF 多行水印（角度可调）、
保密输出（加水印 → 逐页转图片 → 合回 PDF）、图层式文件管理
（双击切换预览对象）、缩放预览、打印。
"""

from __future__ import annotations

import os
import sys

# 允许以 `python main.py` 直接运行，也兼容 py2app 打包后的资源布局
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication   # noqa: E402

from src.ui.main_window import MainWindow    # noqa: E402
from src.ui.theme import APP_STYLE           # noqa: E402

APP_NAME = "PDF 工具箱"

SELFTEST_FLAG = "--selftest"


def bundle_resources() -> str | None:
    """返回打包产物的 Contents/Resources 路径；以源码方式运行时返回 None。

    判据是「启动器的上一级是否叫 Contents」：py2app 的启动器位于
    `Xxx.app/Contents/MacOS/Xxx`，而源码方式运行时 sys.executable 指向
    /usr/bin/python3 之类的解释器，上一级不可能是 Contents。
    """
    contents = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    if (os.path.basename(contents) == "Contents"
            and os.path.exists(os.path.join(contents, "Info.plist"))):
        return os.path.join(contents, "Resources")
    return None


def check_localization() -> None:
    """校验 bundle 声明了中文本地化。

    这一步挡的是「原生文件选择面板全是英文」：打开/保存面板由 AppKit 直接绘制，
    语言不取决于系统设置，而取决于 bundle 声明的支持语言。py2app 默认只写
    CFBundleDevelopmentRegion=English，于是面板整体走英文，与中文界面割裂。
    这个问题不会报错、不会崩溃，只有肉眼能看出来，所以必须在构建阶段查。
    """
    import plistlib

    resources = bundle_resources()
    if resources is None:
        print("本地化：跳过（源码方式运行，无 bundle 声明）")
        return

    with open(os.path.join(os.path.dirname(resources), "Info.plist"), "rb") as fh:
        info = plistlib.load(fh)

    locs = [str(x) for x in (info.get("CFBundleLocalizations") or [])]
    region = str(info.get("CFBundleDevelopmentRegion") or "")
    lprojs = sorted(n for n in os.listdir(resources) if n.endswith(".lproj"))

    if not (any(x.startswith("zh") for x in locs)
            and region.startswith("zh")
            and any(n.startswith("zh") for n in lprojs)):
        print("SELFTEST-FAIL: 中文本地化声明缺失，文件选择面板会显示英文"
              f"（CFBundleLocalizations={locs}，"
              f"CFBundleDevelopmentRegion={region!r}，.lproj={lprojs}）")
        raise SystemExit(1)

    print(f"本地化：正常（开发区域 {region}，"
          f"支持 {'、'.join(locs)}，{len(lprojs)} 个 .lproj）")


def selftest() -> int:
    """打包产物自检：字体、拼版、水印、图层、打印各走一遍真实路径。

    build_thin.py 会在瘦身后调用它并校验输出里的 SELFTEST-OK。
    只比体积不验证的话，很容易交付一个「很小但打不开」的 App，
    所以这一步是硬性关卡，不是可选装饰。
    """
    import tempfile

    import pymupdf
    # 打印链路依赖 QtPrintSupport：瘦身时若被当成「未使用的 Qt 框架」删掉，
    # 会在用户按下打印的那一刻才炸。这里 import 一次，把风险提前到构建阶段。
    from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo  # noqa: F401

    from src.core.fonts import default_font, discover_fonts
    from src.core.pdf_io import save_pdf
    from src.core.pdf_merger import PDFMerger
    from src.core.watermark import WatermarkConfig, Watermarker
    from src.ui.print_pdf import print_to_file

    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    window = MainWindow()                  # 顺带确认界面依赖完整
    assert window.btn_print is not None, "顶部工具条缺少打印入口"
    print("界面：正常（含图层列表、预览模式切换、打印入口）")

    fonts = discover_fonts()
    print(f"字体可用数：{len(fonts)}")
    for f in fonts:
        print(f"  - {f.name}（嵌入约 {f.subset_kb:.0f} KB）")
    if not fonts:
        print("SELFTEST-FAIL: 无可用中文字体")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        # 造一个 3 页样本，验证多页不会被丢
        src = pymupdf.open()
        for n in range(1, 4):
            page = src.new_page(width=595, height=842)
            page.insert_text((72, 100), f"PAGE {n}", fontsize=24)
        sample = os.path.join(tmp, "sample.pdf")
        src.save(sample)
        src.close()

        merged_path = os.path.join(tmp, "merged.pdf")
        doc = PDFMerger(rows=2, cols=2).build([sample, sample])
        try:
            assert doc.page_count >= 1, "拼版输出为空"
            text = " ".join(doc[i].get_text() for i in range(doc.page_count))
            assert "PAGE 1" in text and "PAGE 2" in text and "PAGE 3" in text, \
                "多页内容缺失，拼版逻辑异常"
            save_pdf(doc, merged_path)          # 落盘后才能当图层加载回来
        finally:
            doc.close()
        print("拼版：正常（多页内容完整）")

        # 图层：结果插到最上方并成为当前预览对象，原有条目必须原样保留
        layers = window.merge_module.files
        layers.add_paths([sample])
        assert layers.add_result(merged_path), "结果图层未能插入"
        order = layers.paths()
        assert order[0] == merged_path, f"结果图层没有排到最上方：{order}"
        assert len(order) == 2 and sample in order, f"原有图层被覆盖或删除：{order}"
        assert layers.focus_path() == merged_path, "结果图层没有成为当前预览对象"
        with pymupdf.open(merged_path) as merged_doc:
            expected_pages = 3 + merged_doc.page_count
        assert layers.total_pages() == expected_pages, \
            f"图层页数统计异常：{layers.total_pages()} != {expected_pages}"
        print("图层：正常（结果置顶、原图层保留、当前预览对象已切换）")

        font = default_font(fonts)
        cfg = WatermarkConfig(text="机密文件 请勿外传\n张三", angle=-45, font=font)
        png, pw, ph = Watermarker(cfg).render_preview()
        assert png and len(png) > 1000, "水印预览渲染失败"
        assert (pw, ph) == (595.0, 842.0), f"预览回传的页面尺寸异常：{pw}×{ph}"
        print("水印：正常（预览可渲染）")

        applied = os.path.join(tmp, "wm.pdf")
        _, count = Watermarker(cfg).apply_to_file(sample, applied)
        assert count == 3, f"水印页数异常：{count}"
        size_kb = os.path.getsize(applied) / 1024
        assert size_kb < 2000, f"水印后体积异常膨胀：{size_kb:.0f} KB（字体子集化可能失效）"
        print(f"水印落盘：正常（字体 {font.name}，3 页，输出 {size_kb:.1f} KB）")

        # 预览清晰度：渲染倍率必须跟着预览区尺寸走。
        # 写死 72 dpi 时 A4 只有 595 px 宽，拼版发票里 3pt 出头的小字落到
        # 画布上只剩 3 个像素 —— 这正是用户看到的「预览不清晰」。
        # 判据是「渲染出的像素不少于屏幕上真正需要的像素」，与屏幕是否是
        # Retina 无关，所以 offscreen 下跑也有意义。
        canvas = window.watermark_module.preview.canvas
        canvas.resize(900, 620)
        dpr = float(canvas.devicePixelRatioF()) or 1.0
        scale = canvas.render_scale()
        fit = canvas.fit_zoom()
        assert scale >= fit * dpr * 1.2, \
            f"渲染倍率 {scale:.2f} 没覆盖显示所需（fit={fit:.2f} × dpr={dpr:g}）"
        pix = pymupdf.open(sample)[0].get_pixmap(
            matrix=pymupdf.Matrix(scale, scale), alpha=False)
        assert pix.width >= 500, f"按倍率渲染后宽度不足：{pix.width}px"
        print(f"预览清晰度：正常（适应窗口 {fit:.2f}× 显示 → 按 {scale:.2f}× 渲染，"
              f"A4 出图 {pix.width}px 宽，屏幕缩放比 {dpr:g}）")

        # 缩放：放大后必须真的向渲染侧要更多像素，否则只是把糊图拉大；
        # 倍率被上限夹住时不再重复渲染，这是刻意的（多要像素没意义）。
        before = canvas.render_scale()
        canvas.zoom_in()
        assert canvas.zoom() > fit, "放大后缩放比没有提高"
        assert canvas.render_scale() > before, \
            f"放大后渲染倍率没跟着提高：{before:.2f} → {canvas.render_scale():.2f}"
        zoomed = canvas.render_scale()
        for _ in range(6):                 # 一直放大到上限，不能崩也不能回到更小
            canvas.zoom_in()
        assert canvas.render_scale() >= zoomed, "连续放大后倍率反而下降了"
        canvas.zoom_fit()
        assert canvas.is_fit(), "回到适应窗口失败"
        print(f"预览缩放：正常（放大后渲染倍率 {before:.2f}× → {zoomed:.2f}×，"
              "可回适应窗口）")

        # 保密输出：默认档应为无损 PNG，且四档画质逐级递增
        from src.core.secure_export import DEFAULT_QUALITY, QUALITIES, export_secure
        default_q = next(q for q in QUALITIES if q.key == DEFAULT_QUALITY)
        assert default_q.lossless, f"默认画质档应为无损：{default_q.describe()}"
        sizes = []
        for q in QUALITIES:
            out = os.path.join(tmp, f"secure_{q.key}.pdf")
            info = export_secure(sample, out, cfg, dpi=q.dpi, lossless=q.lossless,
                                 jpeg_quality=q.jpeg_quality)
            assert info["pages"] == 3, f"{q.label} 档页数异常：{info['pages']}"
            sizes.append((q.label, q.dpi, info["bytes"] // 1024))
        assert sizes[-1][2] > sizes[0][2], f"画质档位体积未递增：{sizes}"
        detail = "、".join(f"{n}{d}dpi={k}K" for n, d, k in sizes)
        print(f"保密输出：正常（默认 {default_q.describe()}；{detail}）")

        # 打印：走 QPrinter 的完整绘制流程，只是把输出重定向到文件。
        # 这样无人值守也能验证「光栅化 → QPainter → 打印设备」这条链路。
        printed = os.path.join(tmp, "printed.pdf")
        pages = print_to_file(applied, printed, [1, 2, 3])
        assert pages == 3, f"打印输出页数异常：{pages}"
        with pymupdf.open(printed) as out:
            assert out.page_count == 3, f"打印结果页数不符：{out.page_count}"
        print(f"打印：正常（3 页 → {os.path.getsize(printed) / 1024:.1f} KB）")

        # print_to_file 走的是「输出到 PDF 文件」，绕开了系统打印设备。
        # 真正会崩的是枚举打印机那一步（打印平台插件被漏打包时就在这里炸），
        # 所以单独探一次。没有打印机不算失败，机器上本来就可能一台都没接。
        try:
            printers = QPrinterInfo.availablePrinters()
            names = [p.printerName() for p in printers]
            probe = QPrinter(QPrinter.HighResolution)
            assert probe.isValid(), "QPrinter 构造后无效，打印平台插件可能缺失"
        except Exception as exc:  # noqa: BLE001 - 这里就是要把异常提前暴露
            print(f"SELFTEST-FAIL: 打印设备不可用（{exc}）")
            return 1
        print(f"打印设备：正常（发现 {len(names)} 台{('：' + '、'.join(names)) if names else ''}）")

    check_localization()

    print("SELFTEST-OK")
    app.quit()
    return 0


def apply_source_icon(app: QApplication) -> None:
    """以源码方式运行时补上图标。

    打包后的 Dock 图标由 bundle 里的 .icns 决定（见 setup.py 的 iconfile），
    但直接 `python main.py` 时没有 bundle，会显示一个通用占位图标。
    这里在项目目录下找 PNG 主图兜一下；找不到就静默跳过 ——
    图标只是观感问题，不该因为它缺失就让应用起不来。
    """
    from PySide6.QtGui import QIcon

    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "assets", "icon-1024.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))


def main() -> int:
    if SELFTEST_FLAG in sys.argv:
        return selftest()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    # Fusion 在 macOS 上对 QSS 的支持比原生风格更一致，配色也更好控制
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    apply_source_icon(app)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
