"""转写服务 —— 统一 CLI / GUI 的转写逻辑，支持断点续传"""

import hashlib
import json
import os
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
from src.utils.exceptions import TranscriptionError, Video2TextError
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
                    video_info=video_info,
                )

            if video_info.duration > self.max_chunk_duration:
                with log_step(f"长视频切片转写 ({video_name})"):
                    segments = self._transcribe_chunked(
                        temp_audio, video_name, video_path, output_dir
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
        video_path: str,
        output_dir: str,
    ) -> List[TranscriptSegment]:
        """长音频切片转写，支持断点续传。"""
        hash_input = f"{video_path}:chunk={self.max_chunk_duration}"
        path_hash = hashlib.md5(hash_input.encode("utf-8")).hexdigest()[:8]
        checkpoint_file = self._checkpoint_dir / f"{video_name}_{path_hash}_chunks.json"

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
            try:
                subprocess.run(
                    split_cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                    creationflags=CREATE_NO_WINDOW,
                    encoding="utf-8",
                    errors="ignore",
                )
            except subprocess.CalledProcessError as e:
                raise TranscriptionError(
                    f"FFmpeg 音频切片失败: {e.stderr or e.stdout}"
                ) from e
            chunk_files = sorted(chunk_dir.glob("chunk_*.wav"))
            total_chunks = len(chunk_files)

            if total_chunks == 0:
                raise TranscriptionError(
                    f"FFmpeg 音频切片失败，未生成任何切片文件: {audio_path}"
                )

            # 加载已完成的切片结果（断点续传）
            done_chunks: dict = {}
            if checkpoint_file.exists():
                try:
                    loaded = json.loads(checkpoint_file.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        done_chunks = loaded
                    else:
                        logger.warning(
                            "断点文件格式异常（非 dict），忽略: %s", checkpoint_file
                        )
                        done_chunks = {}
                    logger.info(
                        "加载断点续传数据: %d/%d 个切片已完成 (%d 个失败待重试)",
                        len(done_chunks)
                        - sum(1 for v in done_chunks.values() if "error" in v),
                        total_chunks,
                        sum(1 for v in done_chunks.values() if "error" in v),
                    )
                except (json.JSONDecodeError, OSError):
                    done_chunks = {}

            all_segments: List[TranscriptSegment] = []
            cumulative_offset = 0.0
            cancelled = False

            for idx, chunk_path in enumerate(chunk_files):
                if self.cancel_check and self.cancel_check():
                    cancelled = True
                    break

                self._wait_if_paused()
                if self.cancel_check and self.cancel_check():
                    cancelled = True
                    break

                chunk_key = f"chunk_{idx:03d}"

                if chunk_key in done_chunks:
                    cached = done_chunks[chunk_key]
                    if not isinstance(cached, dict):
                        logger.warning("断点数据格式异常，跳过: %s", chunk_key)
                        del done_chunks[chunk_key]
                    elif "error" in cached:
                        logger.info("重试之前失败的切片 %d/%d", idx + 1, total_chunks)
                        del done_chunks[chunk_key]
                    elif "segments" not in cached or "duration" not in cached:
                        logger.warning("断点数据缺少字段，重新转写: %s", chunk_key)
                        del done_chunks[chunk_key]
                    else:
                        # 从断点恢复
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

                try:
                    chunk_segments = self.transcriber.transcribe(
                        str(chunk_path),
                        language=self.language,
                        beam_size=self.beam_size,
                        temperature=self.temperature,
                        vad_filter=self.vad_filter,
                    )
                except Exception as chunk_err:
                    logger.error(
                        "切片 %d/%d 转写失败: %s", idx + 1, total_chunks, chunk_err
                    )
                    self._log(f"切片 {idx + 1}/{total_chunks} 转写失败: {chunk_err}")
                    failed_duration = self._get_chunk_duration(chunk_path)
                    done_chunks[chunk_key] = {
                        "duration": failed_duration,
                        "segments": [],
                        "error": str(chunk_err),
                    }
                    self._write_checkpoint(checkpoint_file, done_chunks)
                    cumulative_offset += failed_duration
                    continue

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
                self._write_checkpoint(checkpoint_file, done_chunks)

                # 调整时间戳
                for seg in chunk_segments:
                    seg.start += cumulative_offset
                    seg.end += cumulative_offset
                all_segments.extend(chunk_segments)

                cumulative_offset += chunk_duration

            failed_chunks = [k for k, v in done_chunks.items() if "error" in v]
            if failed_chunks or cancelled:
                logger.warning(
                    "%d/%d 个切片转写失败，已保留断点文件供重试 (%s)",
                    len(failed_chunks),
                    total_chunks,
                    f"{len(failed_chunks)} 个失败" + (", 已取消" if cancelled else ""),
                )
            else:
                checkpoint_file.unlink(missing_ok=True)

            return all_segments

        finally:
            shutil.rmtree(chunk_dir, ignore_errors=True)

    def _get_chunk_duration(
        self, chunk_path: Path, segments: Optional[List] = None
    ) -> float:
        """通过 ffprobe 获取音频切片时长（秒）。

        Args:
            chunk_path: 音频切片文件路径
            segments: 该切片的转写段列表，用于回退

        Returns:
            切片时长（秒）
        """
        try:
            result = subprocess.run(
                [
                    self.video_processor.ffprobe_path,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "csv=p=0",
                    str(chunk_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=CREATE_NO_WINDOW,
                encoding="utf-8",
                errors="ignore",
            )
            duration_str = result.stdout.strip()
            if duration_str:
                return float(duration_str)
        except Exception:
            logger.warning("ffprobe 获取切片时长失败，尝试使用转写段最大时间戳")

        if segments:
            max_end = max(seg.end for seg in segments)
            if max_end > 0:
                logger.info("使用转写段最大 end 值作为切片时长: %.2f", max_end)
                return max_end

        try:
            file_size = chunk_path.stat().st_size
            wav_data_size = file_size - 44
            if wav_data_size > 0:
                byte_rate = 16000 * 1 * 2  # sample_rate * channels * sample_width
                duration_from_size = wav_data_size / byte_rate
                logger.info(
                    "使用文件大小估算切片时长: %.2f (文件 %d bytes)",
                    duration_from_size,
                    file_size,
                )
                return duration_from_size
        except OSError:
            pass

        logger.error("无法确定切片时长，使用默认值 %.1f", self.max_chunk_duration)
        return float(self.max_chunk_duration)

    def _log(self, message: str):
        """记录日志并通过回调通知调用方。"""
        logger.info(message)
        if self.on_progress:
            self.on_progress(message)

    @staticmethod
    def _write_checkpoint(checkpoint_file: Path, data: dict) -> None:
        """原子写入断点文件，防止崩溃导致数据损坏。"""
        fd, tmp_path = tempfile.mkstemp(dir=checkpoint_file.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, str(checkpoint_file))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _cleanup_stale_checkpoints(self, video_files: List[str]) -> None:
        """清理不属于当前批次的过期断点文件。"""
        if not self._checkpoint_dir or not self._checkpoint_dir.exists():
            return
        current_stems = {Path(v).stem for v in video_files}
        for cp_file in self._checkpoint_dir.glob("*_chunks.json"):
            name_part = cp_file.name.removesuffix("_chunks.json")
            # 文件名格式: {video_name}_{hash}_chunks.json
            # 提取 video_name: 取最后一个 '_' 前的部分（hash 固定 8 字符）
            underscore_idx = name_part.rfind("_")
            if underscore_idx > 0:
                stem = name_part[:underscore_idx]
            else:
                stem = name_part
            if stem not in current_stems:
                try:
                    cp_file.unlink()
                    logger.info("清理过期断点文件: %s", cp_file.name)
                except OSError:
                    pass
