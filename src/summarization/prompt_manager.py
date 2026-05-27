"""提示词模板管理器 —— 管理用户自定义提示词模板，支持持久化"""

import threading
from pathlib import Path
from typing import Optional

from src.utils.exceptions import ConfigurationError
from src.utils.json_utils import atomic_write_json, safe_read_json
from src.utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_MARKDOWN_PROMPT = (
    "\n请将总结内容以Markdown格式输出，形式如下：\n"
    "- **要点标题**\n\t- 内容\n\t- 内容\n"
    "- **要点标题**\n\t- 内容\n\t- 内容\n\n"
    "保持Markdown格式的正确性，确保输出可以直接渲染。\n"
)


class PromptManager:
    """提示词模板管理器 - 将用户自定义提示词保存为命名模板，支持持久化

    数据存储在 prompts.json 文件中（与 config.ini 同目录），格式：
    {
        "templates": {"模板名": "提示词内容", ...},
        "last_used": "模板名"
    }

    第一次运行时文件不存在，模板列表为空。
    """

    _instance: Optional["PromptManager"] = None
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
        self._file_path = base_dir / "prompts.json"
        self._templates: dict[str, str] = {}
        self._last_used: str = ""
        self._markdown_prompt: str = _DEFAULT_MARKDOWN_PROMPT
        self._markdown_enabled: bool = True
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
            logger.warning("PromptManager: ✗ 加载失败 (%s)", self._file_path)
            return
        self._templates = data.get("templates", {})
        self._last_used = data.get("last_used", "")
        self._markdown_prompt = data.get("markdown_prompt", _DEFAULT_MARKDOWN_PROMPT)
        self._markdown_enabled = data.get("markdown_enabled", True)
        logger.info("PromptManager: ✓ 加载 (%s)", self._file_path)

    def save(self) -> None:
        try:
            data = {
                "templates": self._templates,
                "last_used": self._last_used,
                "markdown_prompt": self._markdown_prompt,
                "markdown_enabled": self._markdown_enabled,
            }
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self._file_path, data)
        except ConfigurationError:
            raise
        except Exception as e:
            logger.error("PromptManager: ✗ 保存失败 (%s)", e)
            raise ConfigurationError(f"提示词模板保存失败: {e}")

    def get_names(self) -> list[str]:
        return list(self._templates.keys())

    def get_content(self, name: str) -> str:
        return self._templates.get(name, "")

    def set_template(self, name: str, content: str) -> None:
        self._templates[name] = content
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

    def get_last_used_content(self) -> str:
        if self._last_used and self._last_used in self._templates:
            return self._templates[self._last_used]
        return ""

    def get_markdown_prompt(self) -> str:
        return self._markdown_prompt

    def set_markdown_prompt(self, value: str) -> None:
        self._markdown_prompt = value
        self.save()

    def get_markdown_enabled(self) -> bool:
        return self._markdown_enabled

    def set_markdown_enabled(self, value: bool) -> None:
        self._markdown_enabled = value
        self.save()

    def build_prompt(self, text: str, custom_prompt: str = "") -> str:
        """构建完整的用户提示词

        包含默认 system prompt、Markdown 格式指令（如果启用）、用户文本。
        如果 custom_prompt 非空则替换默认 system prompt。
        """
        if custom_prompt and custom_prompt.strip():
            base = custom_prompt.strip()
        else:
            base = "你是一个专业的文本总结助手，擅长提取关键信息并生成简洁准确的总结。"

        if self._markdown_enabled:
            md_prompt = self._markdown_prompt
            if md_prompt.strip():
                return f"{base}\n\n{md_prompt}\n\n文本内容：\n{text}"
        return f"{base}\n\n文本内容：\n{text}"
