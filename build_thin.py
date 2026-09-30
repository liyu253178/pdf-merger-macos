#!/usr/bin/env python3
"""对 py2app 产物做体积瘦身（打包后处理）。

用法：
    python build_thin.py                  # 自动定位 dist/*.app 并处理
    python build_thin.py --app path.app
    python build_thin.py --dry-run        # 只报告会删什么，不真的动
    python build_thin.py --keep-x86       # 保留双架构（不推荐，体积翻倍）

做三件事
--------
1. **删除冗余 Qt 资源与未使用的 PySide6 模块**：QML 模块、多语言翻译包、
   用不到的插件与命令行工具，以及 Addons 全家桶（Qt3D / QtWebEngine /
   QtMultimedia / QtCharts …）的绑定、metatypes 与随附的 FFmpeg 动态库。
   这一项是体积的大头 —— 单它就能从 1197 MB 砍到 118 MB。
2. **二进制切片**：PySide6 的 macOS wheel 是 universal2，同时含 x86_64 与 arm64
   两套机器码。只发行 Apple Silicon 版本时切掉 x86_64，可省约 50%。
3. **验证**：调用 App 内置的 --selftest 自检，确认瘦身后仍能正常导入与渲染；
   自检之后再跑一次体积守卫，超出阈值就列出最大的残留项。
   这一步不能省 —— 瘦身最容易出的问题就是把某个必需的库删掉，
   如果只比体积不验证，等于交付一个装不起来的 App。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 这三个插件目录必须保留：cocoa 平台插件、macOS 原生样式、fusion 样式
KEEP_PLUGIN_DIRS = {"platforms", "styles", "imageformats", "iconengines"}

# imageformats 里只留实际可能用到的图片解码器
KEEP_IMAGEFORMATS = {"libqjpeg.dylib", "libqtiff.dylib", "libqwebp.dylib", "libqgif.dylib"}

# Qt 框架保留清单。
# QtCore/QtGui/QtWidgets 是必备；QtDBus 是 macOS 上部分功能的软依赖。
# QtSvg 是界面图标用的：图标以内联 SVG 字符串保存（见 src/ui/icons.py），
# 运行时由 QSvgRenderer 渲染 —— 代价是切片后约 0.7 MB，换掉几十个图片资源
# 文件和一套与分辨率无关的图标，划算。删掉它 App 会启动即崩。
# QtPrintSupport 是「打印」入口用的：它承载 QPrinter / QPrintDialog，
# 在 macOS 上还负责把绘制结果交给 CUPS。删掉它打印按钮一按就报模块缺失。
KEEP_FRAMEWORKS = {"QtCore", "QtGui", "QtWidgets", "QtDBus", "QtSvg",
                   "QtPrintSupport"}

# 必须保留的 Qt 翻译文件（中文界面）
KEEP_TRANSLATIONS = {"qt_zh_CN.qm", "qtbase_zh_CN.qm"}

# 根目录下的命令行工具，运行时完全不需要
TOOL_NAMES = {
    "qmlls", "qmlformat", "qmltyperegistrar", "qmlimportscanner", "qmlcachegen",
    "lupdate", "lrelease", "lconvert", "uic", "rcc", "designer", "linguist",
    "assistant", "qmlplugindump", "qmlscene", "pyside6-uic", "pyside6-rcc",
    "pyside6-designer", "pyside6-lupdate", "pyside6-linguist", "pyside6-qmllint",
}

# ---------------------------------------------------------------------------
# PySide6 层级清理
#
# 为什么必须单独做这一步：py2app 处理 includes 里的 PySide6.QtCore 时，会把
# **整个 PySide6 包目录**整体拷进产物，而不是只拷被 import 到的模块。
# PySide6 6.10 把 Qt 模块分成 Essentials 与 Addons 两档，于是 Qt3D /
# QtWebEngine / QtMultimedia / QtCharts / QtLocation / QtGraphs / QtPdf …
# 连同各自的 QML 模块、metatypes 描述与 FFmpeg 动态库全部进了 bundle。
#
# 实测（同一份源码、同一台机器）：
#   不做这一步 —— 瘦身后 217 MB
#   做完这一步 —— 瘦身后 151 MB
# 差出来的 66 MB 全是本应用一行都不会 import 的东西。
#
# 用白名单而不是黑名单：Addons 的模块清单每次 PySide6 升级都可能变，黑名单漏
# 一个只会少省几 MB 且毫无提示；白名单漏了则会在 verify() 的自检里以导入失败
# 的形式立刻暴露 —— 「响了比不响好」的失败模式更适合这种静默变胖的问题。
#
# 清单直接对应 src/ 下的 import：QtCore / QtGui / QtWidgets / QtPrintSupport /
# QtSvg 五个。QtDBus 与 QtNetwork 是 Qt 内部的软依赖（某些系统的打印对话框会
# 经 QtDBus 走系统服务），一并留着，合计不到 2 MB。
KEEP_PYSIDE_MODULES = {
    "QtCore", "QtGui", "QtWidgets", "QtPrintSupport", "QtSvg",
    "QtDBus", "QtNetwork",
}

# 被删模块对应的 metatypes 描述文件关键词。
# metatypes 是 QML / Designer 工具链用来生成绑定的元数据，Widgets 运行时
# 用不到；但为稳妥起见只删与「已删模块」对应的那些，不清空整个目录。
DROP_METATYPE_HINTS = {
    "3d", "bluetooth", "charts", "datavisualization", "graphs", "httpserver",
    "location", "multimedia", "networkauth", "nfc", "pdf", "positioning",
    "quick3d", "remoteobjects", "scxml", "sensors", "serialbus", "serialport",
    "shadertools", "spatialaudio", "statemachine", "texttospeech",
    "virtualkeyboard", "webchannel", "webengine", "websockets", "webview",
}

# PySide6 根目录下需要单独点名的东西：Addons 清单文件与 Qt 自带的命令行工具。
# 这些不以 "Qt" 开头，走不到模块白名单那条分支。
DROP_PYSIDE_EXTRAS = {
    "PySide6_Addons.json": "Addons 模块清单",
    "PySide6_Essentials.json": "Essentials 模块清单",
    "balsam": "Qt 命令行工具",
    "balsamui": "Qt 命令行工具",
    "qsb": "Qt 命令行工具",
}

# 体积守卫：瘦身后超过这个值就告警并列出最大的残留项。
# 目的不是拦住构建，而是让「产物又悄悄变胖」在构建日志里可见 ——
# 否则只会在交付那一刻才发现，而那时已经不容易回溯是哪一步变的。
SIZE_GUARD_MB = 200


def human(num_bytes: float) -> str:
    return f"{num_bytes / 1024 / 1024:.1f} MB"


def dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            fp = os.path.join(root, name)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def archs_of(path: Path) -> list[str]:
    """返回 Mach-O 文件的架构列表；非 Mach-O 返回空列表。"""
    try:
        out = subprocess.run(["lipo", "-archs", str(path)],
                             capture_output=True, text=True, timeout=20)
    except Exception:
        return []
    if out.returncode != 0:
        return []
    return out.stdout.split()


def find_macho_files(root: Path) -> list[Path]:
    result = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            fp = Path(dirpath) / name
            if fp.is_symlink():
                continue          # 符号链接交给真实文件处理，避免破坏 bundle 结构
            if archs_of(fp):
                result.append(fp)
    return result


class Thinner:
    def __init__(self, app: Path, dry_run: bool = False, keep_x86: bool = False):
        self.app = app
        self.dry_run = dry_run
        self.keep_x86 = keep_x86
        self.removed_bytes = 0
        self.removed_items: list[str] = []

    # ---------- 删除 ----------

    def _drop(self, path: Path, reason: str) -> None:
        if not path.exists():
            return
        size = dir_size(path) if path.is_dir() else path.stat().st_size
        self.removed_bytes += size
        self.removed_items.append(f"{human(size):>9}  {path.relative_to(self.app)}   ({reason})")
        if not self.dry_run:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            else:
                try:
                    path.unlink()
                except OSError:
                    pass

    def clean_qt(self) -> None:
        candidates = list(self.app.rglob("PySide6"))
        qt_dirs = [p / "Qt" for p in candidates if (p / "Qt").is_dir()]
        if not qt_dirs:
            qt_dirs = [p for p in self.app.rglob("Qt") if (p / "lib").is_dir()]

        for qt in qt_dirs:
            lib = qt / "lib"
            if lib.is_dir():
                for entry in lib.iterdir():
                    base = entry.name.split(".")[0]
                    if entry.suffix in {".framework", ".dylib"} and base not in KEEP_FRAMEWORKS:
                        if base.startswith("Qt") or base.startswith("libQt"):
                            self._drop(entry, "未使用的 Qt 框架")

            # 多语言翻译：只留中文
            translations = qt / "translations"
            if translations.is_dir():
                for f in translations.iterdir():
                    if f.name not in KEEP_TRANSLATIONS:
                        self._drop(f, "非中文翻译包")

            # 插件目录白名单
            plugins = qt / "plugins"
            if plugins.is_dir():
                for entry in plugins.iterdir():
                    if entry.name not in KEEP_PLUGIN_DIRS:
                        self._drop(entry, "未使用的 Qt 插件目录")
                        continue
                    if entry.name == "imageformats":
                        for fmt in entry.iterdir():
                            if fmt.name not in KEEP_IMAGEFORMATS:
                                self._drop(fmt, "未使用的图片解码器")

            # QML 模块整棵删掉：Quick 系列绑定已在 clean_pyside() 里删除，
            # QML 引擎不会被加载，这些 .qml / .qmltypes 也就无人读取。
            self._drop(qt / "qml", "QML 模块（本应用是纯 QtWidgets 界面）")

            # metatypes：只删与已删模块对应的描述文件
            metatypes = qt / "metatypes"
            if metatypes.is_dir():
                for f in metatypes.iterdir():
                    stem = f.name.lower()
                    if any(h in stem for h in DROP_METATYPE_HINTS):
                        self._drop(f, "已删模块的 metatypes")

            # Qt/lib 下非 Qt 的动态库。FFmpeg（libav* / libsw*）是随
            # QtMultimedia 与 QtWebEngine 一起进来的，上面的框架白名单按
            # "Qt"/"libQt" 前缀判断，漏不到它们，得单独处理。
            if lib.is_dir():
                for entry in lib.iterdir():
                    if entry.suffix == ".dylib" and not (
                            entry.name.startswith("Qt")
                            or entry.name.startswith("libQt")):
                        self._drop(entry, "非 Qt 动态库（FFmpeg 等）")

        # PySide6 根目录下的命令行工具与 Qt Designer/Assistant 等 GUI 工具
        for pyside in candidates:
            for entry in pyside.iterdir():
                if entry.name in TOOL_NAMES or entry.name.startswith("pyside6-"):
                    self._drop(entry, "命令行工具")
                elif entry.name.endswith(".app"):
                    self._drop(entry, "Qt 自带 GUI 工具")

        # pymupdf 的开发文件与字节码缓存
        for mupdf in self.app.rglob("pymupdf"):
            for name, reason in (("mupdf-devel", "开发头文件/静态库"),
                                 ("__pycache__", "字节码缓存")):
                self._drop(mupdf / name, reason)

        # 全包范围的字节码缓存
        for cache in self.app.rglob("__pycache__"):
            self._drop(cache, "字节码缓存")

        self.clean_pyside()

    def clean_pyside(self) -> None:
        """删掉未被使用的 PySide6 绑定模块。

        PySide6 下每个 Qt 模块是一组独立的文件：
        `QtXxx.abi3.so`（二进制绑定）+ `QtXxx.pyi`（类型桩），少数以包目录
        形式存在（如 QtAsyncio）。它们之间没有编译期耦合，删掉不影响留在
        白名单里的模块 —— 绑定只在该模块被 import 时由 loader 加载。

        唯一必须避开的是 `PySide6/Qt/` 本身：它是以 "Qt" 开头的目录，但装的
        是被保留模块共用的 Qt 框架与插件，误删会让 App 启动即崩。
        """
        for pyside in self.app.rglob("PySide6"):
            if not pyside.is_dir():
                continue

            for entry in sorted(pyside.iterdir()):
                name = entry.name

                if name == "Qt":            # 框架目录，绝不动
                    continue

                stem = name
                for suffix in (".abi3.so", ".so", ".pyi"):
                    if stem.endswith(suffix):
                        stem = stem[: -len(suffix)]
                        break
                else:
                    # 不带上述后缀的，只有以 Qt 开头的包目录算模块
                    if not (entry.is_dir() and name.startswith("Qt")):
                        continue

                if stem in KEEP_PYSIDE_MODULES:
                    continue
                self._drop(entry, "未使用的 PySide6 模块")

            for name, reason in DROP_PYSIDE_EXTRAS.items():
                self._drop(pyside / name, reason)

    # ---------- 体积守卫 ----------

    def warn_if_fat(self) -> None:
        """产物体积超过守卫阈值时告警，并列出最大的几个体积来源。

        只告警、不失败：体积受 PySide6 版本影响，偶尔变大不见得是错的。
        但要让它出现在构建日志里，否则「悄悄胖 60 MB」这种事只能等到交付
        时才发觉，而那时已经很难回溯是哪一步引入的。
        """
        total = dir_size(self.app)
        if total <= SIZE_GUARD_MB * 1024 * 1024:
            return

        print(f"  ⚠ 产物体积 {human(total)}，超过守卫阈值 {SIZE_GUARD_MB} MB。"
              "最大的体积来源：")
        libs = list(self.app.rglob("lib/python3.9"))
        for lib in libs:
            for rel in ("PySide6", "PySide6/Qt", "PySide6/Qt/qml", "pymupdf"):
                p = lib / rel
                if p.is_dir():
                    print(f"     {human(dir_size(p)):>9}  {rel}")
        print("     提示：多半是 PySide6 的 Addons 又被整包拷进来了，"
              "检查 KEEP_PYSIDE_MODULES 是否覆盖到了新模块。")

    # ---------- 切片 ----------

    def thin_binaries(self) -> tuple[int, int, int]:
        """把 universal2 二进制切成 arm64。

        返回 (切片文件数, 切片前总字节, 切片后总字节)。
        """
        if self.keep_x86:
            return 0, 0, 0

        before_total = after_total = 0
        thinned = 0
        files = find_macho_files(self.app)
        print(f"  发现 {len(files)} 个 Mach-O 二进制，开始切片为 arm64…")

        with tempfile.TemporaryDirectory() as tmpdir:
            for fp in files:
                archs = archs_of(fp)
                if "arm64" not in archs or len(archs) < 2:
                    continue

                size_before = fp.stat().st_size
                before_total += size_before

                if self.dry_run:
                    # dry-run 也必须实测，否则算出来的节省量是 0，报告会失真。
                    # 切片结果写到临时目录量一下再删掉，全程不碰 bundle。
                    probe = Path(tmpdir) / "probe"
                    r = subprocess.run(
                        ["lipo", "-thin", "arm64", str(fp), "-output", str(probe)],
                        capture_output=True, text=True,
                    )
                    if r.returncode == 0 and probe.exists():
                        after_total += probe.stat().st_size
                        thinned += 1
                        probe.unlink(missing_ok=True)
                    continue

                # 符号链接要写回其指向的真实文件，否则会破坏 framework 结构
                real = Path(os.path.realpath(fp))
                tmp = real.with_suffix(real.suffix + ".thin")
                r = subprocess.run(
                    ["lipo", "-thin", "arm64", str(real), "-output", str(tmp)],
                    capture_output=True, text=True,
                )
                if r.returncode != 0 or not tmp.exists():
                    continue

                after = tmp.stat().st_size
                if after >= size_before:      # 本来就不是 fat，或用处不大
                    tmp.unlink(missing_ok=True)
                    continue

                os.replace(tmp, real)
                thinned += 1
                after_total += after

        self.removed_bytes += before_total - after_total
        return thinned, before_total, after_total

    # ---------- 验证 ----------

    def verify(self) -> bool:
        """调用 App 自检，确认瘦身后仍可正常导入与渲染。

        这一步是整套流程的安全阀：删错东西时能立刻发现，
        而不是把问题留到用户双击图标的时候。

        必须选**真正的 App 启动器**。py2app 的 Contents/MacOS 下同时存在
        以 App 名命名的启动器和辅助用的 python 可执行文件，后者带着不同的
        动态库路径（直接跑它会报 Library not loaded），拿它验证会得到假阴性。
        """
        icon_ok = self._verify_icon()

        macos_dir = self.app / "Contents" / "MacOS"
        if not macos_dir.is_dir():
            print("  ⚠ 未找到 Contents/MacOS，跳过自检")
            return icon_ok

        stem = self.app.stem
        exes = [p for p in macos_dir.iterdir() if p.is_file() and os.access(p, os.X_OK)]
        launcher = next((p for p in exes if p.name == stem), None)
        if launcher is None:                      # 命名不一致时退而求其次
            launcher = next((p for p in exes if p.name != "python"), None)
        if launcher is None:
            print("  ⚠ 未找到 App 启动器，跳过自检")
            return icon_ok

        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        r = subprocess.run([str(launcher), "--selftest"],
                           capture_output=True, text=True, timeout=300, env=env)
        ok = r.returncode == 0 and "SELFTEST-OK" in (r.stdout or "")
        print(f"  启动器：{launcher.name}")
        print("  ✅ 自检通过：导入、字体、拼版、水印、图层、预览清晰度、缩放、"
              "保密输出、打印、打印设备、本地化均正常"
              if ok else "  ❌ 自检失败")
        if not ok:
            print("     stdout:", (r.stdout or "")[-1000:])
            print("     stderr:", (r.stderr or "")[-1000:])
        return ok and icon_ok

    def _verify_icon(self) -> bool:
        """确认图标真的进了产物。

        图标是「不报错也会丢」的典型：py2app 在 iconfile 指向的文件不存在时
        会直接失败，但如果 plist 里没写上 CFBundleIconFile，产物照样能启动，
        只是 Finder 里显示成默认白纸图标 —— 只有肉眼才看得出来，所以这里查两处：
        plist 声明 + Resources 下真有那个 .icns。
        """
        plist = self.app / "Contents" / "Info.plist"
        resources = self.app / "Contents" / "Resources"
        if not plist.exists():
            print("  ⚠ 未找到 Info.plist，跳过图标检查")
            return True

        r = subprocess.run(["/usr/libexec/PlistBuddy", "-c",
                            "Print :CFBundleIconFile", str(plist)],
                           capture_output=True, text=True)
        declared = (r.stdout or "").strip()
        icns = sorted(resources.glob("*.icns")) if resources.is_dir() else []
        if declared and icns:
            print(f"  图标：{icns[0].name}（{icns[0].stat().st_size / 1024:.0f} KB）")
            return True
        print(f"  ❌ 图标缺失（plist 声明={declared or '无'}，"
              f"Resources 下 icns={[p.name for p in icns] or '无'}）")
        return False


def locate_app(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    dist = Path("dist")
    if not dist.is_dir():
        sys.exit("未找到 dist/ 目录，请先执行 python setup.py py2app")
    apps = sorted(dist.glob("*.app"))
    if not apps:
        sys.exit("dist/ 下没有 .app，请先执行 python setup.py py2app")
    return apps[0].resolve()


def main() -> int:
    ap = argparse.ArgumentParser(description="py2app 产物瘦身")
    ap.add_argument("--app", help=".app 路径（默认自动查找 dist/）")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不修改")
    ap.add_argument("--keep-x86", action="store_true", help="保留 x86_64（不切片）")
    args = ap.parse_args()

    app = locate_app(args.app)
    if not app.is_dir():
        sys.exit(f"不是有效的 .app：{app}")

    original = dir_size(app)
    print(f"目标：{app}")
    print(f"原始体积：{human(original)}\n")

    thinner = Thinner(app, dry_run=args.dry_run, keep_x86=args.keep_x86)

    print("[1/3] 清理冗余 Qt 资源与未使用的 PySide6 模块…")
    thinner.clean_qt()
    print(f"      删除 {len(thinner.removed_items)} 项")

    print("[2/3] 二进制切片…")
    thinned, before, after = thinner.thin_binaries()
    if args.dry_run:
        print(f"      预计切片 {thinned} 个文件，{human(before)} -> {human(after)}")
    else:
        print(f"      切片 {thinned} 个文件，{human(before)} -> {human(after)}")

    final = dir_size(app)
    removed = original - final
    pct = (removed / original * 100) if original else 0
    if args.dry_run:
        # dry-run 未真正改动磁盘，用累计统计给出预估结果
        projected = original - thinner.removed_bytes
        ppct = (thinner.removed_bytes / original * 100) if original else 0
        print(f"\n体积预估：{human(original)} -> {human(projected)}   "
              f"预计减少 {human(thinner.removed_bytes)}（{ppct:.0f}%）")
        print("\n（dry-run，未实际修改。明细如下）")
        for line in thinner.removed_items[:60]:
            print("  " + line)
        if len(thinner.removed_items) > 60:
            print(f"  … 另有 {len(thinner.removed_items) - 60} 项")
        return 0

    print(f"\n体积：{human(original)} -> {human(final)}   减少 {human(removed)}（{pct:.0f}%）")
    thinner.warn_if_fat()

    print("\n[3/3] 自检…")
    ok = thinner.verify()
    if not ok:
        print("\n瘦身导致 App 无法启动，请检查上面的删除清单。")
        return 2

    print("\n完成。可直接双击运行，或拖入「应用程序」文件夹。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
