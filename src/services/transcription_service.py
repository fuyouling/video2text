"""转写服务 —— 统一 CLI / GUI 的转写逻辑，支持断点续传"""

import hashlib
import os
import shutil
import math
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from src.config.settings import Settings
from src.preprocessing.video_processor import VideoProcessor
from src.storage.file_writer import FileWriter
from src.transcription.transcriber import TranscriptSegment, Transcriber
from src.utils.exceptions import TranscriptionError, Video2TextError
from src.utils.json_utils import atomic_write_json, safe_read_json
from src.utils.logger import get_logger
from src.utils.subprocess_compat import CREATE_NO_WINDOW

logger = get_logger(__name__)


@dataclass
class TranscribeResult:
    """单个媒体的转写结果"""

    video_name: str
    segments: List[TranscriptSegment]
    output_paths: List[str]


class TranscriptionService:
    """转写服务 —— 统一 CLI / GUI 的转写逻辑

    主要职责：
    1. 验证媒体 → 提取音频 → 切片（长音频）→ 转写 → 保存结果
    2. 支持断点续传：长音频切片转写时，将已完成的切片结果持久化到
       ``<output_dir>/.checkpoint/<video_name>_chunks.json``，
       重新运行时自动跳过已完成的切片。
    3. 每完成一个文件立即通过回调通知调用方（用于 GUI 实时刷新）。
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
        best_of: int = 5,
        temperature: float = 0.0,
        condition_on_previous_text: bool = True,
        word_timestamps: bool = False,
        vad_filter: bool = True,
        max_chunk_duration: int = 300,
        output_formats: Optional[List[str]] = None,
        input_folder: Optional[str] = None,
        mirror_depth: int = 1,
        # 回调
        on_video_done: Optional[Callable[[TranscribeResult], None]] = None,
        on_video_error: Optional[Callable[[str, str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ):
        self.transcriber = transcriber
        self.video_processor = video_processor
        self.file_writer = file_writer

        self.language = language
        self.beam_size = beam_size
        self.best_of = best_of
        self.temperature = temperature
        self.condition_on_previous_text = condition_on_previous_text
        self.word_timestamps = word_timestamps
        self.vad_filter = vad_filter
        self.max_chunk_duration = max_chunk_duration
        self.output_formats = output_formats or ["txt"]
        self.input_folder = input_folder
        self.mirror_depth = mirror_depth

        self.on_video_done = on_video_done
        self.on_video_error = on_video_error
        self.cancel_check = cancel_check

        self._checkpoint_dir: Optional[Path] = None
        self._pause_event = threading.Event()
        self._pause_event.set()  # 初始状态：非暂停

        self._history_file = (
            Path(Settings().config_path).parent / "transcription_history.json"
        )

    # ------------------------------------------------------------------
    # 暂停 / 继续
    # ------------------------------------------------------------------

    def pause(self) -> None:
        """暂停转写。当前切片完成后会阻塞，直到调用 resume()。"""
        self._pause_event.clear()
        logger.info("  ├─ ⏸ 转写暂停请求已接收，等待当前任务完成…")

    def resume(self) -> None:
        """继续被暂停的转写。"""
        self._pause_event.set()

    @property
    def is_paused(self) -> bool:
        """是否处于暂停状态。"""
        return not self._pause_event.is_set()

    def _wait_if_paused(self) -> None:
        """如果处于暂停状态，则阻塞直到恢复。"""
        if self._pause_event.is_set():
            return
        logger.info("  ├─ ✅ 转写已暂停 — 等待恢复…")
        while not self._pause_event.wait(timeout=0.5):
            if self.cancel_check and self.cancel_check():
                break
        if not (self.cancel_check and self.cancel_check()):
            logger.info("  └─ ▶ 转写已继续")

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def run(self, video_files: List[str], output_dir: str) -> List[TranscribeResult]:
        """批量转写多个文件，每完成一个文件立即回调 & 保存结果。

        Args:
            video_files: 文件路径列表
            output_dir: 输出目录

        Returns:
            所有文件的转写结果列表
        """
        self._checkpoint_dir = Path(output_dir) / ".checkpoint"
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_stale_checkpoints(video_files)

        results: List[TranscribeResult] = []
        total = len(video_files)

        for idx, video_path in enumerate(video_files):
            if self.cancel_check and self.cancel_check():
                logger.info("  └─ 用户取消转写")
                break

            self._wait_if_paused()
            if self.cancel_check and self.cancel_check():
                logger.info("  └─ 用户取消转写")
                break

            video_name = Path(video_path).stem
            logger.info(f"[{idx + 1}/{total}] {video_name}")

            try:
                file_output_dir = self.get_file_output_dir(
                    video_path, output_dir, self.input_folder, self.mirror_depth
                )
                result = self._transcribe_single(video_path, file_output_dir)
                results.append(result)

                if self.on_video_done:
                    self.on_video_done(result)

            except Video2TextError as e:
                logger.info("  └─ 转写失败: %s", e)
                if self.on_video_error:
                    self.on_video_error(video_name, str(e))
            except Exception as e:
                logger.exception("  └─ 未知错误: %s", e)
                if self.on_video_error:
                    self.on_video_error(video_name, str(e))

        return results

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _transcribe_single(self, video_path: str, output_dir: str) -> TranscribeResult:
        """转写单个文件。"""
        video_name = Path(video_path).stem
        temp_audio = Path(output_dir) / f"temp_{video_name}.wav"

        try:
            t0 = time.monotonic()
            self.video_processor.validate_media(video_path)
            video_info = self.video_processor.get_video_info(video_path)
            self.video_processor.extract_audio(
                video_path,
                str(temp_audio),
                sample_rate=16000,
                channels=1,
                video_info=video_info,
            )
            elapsed = time.monotonic() - t0
            logger.info("  ├─ 音频提取 ✓ (%.1fs)", elapsed)

            duration_str = self._format_duration(video_info.duration)
            est = self._estimate_transcribe_time(video_info.duration)
            est_str = self._format_duration(est) if est is not None else "-"
            logger.info("  ├─ 音频时长: %s | 预估转写时间: %s", duration_str, est_str)

            t0 = time.monotonic()
            last_progress_ts = [t0]

            def _on_progress(start: float, end: float, count: int) -> None:
                now = time.monotonic()
                if now - last_progress_ts[0] >= 30:
                    last_progress_ts[0] = now
                    elapsed = now - t0
                    logger.info("  ├─ 语音转写 … (%d 段, %.0fs)", count, elapsed)

            if video_info.duration > self.max_chunk_duration:
                segments = self._transcribe_chunked(
                    temp_audio, video_name, video_path, output_dir
                )
            else:
                segments = self.transcriber.transcribe(
                    str(temp_audio),
                    language=self.language,
                    beam_size=self.beam_size,
                    best_of=self.best_of,
                    temperature=self.temperature,
                    vad_filter=self.vad_filter,
                    word_timestamps=self.word_timestamps,
                    condition_on_previous_text=self.condition_on_previous_text,
                    progress_callback=_on_progress,
                )
            elapsed = time.monotonic() - t0
            lang = segments[0].language if segments else self.language
            logger.info(
                "  ├─ 语音转写 ✓ (%d 段, 语言: %s, 耗时 %s)",
                len(segments), lang, self._format_duration(elapsed),
            )
            self._save_history_record(video_info.duration, elapsed)

            output_paths = []
            file_fw = FileWriter(output_dir) if self.input_folder else self.file_writer
            for fmt in self.output_formats:
                p = file_fw.write_transcript(segments, video_name, fmt=fmt)
                output_paths.append(p)
            formats = ", ".join(f".{fmt}" for fmt in self.output_formats)
            logger.info("  └─ 保存完成 ✓ (%s)", formats)

            return TranscribeResult(
                video_name=video_name,
                segments=segments,
                output_paths=output_paths,
            )

        except Exception as e:
            # log_error_with_context(__name__, "转写流程", e, video_path)
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
        path_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()[:12]
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

            logger.info(
                "  ├─ 音频切片: %d 个切片 (每段 ≤ %s)",
                total_chunks, self._format_duration(self.max_chunk_duration),
            )

            # 加载已完成的切片结果（断点续传）
            done_chunks: dict = {}
            if checkpoint_file.exists():
                loaded = safe_read_json(checkpoint_file)
                if isinstance(loaded, dict):
                    done_chunks = loaded
                else:
                    if loaded is not None:
                        logger.info("  ├─ ⚠ 断点文件格式异常，忽略: %s", checkpoint_file.name)
                    done_chunks = {}
                n_done = (
                    len(done_chunks)
                    - sum(1 for v in done_chunks.values() if "error" in v)
                )
                n_fail = sum(1 for v in done_chunks.values() if "error" in v)
                logger.info("  ├─ 断点续传: %d/%d 已完成, %d 待重试", n_done, total_chunks, n_fail)

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
                        logger.info("  ├─ ⚠ 断点数据异常，跳过 %s", chunk_key)
                        del done_chunks[chunk_key]
                    elif "error" in cached:
                        logger.info("  ├─ 切片 %d/%d 重试中…", idx + 1, total_chunks)
                        del done_chunks[chunk_key]
                    elif "segments" not in cached or "duration" not in cached:
                        logger.info("  ├─ ⚠ 断点数据不完整，重新转写 %s", chunk_key)
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
                        logger.info("  ├─ 切片 %d/%d ✓ 跳过", idx + 1, total_chunks)
                        continue

                logger.info("  ├─ 切片 %d/%d 转写中 …", idx + 1, total_chunks)

                chunk_t0 = time.monotonic()
                try:
                    chunk_segments = self.transcriber.transcribe(
                        str(chunk_path),
                        language=self.language,
                        beam_size=self.beam_size,
                        best_of=self.best_of,
                        temperature=self.temperature,
                        vad_filter=self.vad_filter,
                        word_timestamps=self.word_timestamps,
                        condition_on_previous_text=self.condition_on_previous_text,
                    )
                except Exception as chunk_err:
                    logger.warning(
                        "  ├─ 切片 %d/%d ✗ 转写失败: %s", idx + 1, total_chunks, chunk_err,
                    )
                    failed_duration = self._get_chunk_duration(chunk_path)
                    done_chunks[chunk_key] = {
                        "duration": failed_duration,
                        "segments": [],
                        "error": str(chunk_err),
                    }
                    self._write_checkpoint(checkpoint_file, done_chunks)
                    cumulative_offset += failed_duration
                    continue

                chunk_elapsed = time.monotonic() - chunk_t0
                logger.info(
                    "  ├─ 切片 %d/%d ✓ (%.1fs)", idx + 1, total_chunks, chunk_elapsed,
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
                self._write_checkpoint(checkpoint_file, done_chunks)

                # 调整时间戳
                for seg in chunk_segments:
                    seg.start += cumulative_offset
                    seg.end += cumulative_offset
                all_segments.extend(chunk_segments)

                cumulative_offset += chunk_duration

            failed_chunks = [k for k, v in done_chunks.items() if "error" in v]
            if failed_chunks or cancelled:
                reason = f"{len(failed_chunks)} 个失败" + (", 已取消" if cancelled else "")
                logger.info("  ├─ ⚠ %d/%d 切片失败，断点已保留 (%s)", len(failed_chunks), total_chunks, reason)
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
        if segments is not None and not segments:
            return 0.0
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
            logger.info("  ├─ ⚠ ffprobe 失效，尝试备用估算")

        if segments:
            max_end = max(seg.end for seg in segments)
            if max_end > 0:
                logger.info("  ├─ 使用转写段末尾时间估算: %.1fs", max_end)
                return max_end

        try:
            file_size = chunk_path.stat().st_size
            wav_data_size = file_size - 44
            if wav_data_size > 0:
                byte_rate = 16000 * 1 * 2  # sample_rate * channels * sample_width
                duration_from_size = wav_data_size / byte_rate
                logger.info("  ├─ 使用文件大小估算: %.1fs", duration_from_size)
                return duration_from_size
        except OSError:
            pass

        logger.info("  ├─ ⚠ 切片时长估算失败，使用默认值 %.0fs", self.max_chunk_duration)
        return float(self.max_chunk_duration)


    @staticmethod
    def _write_checkpoint(checkpoint_file: Path, data: dict) -> None:
        """原子写入断点文件，防止崩溃导致数据损坏。"""
        atomic_write_json(checkpoint_file, data)

    def _cleanup_stale_checkpoints(self, video_files: List[str]) -> None:
        """清理不属于当前批次的过期断点文件。"""
        if not self._checkpoint_dir or not self._checkpoint_dir.exists():
            return
        current_stems = {Path(v).stem for v in video_files}
        for cp_file in self._checkpoint_dir.glob("*_chunks.json"):
            name_part = cp_file.name.removesuffix("_chunks.json")
            # 文件名格式: {video_name}_{hash}_chunks.json
            # 提取 video_name: 取最后一个 '_' 前的部分（hash 固定 12 字符）
            underscore_idx = name_part.rfind("_")
            if underscore_idx > 0:
                stem = name_part[:underscore_idx]
            else:
                stem = name_part
            if stem not in current_stems:
                try:
                    cp_file.unlink()
                    logger.info("  ├─ 清理过期断点: %s", cp_file.name)
                except OSError:
                    pass

    def _load_history(self) -> List[dict]:
        """加载转写历史记录（用于时间估算）。"""
        if not self._history_file.exists():
            return []
        data = safe_read_json(self._history_file)
        if not isinstance(data, dict):
            return []
        records = data.get("records", [])
        return records if isinstance(records, list) else []

    def _save_history_record(
        self, audio_duration: float, transcribe_time: float
    ) -> None:
        """保存一条转写历史记录（音频时长、耗时、模型信息），最多保留 1000 条。"""
        model = Path(self.transcriber.model_path).name
        device = self.transcriber.device
        compute_type = self.transcriber.compute_type

        record = {
            "audio_duration": round(audio_duration, 2),
            "transcribe_time": round(transcribe_time, 2),
            "model": model,
            "device": device,
            "compute_type": compute_type,
            "timestamp": time.time(),
        }

        records = self._load_history()
        records.append(record)
        if len(records) > 1000:
            records = records[-1000:]

        data = {"records": records}
        try:
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self._history_file, data)
        except Exception as e:
            logger.warning("  ├─ ⚠ 保存转写历史失败（不影响主流程）: %s", e)

    def _estimate_transcribe_time(self, audio_duration: float) -> Optional[float]:
        """基于历史记录估算转写耗时。

        使用同模型/设备的历史记录，计算加权中位数速度（时间衰减），
        样本不足 3 条时返回 None。
        """
        MIN_SAMPLE_THRESHOLD = 3
        HALF_LIFE_DAYS = 30

        model = Path(self.transcriber.model_path).name
        device = self.transcriber.device
        compute_type = self.transcriber.compute_type

        records = self._load_history()
        matched = [
            r
            for r in records
            if r.get("model") == model
            and r.get("device") == device
            and r.get("compute_type") == compute_type
            and r.get("audio_duration", 0) > 0
            and r.get("transcribe_time", 0) > 0
        ]
        if len(matched) < MIN_SAMPLE_THRESHOLD:
            return None

        now = time.time()
        half_life_seconds = HALF_LIFE_DAYS * 86400
        decay_factor = math.log(2) / half_life_seconds

        weighted_speeds = []
        for r in matched:
            speed = r["transcribe_time"] / r["audio_duration"]
            ts = r.get("timestamp", 0)
            if ts > 0:
                age = max(0, now - ts)
                weight = math.exp(-decay_factor * age)
            else:
                weight = 0.5
            weighted_speeds.append((speed, weight))

        weighted_speeds.sort(key=lambda x: x[0])
        total_weight = sum(w for _, w in weighted_speeds)
        cumulative = 0.0
        median_speed = weighted_speeds[0][0]
        for speed, weight in weighted_speeds:
            cumulative += weight
            if cumulative >= total_weight / 2:
                median_speed = speed
                break

        return audio_duration * median_speed

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """将秒数格式化为 'X时X分X秒'、'X分X秒' 或 'X秒' 的中文描述。"""
        total = int(seconds)
        if total < 60:
            return f"{total}秒"
        m, s = divmod(total, 60)
        if m < 60:
            return f"{m}分{s}秒"
        h, m = divmod(m, 60)
        return f"{h}时{m}分{s}秒"

    @staticmethod
    def get_file_output_dir(
        video_path: str,
        base_output_dir: str,
        input_folder: Optional[str],
        mirror_depth: int = 1,
    ) -> str:
        if not input_folder:
            return base_output_dir
        try:
            rel = Path(video_path).relative_to(input_folder)
            parts = rel.parent.parts
            if not parts:
                return base_output_dir
            truncated = Path(*parts[:mirror_depth])
            return str(Path(base_output_dir) / truncated)
        except ValueError:
            return base_output_dir
