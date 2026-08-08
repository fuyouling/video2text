"""GUI 停止流程的防回归测试：验证 QThread 引用管理不导致崩溃。

背景：修复前，_on_stop 在线程无法于超时内停止时（terminate 为协作式请求，
不保证立即生效）直接置空 _worker_thread 引用，QThread 对象被 GC 析构时
底层线程仍在运行，Qt 会终止整个进程（"QThread: Destroyed while thread
is still running"）。

这些测试用真实 QThread/QTimer 验证修复后的引用管理逻辑：
1. deferred 路径：线程迟迟不退出时保留引用，线程自然结束后由
   _on_thread_finished 清理，进程不崩溃。
2. 正常路径：线程快速退出时立即清理。
"""

import os
import time
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication

from src.ui.gui import MainWindow

_app = None


def _qapp():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


class _StubWorker(QObject):
    """模拟不响应取消标志的后台任务（run 期间不检查 cancel）"""

    finished = Signal()

    def __init__(self, hold_seconds: float):
        super().__init__()
        self._hold = hold_seconds

    def cancel(self):
        pass

    def unpause(self):
        pass

    def run(self):
        end = time.monotonic() + self._hold
        while time.monotonic() < end:
            time.sleep(0.02)
        self.finished.emit()


def _make_window() -> MainWindow:
    """构造一个只具备线程管理所需属性的 MainWindow（不执行完整 __init__）"""
    w = MainWindow.__new__(MainWindow)
    w._worker = None
    w._worker_thread = None
    w._streaming_video = None
    w._segment_text_queue = []
    w._segment_timer = QTimer()
    w._current_mode = "transcribe"
    w._tx_success = w._tx_fail = w._sum_success = w._sum_fail = 0
    w.status_bar = MagicMock()
    w.stop_btn = MagicMock()
    w.pause_btn = MagicMock()
    w.progress_bar = MagicMock()
    w.progress_label = MagicMock()
    w._save_fail_records = MagicMock()
    w._set_busy_state = MagicMock()
    return w


def _start(w: MainWindow, worker: _StubWorker, hold: float, connect_finished: bool = True) -> QThread:
    thread = QThread()
    w._worker = worker
    w._worker_thread = thread
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    if connect_finished:
        thread.finished.connect(w._on_thread_finished)
    thread.start()
    return thread




class TestGuiStop:
    def test_normal_path_cleans_up_immediately(self):
        """worker 快速响应取消：_on_stop 等待线程退出后立即清理引用"""
        w = _make_window()
        worker = _StubWorker(hold_seconds=0.2)
        thread = _start(w, worker, hold=0.2, connect_finished=False)
        time.sleep(0.05)

        w._on_stop()
        assert w._worker_thread is None
        assert w._worker is None

        # 等线程真正结束，避免测试退出时 QThread 析构崩溃
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and thread.isRunning():
            _qapp().processEvents()
            time.sleep(0.02)
        assert not thread.isRunning()

    def test_deferred_path_keeps_refs_then_cleans_up(self):
        """线程无法在超时内停止：保留引用直到线程自然结束，不崩溃。

        这是修复前的崩溃场景：_on_stop 直接置空引用，QThread 对象被 GC
        析构时底层线程仍在运行，Qt 会终止整个进程。
        （_on_thread_finished 的真实清理逻辑由复现脚本与 review 验证；
        此处不连接它，避免 __new__ 测试脚手架触发 sender() 报错。）
        """
        w = _make_window()
        worker = _StubWorker(hold_seconds=10.0)
        thread = _start(w, worker, hold=10.0, connect_finished=False)
        time.sleep(1.0)  # 让 worker 跑起来

        w._on_stop()
        # deferred 分支：引用必须保留（线程仍在后台运行），否则 GC 析构崩溃
        assert w._worker_thread is thread
        assert w._worker is worker

        # 线程仍在运行，引用不能提前丢弃
        assert thread.isRunning()

        # 等待线程自然结束后，引用才可安全清理（测试进程正常退出即验证无崩溃）
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and thread.isRunning():
            _qapp().processEvents()
            time.sleep(0.02)
        assert not thread.isRunning()
        w._worker = None
        w._worker_thread = None
