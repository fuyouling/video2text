"""转写提示词模板管理器 —— 管理用户自定义的 initial_prompt 与 hotwords 模板，支持持久化"""

import threading
from pathlib import Path
from typing import Optional

from src.utils.json_utils import atomic_write_json, safe_read_json
from src.utils.logger import get_logger

logger = get_logger(__name__)


class TranscriptionPromptManager:
    """转写提示词模板管理器 - 将 initial_prompt 与 hotwords 保存为命名模板，支持持久化

    数据存储在 transcription_prompts.json 文件中（与 config.ini 同目录），格式：
    {
        "templates": {
            "模板名": {"initial_prompt": "...", "hotwords": "..."},
            ...
        },
        "last_used": "模板名"
    }

    第一次运行时文件不存在，模板列表为空。
    """

    _instance: Optional["TranscriptionPromptManager"] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self, base_dir: Optional[Path] = None):
        if self._initialized:
            return

        if base_dir is None:
            from src.config.settings import Settings

            base_dir = Path(Settings().config_path).parent
        self._file_path = base_dir / "transcription_prompts.json"
        self._templates: dict[str, dict] = {}
        self._last_used: str = ""
        self._load()
        self._initialized = True

    @classmethod
    def _reset(cls) -> None:
        """重置单例（仅供测试使用）"""
        with cls._lock:
            cls._instance = None

    def _load(self) -> None:
        if not self._file_path.exists():
            return
        data = safe_read_json(self._file_path)
        if data is None:
            logger.warning(
                "TranscriptionPromptManager: ✗ 加载失败 (%s)", self._file_path.name
            )
            return
        self._templates = data.get("templates", {})
        self._last_used = data.get("last_used", "")
        logger.info(
            "TranscriptionPromptManager: ✓ 加载 (%s)", self._file_path.name
        )

    def save(self) -> None:
        try:
            data = {
                "templates": self._templates,
                "last_used": self._last_used,
            }
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self._file_path, data)
        except Exception as e:
            logger.error("TranscriptionPromptManager: ✗ 保存失败 (%s)", e)
            raise

    def get_names(self) -> list[str]:
        return list(self._templates.keys())

    def get_template(self, name: str) -> dict:
        return self._templates.get(name, {})

    def get_initial_prompt(self, name: str) -> str:
        return self._templates.get(name, {}).get("initial_prompt", "")

    def get_hotwords(self, name: str) -> str:
        return self._templates.get(name, {}).get("hotwords", "")

    def set_template(self, name: str, initial_prompt: str, hotwords: str) -> None:
        self._templates[name] = {
            "initial_prompt": initial_prompt,
            "hotwords": hotwords,
        }
        self.save()

    def delete_template(self, name: str) -> None:
        self._templates.pop(name, None)
        if self._last_used == name:
            self._last_used = ""
        self.save()

    def get_last_used(self) -> str:
        return self._last_used

    def set_last_used(self, name: str) -> None:
        self._last_used = name
        self.save()
