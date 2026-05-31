"""统一加载 .env 文件中的 API Key"""

import os
from pathlib import Path


def ensure_env_loaded() -> None:
    """从 .env 文件加载环境变量（避免手动 export）。"""
    if getattr(ensure_env_loaded, "_loaded", False):
        return
    candidates = [
        Path.cwd() / ".env",
        Path.home() / ".env",
    ]
    for env_path in candidates:
        if env_path.exists():
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip("\"'")
                    os.environ.setdefault(key, value)
    ensure_env_loaded._loaded = True


def get_api_key(key_name: str) -> str:
    """获取 API Key，自动触发 .env 加载。"""
    ensure_env_loaded()
    return os.environ.get(key_name, "")
