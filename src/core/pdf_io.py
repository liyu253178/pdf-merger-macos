"""PDF 读写与保存策略。

为什么单独抽这一层
------------------
中文字体普遍很大：微软雅黑 MSYH.TTC 有 18.8 MB，而 TTC 集合是整份嵌入的。
实测「合并 + 加一个水印」从 14.5 KB 暴涨到 11.84 MB，就是被字体撑起来的。
调用 `doc.subset_fonts()` 做字体子集化后降到 50 KB —— 缩小约 236 倍，
且水印文字完整无损。

因此所有落盘动作都必须走 save_pdf()，不要直接 doc.save()。
"""

from __future__ import annotations

import os

import pymupdf


def save_pdf(doc: pymupdf.Document, output: str, subset_fonts: bool = True) -> str:
    """带字体子集化与压缩的保存。

    subset_fonts=True 时只嵌入实际用到的字形。字体子集化失败不应阻断导出，
    因此单独兜底 —— 只是文件会大一些，不影响正确性。
    """
    if subset_fonts:
        try:
            doc.subset_fonts()
        except Exception:
            pass

    doc.save(
        output,
        garbage=4,
        deflate=True,
        deflate_images=True,
        deflate_fonts=True,
        clean=True,
    )
    return output


def open_pdf(path: str) -> pymupdf.Document:
    """打开 PDF，失败或需要密码时抛出可读的中文异常。

    加密文件要在这里拦下：PyMuPDF 能"打开"它，直到真正取页时才抛
    `document closed or encrypted`，那时错误信息对用户毫无意义。
    """
    # MuPDF 失败时会自己往 stderr 打英文（"Failed to open file ..."）。
    # 界面已经会把中文错误弹给用户，这里多余的英文只会在终端刷噪声，
    # 所以在尝试期间临时静音，拿到异常后立刻恢复。
    display = pymupdf.TOOLS.mupdf_display_errors(False)
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise ValueError(f"无法打开文件：{os.path.basename(path)}\n{exc}") from exc
    finally:
        pymupdf.TOOLS.mupdf_display_errors(display)

    if doc.needs_pass:
        doc.close()
        raise ValueError(
            f"无法处理已加密的 PDF：{os.path.basename(path)}\n"
            "请先用密码解除保护，再重新添加。"
        )
    return doc
