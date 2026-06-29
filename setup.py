from setuptools import setup

APP = ['main.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': True,
    'packages': ['src'],
    'includes': ['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'fitz', 'PIL'],
    'excludes': [
        'PyQt5',
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.QtWidgets',
        'PyQt5.sip',
        'tkinter',
        'unittest',
        'xmlrpc',
        'curses',
        'cryptography',
    ],
    'iconfile': None,
    'plist': {
        'CFBundleName': 'PDF发票合并助手',
        'CFBundleDisplayName': 'PDF发票合并助手',
        'CFBundleIdentifier': 'com.example.pdfmerger',
        'CFBundleVersion': '2.0.0',
        'CFBundleShortVersionString': '2.0.0',
        'NSHumanReadableCopyright': 'Copyright (c) 2024. All rights reserved.',
        'NSPrincipalClass': 'NSApplication',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.15',
        'CFBundleDocumentTypes': [
            {
                'CFBundleTypeName': 'PDF File',
                'CFBundleTypeRole': 'Editor',
                'LSItemContentTypes': ['com.adobe.pdf'],
                'LSHandlerRank': 'Default'
            },
            {
                'CFBundleTypeName': 'Image File',
                'CFBundleTypeRole': 'Editor',
                'LSItemContentTypes': ['public.jpeg', 'public.png', 'public.tiff', 'com.microsoft.bmp'],
                'LSHandlerRank': 'Default'
            }
        ],
        'NSRequiresAquaSystemAppearance': False,
    },
    'resources': [],
}

setup(
    app=APP,
    name='PDFMerger',
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)