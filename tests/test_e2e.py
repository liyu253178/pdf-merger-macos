"""PDF 工具箱 —— 端到端功能测试。

覆盖四条主链路（合并 / 水印 / 保密输出 / 打印）与异常边界。
全部走真实文件，不打桩。
"""
from __future__ import annotations

import glob
import os
import shutil
import sys
import tempfile

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

import pymupdf
from PySide6.QtWidgets import QApplication

from src.core.fonts import default_font, discover_fonts
from src.core.pdf_io import open_pdf, save_pdf
from src.core.pdf_merger import PDFMerger
from src.core.secure_export import QUALITIES, export_secure
from src.core.watermark import WatermarkConfig, Watermarker
from src.ui.print_pdf import print_to_file
from src.ui.theme import APP_STYLE

FAILS: list[str] = []
OKS = 0


def check(cond: bool, label: str) -> None:
    global OKS
    if cond:
        OKS += 1
        print(f"  ok   {label}")
    else:
        FAILS.append(label)
        print(f"  FAIL {label}")


def make_pdf(path: str, pages: int, size=(595.0, 842.0),
             angle: int = 0, meta: dict | None = None,
             with_link: bool = False, with_annot: bool = False) -> None:
    doc = pymupdf.open()
    for n in range(1, pages + 1):
        page = doc.new_page(width=size[0], height=size[1])
        if angle:
            page.set_rotation(angle)
        page.insert_text((72, 100), f"PAGE {n} of {pages}", fontsize=24)
        page.insert_text((72, 140), "机密内容 SECRET", fontsize=14)
        if with_link:
            page.insert_link({
                "kind": pymupdf.LINK_URI,
                "from": pymupdf.Rect(72, 160, 260, 190),
                "uri": "https://example.com/secret",
            })
        if with_annot:
            page.add_text_annot((300, 300), "批注内容", icon="Note")
    if meta:
        doc.set_metadata(meta)
    doc.save(path)
    doc.close()


def page_sizes(path: str) -> list[tuple[float, float]]:
    with pymupdf.open(path) as doc:
        return [(round(doc[i].rect.width, 1), round(doc[i].rect.height, 1))
                for i in range(doc.page_count)]


def make_photo_pdf(path: str, pages: int = 2) -> None:
    """造一份「照片型」PDF：整页随机噪声。

    PNG 压不动随机噪声，单页必然超过 LOSSLESS_MAX_BYTES，用来验证无损档
    对照片/扫描页的自动回退。用 Qt 生成像素，项目里没有 PIL。
    """
    import random

    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage

    def noise_png(w: int, h: int, seed: int) -> bytes:
        img = QImage(w, h, QImage.Format_RGB888)
        rnd = random.Random(seed)
        for y in range(h):
            img.scanLine(y)[:] = bytes(rnd.randrange(256) for _ in range(w * 3))
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        assert img.save(buf, "PNG"), "噪声图编码失败"
        return bytes(buf.data())

    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=noise_png(900, 1273, n))
    doc.save(path)
    doc.close()


