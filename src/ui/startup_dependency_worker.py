"""启动依赖检测与下载 Worker。

串行执行模型 + DLL 依赖检测与下载，供 GUI 启动时在后台线程调用。
"""

from typing import Optional

from PySide6.QtCore import QObject, Signal

from src.config.settings import Settings
from src.utils.logger import get_logger
from src.utils.model_downloader import check_models_integrity

logger = get_logger("video2text")


class StartupDependencyWorker(QObject):
    """单线程串行执行模型 + DLL 依赖检测与下载。

    相比两个独立线程并行，串行执行可确保：
    - 日志输出不交错（同一线程顺序打印）
    - 进度消息不闪烁切换

    关键说明：
    - 模型下载由 check_models_integrity 内部检查 is_check_model_file 标记。
      因此 Phase 1（主线程）中**不应**将缺失项的 is_check_model_file 设为 false，
      否则 Phase 2 中 check_models_integrity 会直接跳过下载。
      仅在项已完整或用户取消时才设 false。
    - DLL 下载由 DllDownloader.download_and_extract 检查文件是否存在，
      不依赖 is_check_dll_file 标记，所以 DLL 侧无此约束。
    """

    # (source, downloaded_bytes, total_bytes, file_percent, current_item, total_items)
    progress_updated = Signal(str, int, int, int, int, int)
    phase_changed = Signal(str)  # "model" | "dll"
    finished = Signal(bool)

    def __init__(
        self,
        download_model: bool,
        download_dll: bool,
        keep_archive: bool = False,
    ) -> None:
        super().__init__()
        self._download_model = download_model
        self._download_dll = download_dll
        self._keep_archive = keep_archive
        self._cancelled = False

    def cancel(self) -> None:
        """取消当前下载（线程安全）。"""
        self._cancelled = True

    def run(self) -> None:
        """在后台线程串行执行模型下载 → DLL 下载。"""
        ok = False
        try:
            if self._cancelled:
                self.finished.emit(False)
                return

            # Phase 2a: 模型下载
            if self._download_model:
                self.phase_changed.emit("model")
                model_ok = check_models_integrity(
                    Settings(),
                    progress_callback=self._make_model_cb(),
                )
                if not model_ok:
                    self.finished.emit(False)
                    return

            # Phase 2b: DLL 下载
            if self._download_dll:
                if self._cancelled:
                    self.finished.emit(False)
                    return
                self.phase_changed.emit("dll")
                dll_ok = self._run_dll_phase()
                if not dll_ok:
                    self.finished.emit(False)
                    return

            ok = True
        except Exception:
            logger.exception("依赖检测异常")
            ok = False
        self.finished.emit(ok)

    def _run_dll_phase(self) -> bool:
        """执行 DLL 下载与解压。"""
        from src.utils.dll_downloader import DllDownloader

        downloader = DllDownloader()
        if downloader.is_dlls_complete():
            return True
        ok = downloader.download_and_extract(
            progress_callback=self._make_dll_cb(),
        )
        if ok and not self._keep_archive:
            downloader.cleanup_archive()
        return ok

    def _make_model_cb(self):
        def _cb(downloaded: int, total: int, current_item: int, total_items: int) -> None:
            # 以「当前文件」为 100%：percent = downloaded / file_total。
            # 文件总量未知(total<=0)时 percent 置 0，由 GUI 以无限滚动展示。
            percent = int(downloaded / total * 100) if total > 0 else 0
            self.progress_updated.emit(
                "model", downloaded, total, percent, current_item, total_items
            )

        return _cb

    def _make_dll_cb(self):
        def _cb(downloaded: int, total: int, current_item: int, total_items: int) -> None:
            # DLL 仅一个压缩包文件，percent = downloaded / archive_total。
            percent = int(downloaded / total * 100) if total > 0 else 0
            self.progress_updated.emit(
                "dll", downloaded, total, percent, current_item, total_items
            )

        return _cb
