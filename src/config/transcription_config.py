"""转写配置加载 —— 从 Settings 构建 TranscriptionConfig"""

from dataclasses import dataclass
from pathlib import Path

from src.config.settings import Settings

SUPPORTED_TRANSCRIPT_FORMATS = {"txt", "srt", "vtt", "json"}


@dataclass
class TranscriptionConfig:
    """转写配置数据类 —— 从 Settings 加载，供 TranscriptionService 使用。"""

    language: str
    model_path: str
    device: str
    compute_type: str
    beam_size: int
    best_of: int
    temperature: list[float]
    condition_on_previous_text: bool
    word_timestamps: bool
    max_chunk_duration: int
    output_formats: list[str]
    # 新增参数
    vad_filter: bool
    vad_parameters: dict
    initial_prompt: str
    hotwords: str
    compression_ratio_threshold: float
    log_prob_threshold: float
    no_speech_threshold: float
    repetition_penalty: float
    no_repeat_ngram_size: int
    engine: str = "faster-whisper"
    punc_filter: bool = True
    num_workers: int = 4
    quantize: bool = True
    vad_max_end_sil: int = 800



def _parse_temperature(settings: Settings, prefix: str = "transcription.whisper_temperature") -> list[float]:
    """读取 temperature，支持 '0.0,0.2,0.4' 或单值 '0.0'。"""
    raw = settings.get(prefix)
    if raw is None or raw == "":
        raw = settings.get("transcription.temperature", "0.0")
    try:
        vals = [float(x.strip()) for x in str(raw).split(",") if x.strip()]
    except ValueError:
        vals = [0.0]
    return vals or [0.0]


def _build_vad_parameters(settings: Settings, is_whisper: bool = True) -> dict:
    """构建 Silero VAD 参数字典。vad_max_speech_s=0 表示不限制。"""
    prefix = "transcription.whisper_vad_" if is_whisper else "transcription.vad_"
    fallback_prefix = "transcription.vad_"

    def _get_val_float(key: str, default: float) -> float:
        val = settings.get(f"{prefix}{key}")
        if val is not None and val != "":
            try:
                return float(val)
            except ValueError:
                pass
        return settings.get_float(f"{fallback_prefix}{key}", default)

    def _get_val_int(key: str, default: int) -> int:
        val = settings.get(f"{prefix}{key}")
        if val is not None and val != "":
            try:
                return int(val)
            except ValueError:
                pass
        return settings.get_int(f"{fallback_prefix}{key}", default)

    params = {
        "threshold": _get_val_float("threshold", 0.5),
        "min_silence_duration_ms": _get_val_int("min_silence_ms", 2000),
        "speech_pad_ms": _get_val_int("speech_pad_ms", 400),
    }
    max_speech = _get_val_int("max_speech_s", 0)
    if max_speech > 0:
        params["max_speech_duration_s"] = float(max_speech)
    return params


def _get_output_formats(settings: Settings) -> list[str]:
    """从配置中读取输出格式列表，过滤不支持的格式，至少返回 ['txt']。"""
    raw = settings.get_list("output.transcript_format", ["txt"])
    return [f.lower() for f in raw if f.lower() in SUPPORTED_TRANSCRIPT_FORMATS] or [
        "txt"
    ]


