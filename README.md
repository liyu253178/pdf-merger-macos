# PDF发票合并助手

一款专为 macOS 26 及 M 系列芯片优化的 PDF 发票合并工具。

## 功能特点

- **多文件合并**：支持同时选择多个 PDF 文件和图像文件进行合并
- **图像转 PDF**：自动将 JPG、PNG 等图像文件转换为 PDF 格式
- **自定义布局**：允许用户设置每页的行数和列数
- **页面预览**：在合并前提供文件预览功能
- **进度显示**：实时显示合并进度
- **错误日志**：记录运行过程中可能出现的错误

## 技术架构

```
pdf-merger-macos/
├── main.py                 # 应用入口
├── setup.py                # py2app 打包配置
├── requirements.txt        # 依赖列表
├── .gitignore              # Git 忽略规则
└── src/
    ├── core/
    │   └── pdf_merger.py   # PDF 合并核心逻辑
    ├── ui/
    │   └── main_window.py  # 主窗口 UI
    ├── models/             # 数据模型（预留）
    └── utils/              # 工具函数（预留）
```

## 技术栈

- **框架**: PySide6 (Qt 官方 Python 绑定)
- **PDF 处理**: PyMuPDF (高性能 PDF 库)
- **图像处理**: Pillow
- **打包工具**: py2app

## 系统要求

- macOS 10.15 或更高版本
- M 系列芯片或 Intel 芯片
- Python 3.9+

## 安装依赖

```bash
pip install -r requirements.txt
```

## 运行应用

```bash
python main.py
```

## 打包应用

```bash
python setup.py py2app
```

打包完成后，应用位于 `dist/PDF发票合并助手.app`，可直接拖拽到应用程序文件夹。

## 使用说明

1. 点击「添加文件」按钮，选择要合并的 PDF 或图像文件
2. 设置页面方向（纵向/横向）和每页文件数（行数×列数）
3. 预览区域会实时显示合并效果
4. 点击「合并文件」按钮，选择输出路径和文件名
5. 等待合并完成，进度条会实时更新

## 快捷键

- `Ctrl+O`：添加文件
- `Delete`：移除选中文件
- `Ctrl+Shift+Delete`：清空列表
- `Ctrl+M`：合并文件
- `Ctrl+Q`：退出应用

## 许可证

MIT License