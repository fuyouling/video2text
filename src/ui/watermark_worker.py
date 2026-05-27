"""批量去水印 Worker 线程"""

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.ui.watermark_dialog import Region, apply_watermark_removal, imread, imwrite


class WatermarkWorker(QObject):
    """批量去水印后台线程 —— 逐个处理图片，通过信号报告进度和结果。"""

    progress = Signal(int, int)
    file_done = Signal(str)
    file_skipped = Signal(str)
    file_error = Signal(str, str)
    finished = Signal()

    def __init__(
        self,
        tasks: list[tuple[str, list[Region]]],
        mode: str,
        params: dict,
        output_base: str,
        use_flat_output: bool,
    ) -> None:
        super().__init__()
        self._tasks = tasks
        self._mode = mode
        self._params = params
        self._output_base = output_base
        self._use_flat_output = use_flat_output
        self._cancelled = False

    def cancel(self) -> None:
        """标记取消，终止后续图片处理。"""
        self._cancelled = True

    def run(self) -> None:
        """执行批量去水印任务：逐个读取图片 → 去水印 → 保存结果。"""
        total = len(self._tasks)
        done = 0
        skipped = 0
        failed = 0

        for img_path, regions in self._tasks:
            if self._cancelled:
                break
            done += 1

            if not regions:
                skipped += 1
                self.file_skipped.emit(Path(img_path).name)
                self.progress.emit(done, total)
                continue

            try:
                img = imread(img_path)
                if img is None:
                    raise ValueError("imread 返回 None")

                result = apply_watermark_removal(img, regions, self._mode, self._params)

                out_path = self._compute_output_path(
                    img_path, self._output_base, self._use_flat_output
                )
                Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                if not imwrite(out_path, result):
                    raise ValueError(f"写入失败: {out_path}")

                del img, result
                self.file_done.emit(Path(img_path).name)
            except Exception as e:
                failed += 1
                self.file_error.emit(Path(img_path).name, str(e))

            self.progress.emit(done, total)

        self.finished.emit()

    @staticmethod
    def _compute_output_path(src_path: str, output_base: str, use_flat: bool) -> str:
        """计算输出路径：flat 模式直接输出到 base 目录，否则按源目录结构组织。"""
        if not output_base or not output_base.strip():
            output_base = "nowm"
        src = Path(src_path)
        base = Path(output_base)

        if not base.is_absolute():
            if use_flat:
                out_dir = base
            else:
                out_dir = src.parent / base
        else:
            if use_flat:
                out_dir = base
            else:
                parent_name = src.parent.name
                out_dir = base / parent_name

        return str(out_dir / src.name)
