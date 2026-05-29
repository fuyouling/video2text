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
    temperature: float
    condition_on_previous_text: bool
    word_timestamps: bool
    max_chunk_duration: int
    output_formats: list[str]


def _get_output_formats(settings: Settings) -> list[str]:
    """从配置中读取输出格式列表，过滤不支持的格式，至少返回 ['txt']。"""
    raw = settings.get_list("output.transcript_format", ["txt"])
    return [f.lower() for f in raw if f.lower() in SUPPORTED_TRANSCRIPT_FORMATS] or [
        "txt"
    ]


def _load_tx_config(settings: Settings) -> TranscriptionConfig:
    """从配置加载转写参数"""
    language = settings.get("transcription.language", "auto")
    model_name = settings.get("transcription.model_path", "large-v3")
    models_dir = settings.get("paths.models_dir", "models")
    model_path_obj = Path(models_dir) / model_name
    model_path = str(model_path_obj) if model_path_obj.exists() else model_name
    device = settings.get("transcription.device", "auto")
    compute_type = settings.get("transcription.compute_type", "float16")
    beam_size = settings.get_int("transcription.beam_size", 5)
    best_of = settings.get_int("transcription.best_of", 5)
    temperature = settings.get_float("transcription.temperature", 0.0)
    condition_on_previous_text = settings.get_bool(
        "transcription.condition_on_previous_text", True
    )
    word_timestamps = settings.get_bool("transcription.word_timestamps", False)
    max_chunk_duration = settings.get_int("preprocessing.max_chunk_duration", 300)
    output_formats = _get_output_formats(settings)

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
    )
