"""转写提示词数据结构与内置预设定义"""

from dataclasses import dataclass
from typing import Any

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
            name=str(data.get("name") or template_id),
            initial_prompt=str(data.get("initial_prompt") or ""),
            hotwords=str(data.get("hotwords") or ""),
            is_builtin=is_builtin,
            description=str(data.get("description") or ""),
        )


BUILTIN_PRESET_IDS = ("general", "meeting")


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
    }
