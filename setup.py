"""py2app 打包配置。

体积控制的第一道闸门：在打包阶段就把用不到的东西排除掉，
第二道闸门是 build_thin.py（对产物做二进制瘦身与冗余清理）。

编译：python setup.py py2app
"""

import os

from setuptools import setup

APP = ["main.py"]

# 应用图标。由 tools/make_icon.py 生成（squircle + 透明留白 + 10 个尺寸）。
# 路径必须是存在的文件，否则 py2app 会直接报错退出。
# 文件名刻意用 ASCII：它只是包内标识，用户看到的是图标本身，没必要冒
# 非 ASCII 路径在构建链路上被编码搞坏的风险。
ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "assets", "pdftoolkit.icns")

# 本地化资源目录。用途不是翻译界面，而是**让 macOS 认下这个 App 支持中文**。
#
# 起因：添加上传文件时弹出的文件夹窗口（原生 NSOpenPanel）整体是英文。
# 根因在 bundle 的声明 —— py2app 默认只写 CFBundleDevelopmentRegion=English，
# 既没有 CFBundleLocalizations 也没有任何 .lproj，macOS 于是判定本 App 只支持
# 英文，连带把 AppKit 自己的界面（打开/保存面板、右键菜单项）也切成英文，
# 与系统语言无关。因为走的是原生面板，Qt 侧的中文翻译包在这里帮不上忙。
#
# 修法是补齐两处声明（见下面的 plist）并放上真实的 .lproj 目录：
# CFBundleLocalizations 是权威来源，.lproj 目录是传统识别方式，两者都给最稳。
LPROJ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "lproj")

# 本应用只用 QtCore / QtGui / QtWidgets / QtSvg / QtPrintSupport。
# 下面这些 Qt 模块一律排除，它们占了 PySide6 安装体积的很大一块，
# 但一行代码都不会被调用到。
QT_UNUSED = [
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2", "PySide6.QtDesigner", "PySide6.QtUiTools",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtNetworkAuth",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtHelp", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtSvg", "PySide6.QtSvgWidgets",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.Qt3DCore",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtStateMachine", "PySide6.QtTextToSpeech",
    "PySide6.QtHttpServer", "PySide6.QtSpatialAudio",
]

# 标准库里不会被用到的模块，排除后可减小几 MB
#
# 注意：不要排除 multiprocessing / concurrent / asyncio。
# py2app 生成的 __boot__.py 会无条件 `import multiprocessing.spawn`，
# 排掉它会直接导致 App 启动失败（ModuleNotFoundError），
# 而这个错误在打包阶段不会暴露，只有真正启动时才报。
STDLIB_UNUSED = [
    "tkinter", "unittest", "pydoc", "doctest", "lib2to3",
    "xmlrpc", "pdb", "curses",
    "ftplib", "imaplib", "smtplib", "poplib", "nntplib",
    "turtledemo", "ensurepip", "venv", "test", "idlelib",
]

# 其它明确不用的第三方库
#
# 重要：py2app 会把**当前 Python 环境**里可达的包都收进 App。若构建环境里
# 顺带装了数据分析类库，它们的体积会原封不动进到产物里。实测在一个被污染的
# 共享 venv 里构建时，pandas / geopandas / pyogrio / numpy / matplotlib /
# fontTools 全被打包，凭空多出 200 MB 以上。
# 正确做法见 README：用只装运行时依赖的干净 venv 构建。这里再兜一层排除。
OTHER_UNUSED = [
    "PyQt5", "PyQt6", "PySide2", "PyQt5.sip", "PyQt6.sip",
    # 数据分析/绘图栈：本应用完全用不到
    "numpy", "scipy", "pandas", "matplotlib", "mpl_toolkits", "contourpy",
    "kiwisolver", "cycler", "pyparsing", "fontTools", "fonttools",
    "pyogrio", "pyproj", "shapely", "geopandas", "openpyxl", "et_xmlfile",
    "dateutil", "six", "certifi",
    # 图片处理：已从依赖中移除（改用 PyMuPDF 原生读图）
    "PIL", "Pillow",
    # 构建期工具，运行时不需要
    "setuptools", "pip", "pkg_resources", "wheel", "py2app", "modulegraph",
    "macholib", "altgraph", "packaging", "distutils",
]

OPTIONS = {
    "argv_emulation": False,          # 设为 True 会强依赖 Carbon，现代系统上无意义还增依赖
    "packages": ["src"],
    "includes": [
        "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
        # 打印走系统打印对话框，QtPrintSupport 必须显式带上；
        # 注意别把它加进上面的 QT_UNUSED，否则「打印」会直接报模块缺失。
        "PySide6.QtPrintSupport",
        "pymupdf",
    ],
    "excludes": QT_UNUSED + STDLIB_UNUSED + OTHER_UNUSED,
    "strip": True,                    # 去掉符号
    "optimize": 2,                    # 去掉断言与文档字符串
    "iconfile": ICON,
    "plist": {
        "CFBundleName": "PDF 工具箱",
        "CFBundleDisplayName": "PDF 工具箱",
        "CFBundleIdentifier": "com.local.pdftoolkit",
        "CFBundleVersion": "3.0.0",
        "CFBundleShortVersionString": "3.0.0",
        # 开发区域设为中文：本地化全都匹配不上时，macOS 回退到中文而不是英文，
        # 与本应用实际的中文界面一致，不会出现「界面中文、系统面板英文」的割裂。
        # 注意值用 zh_CN 而非 zh-Hans：旧系统（10.15 之前）只认带地区的写法。
        "CFBundleDevelopmentRegion": "zh_CN",
        # 声明支持的界面语言。zh-Hans 与 zh_CN 都写上，覆盖新旧系统的不同判定。
        "CFBundleLocalizations": ["zh-Hans", "zh_CN", "en"],
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "PDF 文件",
                "CFBundleTypeRole": "Editor",
                "LSItemContentTypes": ["com.adobe.pdf"],
                "LSHandlerRank": "Alternate",
            },
        ],
    },
    "resources": [
        os.path.join(LPROJ, "zh-Hans.lproj"),
        os.path.join(LPROJ, "zh_CN.lproj"),
        os.path.join(LPROJ, "en.lproj"),
    ],
}

setup(
    app=APP,
    name="PDF工具箱",
    data_files=[],
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
