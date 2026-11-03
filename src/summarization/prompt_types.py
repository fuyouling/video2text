"""Prompt template data structures and built-in preset definitions"""

from dataclasses import dataclass, field
import threading
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


BUILTIN_PRESET_IDS = ("default", "meeting", "lecture", "tldr", "takeaways")
_CANONICAL_PRESET_IDS = set(BUILTIN_PRESET_IDS)
_NAME_TO_CANONICAL_ID_CACHE: Optional[dict[str, str]] = None
_CACHE_LOCK = threading.Lock()


def _get_name_to_canonical_id_map() -> dict[str, str]:
    """收集所有语言 locale 文件中内置预设的名称与规范 ID 的映射"""
    global _NAME_TO_CANONICAL_ID_CACHE
    with _CACHE_LOCK:
        if _NAME_TO_CANONICAL_ID_CACHE is not None:
            return _NAME_TO_CANONICAL_ID_CACHE

        mapping: dict[str, str] = {}
        for pid in _CANONICAL_PRESET_IDS:
            mapping[pid] = pid

        try:
            from src.i18n import I18N
            i18n = I18N()
            base_locales = i18n._base_dir() / "locales"
            if base_locales.exists():
                import json
                for json_file in base_locales.glob("*.json"):
                    try:
                        data = json.loads(json_file.read_text(encoding="utf-8"))
                        presets = (
                            data.get("services", {})
                            .get("summarization", {})
                            .get("presets", {})
                        )
                        for pid, pdata in presets.items():
                            if pid in _CANONICAL_PRESET_IDS and isinstance(pdata, dict):
                                name = pdata.get("name")
                                if name and isinstance(name, str):
                                    mapping[name.strip()] = pid
                    except Exception:
                        pass
        except Exception:
            pass

        _NAME_TO_CANONICAL_ID_CACHE = mapping
        return _NAME_TO_CANONICAL_ID_CACHE


def resolve_builtin_preset_id(name_or_id: str) -> Optional[str]:
    """跨语言反查内置预设规范 ID (default, meeting, lecture, tldr, takeaways)。

    支持传入规范 ID 或任意受支持语言下的预设显示名称，均可反查其标准英文规范 ID。
    若非内置预设则返回 None。
    """
    if not name_or_id:
        return None
    cleaned = str(name_or_id).strip()
    if cleaned in _CANONICAL_PRESET_IDS:
        return cleaned

    # 优先查当前活跃语言下的预设显示名称
    try:
        current_presets = get_builtin_presets()
        for pid, tmpl in current_presets.items():
            if tmpl.name == cleaned:
                return pid
    except Exception:
        pass

    # 跨语言反查完整映射表
    name_map = _get_name_to_canonical_id_map()
    return name_map.get(cleaned)
