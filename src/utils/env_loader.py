"""统一加载 .env 文件中的 API Key"""

import os
from pathlib import Path


def ensure_env_loaded() -> None:
    """从 .env 文件加载环境变量（避免手动 export）。"""
    if getattr(ensure_env_loaded, "_loaded", False):
        return
    from src.utils.api_key_store import parse_env

    candidates = [
        Path.cwd() / ".env",
        Path.home() / ".env",
    ]
    for env_path in candidates:
        if env_path.exists():
            try:
                text = env_path.read_text(encoding="utf-8")
            except Exception:
                text = env_path.read_text(encoding="utf-8", errors="replace")
            for entry in parse_env(text):
                if entry.kind == "kv" and entry.key:
                    os.environ.setdefault(entry.key, entry.value)
    ensure_env_loaded._loaded = True


def get_api_key(key_name: str) -> str:
    """获取 API Key，自动触发 .env 加载。"""
    ensure_env_loaded()
    return os.environ.get(key_name, "")