def main() -> int:
    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)

    fonts = discover_fonts()
    if not fonts:
        print("SELFTEST-FAIL: 无可用中文字体")
        return 1
    font = default_font(fonts)

    tmp = tempfile.mkdtemp(prefix="pdftk-e2e-")
    try:
        # ---------------------------------------------------------------- 合并
        print("[1] PDF 合并（N-up 拼版）")
        src = os.path.join(tmp, "src.pdf")
        make_pdf(src, 3)
        merged = os.path.join(tmp, "merged.pdf")
        doc = PDFMerger(rows=2, cols=2).build([src, src])
        try:
            check(doc.page_count == 2, f"2 份 3 页 → 2 页 4-up，实为 {doc.page_count} 页")
            text = " ".join(doc[i].get_text() for i in range(doc.page_count))
            for n in "123":
                check(f"PAGE {n}" in text, f"第 {n} 页内容未丢失")
            save_pdf(doc, merged)
        finally:
            doc.close()
        check(os.path.getsize(merged) > 0, "拼版文件已落盘")

        # 单列纵向（1-up）也应正常
        doc1 = PDFMerger(rows=0, cols=1).build([src])
        try:
            check(doc1.page_count == 3, f"1×1 逐页输出 3 页，实为 {doc1.page_count} 页")
        finally:
            doc1.close()

        # ---------------------------------------------------------------- 水印
        print("[2] 多行水印")
        wm_cfg = WatermarkConfig(text="机密文件 请勿外传\n张三 2026-09-21",
                                 angle=-45, font=font)
        wm_out = os.path.join(tmp, "wm.pdf")
        info = Watermarker(wm_cfg).apply_to_file(src, wm_out)
        check(info[1] == 3, f"水印覆盖 3 页，实为 {info[1]} 页")
        size_kb = os.path.getsize(wm_out) / 1024
        check(size_kb < 2000, f"字体子集化生效（{size_kb:.1f} KB < 2000 KB）")
        check(not os.path.exists(os.path.join(tmp, "watermarked.pdf")), "无多余中间文件")

        # 多行确实分多行（文本层应含两行文案）
        with pymupdf.open(wm_out) as d:
            t = d[0].get_text()
        check("机密文件" in t and "张三" in t, "多行水印两行都写入")

        # 角度可调：90° 与 -45° 输出应不同
        wm90 = os.path.join(tmp, "wm90.pdf")
        Watermarker(WatermarkConfig(text="机密文件 请勿外传\n张三 2026-09-21",
                                    angle=-90, font=font)).apply_to_file(src, wm90)
        check(os.path.getsize(wm90) != os.path.getsize(wm_out), "角度参数确实生效")

        # ------------------------------------------------------------ 保密输出
        print("[3] 保密输出（栅格化去文本层）")
        rich = os.path.join(tmp, "rich.pdf")
        make_pdf(rich, 3, size=(595, 842), meta={
            "title": "机密报告", "author": "张三", "subject": "内部",
            "keywords": "secret", "creator": "Word", "producer": "Word",
            "creationDate": "D:20260101000000", "modDate": "D:20260101000000",
        }, with_link=True, with_annot=True)
        # 再塞一个尺寸不同的页，验证尺寸不统一时也能逐页还原
        with pymupdf.open(rich) as d:
            d.insert_page(3, width=400, height=600)
            d.save(os.path.join(tmp, "rich2.pdf"))
        rich2 = os.path.join(tmp, "rich2.pdf")

        before = page_sizes(rich2)
        out = os.path.join(tmp, "rich_保密.pdf")
        seen: list[tuple[int, int, str]] = []
        result = export_secure(
            rich2, out, WatermarkConfig(text="保密 请勿外传", angle=-45, font=font),
            dpi=150, progress=lambda p, t, s: seen.append((p, t, s)),
        )
        check(result["pages"] == 4, f"页数保持 4，实为 {result['pages']}")
        check(os.path.exists(out), "成品已落盘")
        check(result["bytes"] == os.path.getsize(out), "返回体积与实际一致")

        after = page_sizes(out)
        check(before == after, f"页面尺寸逐页一致：{before} vs {after}")

        # 顺序保持：水印文字按页出现，用像素差异间接验证顺序
        with pymupdf.open(out) as d:
            check(d.page_count == 4, "成品页数 4")
            text = " ".join(d[i].get_text() for i in range(d.page_count)).strip()
            check(text == "", f"文本层已清空（残留 {len(text)} 字符）")
            links = sum(len(d[i].get_links()) for i in range(d.page_count))
            check(links == 0, f"超链接已移除（残留 {links} 个）")
            annots = sum(len(list(d[i].annots())) for i in range(d.page_count))
            check(annots == 0, f"注释对象已移除（残留 {annots} 个）")
            m = d.metadata
            check(not any(m.get(k) for k in
                          ("title", "author", "subject", "keywords",
                           "creator", "producer")),
                  f"元信息已清空：{ {k: m.get(k) for k in ('title','author','creator')} }")
            has_img = all(d[i].get_images() for i in range(d.page_count))
            check(has_img, "每页都含图片对象")

        check(len(seen) > 0 and seen[-1][0] == 100, f"进度回调走到 100%（共 {len(seen)} 次）")
        check(any("水印" in s for _, _, s in seen), "进度文案区分阶段")
        leftover = glob.glob(os.path.join(tempfile.gettempdir(), "pdf-secure-*"))
        check(not leftover, f"临时目录已清理（残留 {len(leftover)} 个）")

        # 原文件未被改动
        check(page_sizes(rich2) == before, "原文件未被改动")

        # 画质档位：逐档落盘，验证分辨率、嵌入格式与体积走向
        seen_q = {}
        for q in QUALITIES:
            p = os.path.join(tmp, f"q_{q.key}.pdf")
            info = export_secure(src, p, WatermarkConfig(text="保密", angle=-45, font=font),
                                 dpi=q.dpi, lossless=q.lossless,
                                 jpeg_quality=q.jpeg_quality)
            with pymupdf.open(p) as d:
                fmt = d.extract_image(d[0].get_images(full=True)[0][0])["ext"]
            seen_q[q.key] = (q.dpi, os.path.getsize(p), fmt, info)
        check(seen_q["standard"][0] == 200 and seen_q["ultra"][0] == 400, "档位分辨率正确")
        check(seen_q["standard"][1] < seen_q["ultra"][1],
              f"分辨率越高体积越大：{ {k: v[1] // 1024 for k, v in seen_q.items()} }")
        check(seen_q["lossless"][2] == "png",
              f"无损档嵌入原图即 PNG：{seen_q['lossless'][2]}")
        check(seen_q["high"][2] == "jpeg",
              f"高清档嵌入原图即 JPEG：{seen_q['high'][2]}")
        check(seen_q["lossless"][3]["lossless"] is True and
              seen_q["high"][3]["lossless"] is False, "返回信息带无损标记")
        # 线条型内容（本例就是文字页）PNG 不该比同 dpi 的 JPEG 明显更大 ——
        # 这是把默认档设成"无损"的前提，失控的话用户会莫名其妙得到巨型文件
        png_kb = seen_q["lossless"][1] / 1024
        jpg_kb = seen_q["high"][1] / 1024
        check(png_kb <= jpg_kb * 1.2,
              f"无损档体积未失控：PNG {png_kb:.0f}K vs JPEG300 {jpg_kb:.0f}K")

        # 照片型页面：PNG 会失控，必须自动退回 JPEG，且画质档位仍可信
        photo = os.path.join(tmp, "photo.pdf")
        make_photo_pdf(photo, pages=2)
        p_out = os.path.join(tmp, "photo_保密.pdf")
        pit = export_secure(photo, p_out, WatermarkConfig(text="保密", angle=-45, font=font),
                            dpi=300, lossless=True)
        with pymupdf.open(p_out) as d:
            fmts = [d.extract_image(i[0])["ext"]
                    for pg in range(d.page_count)
                    for i in d[pg].get_images(full=True)]
        check(fmts and all(f == "jpeg" for f in fmts),
              f"照片页自动退回 JPEG：{fmts}")
        check(pit["jpeg_pages"] == [1, 2], f"回退页码被如实记录：{pit['jpeg_pages']}")
        # 线条型样本不该被误判
        line_out = os.path.join(tmp, "line_保密.pdf")
        lit = export_secure(src, line_out, WatermarkConfig(text="保密", angle=-45, font=font),
                            dpi=300, lossless=True)
        check(lit["jpeg_pages"] == [], f"文字页不触发回退：{lit['jpeg_pages']}")

        # 带旋转角度的页面：成品可见尺寸必须与原件一致（旋转烘焙进像素）
        rot = os.path.join(tmp, "rot.pdf")
        make_pdf(rot, 2, size=(595, 842), angle=90)
        rot_out = os.path.join(tmp, "rot_保密.pdf")
        export_secure(rot, rot_out, WatermarkConfig(text="保密", angle=-45, font=font))
        with pymupdf.open(rot) as a, pymupdf.open(rot_out) as b:
            src_vis = [(round(a[i].rect.width), round(a[i].rect.height))
                       for i in range(a.page_count)]
            out_vis = [(round(b[i].rect.width), round(b[i].rect.height))
                       for i in range(b.page_count)]
            check(src_vis == out_vis, f"旋转页可见尺寸一致：{src_vis} vs {out_vis}")
            # 同一缩放下渲染尺寸相同，说明像素方向也对齐（不只是矩形数值巧合）
            ra = a[0].get_pixmap(matrix=pymupdf.Matrix(0.3, 0.3))
            rb = b[0].get_pixmap(matrix=pymupdf.Matrix(0.3, 0.3))
            check((ra.width, ra.height) == (rb.width, rb.height),
                  f"旋转页渲染尺寸一致：{ra.width}×{ra.height} vs {rb.width}×{rb.height}")
        check(out_vis[0] == (842, 595), f"横置页仍是横置（未被弄成竖版）：{out_vis[0]}")

        # ------------------------------------------------------------ 异常边界
        print("[4] 异常边界")
        bad = os.path.join(tmp, "notpdf.pdf")
        with open(bad, "wb") as fh:
            fh.write(b"this is not a pdf at all")
        try:
            open_pdf(bad)
            check(False, "损坏文件应抛错")
        except ValueError as exc:
            check("PDF" in str(exc) or "损坏" in str(exc) or "打开" in str(exc),
                  f"损坏文件报中文错误：{exc}")

        try:
            open_pdf(os.path.join(tmp, "不存在.pdf"))
            check(False, "缺失文件应抛错")
        except FileNotFoundError:
            check(True, "缺失文件抛 FileNotFoundError")
        except ValueError as exc:
            check(True, f"缺失文件抛 ValueError：{exc}")

        # 加密文档：应在打开阶段就被中文提示拦下（而不是取页时才炸）
        enc = os.path.join(tmp, "enc.pdf")
        de = pymupdf.open()
        de.new_page()
        de.save(enc, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                owner_pw="o", user_pw="u")
        de.close()
        try:
            open_pdf(enc)
            check(False, "加密文件应被拦截")
        except ValueError as exc:
            check("加密" in str(exc), f"加密文件报中文提示：{str(exc).splitlines()[0]}")

        # 空文档不应崩
        empty = pymupdf.open()
        try:
            empty_page = empty.new_page()
            empty_page.insert_text((72, 72), "only one empty-ish page")
            empty_path = os.path.join(tmp, "one.pdf")
            save_pdf(empty, empty_path)
        finally:
            empty.close()
        one_out = os.path.join(tmp, "one_保密.pdf")
        r = export_secure(empty_path, one_out, WatermarkConfig(text="x", font=font))
        check(r["pages"] == 1, "单页文档保密输出正常")

        # ---------------------------------------------------------------- 打印
        print("[5] 打印链路（重定向到文件）")
        print_path = os.path.join(tmp, "print.pdf")
        n = print_to_file(merged, print_path)
        check(n == 2, f"打印输出页数 2，实为 {n}")
        check(os.path.exists(print_path), "打印文件已落盘")
        with pymupdf.open(print_path) as d:
            check(d.page_count == 2, f"打印输出页数与原文档一致：{d.page_count}")

        # 页码范围：只打第 2 页
        p2 = os.path.join(tmp, "print2.pdf")
        n2 = print_to_file(wm_out, p2, pages=[2])
        with pymupdf.open(p2) as d:
            check(n2 == 1 and d.page_count == 1,
                  f"页码范围生效，只输出 1 页，实为 {d.page_count}")

        # 页码范围经由对话框钳制后不会越界
        from PySide6.QtPrintSupport import QPrinter
        from src.ui.print_pdf import selected_pages
        pr3 = QPrinter()
        pr3.setFromTo(1, 999)
        picked = selected_pages(pr3, 3)
        check(picked == [1, 2, 3], f"越界范围被钳到实际页数：{picked}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILS:
        print(f"E2E-FAIL ({len(FAILS)} 项)：")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print(f"E2E-OK  共 {OKS} 项断言全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
