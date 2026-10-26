"""转写提示词数据结构与内置预设定义"""

from dataclasses import dataclass
from typing import Any, Optional

from src.i18n import t


@dataclass
class TranscriptionPromptTemplate:
    """转写提示词模板数据类 —— 包含 initial_prompt 与 hotwords"""

    id: str
    name: str
    initial_prompt: str = ""
    hotwords: str = ""
    is_builtin: bool = False
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "initial_prompt": self.initial_prompt,
            "hotwords": self.hotwords,
            "description": self.description,
        }

    @classmethod
    def from_dict(
        cls,
        template_id: str,
        data: dict[str, Any],
        is_builtin: bool = False,
    ) -> "TranscriptionPromptTemplate":
        return cls(
            id=template_id,
            name=data.get("name", template_id),
            initial_prompt=data.get("initial_prompt", ""),
            hotwords=data.get("hotwords", ""),
            is_builtin=is_builtin,
            description=data.get("description", ""),
        )


def get_builtin_tx_presets() -> dict[str, TranscriptionPromptTemplate]:
    """获取动态本地化的内置转写提示词预设字典"""
    return {
        "general": TranscriptionPromptTemplate(
            id="general",
            name=t("transcription_prompt_manager.presets.general.name"),
            initial_prompt=t("transcription_prompt_manager.presets.general.prompt"),
            hotwords=t("transcription_prompt_manager.presets.general.hotwords"),
            is_builtin=True,
            description=t("transcription_prompt_manager.presets.general.desc"),
        ),
        "meeting": TranscriptionPromptTemplate(
            id="meeting",
            name=t("transcription_prompt_manager.presets.meeting.name"),
            initial_prompt=t("transcription_prompt_manager.presets.meeting.prompt"),
            hotwords=t("transcription_prompt_manager.presets.meeting.hotwords"),
            is_builtin=True,
            description=t("transcription_prompt_manager.presets.meeting.desc"),
        ),
        "ecommerce": TranscriptionPromptTemplate(
            id="ecommerce",
            name=t("transcription_prompt_manager.presets.ecommerce.name"),
            initial_prompt=t("transcription_prompt_manager.presets.ecommerce.prompt"),
            hotwords=t("transcription_prompt_manager.presets.ecommerce.hotwords"),
            is_builtin=True,
            description=t("transcription_prompt_manager.presets.ecommerce.desc"),
        ),
        "tech": TranscriptionPromptTemplate(
            id="tech",
            name=t("transcription_prompt_manager.presets.tech.name"),
            initial_prompt=t("transcription_prompt_manager.presets.tech.prompt"),
            hotwords=t("transcription_prompt_manager.presets.tech.hotwords"),
            is_builtin=True,
            description=t("transcription_prompt_manager.presets.tech.desc"),
        ),
        "bilingual": TranscriptionPromptTemplate(
            id="bilingual",
            name=t("transcription_prompt_manager.presets.bilingual.name"),
            initial_prompt=t("transcription_prompt_manager.presets.bilingual.prompt"),
            hotwords=t("transcription_prompt_manager.presets.bilingual.hotwords"),
            is_builtin=True,
            description=t("transcription_prompt_manager.presets.bilingual.desc"),
        ),
    }
