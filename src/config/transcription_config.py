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


def _parse_temperature(settings: Settings) -> list[float]:
    """读取 temperature，支持 '0.0,0.2,0.4' 或单值 '0.0'。"""
    raw = settings.get("transcription.temperature", "0.0")
    try:
        vals = [float(x.strip()) for x in str(raw).split(",") if x.strip()]
    except ValueError:
        vals = [0.0]
    return vals or [0.0]


def _build_vad_parameters(settings: Settings) -> dict:
    """构建 Silero VAD 参数字典。vad_max_speech_s=0 表示不限制。"""
    params = {
        "threshold": settings.get_float("transcription.vad_threshold", 0.5),
        "min_silence_duration_ms": settings.get_int(
            "transcription.vad_min_silence_ms", 2000
        ),
        "speech_pad_ms": settings.get_int("transcription.vad_speech_pad_ms", 400),
    }
    max_speech = settings.get_int("transcription.vad_max_speech_s", 0)
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
    """从配置加载转写参数"""
    language = settings.get("transcription.language", "auto")
    model_name = settings.get("transcription.model_path", "faster-whisper-large-v3-turbo-ct2")
    models_dir = settings.get("paths.models_dir", "models")
    model_path_obj = Path(models_dir) / model_name
    model_path = str(model_path_obj) if model_path_obj.exists() else model_name
    device = settings.get("transcription.device", "auto")
    compute_type = settings.get("transcription.compute_type", "float16")
    beam_size = settings.get_int("transcription.beam_size", 5)
    best_of = settings.get_int("transcription.best_of", 5)
    temperature = _parse_temperature(settings)
    condition_on_previous_text = settings.get_bool(
        "transcription.condition_on_previous_text", False
    )
    word_timestamps = settings.get_bool("transcription.word_timestamps", False)
    max_chunk_duration = settings.get_int("preprocessing.max_chunk_duration", 300)
    output_formats = _get_output_formats(settings)

    vad_filter = settings.get_bool("transcription.vad_filter", True)
    vad_parameters = _build_vad_parameters(settings) if vad_filter else None
    initial_prompt = settings.get("transcription.initial_prompt", "") or ""
    hotwords = settings.get("transcription.hotwords", "") or ""
    compression_ratio_threshold = settings.get_float(
        "transcription.compression_ratio_threshold", 2.4
    )
    log_prob_threshold = settings.get_float("transcription.log_prob_threshold", -1.0)
    no_speech_threshold = settings.get_float(
        "transcription.no_speech_threshold", 0.6
    )
    repetition_penalty = settings.get_float("transcription.repetition_penalty", 1.0)
    no_repeat_ngram_size = settings.get_int(
        "transcription.no_repeat_ngram_size", 0
    )

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
    )