def _load_tx_config(settings: Settings) -> TranscriptionConfig:
    """从配置加载转写参数，支持 whisper_* 与 funasr_* 独立配置，并对通用字段平滑回退"""
    engine = settings.get("transcription.engine", "faster-whisper").lower().strip()
    is_funasr = engine in ("funasr", "paraformer")
    resolved_engine = "funasr" if is_funasr else "faster-whisper"

    models_dir = settings.get("paths.models_dir", "models")
    output_formats = _get_output_formats(settings)
    max_chunk_duration = settings.get_int("preprocessing.max_chunk_duration", 300)

    if is_funasr:
        model_name = (
            settings.get("transcription.funasr_model")
            or settings.get("transcription.model_path", "paraformer-large-vad-punc")
        )
        device = (
            settings.get("transcription.funasr_device")
            or settings.get("transcription.device", "auto")
        )
        language = (
            settings.get("transcription.funasr_language")
            or settings.get("transcription.language", "zh")
        )
        vad_val = settings.get("transcription.funasr_vad_filter")
        if vad_val is not None and vad_val != "":
            vad_filter = str(vad_val).lower() in ("true", "1", "yes")
        else:
            vad_filter = settings.get_bool("transcription.vad_filter", True)

        hotwords = settings.get("transcription.hotwords", "") or ""
        initial_prompt = ""

        # FunASR 计算精度与量化
        compute_type_val = (
            settings.get("transcription.funasr_compute_type")
            or settings.get("transcription.funasr_quantize")
        )
        if compute_type_val is not None and str(compute_type_val).lower() in ("true", "1", "yes"):
            compute_type = "int8"
        elif compute_type_val is not None and str(compute_type_val).lower() in ("false", "0", "no"):
            compute_type = "float32"
        elif compute_type_val:
            compute_type = str(compute_type_val).lower().strip()
        else:
            compute_type = "int8"

        quantize = (compute_type == "int8")

        beam_size = 1
        best_of = 1
        temperature = [0.0]
        condition_on_previous_text = False
        word_timestamps = False
        vad_parameters = None
        compression_ratio_threshold = 2.4
        log_prob_threshold = -1.0
        no_speech_threshold = 0.6
        repetition_penalty = 1.0
        no_repeat_ngram_size = 0

        punc_val = settings.get("transcription.funasr_punc_filter", "True")
        punc_filter = str(punc_val).lower() in ("true", "1", "yes")

        num_workers_val = settings.get("transcription.funasr_num_workers")
        if num_workers_val is not None and num_workers_val != "":
            try:
                num_workers = int(num_workers_val)
            except ValueError:
                num_workers = 4
        else:
            num_workers = settings.get_int("transcription.num_workers", 4)

        vad_sil_val = settings.get("transcription.funasr_vad_max_end_sil")
        if vad_sil_val is not None and vad_sil_val != "":
            try:
                vad_max_end_sil = int(vad_sil_val)
            except ValueError:
                vad_max_end_sil = 800
        else:
            vad_max_end_sil = 800
    else:
        model_name = (
            settings.get("transcription.whisper_model")
            or settings.get("transcription.model_path", "faster-whisper-large-v3-turbo-ct2")
        )
        device = (
            settings.get("transcription.whisper_device")
            or settings.get("transcription.device", "cuda")
        )
        language = (
            settings.get("transcription.whisper_language")
            or settings.get("transcription.language", "auto")
        )
        compute_type = (
            settings.get("transcription.whisper_compute_type")
            or settings.get("transcription.compute_type", "float16")
        )

        def _get_int_setting(k: str, default: int) -> int:
            val = settings.get(f"transcription.whisper_{k}")
            if val is not None and val != "":
                try:
                    return int(val)
                except ValueError:
                    pass
            return settings.get_int(f"transcription.{k}", default)

        def _get_float_setting(k: str, default: float) -> float:
            val = settings.get(f"transcription.whisper_{k}")
            if val is not None and val != "":
                try:
                    return float(val)
                except ValueError:
                    pass
            return settings.get_float(f"transcription.{k}", default)

        def _get_bool_setting(k: str, default: bool) -> bool:
            val = settings.get(f"transcription.whisper_{k}")
            if val is not None and val != "":
                return str(val).lower() in ("true", "1", "yes")
            return settings.get_bool(f"transcription.{k}", default)

        num_workers = _get_int_setting("num_workers", 1)
        beam_size = _get_int_setting("beam_size", 5)
        best_of = _get_int_setting("best_of", 5)
        temperature = _parse_temperature(settings, "transcription.whisper_temperature")
        condition_on_previous_text = _get_bool_setting("condition_on_previous_text", False)
        word_timestamps = _get_bool_setting("word_timestamps", False)
        vad_filter = _get_bool_setting("vad_filter", True)
        vad_parameters = _build_vad_parameters(settings, is_whisper=True) if vad_filter else None
        initial_prompt = settings.get("transcription.initial_prompt", "") or ""
        hotwords = settings.get("transcription.hotwords", "") or ""
        compression_ratio_threshold = _get_float_setting("compression_ratio_threshold", 2.4)
        log_prob_threshold = _get_float_setting("log_prob_threshold", -1.0)
        no_speech_threshold = _get_float_setting("no_speech_threshold", 0.6)
        repetition_penalty = _get_float_setting("repetition_penalty", 1.0)
        no_repeat_ngram_size = _get_int_setting("no_repeat_ngram_size", 0)
        punc_filter = True
        quantize = True
        vad_max_end_sil = 800

    model_path_obj = Path(models_dir) / model_name
    model_path = str(model_path_obj) if model_path_obj.exists() else model_name

    return TranscriptionConfig(
        language=language,
        model_path=model_path,
        device=device,
        compute_type=compute_type,
        beam_size=beam_size,
        best_of=best_of,
        temperature=temperature,
        condition_on_previous_text=condition_on_previous_text,
        word_timestamps=word_timestamps,
        max_chunk_duration=max_chunk_duration,
        output_formats=output_formats,
        vad_filter=vad_filter,
        vad_parameters=vad_parameters,
        initial_prompt=initial_prompt,
        hotwords=hotwords,
        compression_ratio_threshold=compression_ratio_threshold,
        log_prob_threshold=log_prob_threshold,
        no_speech_threshold=no_speech_threshold,
        repetition_penalty=repetition_penalty,
        no_repeat_ngram_size=no_repeat_ngram_size,
        engine=resolved_engine,
        punc_filter=punc_filter,
        num_workers=num_workers,
        quantize=quantize,
        vad_max_end_sil=vad_max_end_sil,
    )

