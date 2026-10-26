"""Prompt template manager — manages user-defined prompt templates and presets with persistence"""

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.i18n import t
from src.summarization.prompt_types import PromptTemplate, get_builtin_presets
from src.utils.exceptions import ConfigurationError
from src.utils.json_utils import atomic_write_json, safe_read_json
from src.utils.logger import get_logger

logger = get_logger(__name__)


class PromptManager:
    """Prompt template manager — saves user-defined prompt templates and presets with persistence

    Data is stored in summary_prompts.json (same directory as config.ini), format:
    {
        "version": 2,
        "templates": {"template_name": {"system_prompt": "...", "description": "..."}, ...},
        "last_used": "template_name_or_preset_id",
        "markdown_enabled": true,
        "custom_markdown_prompt": ""
    }

    Auto-migrates from legacy prompts.json on first run if present.
    """

    _instance: Optional["PromptManager"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
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

        self._base_dir = base_dir
        self._file_path = base_dir / "summary_prompts.json"
        self._legacy_file_path = base_dir / "prompts.json"

        self._custom_templates: dict[str, PromptTemplate] = {}
        self._last_used: str = "default"
        self._markdown_enabled: bool = True
        self._custom_markdown_prompt: str = ""

        self._load()
        self._initialized = True

    @classmethod
    def _reset(cls) -> None:
        """Reset singleton (for testing only)"""
        with cls._lock:
            cls._instance = None

    def _load(self) -> None:
        # 1. 尝试加载 summary_prompts.json
        target_path = self._file_path
        is_legacy = False

        if not target_path.exists():
            if self._legacy_file_path.exists():
                target_path = self._legacy_file_path
                is_legacy = True
            else:
                return

        data = safe_read_json(target_path)
        if data is None:
            logger.warning(
                "PromptManager: ✗ %s",
                t("services.summarization.prompt_load_failed", name=target_path.name),
            )
            return

        self._last_used = data.get("last_used", "default") or "default"
        self._markdown_enabled = data.get("markdown_enabled", True)
        self._custom_markdown_prompt = data.get("custom_markdown_prompt", "")

        raw_templates = data.get("templates", {})
        self._custom_templates.clear()

        for name, tmpl_data in raw_templates.items():
            if isinstance(tmpl_data, str):
                # 兼容旧版本字符串格式
                self._custom_templates[name] = PromptTemplate(
                    id=name,
                    name=name,
                    system_prompt=tmpl_data,
                    enable_markdown=self._markdown_enabled,
                    is_builtin=False,
                )
            elif isinstance(tmpl_data, dict):
                self._custom_templates[name] = PromptTemplate.from_dict(
                    name, tmpl_data, is_builtin=False
                )

        logger.info(
            "PromptManager: ✓ %s",
            t("services.summarization.prompt_loaded", name=target_path.name),
        )

        # 若从旧 prompts.json 读取，自动平滑持久化迁移到 summary_prompts.json
        if is_legacy:
            try:
                self.save()
                logger.info(
                    "PromptManager: migrated legacy prompts.json -> %s",
                    self._file_path.name,
                )
            except Exception as e:
                logger.warning("PromptManager: migration save failed: %s", e)

    def save(self) -> None:
        try:
            templates_dict = {
                name: tmpl.to_dict()
                for name, tmpl in self._custom_templates.items()
            }
            data = {
                "version": 2,
                "templates": templates_dict,
                "last_used": self._last_used,
                "markdown_enabled": self._markdown_enabled,
                "custom_markdown_prompt": self._custom_markdown_prompt,
            }
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self._file_path, data)
        except ConfigurationError:
            raise
        except Exception as e:
            logger.error(
                "PromptManager: ✗ %s",
                t("services.summarization.prompt_save_error", error=e),
            )
            raise ConfigurationError(
                t("services.summarization.prompt_save_failed", error=str(e))
            )

    # ── 预设与模板查询 ──

    def get_builtin_presets(self) -> dict[str, PromptTemplate]:
        """获取系统内置预设字典 (id -> PromptTemplate)"""
        return get_builtin_presets()

    def is_builtin(self, name_or_id: str) -> bool:
        """判断是否为内置预设"""
        presets = self.get_builtin_presets()
        if name_or_id in presets:
            return True
        for p in presets.values():
            if p.name == name_or_id:
                return True
        return False

    def get_template(self, name_or_id: str) -> Optional[PromptTemplate]:
        """根据名称或预设 ID 获取模板对象"""
        if not name_or_id:
            return None

        # 1. 查找用户自定义模板
        if name_or_id in self._custom_templates:
            return self._custom_templates[name_or_id]

        # 2. 查找内置预设 (通过 id 或 localized name)
        presets = self.get_builtin_presets()
        if name_or_id in presets:
            return presets[name_or_id]
        for p in presets.values():
            if p.name == name_or_id:
                return p

        return None

    def get_names(self) -> list[str]:
        """获取所有自定义模板名称列表（保持向前兼容）"""
        return list(self._custom_templates.keys())

    def get_content(self, name_or_id: str) -> str:
        """获取模板的系统提示词内容"""
        tmpl = self.get_template(name_or_id)
        if tmpl is not None:
            return tmpl.system_prompt
        # 兼容旧代码直接在 _custom_templates 查未命中的兜底
        return ""

    def set_template(
        self,
        name: str,
        content: str,
        user_instruction: str = "",
        enable_markdown: bool = True,
        description: str = "",
    ) -> None:
        """保存或新增用户自定义模板"""
        if not name or not name.strip():
            return
        name = name.strip()
        self._custom_templates[name] = PromptTemplate(
            id=name,
            name=name,
            system_prompt=content,
            user_instruction=user_instruction,
            enable_markdown=enable_markdown,
            is_builtin=False,
            description=description,
        )
        self.save()

    def delete_template(self, name: str) -> None:
        """删除自定义模板（内置预设不可删除）"""
        if self.is_builtin(name):
            logger.warning("PromptManager: Cannot delete built-in preset %s", name)
            return

        self._custom_templates.pop(name, None)
        if self._last_used == name:
            self._last_used = "default"
        self.save()

    def get_last_used(self) -> str:
        return self._last_used or "default"

    def set_last_used(self, name_or_id: str) -> None:
        self._last_used = name_or_id or "default"
        self.save()

    def get_last_used_content(self) -> str:
        """获取上次使用的提示词内容，缺省时返回默认预设提示词"""
        name = self.get_last_used()
        content = self.get_content(name)
        if content:
            return content
        default_tmpl = self.get_template("default")
        return default_tmpl.system_prompt if default_tmpl else ""

    # ── Markdown 格式控制 ──

    def get_markdown_prompt(self) -> str:
        if self._custom_markdown_prompt and self._custom_markdown_prompt.strip():
            return self._custom_markdown_prompt
        return t("services.summarization.markdown_prompt")

    def set_custom_markdown_prompt(self, prompt: str) -> None:
        self._custom_markdown_prompt = prompt
        self.save()

    def get_markdown_enabled(self) -> bool:
        """兼容保留：界面已全面采用所见即所得，格式由提示词自身控制"""
        return True

    def set_markdown_enabled(self, value: bool) -> None:
        """兼容保留"""
        pass

    # ── 提示词构建 ──

    def build_messages(
        self,
        text: str,
        custom_prompt: str = "",
        enable_markdown: Optional[bool] = None,
        user_instruction: str = "",
        **kwargs,
    ) -> list[dict[str, str]]:
        """构建现代 Chat Completion 标准 messages 结构 (System / User 分离)

        所见即所得：直接使用 custom_prompt（或默认预设提示词）作为 system 角色内容，
        不暗中追加任何隐式格式字符串。排版与格式规范由提示词自身完全显式定义。

        Args:
            text: 待总结的正文内容
            custom_prompt: 完整的系统提示词（由界面编辑框直接提供，包含角色设定与排版要求）
            enable_markdown: 已废弃参数，保持签名兼容
            user_instruction: 用户的附加指令要求（可选）

        Returns:
            符合 OpenAI / Ollama 等 Chat 规范的 messages 列表
        """
        if custom_prompt and custom_prompt.strip():
            system_content = custom_prompt.strip()
        else:
            default_tmpl = self.get_template("default")
            system_content = (
                default_tmpl.system_prompt
                if default_tmpl
                else t("services.summarization.default_prompt")
            )

        # User Content 构建：仅包含用户附加指令与待总结正文
        text_content_label = t("services.summarization.text_content_label")
        user_parts = []
        if user_instruction and user_instruction.strip():
            user_parts.append(user_instruction.strip())
        user_parts.append(f"{text_content_label}\n{text}")
        user_content = "\n\n".join(user_parts)

        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

    def build_prompt(
        self,
        text: str,
        custom_prompt: str = "",
        is_use_gui_markdown_flag: bool = True,
        **kwargs,
    ) -> str:
        """构建单字符串完整 prompt（所见即所得，保持向后兼容）"""
        if custom_prompt and custom_prompt.strip():
            base = custom_prompt.strip()
        else:
            default_tmpl = self.get_template("default")
            base = (
                default_tmpl.system_prompt
                if default_tmpl
                else t("services.summarization.default_prompt")
            )

        text_content_label = t("services.summarization.text_content_label")
        return f"{base}\n\n{text_content_label}\n{text}"
