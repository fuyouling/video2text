"""对话 JSON 存储 —— 每次对话独立存储为一个 JSON 文件"""

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional

from src.config.settings import Settings
from src.i18n import t
from src.utils.exceptions import Video2TextError
from src.utils.json_utils import atomic_write_json
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class VoiceMessage:
    role: str
    content: str
    uuid: str
    mode: Literal["normal", "realtime"]
    corrected: bool = False
    parent_uuid: str = ""
    timestamp: float = 0.0


@dataclass
class VoiceConversation:
    id: str
    title: str
    created_at: float
    updated_at: float
    messages: List[VoiceMessage] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    summary_md_path: Optional[str] = None
    version: str = "1.0"


class VoiceConversationStore:
    """对话存储管理器 —— 每次对话一个 JSON 文件"""

    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings or Settings()
        self._voice_dir = self._settings.get(
            "voice_to_text.voice_dir", "voice/voice_to_text"
        )
        self._summary_dir = self._settings.get(
            "voice_to_text.summary_dir", "voice/summary"
        )
        Path(self._voice_dir).mkdir(parents=True, exist_ok=True)
        Path(self._summary_dir).mkdir(parents=True, exist_ok=True)

    def _conv_path(self, conv_id: str) -> Path:
        return Path(self._voice_dir) / f"{conv_id}.json"

    def _generate_uuid(self) -> str:
        import uuid
        return uuid.uuid4().hex[:16]

    def _generate_conv_id(self) -> str:
        return datetime.now().strftime('%Y%m%d%H%M%S')

    def create_conversation(self, first_text: str, mode: str) -> str:
        conv_id = self._generate_conv_id()
        now = datetime.now().timestamp()
        title = first_text[:20] + ("..." if len(first_text) > 20 else "")
        conv = VoiceConversation(
            id=conv_id,
            title=title,
            created_at=now,
            updated_at=now,
            messages=[
                VoiceMessage(
                    role="user",
                    content=first_text,
                    uuid=self._generate_uuid(),
                    mode=mode,
                    timestamp=now,
                )
            ],
        )
        self._save(conv)
        logger.info(t("storage.voice_store.create_conv"), conv_id)
        return conv_id

    def append_message(
        self, conv_id: str, msg: VoiceMessage
    ) -> None:
        conv = self.get_conversation(conv_id)
        if conv is None:
            raise Video2TextError(t("storage.voice_store.conv_not_found", conv_id=conv_id))
        conv.messages.append(msg)
        conv.updated_at = datetime.now().timestamp()
        self._save(conv)

    def get_conversation(self, conv_id: str) -> Optional[VoiceConversation]:
        path = self._conv_path(conv_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            messages = []
            for i, m in enumerate(data.get("messages", [])):
                msg_data = m.copy()
                if "timestamp" in msg_data and "uuid" not in msg_data:
                    import uuid as _uuid
                    msg_data["uuid"] = _uuid.uuid4().hex[:16]
                if "patent_uuid" in msg_data and "parent_uuid" not in msg_data:
                    msg_data["parent_uuid"] = msg_data.pop("patent_uuid")
                if "timestamp" not in msg_data:
                    msg_data["timestamp"] = data["created_at"] + i
                messages.append(VoiceMessage(**msg_data))
            return VoiceConversation(
                id=data["id"],
                title=data["title"],
                created_at=data["created_at"],
                updated_at=data["updated_at"],
                messages=messages,
                tags=data.get("tags", []),
                summary_md_path=data.get("summary_md_path"),
                version=data.get("version", "1.0"),
            )
        except Exception as exc:
            logger.error(t("storage.voice_store.read_conv_fail"), conv_id, exc)
            return None

    def list_conversations(self) -> List[dict]:
        results = []
        voice_path = Path(self._voice_dir)
        if not voice_path.exists():
            return results
        for f in sorted(voice_path.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                results.append(
                    {
                        "id": data["id"],
                        "title": data.get("title", ""),
                        "updated_at": data.get("updated_at", 0),
                        "message_count": len(data.get("messages", [])),
                        "summary_md_path": data.get("summary_md_path"),
                    }
                )
            except Exception as exc:
                logger.warning(t("storage.voice_store.read_list_fail"), f.name, exc)
        return results

    def delete_conversation(self, conv_id: str) -> None:
        path = self._conv_path(conv_id)
        if path.exists():
            path.unlink()
            logger.info(t("storage.voice_store.delete_conv"), conv_id)

    def update_summary_path(self, conv_id: str, path: str) -> None:
        conv = self.get_conversation(conv_id)
        if conv is None:
            raise Video2TextError(t("storage.voice_store.conv_not_found", conv_id=conv_id))
        conv.summary_md_path = path
        conv.updated_at = datetime.now().timestamp()
        self._save(conv)

    def _save(self, conv: VoiceConversation) -> None:
        data = {
            "id": conv.id,
            "title": conv.title,
            "created_at": conv.created_at,
            "updated_at": conv.updated_at,
            "messages": [asdict(m) for m in conv.messages],
            "tags": conv.tags,
            "summary_md_path": conv.summary_md_path,
            "version": conv.version,
        }
        atomic_write_json(self._conv_path(conv.id), data)