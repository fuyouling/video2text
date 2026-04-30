"""转写器"""

import os
import sys
from pathlib import Path
from typing import List, Optional, Dict, Any, Callable
from dataclasses import dataclass
from src.utils.exceptions import TranscriptionError
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 禁用 Hugging Face Hub 的警告
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_OFFLINE"] = "0"


def _logprob_to_confidence(avg_logprob: float) -> float:
    """将 avg_logprob（负值）转换为 0~100% 的置信度。"""
    import math

    return round(max(0.0, min(100.0, math.exp(avg_logprob) * 100)), 2)


_CORE_FILES = [
    "model.bin",
    "config.json",
    "tokenizer.json",
    "preprocessor_config.json",
    "vocabulary.json",
]


@dataclass
class TranscriptSegment:
    """转写段数据结构"""

    start: float
    end: float
    text: str
    confidence: float
    language: str


class Transcriber:
    """转写器"""

    def __init__(
        self,
        model_path: str,
        device: str = "auto",
        compute_type: str = "float16",
        num_workers: int = 1,
        download_root: Optional[str] = None,
    ):
        """初始化转写器

        Args:
            model_path: 模型路径或名称（如 "large-v3" 或 "models/large-v3"）
            device: 设备类型 (auto, cpu, cuda)
            compute_type: 计算类型 (float16, int8, float32, int8_float16)
            num_workers: 工作线程数
            download_root: 模型下载根目录
        """
        self.model_path = self._resolve_model_path(model_path, download_root)
        self.device = device
        self.compute_type = compute_type
        self.num_workers = num_workers
        self.download_root = download_root
        self.model = None
        self._loaded = False

    def _resolve_model_path(
        self, model_path: str, download_root: Optional[str] = None
    ) -> str:
        """解析模型路径

        Args:
            model_path: 模型路径或名称
            download_root: 模型下载根目录

        Returns:
            解析后的模型路径
        """
        # 如果是绝对路径，直接使用
        if Path(model_path).is_absolute():
            resolved_path = Path(model_path)
        # 如果是相对路径，检查是否是本地目录
        elif Path(model_path).exists():
            resolved_path = Path(model_path).resolve()
        # 否则，检查 models 目录
        else:
            # 确定基目录（支持绿色版）
            if getattr(sys, "frozen", False):
                base_dir = Path(sys.executable).parent
            else:
                base_dir = Path(__file__).resolve().parent.parent.parent

            models_dir = Path(download_root) if download_root else base_dir / "models"
            resolved_path = models_dir / model_path

        if resolved_path.exists():
            logger.info(f"使用本地模型: {resolved_path}")
            missing_files = [f for f in _CORE_FILES if not (resolved_path / f).exists()]
            if missing_files:
                logger.warning("模型目录不完整，缺少核心文件: %s", missing_files)
            return str(resolved_path)
        else:
            logger.info("模型目录不存在，将尝试下载: %s", resolved_path)
            return str(resolved_path)

    def load_model(self, progress_callback: Optional[Callable] = None) -> None:
        """加载模型

        加载策略：
        1. 检查 _loaded 标志，已加载则直接返回（单例保护）
        2. 优先按用户配置的 device/compute_type 加载
        3. 若 CUDA OOM，自动降级 compute_type（float16→int8→float32）
        4. 若仍然失败，回退到 CPU + int8

        Args:
            progress_callback: 进度回调函数，接收 (downloaded_bytes, total_bytes) 参数
        """
        if self._loaded:
            logger.info("模型已加载")
            return

        model_path_obj = Path(self.model_path)
        has_core_files = all((model_path_obj / f).exists() for f in _CORE_FILES)
        if not has_core_files:
            logger.info("模型文件不完整，尝试下载: %s", self.model_path)
            try:
                from src.utils.model_downloader import ModelDownloader

                downloader = ModelDownloader()
                if not downloader.download_model(progress_callback):
                    raise TranscriptionError(
                        "模型下载失败，请检查网络连接或配置代理。\n"
                        "可在 config.ini 的 [network] 节设置 proxy。"
                    )
            except ImportError:
                logger.warning(
                    "model_downloader模块不可用，尝试使用faster-whisper自动下载"
                )

        try:
            from faster_whisper import WhisperModel

            logger.info(f"开始加载模型: {self.model_path}")
            logger.info(
                f"设备: {self.device}, 计算类型: {self.compute_type}, "
                f"工作线程: {self.num_workers}"
            )

            self.model = WhisperModel(
                self.model_path,
                device=self.device,
                compute_type=self.compute_type,
                num_workers=self.num_workers,
                download_root=self.download_root or "models",
                local_files_only=False,
            )

            self._loaded = True
            logger.info("模型加载成功")

        except ImportError:
            raise TranscriptionError(
                "faster_whisper未安装，请运行: pip install faster-whisper"
            )
        except RuntimeError as e:
            error_str = str(e).lower()
            is_oom = (
                "out of memory" in error_str
                or "cuda" in error_str
                and "memory" in error_str
                or "cublas" in error_str
                or "cudnn" in error_str
            )
            if is_oom:
                logger.warning("GPU 显存不足 (%s)，尝试降级加载...", e)
                self._load_model_fallback(progress_callback)
            else:
                raise TranscriptionError(f"模型加载失败: {e}")
        except Exception as e:
            error_str = str(e).lower()
            is_oom = "out of memory" in error_str or "oom" in error_str
            if is_oom:
                logger.warning("GPU 显存不足 (%s)，尝试降级加载...", e)
                self._load_model_fallback(progress_callback)
            else:
                raise TranscriptionError(f"模型加载失败: {e}")

    def _load_model_fallback(
        self, progress_callback: Optional[Callable] = None
    ) -> None:
        """OOM 回退加载策略。

        依次尝试：
        1. 当前 device + 降低 compute_type（float16→int8→float32）
        2. CPU + int8（最终兜底）
        """
        from faster_whisper import WhisperModel

        fallback_compute_types = ["int8", "float32", "int8_float16"]

        for ct in fallback_compute_types:
            if ct == self.compute_type:
                continue
            try:
                logger.info("尝试回退加载: device=%s, compute_type=%s", self.device, ct)
                self.model = WhisperModel(
                    self.model_path,
                    device=self.device,
                    compute_type=ct,
                    num_workers=self.num_workers,
                    download_root=self.download_root or "models",
                    local_files_only=False,
                )
                self.compute_type = ct
                self._loaded = True
                logger.info("回退加载成功: device=%s, compute_type=%s", self.device, ct)
                return
            except Exception as inner_e:
                logger.debug("回退 %s 失败: %s", ct, inner_e)
                continue

        if self.device != "cpu":
            for ct in ["int8", "float32"]:
                try:
                    logger.info("最终回退: device=cpu, compute_type=%s", ct)
                    self.model = WhisperModel(
                        self.model_path,
                        device="cpu",
                        compute_type=ct,
                        num_workers=self.num_workers,
                        download_root=self.download_root or "models",
                        local_files_only=False,
                    )
                    self.device = "cpu"
                    self.compute_type = ct
                    self._loaded = True
                    logger.info("CPU 回退加载成功: compute_type=%s", ct)
                    return
                except Exception as inner_e:
                    logger.debug("CPU 回退 %s 失败: %s", ct, inner_e)
                    continue

        raise TranscriptionError(
            "模型加载失败：GPU 显存不足且回退到 CPU 也失败。"
            "请关闭其他 GPU 程序，或使用更小的模型。"
        )

    def transcribe(
        self,
        audio_path: str,
        language: str = "auto",
        beam_size: int = 5,
        best_of: int = 5,
        temperature: float = 0.0,
        vad_filter: bool = True,
        word_timestamps: bool = False,
        condition_on_previous_text: bool = True,
        progress_callback: Optional[Callable] = None,
    ) -> List[TranscriptSegment]:
        """转写音频

        Args:
            audio_path: 音频文件路径
            language: 语言代码 (auto表示自动检测)
            beam_size: beam search大小
            best_of: 采样数量
            temperature: 温度参数
            vad_filter: 是否使用VAD过滤
            word_timestamps: 是否生成词级时间戳
            condition_on_previous_text: 是否基于前文条件
            progress_callback: 进度回调函数，接收 (start, end, segment_count)

        Returns:
            转写段列表

        Raises:
            TranscriptionError: 转写失败
        """
        if not self._loaded:
            self.load_model()

        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise TranscriptionError(f"音频文件不存在: {audio_path}")

        logger.info(f"开始转写: {audio_path}")
        logger.info(
            f"语言: {language}, beam_size: {beam_size}, temperature: {temperature}"
        )

        try:
            segments, info = self.model.transcribe(
                audio_path,
                language=language if language != "auto" else None,
                beam_size=beam_size,
                best_of=best_of,
                temperature=temperature,
                vad_filter=vad_filter,
                word_timestamps=word_timestamps,
                condition_on_previous_text=condition_on_previous_text,
            )

            detected_language = info.language
            language_probability = info.language_probability

            logger.info(
                f"检测到语言: {detected_language} (置信度: {language_probability:.2f})"
            )

            transcript_segments = []

            for segment in segments:
                transcript_segment = TranscriptSegment(
                    start=segment.start,
                    end=segment.end,
                    text=segment.text.strip(),
                    confidence=_logprob_to_confidence(segment.avg_logprob),
                    language=detected_language,
                )
                transcript_segments.append(transcript_segment)

                if progress_callback:
                    progress_callback(
                        segment.start, segment.end, len(transcript_segments)
                    )

            logger.info(f"转写完成，共 {len(transcript_segments)} 个段落")
            return transcript_segments

        except Exception as e:
            raise TranscriptionError(f"转写失败: {e}")

    def unload_model(self) -> None:
        """卸载模型并释放 GPU 显存。"""
        if self.model is not None:
            del self.model
            self.model = None
            self._loaded = False
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
            logger.info("模型已卸载")

    def get_supported_languages(self) -> Dict[str, str]:
        """获取支持的语言列表

        Returns:
            语言代码到语言名称的映射
        """
        if not self._loaded:
            self.load_model()

        try:
            return self.model.supported_languages
        except Exception as e:
            logger.warning(f"获取支持语言失败: {e}")
            return {}

    def detect_language(self, audio_path: str) -> tuple:
        """检测音频语言

        Args:
            audio_path: 音频文件路径

        Returns:
            (语言代码, 置信度)
        """
        if not self._loaded:
            self.load_model()

        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise TranscriptionError(f"音频文件不存在: {audio_path}")

        try:
            segments, info = self.model.transcribe(
                audio_path, language=None, beam_size=5, vad_filter=True
            )

            detected_language = info.language
            language_probability = info.language_probability

            logger.info(
                f"检测到语言: {detected_language} (置信度: {language_probability:.2f})"
            )

            return detected_language, language_probability

        except Exception as e:
            raise TranscriptionError(f"语言检测失败: {e}")

    def get_model_info(self) -> Dict[str, Any]:
        """获取模型信息

        Returns:
            模型信息字典
        """
        if not self._loaded:
            self.load_model()

        return {
            "model_path": self.model_path,
            "device": self.device,
            "compute_type": self.compute_type,
            "num_workers": self.num_workers,
        }
