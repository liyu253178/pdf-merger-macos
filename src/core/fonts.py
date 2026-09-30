"""中文字体发现与回退。

为什么需要这个模块
------------------
1. Windows 的「微软雅黑」在 macOS 上默认不存在（未装 Microsoft Office 就不会有）。
2. macOS 自带的中文字体多为 .ttc 字体集合，PyMuPDF 加载时**必须同时传 fontfile 和
   fontname**，只传 fontfile 会静默回退到 Helvetica —— 中文全部变成空白/豆腐块，
   而且不报任何错。这是本项目踩过的最隐蔽的坑。
3. 新版 macOS（如 26.x）把 PingFang 移出了 /System/Library/Fonts，路径不能硬编码。

因此这里不猜路径，而是「候选枚举 -> 真实渲染校验 -> 给出结论」。
每个候选字体都会被真正写入一个临时页并提取文本，只有中文能原样往返的才会入选。
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass

import pymupdf

# 校验用样本：包含汉字与全角标点，能同时暴露「缺字形」和「编码映射错误」
_PROBE_TEXT = "机密文件测试"


@dataclass(frozen=True)
class FontOption:
    """一个通过真实渲染 + 子集化校验的可用字体。"""

    name: str                 # 展示名，如「微软雅黑」
    fontfile: str | None      # None 表示使用 PyMuPDF 内置 CJK 字体
    fontname: str             # 注册进 PDF 用的内部名
    source: str               # 来源说明，用于界面提示
    subset_kb: float = 0.0    # 子集化后嵌进 PDF 的字体体积，供界面展示与排序参考

    @property
    def is_builtin(self) -> bool:
        return self.fontfile is None

    def describe(self) -> str:
        """界面上展示的一行说明。"""
        where = self.source if self.fontfile else "内置，无需外部字体"
        return f"{self.name}（{where}，嵌入约 {self.subset_kb:.0f} KB）"


_CACHE: list[FontOption] | None = None


# 候选顺序即回退优先级。路径支持 glob，覆盖多版本/多安装位置的差异。
_CANDIDATES: list[tuple[str, list[str], str]] = [
    (
        "微软雅黑",
        [
            "/Library/Fonts/Microsoft/MSYH.TTC",
            "/Library/Fonts/Microsoft/MSYH.TTF",
            "/Library/Fonts/MSYH.TTC",
            "/Library/Fonts/msyh.ttc",
            "~/Library/Fonts/MSYH.TTC",
            "~/Library/Fonts/msyh.ttc",
            "/Applications/Microsoft Word.app/Contents/Resources/DFonts/MSYH.TTC",
            "/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts/MSYH.TTC",
            "/Applications/Microsoft Excel.app/Contents/Resources/DFonts/MSYH.TTC",
        ],
        "系统已安装",
    ),
    (
        "苹方-简",
        ["/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/PingFangSC-Regular.otf"],
        "macOS 系统字体",
    ),
    (
        "冬青黑体简体中文",
        ["/System/Library/Fonts/Hiragino Sans GB.ttc"],
        "macOS 系统字体",
    ),
    (
        "华文黑体",
        ["/System/Library/Fonts/STHeiti Medium.ttc", "/System/Library/Fonts/STHeiti Light.ttc"],
        "macOS 系统字体",
    ),
    (
        "宋体",
        ["/System/Library/Fonts/Supplemental/Songti.ttc"],
        "macOS 系统字体",
    ),
]

# 兜底：PyMuPDF 内置 CJK 字体，无需任何外部文件
_BUILTIN = ("思源宋体（内置）", None, "内置字体，任何机器都可用")

# 子集化后的体积上限。超过说明该字体（多为 .ttc 集合）无法被有效子集化，
# 会把整份字体嵌进 PDF —— 实测冬青黑体就会留下约 10 MB。
_SUBSET_LIMIT_KB = 400


def _make_font(fontfile: str | None, fontname: str) -> pymupdf.Font:
    """构造字体对象。fontname 必须传，否则中文会静默丢失。"""
    if fontfile:
        return pymupdf.Font(fontfile=fontfile, fontname=fontname)
    return pymupdf.Font(fontname=fontname)


def _probe(fontfile: str | None, fontname: str) -> tuple[bool, float]:
    """用**真实渲染路径**探测字体的可用性，返回 (是否可用, 子集化后体积KB)。

    两个关键点，都是踩坑得来的：
    1. 必须用 TextWriter 而不是 page.insert_text。二者对字体的容忍度不同 ——
       宋体 Songti.ttc 在 insert_text 下正常，走 TextWriter 却抛
       `FzErrorUnsupported: substitute font creation is not implemented yet`。
       用 insert_text 校验会漏掉这类字体，导致用到时才崩。
    2. 必须实测子集化效果。.ttc 字体集合常无法被有效子集化，中文大字体
       （冬青黑体、微软雅黑原始文件都是 18 MB 级）会整份嵌入，让一份小 PDF
       膨胀到 10 MB 以上。体积超标的一律不采用。
    """
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=200, height=120)
        font = _make_font(fontfile, fontname)
        writer = pymupdf.TextWriter(page.rect)
        writer.append(pymupdf.Point(12, 70), _PROBE_TEXT, font=font, fontsize=20)
        writer.write_text(page, color=(0, 0, 0), opacity=0.5)

        if _PROBE_TEXT not in page.get_text():
            return False, 0.0

        try:
            doc.subset_fonts()
        except Exception:
            pass
        size_kb = len(doc.tobytes(garbage=4, deflate=True, clean=True)) / 1024
        return size_kb <= _SUBSET_LIMIT_KB, size_kb
    except Exception:
        return False, 0.0
    finally:
        doc.close()


def _probe_quietly(fontfile: str | None, fontname: str) -> tuple[bool, float]:
    """探测期间屏蔽 MuPDF 的 stderr 噪音。

    不兼容字体（如宋体）会让 MuPDF 直接往 stderr 打
    「format error: Index bounds」之类的信息。这是预期内的排除结果，
    不该吓到用户，所以在探测范围内临时关闭错误回显。
    """
    try:
        pymupdf.TOOLS.mupdf_display_errors(False)
    except Exception:
        pass
    try:
        return _probe(fontfile, fontname)
    finally:
        try:
            pymupdf.TOOLS.mupdf_display_errors(True)
        except Exception:
            pass


def _first_existing(patterns: list[str]) -> str | None:
    for pat in patterns:
        path = os.path.expanduser(pat)
        if os.path.exists(path):
            return path
        for hit in sorted(glob.glob(path)):
            return hit
    return None


def discover_fonts(use_cache: bool = True) -> list[FontOption]:
    """枚举所有通过渲染 + 子集化校验的可用中文字体，按优先级排列。

    探测涉及真实渲染与写盘级操作，故结果做进程内缓存。
    """
    global _CACHE
    if use_cache and _CACHE is not None:
        return list(_CACHE)

    found: list[FontOption] = []
    for name, patterns, source in _CANDIDATES:
        path = _first_existing(patterns)
        if not path:
            continue
        fontname = "wm_" + os.path.splitext(os.path.basename(path))[0].replace(" ", "")
        ok, size_kb = _probe_quietly(path, fontname)
        if ok:
            found.append(FontOption(name, path, fontname, source, size_kb))

    builtin_name, _, builtin_source = _BUILTIN
    ok, size_kb = _probe_quietly(None, "china-s")
    if ok:
        found.append(FontOption(builtin_name, None, "china-s", builtin_source, size_kb))

    _CACHE = list(found)
    return found


def default_font(options: list[FontOption], preferred: str = "微软雅黑") -> FontOption:
    """优先返回 preferred，其次回退到列表中第一个可用项。"""
    for opt in options:
        if opt.name == preferred:
            return opt
    if not options:
        # 理论上不会发生（内置字体总能通过校验），保险起见给出兜底
        return FontOption("思源宋体（内置）", None, "china-s", "内置字体")
    return options[0]


def font_path_for(name: str) -> str | None:
    """按展示名反查字体文件路径，供不需要实例化 FontOption 的场景使用。"""
    for opt in discover_fonts():
        if opt.name == name:
            return opt.fontfile
    return None


if __name__ == "__main__":  # 便于命令行排查字体环境
    import time

    t0 = time.time()
    opts = discover_fonts(use_cache=False)
    print(f"已通过「渲染 + 子集化」双重校验的中文字体（耗时 {(time.time() - t0):.2f}s）：")
    for i, o in enumerate(opts, 1):
        print(f"  {i}. {o.describe()}")
    print("\n未被采用的原因通常是：走 TextWriter 崩溃，或子集化失效导致体积超标。")
