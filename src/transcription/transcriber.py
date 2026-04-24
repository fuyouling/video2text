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

        # 验证模型路径
        if resolved_path.exists():
            logger.info(f"使用本地模型: {resolved_path}")
            core_files = [
                "model.bin",
                "config.json",
                "tokenizer.json",
                "preprocessor_config.json",
                "vocabulary.json",
            ]
            missing_files = [f for f in core_files if not (resolved_path / f).exists()]
            if missing_files:
                logger.warning("模型目录不完整，缺少核心文件: %s", missing_files)
            return str(resolved_path)
        else:
            logger.info("模型目录不存在，将尝试下载: %s", resolved_path)
            return str(resolved_path)

    def load_model(self, progress_callback: Optional[Callable] = None) -> None:
        """加载模型

        Args:
            progress_callback: 进度回调函数，接收 (downloaded_bytes, total_bytes) 参数
        """
        if self._loaded:
            logger.info("模型已加载")
            return

        # 检查模型是否存在，如果不存在则下载
        model_path_obj = Path(self.model_path)
        core_files = [
            "model.bin",
            "config.json",
            "tokenizer.json",
            "preprocessor_config.json",
            "vocabulary.json",
        ]
        has_core_files = all((model_path_obj / f).exists() for f in core_files)
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
        elif not model_path_obj.exists():
            logger.info("模型目录不存在但核心文件就绪，准备加载")

        try:
            from faster_whisper import WhisperModel

            logger.info(f"开始加载模型: {self.model_path}")
            logger.info(
                f"设备: {self.device}, 计算类型: {self.compute_type}, "
                f"工作线程: {self.num_workers}"
            )

            # 优化模型加载参数
            self.model = WhisperModel(
                self.model_path,
                device=self.device,
                compute_type=self.compute_type,
                num_workers=self.num_workers,
                download_root="models",  # 指定模型下载目录
                local_files_only=False,  # 允许在线下载（如果本地不存在）
            )

            self._loaded = True
            logger.info("模型加载成功")

        except ImportError:
            raise TranscriptionError(
                "faster_whisper未安装，请运行: pip install faster-whisper"
            )
        except Exception as e:
            raise TranscriptionError(f"模型加载失败: {e}")

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
                    confidence=segment.avg_logprob,
                    language=detected_language,
                )
                transcript_segments.append(transcript_segment)

            logger.info(f"转写完成，共 {len(transcript_segments)} 个段落")
            return transcript_segments

        except Exception as e:
            raise TranscriptionError(f"转写失败: {e}")

    def transcribe_with_progress(
        self,
        audio_path: str,
        language: str = "auto",
        beam_size: int = 5,
        best_of: int = 5,
        temperature: float = 0.0,
        vad_filter: bool = True,
        progress_callback=None,
    ) -> List[TranscriptSegment]:
        """带进度回调的转写

        Args:
            audio_path: 音频文件路径
            language: 语言代码
            beam_size: beam search大小
            best_of: 采样数量
            temperature: 温度参数
            vad_filter: 是否使用VAD过滤
            progress_callback: 进度回调函数

        Returns:
            转写段列表
        """
        if not self._loaded:
            self.load_model()

        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise TranscriptionError(f"音频文件不存在: {audio_path}")

        logger.info(f"开始转写（带进度）: {audio_path}")

        try:
            segments, info = self.model.transcribe(
                audio_path,
                language=language if language != "auto" else None,
                beam_size=beam_size,
                best_of=best_of,
                temperature=temperature,
                vad_filter=vad_filter,
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
                    confidence=segment.avg_logprob,
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
        """卸载模型"""
        if self.model is not None:
            del self.model
            self.model = None
            self._loaded = False
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
