import sys
import os
import logging
import traceback
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QPushButton, QListWidget, QLabel, QFileDialog, QSpinBox,
                               QComboBox, QMessageBox, QScrollArea, QProgressBar)
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QPixmap, QAction
from src.core.pdf_merger import PDFMergerCore


log_file = 'pdf_merger_error.log'
logging.basicConfig(
    filename=log_file,
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


class PreviewWorker(QThread):
    preview_ready = Signal(bytes)
    progress_updated = Signal(int, str)
    error_occurred = Signal(str)

    def __init__(self, files, rows, cols, orientation):
        super().__init__()
        self.files = files.copy()
        self.rows = rows
        self.cols = cols
        self.orientation = orientation
        self.core = PDFMergerCore()

    def run(self):
        try:
            img_data = self.core.generate_preview_image(
                self.files,
                rows=self.rows,
                cols=self.cols,
                orientation=self.orientation
            )
            if img_data:
                self.preview_ready.emit(img_data)
        except Exception as e:
            self.error_occurred.emit(f'预览生成失败：{str(e)}')
        finally:
            self.core.cleanup_temp_files()


class MergeWorker(QThread):
    merge_completed = Signal(str)
    progress_updated = Signal(int, str)
    error_occurred = Signal(str)

    def __init__(self, files, rows, cols, orientation, output_file):
        super().__init__()
        self.files = files.copy()
        self.rows = rows
        self.cols = cols
        self.orientation = orientation
        self.output_file = output_file
        self.core = PDFMergerCore()

    def run(self):
        try:
            def progress_callback(value, text):
                self.progress_updated.emit(value, text)

            output_doc = self.core.merge_files(
                self.files,
                rows=self.rows,
                cols=self.cols,
                orientation=self.orientation,
                progress_callback=progress_callback
            )

            output_doc.save(self.output_file)
            output_doc.close()
            self.merge_completed.emit(self.output_file)
        except Exception as e:
            self.error_occurred.emit(f'文件合并失败：{str(e)}')
        finally:
            self.core.cleanup_temp_files()


class PDFMergerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.files = []
        self.preview_label = None
        self.progress_bar = None
        self.preview_worker = None
        self.merge_worker = None
        self.initUI()

    def log_error(self, error_msg, exc_info=None):
        if exc_info:
            logging.error(f"{error_msg}\n{traceback.format_exc()}")
        else:
            logging.error(error_msg)

    def initUI(self):
        self.setWindowTitle('PDF发票合并助手')
        self.setGeometry(100, 100, 1200, 800)

        self.setup_menu_bar()

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QHBoxLayout(central_widget)

        left_layout = QVBoxLayout()

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QListWidget.ExtendedSelection)
        left_layout.addWidget(QLabel('已选择的文件：'))
        left_layout.addWidget(self.file_list)

        button_layout = QHBoxLayout()
        add_button = QPushButton('添加文件')
        remove_button = QPushButton('移除文件')
        remove_all_button = QPushButton('移除全部文件')
        button_layout.addWidget(add_button)
        button_layout.addWidget(remove_button)
        button_layout.addWidget(remove_all_button)
        left_layout.addLayout(button_layout)

        middle_layout = QVBoxLayout()

        middle_layout.addWidget(QLabel('页面方向：'))
        self.orientation = QComboBox()
        self.orientation.addItems(['纵向', '横向'])
        middle_layout.addWidget(self.orientation)

        middle_layout.addWidget(QLabel('每页文件数：'))
        layout_options = QHBoxLayout()
        self.rows = QSpinBox()
        self.cols = QSpinBox()
        self.rows.setMinimum(1)
        self.cols.setMinimum(1)
        self.rows.setValue(3)
        self.cols.setValue(2)
        layout_options.addWidget(QLabel('行数：'))
        layout_options.addWidget(self.rows)
        layout_options.addWidget(QLabel('列数：'))
        layout_options.addWidget(self.cols)
        middle_layout.addLayout(layout_options)

        self.merge_button = QPushButton('合并文件')
        self.merge_button.setStyleSheet("QPushButton { background-color: #4CAF50; color: white; font-weight: bold; padding: 10px; }")
        middle_layout.addWidget(self.merge_button)

        middle_layout.addStretch()

        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel('预览：'))

        preview_scroll = QScrollArea()

        self.progress_bar = QProgressBar()
        self.progress_bar.setAlignment(Qt.AlignCenter)
        self.progress_bar.setTextVisible(True)
        right_layout.addWidget(self.progress_bar)

        preview_scroll.setWidgetResizable(True)
        preview_container = QWidget()
        preview_layout = QVBoxLayout(preview_container)
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setStyleSheet("QLabel { background-color: #f5f5f5; }")
        preview_layout.addWidget(self.preview_label)
        preview_scroll.setWidget(preview_container)
        right_layout.addWidget(preview_scroll)

        layout.addLayout(left_layout, 2)
        layout.addLayout(middle_layout, 1)
        layout.addLayout(right_layout, 3)

        add_button.clicked.connect(self.add_files)
        remove_button.clicked.connect(self.remove_files)
        remove_all_button.clicked.connect(self.remove_all_files)
        self.merge_button.clicked.connect(self.merge_files)
        self.orientation.currentIndexChanged.connect(self.update_preview)
        self.rows.valueChanged.connect(self.update_preview)
        self.cols.valueChanged.connect(self.update_preview)

    def setup_menu_bar(self):
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu('文件')

        add_action = QAction('添加文件', self)
        add_action.setShortcut('Ctrl+O')
        add_action.triggered.connect(self.add_files)

        remove_action = QAction('移除选中', self)
        remove_action.setShortcut('Delete')
        remove_action.triggered.connect(self.remove_files)

        clear_action = QAction('清空列表', self)
        clear_action.setShortcut('Ctrl+Shift+Delete')
        clear_action.triggered.connect(self.remove_all_files)

        merge_action = QAction('合并文件', self)
        merge_action.setShortcut('Ctrl+M')
        merge_action.triggered.connect(self.merge_files)

        quit_action = QAction('退出', self)
        quit_action.setShortcut('Ctrl+Q')
        quit_action.triggered.connect(self.close)

        file_menu.addAction(add_action)
        file_menu.addAction(remove_action)
        file_menu.addAction(clear_action)
        file_menu.addSeparator()
        file_menu.addAction(merge_action)
        file_menu.addSeparator()
        file_menu.addAction(quit_action)

        help_menu = menu_bar.addMenu('帮助')
        about_action = QAction('关于', self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def show_about(self):
        QMessageBox.about(self, '关于', 'PDF发票合并助手\n\n版本: 2.0.0\n\n基于 PySide6 + PyMuPDF 构建\n专为 macOS 26 及 M 系列芯片优化')

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择文件",
            "",
            "支持的文件 (*.pdf *.jpg *.jpeg *.png *.tif *.bmp)"
        )
        for file in files:
            if file not in self.files:
                self.files.append(file)
                self.file_list.addItem(os.path.basename(file))
        if files:
            self.update_preview()
        self.update_progress_bar()

    def remove_files(self):
        for item in self.file_list.selectedItems():
            idx = self.file_list.row(item)
            self.file_list.takeItem(idx)
            self.files.pop(idx)
        self.update_preview()
        self.update_progress_bar()

    def remove_all_files(self):
        self.files.clear()
        self.file_list.clear()
        self.update_preview()
        self.update_progress_bar()

    def update_progress_bar(self):
        total_files = len(self.files)
        if total_files == 0:
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("等待添加文件")
        else:
            self.progress_bar.setValue(100)
            self.progress_bar.setFormat(f"已选择 {total_files} 个文件")

    def update_preview(self):
        if not self.files:
            self.preview_label.clear()
            return

        if self.preview_worker and self.preview_worker.isRunning():
            self.preview_worker.quit()
            self.preview_worker.wait()

        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("正在生成预览...")

        self.preview_worker = PreviewWorker(
            self.files,
            rows=self.rows.value(),
            cols=self.cols.value(),
            orientation=self.orientation.currentText()
        )
        self.preview_worker.preview_ready.connect(self.on_preview_ready)
        self.preview_worker.error_occurred.connect(self.on_preview_error)
        self.preview_worker.finished.connect(self.on_preview_finished)
        self.preview_worker.start()

    def on_preview_ready(self, img_data):
        qimg = QPixmap()
        qimg.loadFromData(img_data)
        self.preview_label.setPixmap(qimg)

    def on_preview_error(self, error_msg):
        self.log_error(error_msg)
        QMessageBox.warning(self, '警告', error_msg)
        self.preview_label.clear()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("预览生成失败")

    def on_preview_finished(self):
        self.progress_bar.setValue(100)
        self.progress_bar.setFormat("预览完成")

    def merge_files(self):
        if not self.files:
            QMessageBox.warning(self, '警告', '请先添加文件！')
            return

        output_file, _ = QFileDialog.getSaveFileName(
            self,
            "保存合并后的PDF",
            "",
            "PDF文件 (*.pdf)"
        )

        if not output_file:
            return

        if self.merge_worker and self.merge_worker.isRunning():
            self.merge_worker.quit()
            self.merge_worker.wait()

        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("正在处理文件...")
        self.merge_button.setEnabled(False)

        self.merge_worker = MergeWorker(
            self.files,
            rows=self.rows.value(),
            cols=self.cols.value(),
            orientation=self.orientation.currentText(),
            output_file=output_file
        )
        self.merge_worker.merge_completed.connect(self.on_merge_completed)
        self.merge_worker.progress_updated.connect(self.on_merge_progress)
        self.merge_worker.error_occurred.connect(self.on_merge_error)
        self.merge_worker.finished.connect(self.on_merge_finished)
        self.merge_worker.start()

    def on_merge_progress(self, value, text):
        self.progress_bar.setValue(value)
        self.progress_bar.setFormat(text)

    def on_merge_completed(self, output_file):
        QMessageBox.information(self, '成功', '文件合并完成！')
        self.progress_bar.setValue(100)
        self.progress_bar.setFormat("合并完成")

    def on_merge_error(self, error_msg):
        self.log_error(error_msg)
        QMessageBox.critical(self, '错误', error_msg)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("合并失败")

    def on_merge_finished(self):
        self.merge_button.setEnabled(True)


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    merger = PDFMergerWindow()
    merger.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()