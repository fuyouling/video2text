"""Prompt template data structures and built-in preset definitions"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from src.i18n import t


@dataclass
class PromptTemplate:
    """Prompt template representing a summarization style or custom instruction"""

    id: str
    name: str
    system_prompt: str
    user_instruction: str = ""
    enable_markdown: bool = True
    is_builtin: bool = False
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "system_prompt": self.system_prompt,
            "user_instruction": self.user_instruction,
            "enable_markdown": self.enable_markdown,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, template_id: str, data: dict[str, Any], is_builtin: bool = False) -> "PromptTemplate":
        return cls(
            id=template_id,
            name=data.get("name", template_id),
            system_prompt=data.get("system_prompt", ""),
            user_instruction=data.get("user_instruction", ""),
            enable_markdown=data.get("enable_markdown", True),
            is_builtin=is_builtin,
            description=data.get("description", ""),
        )


def get_builtin_presets() -> dict[str, PromptTemplate]:
    """Get built-in summarization presets dynamically localized via i18n"""
    return {
        "default": PromptTemplate(
            id="default",
            name=t("services.summarization.presets.default.name"),
            system_prompt=t("services.summarization.presets.default.prompt"),
            enable_markdown=True,
            is_builtin=True,
            description=t("services.summarization.presets.default.desc"),
        ),
        "meeting": PromptTemplate(
            id="meeting",
            name=t("services.summarization.presets.meeting.name"),
            system_prompt=t("services.summarization.presets.meeting.prompt"),
            enable_markdown=True,
            is_builtin=True,
            description=t("services.summarization.presets.meeting.desc"),
        ),
        "lecture": PromptTemplate(
            id="lecture",
            name=t("services.summarization.presets.lecture.name"),
            system_prompt=t("services.summarization.presets.lecture.prompt"),
            enable_markdown=True,
            is_builtin=True,
            description=t("services.summarization.presets.lecture.desc"),
        ),
        "tldr": PromptTemplate(
            id="tldr",
            name=t("services.summarization.presets.tldr.name"),
            system_prompt=t("services.summarization.presets.tldr.prompt"),
            enable_markdown=False,
            is_builtin=True,
            description=t("services.summarization.presets.tldr.desc"),
        ),
        "takeaways": PromptTemplate(
            id="takeaways",
            name=t("services.summarization.presets.takeaways.name"),
            system_prompt=t("services.summarization.presets.takeaways.prompt"),
            enable_markdown=True,
            is_builtin=True,
            description=t("services.summarization.presets.takeaways.desc"),
        ),
    }
