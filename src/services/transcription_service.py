"""转写服务 —— 统一 CLI / GUI 的转写逻辑，支持断点续传"""

import json
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from src.preprocessing.video_processor import VideoProcessor
from src.storage.file_writer import FileWriter
from src.transcription.transcriber import TranscriptSegment, Transcriber
from src.utils.exceptions import TranscriptionError, Video2TextError, VideoFileError
from src.utils.logger import get_logger, log_step, log_error_with_context

logger = get_logger(__name__)

if sys.platform == "win32":
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    CREATE_NO_WINDOW = 0


@dataclass
class TranscribeResult:
    """单个视频的转写结果"""

    video_name: str
    segments: List[TranscriptSegment]
    output_paths: List[str]


class TranscriptionService:
    """转写服务 —— 统一 CLI / GUI 的转写逻辑

    主要职责：
    1. 验证视频 → 提取音频 → 切片（长视频）→ 转写 → 保存结果
    2. 支持断点续传：长音频切片转写时，将已完成的切片结果持久化到
       ``<output_dir>/.checkpoint/<video_name>_chunks.json``，
       重新运行时自动跳过已完成的切片。
    3. 每完成一个视频立即通过回调通知调用方（用于 GUI 实时刷新）。
    4. 支持暂停/继续：通过 pause()/resume() 控制，暂停期间阻塞当前转写切片。
    """

    def __init__(
        self,
        transcriber: Transcriber,
        video_processor: VideoProcessor,
        file_writer: FileWriter,
        *,
        language: str = "auto",
        beam_size: int = 5,
        temperature: float = 0.0,
        vad_filter: bool = True,
        max_chunk_duration: int = 300,
        output_formats: Optional[List[str]] = None,
        # 回调
        on_video_done: Optional[Callable[[TranscribeResult], None]] = None,
        on_video_error: Optional[Callable[[str, str], None]] = None,
        on_progress: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ):
        self.transcriber = transcriber
        self.video_processor = video_processor
        self.file_writer = file_writer

        self.language = language
        self.beam_size = beam_size
        self.temperature = temperature
        self.vad_filter = vad_filter
        self.max_chunk_duration = max_chunk_duration
        self.output_formats = output_formats or ["txt"]

        self.on_video_done = on_video_done
        self.on_video_error = on_video_error
        self.on_progress = on_progress
        self.cancel_check = cancel_check

        self._checkpoint_dir: Optional[Path] = None
        self._pause_event = threading.Event()
        self._pause_event.set()  # 初始状态：非暂停

    # ------------------------------------------------------------------
    # 暂停 / 继续
    # ------------------------------------------------------------------

    def pause(self) -> None:
        """暂停转写。当前切片完成后会阻塞，直到调用 resume()。"""
        self._pause_event.clear()
        logger.info("转写已暂停")

    def resume(self) -> None:
        """继续被暂停的转写。"""
        self._pause_event.set()
        logger.info("转写已继续")

    @property
    def is_paused(self) -> bool:
        """是否处于暂停状态。"""
        return not self._pause_event.is_set()

    def _wait_if_paused(self) -> None:
        """如果处于暂停状态，则阻塞直到恢复。"""
        self._pause_event.wait()

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def run(self, video_files: List[str], output_dir: str) -> List[TranscribeResult]:
        """批量转写多个视频，每完成一个视频立即回调 & 保存结果。

        Args:
            video_files: 视频文件路径列表
            output_dir: 输出目录

        Returns:
            所有视频的转写结果列表
        """
        self._checkpoint_dir = Path(output_dir) / ".checkpoint"
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_stale_checkpoints(video_files)

        results: List[TranscribeResult] = []
        total = len(video_files)

        for idx, video_path in enumerate(video_files):
            if self.cancel_check and self.cancel_check():
                logger.info("用户取消转写")
                break

            self._wait_if_paused()
            if self.cancel_check and self.cancel_check():
                logger.info("用户取消转写")
                break

            video_name = Path(video_path).stem
            self._log(f"[{idx + 1}/{total}] 开始转写: {video_name}")

            try:
                result = self._transcribe_single(video_path, output_dir)
                results.append(result)

                if self.on_video_done:
                    self.on_video_done(result)

                self._log(
                    f"[{idx + 1}/{total}] 转写完成: {video_name} ({len(result.segments)} 段)"
                )

            except Video2TextError as e:
                logger.error("转写失败 %s: %s", video_path, e)
                self._log(f"[{idx + 1}/{total}] 转写失败: {video_name} - {e}")
                if self.on_video_error:
                    self.on_video_error(video_name, str(e))
            except Exception as e:
                logger.exception("未知错误 %s", video_path)
                self._log(f"[{idx + 1}/{total}] 未知错误: {video_name}")
                if self.on_video_error:
                    self.on_video_error(video_name, str(e))

        return results

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _transcribe_single(self, video_path: str, output_dir: str) -> TranscribeResult:
        """转写单个视频。"""
        video_name = Path(video_path).stem
        temp_audio = Path(output_dir) / f"temp_{video_name}.wav"

        try:
            with log_step(f"视频校验 ({video_name})"):
                self.video_processor.validate_video(video_path)
                video_info = self.video_processor.get_video_info(video_path)
                self._log(f"视频时长: {video_info.duration:.2f} 秒")

            with log_step(f"音频提取 ({video_name})"):
                self.video_processor.extract_audio(
                    video_path,
                    str(temp_audio),
                    sample_rate=16000,
                    channels=1,
                )

            if video_info.duration > self.max_chunk_duration:
                with log_step(f"长视频切片转写 ({video_name})"):
                    segments = self._transcribe_chunked(
                        temp_audio, video_name, output_dir
                    )
            else:
                with log_step(f"语音转写 ({video_name})"):
                    segments = self.transcriber.transcribe(
                        str(temp_audio),
                        language=self.language,
                        beam_size=self.beam_size,
                        temperature=self.temperature,
                        vad_filter=self.vad_filter,
                    )

            output_paths = []
            for fmt in self.output_formats:
                with log_step(f"保存 {fmt.upper()} ({video_name})"):
                    p = self.file_writer.write_transcript(
                        segments, video_name, format=fmt
                    )
                    output_paths.append(p)

            return TranscribeResult(
                video_name=video_name,
                segments=segments,
                output_paths=output_paths,
            )

        except Exception as e:
            log_error_with_context(__name__, "转写流程", e, video_path)
            raise
        finally:
            temp_audio.unlink(missing_ok=True)

    def _transcribe_chunked(
        self,
        audio_path: Path,
        video_name: str,
        output_dir: str,
    ) -> List[TranscriptSegment]:
        """长音频切片转写，支持断点续传。"""
        checkpoint_file = self._checkpoint_dir / f"{video_name}_chunks.json"

        chunk_dir = Path(tempfile.mkdtemp(prefix="audio_chunks_", dir=output_dir))
        try:
            split_cmd = [
                self.video_processor.ffmpeg_path,
                "-i",
                str(audio_path),
                "-f",
                "segment",
                "-segment_time",
                str(self.max_chunk_duration),
                "-acodec",
                "pcm_s16le",
                "-reset_timestamps",
                "1",
                str(chunk_dir / "chunk_%03d.wav"),
            ]
            subprocess.run(
                split_cmd,
                capture_output=True,
                text=True,
                check=True,
                creationflags=CREATE_NO_WINDOW,
                encoding="utf-8",
                errors="ignore",
            )
            chunk_files = sorted(chunk_dir.glob("chunk_*.wav"))
            total_chunks = len(chunk_files)

            # 加载已完成的切片结果（断点续传）
            done_chunks: dict = {}
            if checkpoint_file.exists():
                try:
                    done_chunks = json.loads(
                        checkpoint_file.read_text(encoding="utf-8")
                    )
                    logger.info(
                        "加载断点续传数据: %d/%d 个切片已完成",
                        len(done_chunks),
                        total_chunks,
                    )
                except (json.JSONDecodeError, OSError):
                    done_chunks = {}

            all_segments: List[TranscriptSegment] = []
            cumulative_offset = 0.0

            for idx, chunk_path in enumerate(chunk_files):
                if self.cancel_check and self.cancel_check():
                    break

                self._wait_if_paused()
                if self.cancel_check and self.cancel_check():
                    break

                chunk_key = f"chunk_{idx:03d}"

                if chunk_key in done_chunks:
                    # 从断点恢复
                    cached = done_chunks[chunk_key]
                    for seg_data in cached["segments"]:
                        seg = TranscriptSegment(
                            start=seg_data["start"] + cumulative_offset,
                            end=seg_data["end"] + cumulative_offset,
                            text=seg_data["text"],
                            confidence=seg_data.get("confidence", 0.0),
                            language=seg_data.get("language", ""),
                        )
                        all_segments.append(seg)
                    cumulative_offset += cached["duration"]
                    self._log(f"跳过已完成切片 {idx + 1}/{total_chunks}")
                    continue

                self._log(f"转写切片 {idx + 1}/{total_chunks}")

                chunk_segments = self.transcriber.transcribe(
                    str(chunk_path),
                    language=self.language,
                    beam_size=self.beam_size,
                    temperature=self.temperature,
                    vad_filter=self.vad_filter,
                )

                # 计算切片实际时长（用 FFmpeg 获取更精确的值）
                chunk_duration = self._get_chunk_duration(chunk_path, chunk_segments)

                # 保存切片原始结果到断点文件
                done_chunks[chunk_key] = {
                    "duration": chunk_duration,
                    "segments": [
                        {
                            "start": seg.start,
                            "end": seg.end,
                            "text": seg.text,
                            "confidence": seg.confidence,
                            "language": seg.language,
                        }
                        for seg in chunk_segments
                    ],
                }
                checkpoint_file.write_text(
                    json.dumps(done_chunks, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                # 调整时间戳
                for seg in chunk_segments:
                    seg.start += cumulative_offset
                    seg.end += cumulative_offset
                all_segments.extend(chunk_segments)

                cumulative_offset += chunk_duration

            # 清理断点文件
            checkpoint_file.unlink(missing_ok=True)
            return all_segments

        finally:
            shutil.rmtree(chunk_dir, ignore_errors=True)

    def _get_chunk_duration(
        self, chunk_path: Path, segments: Optional[List] = None
    ) -> float:
        """通过 FFmpeg 精确获取音频切片时长（秒）。

        Args:
            chunk_path: 音频切片文件路径
            segments: 该切片的转写段列表，用于 FFmpeg 失败时的回退

        Returns:
            切片时长（秒）
        """
        cmd = [
            self.video_processor.ffmpeg_path,
            "-i",
            str(chunk_path),
            "-f",
            "null",
            "-",
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=CREATE_NO_WINDOW,
                encoding="utf-8",
                errors="ignore",
            )
            for line in result.stderr.split("\n"):
                if "Duration:" in line:
                    time_str = line.split("Duration:")[1].split(",")[0].strip()
                    h, m, s = time_str.split(":")
                    return float(h) * 3600 + float(m) * 60 + float(s)
        except Exception:
            logger.warning("无法获取切片时长，尝试使用转写段最大时间戳")

        if segments:
            max_end = max(seg.end for seg in segments)
            if max_end > 0:
                logger.info("使用转写段最大 end 值作为切片时长: %.2f", max_end)
                return max_end

        logger.error("无法确定切片时长，使用默认值 %.1f", self.max_chunk_duration)
        return float(self.max_chunk_duration)

    def _log(self, message: str):
        """记录日志并通过回调通知调用方。"""
        logger.info(message)
        if self.on_progress:
            self.on_progress(message)

    def _cleanup_stale_checkpoints(self, video_files: List[str]) -> None:
        """清理不属于当前批次的过期断点文件。"""
        if not self._checkpoint_dir or not self._checkpoint_dir.exists():
            return
        current_stems = {Path(v).stem for v in video_files}
        for cp_file in self._checkpoint_dir.glob("*_chunks.json"):
            stem = cp_file.name.removesuffix("_chunks.json")
            if stem not in current_stems:
                try:
                    cp_file.unlink()
                    logger.info("清理过期断点文件: %s", cp_file.name)
                except OSError:
                    pass
