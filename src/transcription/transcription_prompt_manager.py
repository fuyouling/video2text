"""转写提示词模板管理器 —— 管理用户自定义与内置的 initial_prompt 与 hotwords 模板，支持持久化"""

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.i18n import t
from src.transcription.transcription_prompt_types import (
    TranscriptionPromptTemplate,
    get_builtin_tx_presets,
)
from src.utils.exceptions import ConfigurationError
from src.utils.json_utils import atomic_write_json, safe_read_json
from src.utils.logger import get_logger

logger = get_logger(__name__)


class TranscriptionPromptManager:
    """转写提示词模板管理器 - 管理 initial_prompt 与 hotwords 模板及内置预设，支持持久化

    数据存储在 transcription_prompts.json 文件中（与 config.ini 同目录），格式：
    {
        "version": 2,
        "templates": {
            "模板名": {
                "name": "模板名",
                "initial_prompt": "...",
                "hotwords": "...",
                "description": "..."
            }
        },
        "last_used": "general"
    }

    自动兼容与平滑迁移旧版本 v1 格式。
    """

    _instance: Optional["TranscriptionPromptManager"] = None
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
        self._file_path = base_dir / "transcription_prompts.json"
        self._custom_templates: dict[str, TranscriptionPromptTemplate] = {}
        self._last_used: str = "general"

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
                t("transcription_prompt_manager.load_failed", name=self._file_path.name)
            )
            return

        self._last_used = data.get("last_used", "general") or "general"
        raw_templates = data.get("templates", {})
        self._custom_templates.clear()

        needs_migration = data.get("version") != 2

        for name, tmpl_data in raw_templates.items():
            if isinstance(tmpl_data, dict):
                # 兼容旧版本 v1 结构及 v2 结构
                self._custom_templates[name] = TranscriptionPromptTemplate.from_dict(
                    template_id=name,
                    data=tmpl_data,
                    is_builtin=False,
                )

        logger.info(
            t("transcription_prompt_manager.loaded", name=self._file_path.name)
        )

        if needs_migration and raw_templates:
            try:
                self.save()
                logger.info(
                    "TranscriptionPromptManager: migrated transcription_prompts.json to v2 format"
                )
            except Exception as e:
                logger.warning(
                    "TranscriptionPromptManager: migration save failed: %s", e
                )

    def save(self) -> None:
        """持久化保存自定义模板与上次使用项"""
        try:
            templates_dict = {
                name: tmpl.to_dict()
                for name, tmpl in self._custom_templates.items()
            }
            data = {
                "version": 2,
                "templates": templates_dict,
                "last_used": self._last_used,
            }
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self._file_path, data)
        except ConfigurationError:
            raise
        except Exception as e:
            logger.error(
                t("transcription_prompt_manager.save_failed", error=e)
            )
            raise ConfigurationError(
                t("transcription_prompt_manager.save_failed", error=str(e))
            )

    # ── 预设与模板查询 ──

    def get_builtin_presets(self) -> dict[str, TranscriptionPromptTemplate]:
        """获取系统内置预设字典 (preset_id -> TranscriptionPromptTemplate)"""
        return get_builtin_tx_presets()

    def is_builtin(self, name_or_id: str) -> bool:
        """判断是否为系统内置预设"""
        if not name_or_id:
            return False
        presets = self.get_builtin_presets()
        if name_or_id in presets:
            return True
        for p in presets.values():
            if p.name == name_or_id:
                return True
        return False

    def get_template(self, name_or_id: str) -> Optional[TranscriptionPromptTemplate]:
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

    def get_all_templates(self) -> dict[str, TranscriptionPromptTemplate]:
        """获取所有可用模板（内置预设 + 自定义模板）"""
        all_tmpls = dict(self.get_builtin_presets())
        all_tmpls.update(self._custom_templates)
        return all_tmpls

    def get_names(self) -> list[str]:
        """获取所有自定义模板名称列表（保持向前兼容）"""
        return list(self._custom_templates.keys())

    def get_initial_prompt(self, name_or_id: str) -> str:
        """获取指定模板的 initial_prompt"""
        tmpl = self.get_template(name_or_id)
        return tmpl.initial_prompt if tmpl else ""

    def get_hotwords(self, name_or_id: str) -> str:
        """获取指定模板的 hotwords"""
        tmpl = self.get_template(name_or_id)
        return tmpl.hotwords if tmpl else ""

    def set_template(
        self,
        name: str,
        initial_prompt: str,
        hotwords: str,
        description: str = "",
    ) -> None:
        """保存或新增用户自定义模板"""
        if not name or not name.strip():
            return
        name = name.strip()
        self._custom_templates[name] = TranscriptionPromptTemplate(
            id=name,
            name=name,
            initial_prompt=initial_prompt,
            hotwords=hotwords,
            is_builtin=False,
            description=description,
        )
        self.save()

    def delete_template(self, name: str) -> None:
        """删除自定义模板（内置预设禁止物理删除）"""
        if self.is_builtin(name):
            logger.warning("TranscriptionPromptManager: Cannot delete built-in preset %s", name)
            return

        self._custom_templates.pop(name, None)
        if self._last_used == name:
            self._last_used = "general"
        self.save()

    def reset_template(self, name_or_id: str) -> Optional[TranscriptionPromptTemplate]:
        """恢复指定模板的内容（对内置预设返回原始预设，对自定义模板返回已保存内容）"""
        if self.is_builtin(name_or_id):
            presets = self.get_builtin_presets()
            if name_or_id in presets:
                return presets[name_or_id]
            for p in presets.values():
                if p.name == name_or_id:
                    return p
        return self._custom_templates.get(name_or_id)

    def get_last_used(self) -> str:
        """获取上次使用的模板 ID 或名称"""
        return self._last_used or "general"

    def set_last_used(self, name_or_id: str) -> None:
        """设置并持久化上次使用的模板 ID 或名称"""
        self._last_used = name_or_id or "general"
        self.save()

    @staticmethod
    def validate_limits(initial_prompt: str, hotwords: str) -> dict[str, Any]:
        """校验 initial_prompt 与 hotwords 长度安全预算

        Whisper 模型的文本上下文上限为 448 Token。
        - initial_prompt 建议在 300 字符（~150-200 Token）以内，避免挤占解码窗口。
        - hotwords 偏置预算建议在 220 Token（约 50 个词）以内，避免在 condition_on_previous_text 时报错。
        """
        prompt_len = len(initial_prompt.strip())
        # 热词粗略按空格/逗号分割
        words = [w for w in hotwords.replace(",", " ").replace("，", " ").split() if w]
        hotwords_count = len(words)

        prompt_warning = None
        hotwords_warning = None

        if prompt_len > 300:
            prompt_warning = t(
                "main.tx_prompt_len_warning",
                count=prompt_len,
            ) if prompt_len else None

        if hotwords_count > 50:
            hotwords_warning = t(
                "main.tx_hotwords_count_warning",
                count=hotwords_count,
            )

        return {
            "safe": prompt_warning is None and hotwords_warning is None,
            "prompt_length": prompt_len,
            "hotwords_count": hotwords_count,
            "prompt_warning": prompt_warning,
            "hotwords_warning": hotwords_warning,
        }
