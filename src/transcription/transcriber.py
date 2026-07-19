"""转写器"""

import os
import sys
import threading
from collections import OrderedDict
from pathlib import Path
from typing import List, Optional, Dict, Any, Callable, Tuple
from dataclasses import dataclass
from src.utils.exceptions import TranscriptionError
from src.utils.logger import get_logger
from src.utils.paths import get_base_dir as _get_base_dir

logger = get_logger(__name__)

# 加载模型前需要预先确认可用的 CUDA/cuDNN 依赖，缺失会导致
# faster-whisper 在转写时挂起（不抛异常）而非快速失败。
try:
    from src.utils.dll_downloader import DLL_REQUIRED_FILES
except Exception:  # pragma: no cover - 极端导入异常兜底
    DLL_REQUIRED_FILES = []

# 禁用 Hugging Face Hub 的警告
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"

_MAX_MODEL_CACHE = 2
_model_cache: OrderedDict[str, "Transcriber"] = OrderedDict()
_model_cache_lock = threading.Lock()


def get_cached_transcriber(
    model_path: str,
    device: str = "auto",
    compute_type: str = "float16",
    num_workers: int = 1,
    download_root: Optional[str] = None,
) -> "Transcriber":
    """获取缓存的 Transcriber 实例，避免重复加载模型。

    相同 (model_path, device, compute_type, num_workers) 组合只创建一次实例。
    缓存最多保留 _MAX_MODEL_CACHE 个实例，超出时淘汰最久未使用的条目。
    """
    cache_key = f"{model_path}|{device}|{compute_type}|{num_workers}"
    with _model_cache_lock:
        if cache_key in _model_cache:
            cached = _model_cache[cache_key]
            if cached._loaded:
                _model_cache.move_to_end(cache_key)
                logger.info("Transcriber: ✓ 复用缓存")
                return cached
            else:
                del _model_cache[cache_key]

        while len(_model_cache) >= _MAX_MODEL_CACHE:
            evicted_key, evicted = _model_cache.popitem(last=False)
            if evicted._loaded:
                evicted.unload_model()
            logger.info("Transcriber: 淘汰旧缓存 (%s)", evicted_key)

        transcriber = Transcriber(
            model_path=model_path,
            device=device,
            compute_type=compute_type,
            num_workers=num_workers,
            download_root=download_root,
        )
        _model_cache[cache_key] = transcriber
    return transcriber


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
    """语音转写器 —— 封装 faster_whisper.WhisperModel，提供线程安全的模型加载与转写。

    支持自动下载模型、OOM 自动降级（float16→int8→float32→CPU）、模型卸载与 GPU 显存释放。
    """

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
        self.download_root = self._normalize_download_root(download_root)
        self.model_path = self._resolve_model_path(model_path)
        self.device = device
        self.compute_type = compute_type
        self.num_workers = num_workers
        self.model = None
        self._loaded = False
        self._model_lock = threading.Lock()
        self.confirm_download_callback = None

    @staticmethod
    def _normalize_download_root(download_root: Optional[str] = None) -> str:
        """统一 download_root 路径，保证 resolve 与 load 阶段一致。"""
        if download_root:
            return str(Path(download_root).resolve())
        return str(_get_base_dir() / "models")

    def _resolve_model_path(self, model_path: str) -> str:
        """解析模型路径

        Args:
            model_path: 模型路径或名称

        Returns:
            解析后的模型路径
        """
        # 如果是绝对路径，直接使用
        if Path(model_path).is_absolute():
            resolved_path = Path(model_path)
        # 如果是相对路径，检查是否是本地目录
        elif Path(model_path).exists():
            resolved_path = Path(model_path).resolve()
        # 否则，检查 download_root 目录
        else:
            resolved_path = Path(self.download_root) / model_path

        if resolved_path.exists():
            logger.debug("使用本地模型: %s", resolved_path)
            missing_files = [f for f in _CORE_FILES if not (resolved_path / f).exists()]
            if missing_files:
                logger.warning("Transcriber: ⚠ 核心文件不完整 (%s)", missing_files)
            return str(resolved_path)
        else:
            logger.info("Transcriber: ⚠ 模型不存在，将下载 (%s)", resolved_path)
            return str(resolved_path)

    def load_model(self, progress_callback: Optional[Callable] = None) -> None:
        """加载模型

        加载策略：
        1. 检查 _loaded 标志，已加载则直接返回（线程安全）
        2. 优先按用户配置的 device/compute_type 加载
        3. 若 CUDA OOM，自动降级 compute_type（float16→int8→float32）
        4. 若仍然失败，回退到 CPU + int8

        Args:
            progress_callback: 进度回调函数，接收 (downloaded_bytes, total_bytes) 参数
        """
        with self._model_lock:
            if self._loaded:
                logger.info("Transcriber: ✓ 模型已加载")
                return

            self._do_load_model(progress_callback)

    def _is_cuda_requested(self) -> bool:
        """判断本次加载是否会使用 CUDA 设备。"""
        if self.device == "cuda":
            return True
        if self.device == "auto":
            return True
        return False

    def _check_cuda_dlls(self) -> None:
        """在加载 CUDA 模型前预先检查 cuBLAS/cuDNN 依赖文件是否存在。

        缺失时 faster-whisper 会在转写阶段（而非加载阶段）挂起且**不抛异常**，
        导致后台 worker 线程无法结束、GUI 转写按钮卡死。此处主动快速失败，
        让上层捕获错误后正常结束并恢复界面。

        注意：仅检查文件存在性，不实际加载 DLL。ctypes.CDLL 预加载会导致
        libs/ 的 cuBLAS/cuDNN 先于 PyTorch 自带的 DLL 进入进程内存，
        引发版本冲突（WinError 127），见 _do_load_model 中的二次验证。
        """
        if not self._is_cuda_requested():
            return
        if not DLL_REQUIRED_FILES:
            return

        from src.utils.dll_downloader import DllDownloader

        downloader = DllDownloader()
        if not downloader.is_dlls_complete():
            missing = [
                name
                for name in DLL_REQUIRED_FILES
                if not (downloader.libs_dir / name).exists()
                or (downloader.libs_dir / name).stat().st_size == 0
            ]
            raise TranscriptionError(
                "CUDA 依赖缺失（cuBLAS/cuDNN DLL 未找到）："
                + "、".join(missing)
                + "。无法启用 GPU 转写：请通过「设置」下载依赖，或在转写设置中"
                "将设备切换为 CPU。"
            )

    def _verify_dlls_loadable(self) -> None:
        """在 PyTorch 导入后尝试验证 libs/ 的 DLL 可加载性，仅警告不阻塞。

        此方法仅在 import faster_whisper（含 torch）之后调用。ctypes.CDLL 逐一
        加载 libs/ DLL 时可能因缺少 CUDA Runtime 驱动（cuda.dll）而失败，但
        这不一定意味着 ctranslate2 无法工作——它可能使用系统已安装的 CUDA
        Runtime 或 PyTorch 自带的 cuBLAS/cuDNN。因此仅记录警告，不阻止加载。
        """
        if not DLL_REQUIRED_FILES:
            return

        from src.utils.dll_downloader import DllDownloader

        downloader = DllDownloader()
        import ctypes

        bad_dlls: list[str] = []
        for name in DLL_REQUIRED_FILES:
            dll_path = downloader.libs_dir / name
            try:
                ctypes.CDLL(str(dll_path))
            except OSError:
                bad_dlls.append(name)

        if bad_dlls:
            logger.warning(
                "Transcriber: ⚠ libs/ DLL 预检失败 (%s)，"
                "ctranslate2 将尝试使用系统/PyTorch 自带的 CUDA 库。"
                "若后续转写失败，请通过「设置」重新下载依赖或切换为 CPU",
                "、".join(bad_dlls),
            )

    def _do_load_model(self, progress_callback: Optional[Callable] = None) -> None:
        """实际加载模型的内部实现（调用方需持有 _model_lock）。

        模型文件的完整性检查与下载统一在程序启动时的
        check_models_integrity 中完成一次，此处不再重复检测。
        """
        self._original_device = self.device
        self._original_compute_type = self.compute_type

        # 加载 CUDA 模型前先确认 cuBLAS/cuDNN 依赖，缺失会导致转写阶段挂起。
        if self.device != "cpu":
            self._check_cuda_dlls()

        try:
            from faster_whisper import WhisperModel

            # PyTorch 已在此处导入完成，其自带的 cuDNN/cuBLAS DLL 已安全加载。
            # 此时再验证 libs/ 的 DLL 可加载性，不会与 PyTorch 的 DLL 冲突。
            if self.device != "cpu":
                self._verify_dlls_loadable()

            logger.debug("开始加载模型")
            logger.debug(
                "设备: %s, 计算类型: %s, 工作线程: %d",
                self.device,
                self.compute_type,
                self.num_workers,
            )

            self.model = WhisperModel(
                self.model_path,
                device=self.device,
                compute_type=self.compute_type,
                num_workers=self.num_workers,
                download_root=self.download_root,
                local_files_only=True,
            )

            self._loaded = True
            logger.debug("模型加载成功")

        except ImportError:
            raise TranscriptionError(
                "faster_whisper未安装，请运行: pip install faster-whisper"
            )
        except RuntimeError as e:
            error_str = str(e).lower()
            is_oom = (
                "out of memory" in error_str
                or ("cuda" in error_str and "memory" in error_str)
                or "cublas" in error_str
                or "cudnn" in error_str
            )
            if is_oom:
                logger.warning("Transcriber: ⚠ 显存不足，尝试降级")
                self.model = None
                self._load_model_fallback()
            else:
                raise TranscriptionError(f"模型加载失败: {e}")
        except Exception as e:
            error_str = str(e).lower()
            is_oom = "out of memory" in error_str or "oom" in error_str
            if is_oom:
                logger.warning("Transcriber: ⚠ 显存不足，尝试降级")
                self.model = None
                self._load_model_fallback()
            else:
                raise TranscriptionError(f"模型加载失败: {e}")

    def _load_model_fallback(self) -> None:
        """OOM 回退加载策略。

        依次尝试：
        1. 当前 device + 降低 compute_type（float16→int8→float32）
        2. CPU + int8（最终兜底）

        每次失败后清理 GPU 显存，避免连续 OOM。
        """
        from faster_whisper import WhisperModel

        fallback_compute_types = ["int8", "float32", "int8_float16"]

        for ct in fallback_compute_types:
            if ct == self.compute_type:
                continue
            try:
                logger.info(
                    "Transcriber: 回退加载 device=%s, compute_type=%s", self.device, ct
                )
                self.model = WhisperModel(
                    self.model_path,
                    device=self.device,
                    compute_type=ct,
                    num_workers=self.num_workers,
                    download_root=self.download_root,
                    local_files_only=True,
                )
                self.compute_type = ct
                self._loaded = True
                logger.info(
                    "Transcriber: ✓ 回退成功 device=%s, compute_type=%s",
                    self.device,
                    ct,
                )
                return
            except Exception as inner_e:
                logger.debug("回退 %s 失败: %s", ct, inner_e)
                self.model = None
                continue

        if self.device != "cpu":
            for ct in ["int8", "float32"]:
                try:
                    logger.info("Transcriber: 最终回退 cpu, compute_type=%s", ct)
                    self.model = WhisperModel(
                        self.model_path,
                        device="cpu",
                        compute_type=ct,
                        num_workers=self.num_workers,
                        download_root=self.download_root,
                        local_files_only=True,
                    )
                    self.device = "cpu"
                    self.compute_type = ct
                    self._loaded = True
                    logger.info("Transcriber: ✓ CPU 回退成功 compute_type=%s", ct)
                    return
                except Exception as inner_e:
                    logger.debug("CPU 回退 %s 失败: %s", ct, inner_e)
                    self.model = None
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
        temperature=0.0,                       # 支持 float 或 list/tuple
        vad_filter: bool = True,
        vad_parameters: Optional[Dict[str, Any]] = None,
        word_timestamps: bool = False,
        condition_on_previous_text: bool = False,
        # ↓ 新增参数
        initial_prompt: Optional[str] = None,
        hotwords: Optional[str] = None,
        compression_ratio_threshold: float = 2.4,
        log_prob_threshold: float = -1.0,
        no_speech_threshold: float = 0.6,
        repetition_penalty: float = 1.0,
        no_repeat_ngram_size: int = 0,
        progress_callback: Optional[Callable] = None,
    ) -> List[TranscriptSegment]:
        """转写音频

        Args:
            audio_path: 音频文件路径
            language: 语言代码 (auto表示自动检测)
            beam_size: beam search大小
            best_of: 采样数量（仅当 temperature 含 >0 值时生效）
            temperature: 温度参数，支持 float 或 list/tuple，传入列表时在失败时逐级升温重采样
            vad_filter: 是否使用VAD过滤
            vad_parameters: VAD参数字典（threshold, min_silence_duration_ms 等）
            word_timestamps: 是否生成词级时间戳
            condition_on_previous_text: 是否基于前文条件
            initial_prompt: 领域提示词，留空不启用
            hotwords: 热词偏置，多个词空格分隔，留空不启用
            compression_ratio_threshold: 压缩比超阈值判重复并重采样
            log_prob_threshold: 平均对数概率低阈值判低置信并重采样
            no_speech_threshold: 无语音概率阈值
            repetition_penalty: 重复惩罚系数
            no_repeat_ngram_size: 禁止重复的 N-gram 大小
            progress_callback: 进度回调函数，接收 (start, end, segment_count)

        Returns:
            转写段列表

        Raises:
            TranscriptionError: 转写失败

        注意：本项目使用 WhisperModel.transcribe()（非 batched）。若改用
        BatchedInferencePipeline，以下参数将失效：compression_ratio_threshold、
        log_prob_threshold、no_speech_threshold、condition_on_previous_text、
        temperature 列表回退。
        """
        if not self._loaded:
            self.load_model()

        # 即使模型已加载，CUDA 依赖缺失也会在推理阶段挂起，故每次转写前都确认。
        if self.device != "cpu":
            self._check_cuda_dlls()

        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise TranscriptionError(f"音频文件不存在: {audio_path}")

        logger.debug("开始转写: %s", audio_file.name)
        logger.debug(
            "语言: %s, beam_size: %d, temperature: %s",
            language,
            beam_size,
            temperature,
        )

        try:
            with self._model_lock:
                model = self.model
                if model is None:
                    raise TranscriptionError("模型未加载或已被卸载")
            segments, info = model.transcribe(
                audio_path,
                language=language if language != "auto" else None,
                beam_size=beam_size,
                best_of=best_of,
                temperature=temperature,
                vad_filter=vad_filter,
                vad_parameters=vad_parameters,
                word_timestamps=word_timestamps,
                condition_on_previous_text=condition_on_previous_text,
                initial_prompt=initial_prompt or None,
                hotwords=hotwords or None,
                compression_ratio_threshold=compression_ratio_threshold,
                log_prob_threshold=log_prob_threshold,
                no_speech_threshold=no_speech_threshold,
                repetition_penalty=repetition_penalty,
                no_repeat_ngram_size=no_repeat_ngram_size,
            )

            detected_language = info.language
            language_probability = info.language_probability

            logger.debug(
                "检测到语言: %s (置信度: %.2f)", detected_language, language_probability
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

            logger.debug("转写完成，共 %d 个段落", len(transcript_segments))
            return transcript_segments

        except Exception as e:
            raise TranscriptionError(f"转写失败: {e}") from e

    def unload_model(self) -> None:
        """卸载模型并释放 GPU 显存，并恢复原始 device/compute_type。

        使用非阻塞锁获取：若模型正在加载中（锁被持有）则跳过卸载，
        避免主线程在窗口关闭时被阻塞（进程退出时操作系统会回收资源）。
        """
        if not self._model_lock.acquire(blocking=False):
            logger.warning("Transcriber: ⚠ 模型正在加载中，跳过卸载")
            return
        try:
            if self.model is not None:
                del self.model
                self.model = None
                self._loaded = False
                if hasattr(self, "_original_device"):
                    self.device = self._original_device
                if hasattr(self, "_original_compute_type"):
                    self.compute_type = self._original_compute_type
                logger.info("Transcriber: ✓ 模型已卸载")
        finally:
            self._model_lock.release()

    def get_supported_languages(self) -> Dict[str, str]:
        """获取支持的语言列表

        Returns:
            语言代码到语言名称的映射

        Raises:
            RuntimeError: 模型尚未加载
        """
        if not self._loaded:
            raise RuntimeError("模型尚未加载，请先调用 load_model()")

        try:
            with self._model_lock:
                model = self.model
                if model is None:
                    raise RuntimeError("模型未加载或已被卸载")
            return model.supported_languages
        except Exception as e:
            logger.warning("Transcriber: ✗ 获取语言失败 (%s)", e)
            return {}

    def detect_language(self, audio_path: str) -> Tuple[str, float]:
        """检测音频语言（使用 faster-whisper 专用方法，无需完整转写）

        Args:
            audio_path: 音频文件路径

        Returns:
            (语言代码, 置信度)

        Raises:
            TranscriptionError: 检测失败
        """
        if not self._loaded:
            self.load_model()

        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise TranscriptionError(f"音频文件不存在: {audio_path}")

        try:
            with self._model_lock:
                model = self.model
                if model is None:
                    raise TranscriptionError("模型未加载或已被卸载")
            language, probability = model.detect_language(audio_path)

            logger.info("Transcriber: 检测语言 %s (置信度 %.2f)", language, probability)

            return language, probability

        except Exception as e:
            raise TranscriptionError(f"语言检测失败: {e}")

    def get_model_info(self) -> Dict[str, Any]:
        """获取模型信息（不触发模型加载）

        Returns:
            模型信息字典，若模型未加载则 _loaded 字段为 False
        """
        info: Dict[str, Any] = {
            "model_path": self.model_path,
            "device": self.device,
            "compute_type": self.compute_type,
            "num_workers": self.num_workers,
            "download_root": self.download_root,
            "loaded": self._loaded,
        }
        return info
