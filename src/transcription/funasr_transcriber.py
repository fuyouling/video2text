"""FunASR (Paraformer ONNX) 转写器

基于 ONNX Runtime 实现的 Paraformer-large + FSMN-VAD + CT-Transformer 标点恢复，
支持 CPU 多线程与 GPU (CUDA) 自动切换，零 PyTorch 依赖，适合便携式部署。
"""

import gc
import os
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

import numpy as np
import soundfile as sf

from src.i18n import t
from src.transcription.transcriber import TranscriptSegment
from src.utils.exceptions import TranscriptionCancelledError, TranscriptionError
from src.utils.logger import get_logger
from src.utils.paths import ensure_cuda_libs, get_base_dir as _get_base_dir

logger = get_logger(__name__)


class FunASRTranscriber:
    """FunASR ONNX 转写器 —— 封装 Paraformer-large + FSMN-VAD + CT-Transformer。

    提供与 Whisper Transcriber 一致的 load_model、transcribe、unload_model 接口。
    """

    def __init__(
        self,
        model_path: str = "paraformer-large-vad-punc",
        device: str = "auto",
        compute_type: str = "int8",
        num_workers: int = 4,
        download_root: Optional[str] = None,
        quantize: Optional[bool] = None,
        punc_filter: bool = True,
        vad_max_end_sil: int = 800,
    ):
        """初始化 FunASR 转写器

        Args:
            model_path: 模型路径或名称（默认 "paraformer-large-vad-punc"）
            device: 设备类型 (auto, cpu, cuda)
            compute_type: 计算类型 (int8, float32, float16)
            num_workers: 线程数 (默认 4)
            download_root: 模型下载根目录
            quantize: 是否使用 INT8 量化模型 (为 None 时依据 compute_type == 'int8' 自动决定)
            punc_filter: 是否启用标点符号恢复 (默认 True)
            vad_max_end_sil: VAD 尾部最大静音毫秒数 (默认 800)
        """
        self.download_root = self._normalize_download_root(download_root)
        self.model_path = self._resolve_model_path(model_path)
        self.device = device
        self.compute_type = compute_type
        self.num_workers = max(1, num_workers)
        if quantize is not None:
            self.quantize = quantize
        else:
            self.quantize = (compute_type == "int8")
        if self.quantize:
            self.compute_type = "int8"
        self.punc_filter = punc_filter
        self.vad_max_end_sil = max(100, int(vad_max_end_sil))

        self.asr_model = None
        self.vad_model = None
        self.punc_model = None
        self._loaded = False
        self._model_lock = threading.Lock()
        self.confirm_download_callback = None

    @staticmethod
    def _normalize_download_root(download_root: Optional[str] = None) -> str:
        if download_root:
            return str(Path(download_root).resolve())
        return str(_get_base_dir() / "models")

    def _resolve_model_path(self, model_path: str) -> str:
        """解析 FunASR 模型根路径"""
        p = Path(model_path)
        if p.is_absolute():
            resolved = p
        elif p.exists():
            resolved = p.resolve()
        else:
            resolved = Path(self.download_root) / model_path

        return str(resolved)

    def _check_and_download_models(self) -> None:
        """确保模型文件存在，缺失时自动触发下载"""
        from src.utils.model_downloader import ModelDownloader

        downloader = ModelDownloader("paraformer-large-vad-punc")
        if not downloader.is_model_exists():
            logger.info(t("transcriber.model_not_exists_download", path=self.model_path))
            ok = downloader.download_model(
                confirm_callback=self.confirm_download_callback
            )
            if not ok or not downloader.is_model_exists():
                raise TranscriptionError(t("transcriber.model_download_failed"))

    def _resolve_device_id(self) -> Union[int, str]:
        """决定 ONNX Runtime 的 device_id (0: GPU, -1: CPU)"""
        if self.device == "cpu":
            return "-1"

        try:
            ensure_cuda_libs()
            import onnxruntime as ort

            providers = ort.get_available_providers()
            if "CUDAExecutionProvider" in providers:
                logger.info("FunASR ONNX: CUDAExecutionProvider 可用，启用 GPU 推理")
                return 0
        except Exception as e:
            logger.debug("Check onnxruntime providers failed: %s", e)

        if self.device == "cuda":
            logger.warning("用户指定了 CUDA 但 CUDAExecutionProvider 不可用，回退至 CPU 推理")
        return "-1"

    def load_model(self, progress_callback: Optional[Callable] = None) -> None:
        """加载 FunASR ONNX 模型组件 (ASR, VAD, PUNC)"""
        with self._model_lock:
            if self._loaded:
                logger.info(t("transcriber.model_loaded"))
                return

            self._original_device = self.device
            self._original_compute_type = self.compute_type

            self._check_and_download_models()

            asr_dir = Path(self.model_path) / "asr"
            vad_dir = Path(self.model_path) / "vad"
            punc_dir = Path(self.model_path) / "punc"

            for d, name in [(asr_dir, "ASR"), (vad_dir, "VAD"), (punc_dir, "PUNC")]:
                if not d.exists():
                    raise TranscriptionError(f"FunASR {name} 模型目录不存在: {d}")

            if not self.quantize:
                asr_fp32 = asr_dir / "model.onnx"
                if not asr_fp32.exists() and (asr_dir / "model_quant.onnx").exists():
                    logger.warning("未检测到 FunASR FP32 模型文件 (%s)，自动回退至 INT8 量化模型", asr_fp32)
                    self.quantize = True
                    self.compute_type = "int8"

            device_id = self._resolve_device_id()
            threads = self.num_workers if self.num_workers > 1 else 4

            logger.info("正在加载 FunASR ONNX 模型 (device_id=%s, threads=%d, quantize=%s, punc=%s)...", device_id, threads, self.quantize, self.punc_filter)

            try:
                from funasr_onnx import CT_Transformer, Fsmn_vad, Paraformer

                # 优先尝试在目标设备加载
                self.asr_model = Paraformer(
                    model_dir=str(asr_dir),
                    device_id=device_id,
                    quantize=self.quantize,
                    intra_op_num_threads=threads,
                )
                self.vad_model = Fsmn_vad(
                    model_dir=str(vad_dir),
                    device_id=device_id,
                    quantize=self.quantize,
                    intra_op_num_threads=threads,
                    max_end_sil=self.vad_max_end_sil,
                )
                if self.punc_filter:
                    self.punc_model = CT_Transformer(
                        model_dir=str(punc_dir),
                        device_id=device_id,
                        quantize=self.quantize,
                        intra_op_num_threads=threads,
                    )
                else:
                    self.punc_model = None

                # 更新实际生效的设备信息
                session = getattr(getattr(self.asr_model, "ort_infer", None), "session", None)
                if session and hasattr(session, "get_providers"):
                    active_providers = session.get_providers()
                    if "CUDAExecutionProvider" in active_providers:
                        self.device = "cuda"
                    else:
                        self.device = "cpu"
                elif device_id != "-1":
                    self.device = "cuda"
                else:
                    self.device = "cpu"

            except Exception as e:
                if device_id != "-1":
                    logger.warning("FunASR GPU 加载失败 (%s)，平滑回退至 CPU 推理", e)
                    device_id = "-1"
                    self.device = "cpu"
                    from funasr_onnx import CT_Transformer, Fsmn_vad, Paraformer

                    self.asr_model = Paraformer(
                        model_dir=str(asr_dir),
                        device_id="-1",
                        quantize=self.quantize,
                        intra_op_num_threads=threads,
                    )
                    self.vad_model = Fsmn_vad(
                        model_dir=str(vad_dir),
                        device_id="-1",
                        quantize=self.quantize,
                        intra_op_num_threads=threads,
                        max_end_sil=self.vad_max_end_sil,
                    )
                    if self.punc_filter:
                        self.punc_model = CT_Transformer(
                            model_dir=str(punc_dir),
                            device_id="-1",
                            quantize=self.quantize,
                            intra_op_num_threads=threads,
                        )
                    else:
                        self.punc_model = None
                else:
                    raise TranscriptionError(f"FunASR 模型加载失败: {e}") from e

            self._loaded = True
            logger.info(t("transcriber.model_loaded"))

    def unload_model(self) -> None:
        """卸载模型并释放内存/显存"""
        with self._model_lock:
            self.asr_model = None
            self.vad_model = None
            self.punc_model = None
            self._loaded = False
            gc.collect()
            logger.info(t("transcriber.model_unloaded"))

    def transcribe(
        self,
        audio_path: str,
        language: str = "auto",
        hotwords: Optional[str] = None,
        vad_filter: bool = True,
        progress_callback: Optional[Callable] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        **kwargs,
    ) -> List[TranscriptSegment]:
        """使用 FunASR 进行语音识别并添加标点

        Args:
            audio_path: 音频文件路径 (推荐 16kHz WAV)
            language: 语言代码 (主要为 zh)
            hotwords: 热词字符串，空格或逗号分隔
            vad_filter: 是否启用 VAD 切分
            progress_callback: 进度回调 (start, end, segment_count, segment)
            cancel_check: 取消检查回调
            **kwargs: 兼容 Whisper 额外参数

        Returns:
            List[TranscriptSegment]
        """
        if not self._loaded:
            self.load_model()

        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise TranscriptionError(t("transcriber.audio_file_not_found", path=audio_path))

        logger.debug("FunASR 开始转写: %s", audio_file.name)

        try:
            # 读取音频数据为 float32 单声道 16kHz
            waveform, sample_rate = sf.read(str(audio_file), dtype="float32")
            if waveform.ndim > 1:
                waveform = np.mean(waveform, axis=1)

            if sample_rate != 16000:
                import librosa

                waveform = librosa.resample(waveform, orig_sr=sample_rate, target_sr=16000)
                sample_rate = 16000

        except Exception as e:
            raise TranscriptionError(f"读取音频数据失败: {e}") from e

        total_duration = len(waveform) / 16000.0
        if total_duration <= 0.05:
            return []

        segments: List[TranscriptSegment] = []

        with self._model_lock:
            if not self._loaded or self.asr_model is None:
                raise TranscriptionError(t("transcriber.model_not_loaded"))

            # 1. 语音活动检测 (VAD 切分)
            vad_times: List[List[int]] = []
            if vad_filter and self.vad_model is not None:
                try:
                    if hasattr(self.vad_model, "max_end_sil"):
                        self.vad_model.max_end_sil = self.vad_max_end_sil
                    raw_vad = self.vad_model(waveform)
                    if raw_vad and isinstance(raw_vad, list) and len(raw_vad) > 0:
                        vad_times = raw_vad[0]
                except Exception as e:
                    logger.warning("VAD 切片异常，回退至全音频转写: %s", e)

            # 若 VAD 未检测出分段或未启用 VAD，按整体处理
            if not vad_times:
                vad_times = [[0, int(total_duration * 1000)]]

            # 2. 逐段 ASR + PUNC 转写
            for idx, seg_time in enumerate(vad_times):
                if cancel_check and cancel_check():
                    raise TranscriptionCancelledError(t("transcriber.transcribe_cancelled"))

                beg_ms, end_ms = seg_time[0], seg_time[1]
                beg_sample = max(0, int(beg_ms * 16))
                end_sample = min(len(waveform), int(end_ms * 16))

                if end_sample - beg_sample < 160:  # < 10ms
                    continue

                sub_waveform = waveform[beg_sample:end_sample]

                try:
                    asr_res = self.asr_model(sub_waveform)
                except Exception as e:
                    logger.warning("Paraformer 片段推理异常 (beg=%d ms): %s", beg_ms, e)
                    continue

                if not asr_res or not isinstance(asr_res, list):
                    continue

                text = asr_res[0].get("preds", "").strip()
                if not text:
                    continue

                # 热词增强替换 (支持用户输入的专有名词偏置)
                if hotwords:
                    text = self._apply_hotwords_bias(text, hotwords)

                # 3. 标点符号恢复
                if self.punc_filter and self.punc_model is not None:
                    try:
                        punc_res, _ = self.punc_model(text)
                        if punc_res and isinstance(punc_res, str):
                            text = punc_res.strip()
                    except Exception as e:
                        logger.debug("CT-Transformer 标点恢复异常: %s", e)

                start_sec = round(beg_ms / 1000.0, 2)
                end_sec = round(end_ms / 1000.0, 2)

                segment = TranscriptSegment(
                    start=start_sec,
                    end=end_sec,
                    text=text,
                    confidence=95.0,
                    language="zh",
                )
                segments.append(segment)

                if progress_callback:
                    progress_callback(start_sec, end_sec, len(segments), segment)

        return segments

    @staticmethod
    def _apply_hotwords_bias(text: str, hotwords: str) -> str:
        """根据用户热词对识别文本进行关键词匹配校准与纠偏"""
        words = [w.strip() for w in hotwords.replace(",", " ").replace("，", " ").split() if w.strip()]
        for hw in words:
            # 过滤掉权重数字 (如 "达摩院 20")
            if hw.isdigit():
                continue
            # 若热词不区分大小写存在于文本中，规范化大小写
            if hw.lower() in text.lower():
                import re
                text = re.sub(re.escape(hw), hw, text, flags=re.IGNORECASE)
        return text
