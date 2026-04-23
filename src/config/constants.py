"""常量定义"""

from typing import List

SUPPORTED_VIDEO_FORMATS: List[str] = [
    ".mp4",
    ".avi",
    ".mov",
    ".mkv",
    ".flv",
    ".wmv",
    ".webm",
]

SUPPORTED_AUDIO_FORMATS: List[str] = [".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"]

SUPPORTED_LANGUAGES: List[str] = [
    "auto",
    "zh",
    "en",
    "ja",
    "ko",
    "es",
    "fr",
    "de",
    "it",
    "pt",
    "ru",
]

DEFAULT_CONFIG = {
    "app": {"name": "video2text", "version": "1.0.0", "log_level": "INFO"},
    "transcription": {
        "model_path": "models/large-v3",
        "device": "auto",
        "language": "auto",
        "beam_size": 5,
        "best_of": 5,
        "temperature": 0.0,
    },
    "summarization": {
        "ollama_url": "http://127.0.0.1:11434",
        "model_name": "qwen2.5:7b-instruct-q4_K_M",
        "max_length": 500,
        "temperature": 0.7,
    },
    "preprocessing": {
        "ffmpeg_path": "ffmpeg",
        "audio_sample_rate": 16000,
        "audio_channels": 1,
    },
    "output": {
        "output_dir": "output",
        "transcript_format": "txt",
        "summary_format": "txt",
        "json_output": "true",
    },
    "paths": {"models_dir": "models", "logs_dir": "logs", "video_dir": "video"},
}

ERROR_CODES = {
    "SUCCESS": 0,
    "UNKNOWN_ERROR": 1,
    "VIDEO_FILE_ERROR": 2,
    "TRANSCRIPTION_ERROR": 3,
    "SUMMARIZATION_ERROR": 4,
    "CONFIGURATION_ERROR": 5,
    "EXTERNAL_SERVICE_ERROR": 6,
    "GPU_NOT_AVAILABLE": 7,
}
