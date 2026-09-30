"""后台任务与预览防抖的基础设施。

对照参考项目 pdf-merger-macos 的教训：它在主线程里对 QThread 调
`quit()` + `wait()`，而线程的 run() 没有事件循环，quit() 不生效，
于是界面被阻塞到任务算完 —— 与「异步防冻结」的初衷正好相反。

这里的做法：
* 后台线程只跑任务本身，全程通过信号汇报进度；
* 主线程绝不 wait()，而是禁用按钮、显示进度条；
* 预览用 QTimer 防抖，避免拖动滑块时疯狂重算。
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, QTimer, Signal


class TaskWorker(QThread):
    """在后台线程执行一个可调用对象。

    约定：被调用的函数接收一个 progress(done, total, text) 回调，
    并通过 self.cancelled 检查是否需要提前退出。
    """

    progressed = Signal(int, int, str)      # done, total, text
    succeeded = Signal(object)              # 函数返回值
    failed = Signal(str)                    # 用户可读的错误信息

    def __init__(self, func: Callable, parent: QObject | None = None):
        super().__init__(parent)
        self._func = func
        self.cancelled = False

    def cancel(self) -> None:
        """只置标志位，不做任何阻塞等待 —— 由任务自身在安全点退出。"""
        self.cancelled = True

    def run(self) -> None:  # noqa: D102 - QThread 约定
        try:
            result = self._func(self._report_progress)
            if self.cancelled:
                self.failed.emit("任务已取消。")
            else:
                self.succeeded.emit(result)
        except Exception as exc:  # 兜底，绝不让异常冒泡到 Qt 事件循环
            self.failed.emit(str(exc))

    def _report_progress(self, done: int, total: int, text: str) -> None:
        self.progressed.emit(done, total, text)


class Debouncer(QObject):
    """把连续触发合并成一次延迟执行，用于预览这类高开销操作。"""

    def __init__(self, delay_ms: int, callback: Callable[[], None], parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(callback)

    def trigger(self) -> None:
        self._timer.start()      # 重复触发会重置计时

    def stop(self) -> None:
        self._timer.stop()
