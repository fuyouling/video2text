"""API Key 存储工具 —— 解析 / 序列化 / 读写 .env 文件（与 UI 完全解耦）。

设计目标：
- 纯 Python、不依赖 Qt，便于独立单测与复用（CLI / 将来迁移存储格式互不牵连）。
- 保留 `.env` 中的注释与空行顺序，仅改写 / 追加 ``KEY=VALUE`` 行。
- 保存采用随机临时文件（tempfile.mkstemp）与原子替换，防并发碰撞。
- POSIX 环境下限制文件访问权限为 0o600，保护敏感凭据。

解析约定：
- ``# ...`` 行             -> 注释，原样保留
- 空行                    -> 空行，原样保留
- 含 ``=`` 的行            -> 键值对（自动去掉值两侧包裹的引号）
- ``export FOO=bar``       -> 去掉前缀 ``export `` 后按键值对处理
- 其它无法识别的行          -> 按注释原样保留
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple


@dataclass
class EnvLine:
    """`.env` 文件中的一行。

    ``kind`` 取值：``kv``（键值对）/ ``comment``（注释或原样保留行）/ ``blank``（空行）/
    ``deleted``（运行时被删除、序列化时跳过）。``raw`` 仅用于 comment / blank 行原样回写。
    """

    kind: str
    raw: str = ""
    key: str = ""
    value: str = ""
    vendor: str = ""


_KNOWN_VENDORS = {
    "OLLAMA_API_KEY": "Ollama",
    "NVIDIA_API_KEY": "NVIDIA",
    "MISTRAL_API_KEY": "Mistral AI",
    "AMD_API_KEY": "AMD Radeon Cloud",
    "OPENAI_API_KEY": "OpenAI",
    "DEEPSEEK_API_KEY": "DeepSeek",
    "ANTHROPIC_API_KEY": "Anthropic",
    "CLAUDE_API_KEY": "Claude",
    "SILICONFLOW_API_KEY": "SiliconFlow",
    "GROQ_API_KEY": "Groq",
    "GEMINI_API_KEY": "Google Gemini",
    "MOONSHOT_API_KEY": "Moonshot",
    "ZHIPU_API_KEY": "智谱 AI",
    "QWEN_API_KEY": "通义千问",
}


def infer_vendor_name(key: str) -> str:
    """根据环境变量 Key 名推导默认的模型厂商名称。"""
    key_upper = key.strip().upper()
    if key_upper in _KNOWN_VENDORS:
        return _KNOWN_VENDORS[key_upper]
    clean_key = key.strip()
    if clean_key.upper().endswith("_API_KEY"):
        base = clean_key[:-8]
    elif clean_key.upper().endswith("_KEY"):
        base = clean_key[:-4]
    else:
        base = clean_key
    if base.isupper():
        return " ".join(part.capitalize() for part in base.split("_"))
    return base


def _extract_kv_and_vendor(line: str) -> Tuple[str, str, str]:
    """从 key=value 行中解析 (key, value, vendor)。"""
    body = line.strip()
    # 修复 startswith("export") 误剥离变量名前缀缺陷（例如 export_foo=bar），改用精确前缀
    if body.startswith("export ") or body.startswith("export\t"):
        body = body[len("export"):].lstrip()

    key, _, rest = body.partition("=")
    key = key.strip()
    rest = rest.strip()

    vendor = ""
    value = ""

    if rest:
        if rest[0] in ('"', "'"):
            quote = rest[0]
            end_idx = -1
            escaped = False
            for idx in range(1, len(rest)):
                ch = rest[idx]
                if escaped:
                    escaped = False
                    continue
                if ch == "\\":
                    escaped = True
                    continue
                if ch == quote:
                    end_idx = idx
                    break

            if end_idx != -1:
                value = rest[1:end_idx].replace('\\' + quote, quote)
                after_quote = rest[end_idx + 1:].strip()
                if "#" in after_quote:
                    _, _, comment = after_quote.partition("#")
                    vendor = comment.strip()
            else:
                if "#" in rest:
                    val_part, _, comment = rest.partition("#")
                    value = val_part.strip().strip(quote)
                    vendor = comment.strip()
                else:
                    value = rest.strip(quote)
        else:
            if "#" in rest:
                val_part, _, comment = rest.partition("#")
                value = val_part.strip()
                vendor = comment.strip()
            else:
                value = rest

    if not vendor and key:
        vendor = infer_vendor_name(key)

    return key, value, vendor


def parse_env(text: str) -> List[EnvLine]:
    """把 `.env` 文本解析为有序条目列表。"""
    entries: List[EnvLine] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "":
            entries.append(EnvLine(kind="blank", raw=line))
        elif stripped.startswith("#"):
            entries.append(EnvLine(kind="comment", raw=line))
        elif "=" in line:
            key, value, vendor = _extract_kv_and_vendor(line)
            entries.append(EnvLine(kind="kv", key=key, value=value, vendor=vendor))
        else:
            # 形如环境变量赋值之外的行：原样保留，避免丢信息
            entries.append(EnvLine(kind="comment", raw=line))
    return entries


def serialize_env(entries: List[EnvLine]) -> str:
    """把条目列表还原为 `.env` 文本（跳过被删除的条目）。

    对 key, value, vendor 中的 \\r, \\n 进行清洗与转义，杜绝 CRLF 注入。
    """
    lines: List[str] = []
    for entry in entries:
        if entry.kind == "deleted":
            continue
        if entry.kind == "kv":
            # 清洗 key 中的换行符
            clean_key = entry.key.replace("\r", "").replace("\n", "").strip()
            if not clean_key:
                continue

            # 清洗与转义 value 中的 \r 与 \n
            val = entry.value.replace("\r", "\\r").replace("\n", "\\n")
            # 值含空格 / 特殊字符 / 转义符号或为空时加双引号
            if val == "" or any(c in val for c in " #\"'=\t\\"):
                val = '"' + val.replace('"', '\\"') + '"'
            line = f"{clean_key}={val}"

            # 清洗 vendor 中的换行符
            clean_vendor = " ".join(entry.vendor.replace("\r", " ").replace("\n", " ").split())
            if clean_vendor:
                line += f" # {clean_vendor}"
            lines.append(line)
        else:
            lines.append(entry.raw)
    text = "\n".join(lines)
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def load_env_file(path: Path) -> Tuple[List[EnvLine], bool]:
    """读取 `.env` 文件，返回 (条目列表, 文件是否存在)。

    文件不存在时返回空列表与 ``False``（对话框据此提示「文件不存在，保存后将新建」）。
    """
    if path.exists():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            text = path.read_text(encoding="utf-8", errors="replace")
        return parse_env(text), True
    return [], False


def save_env_file(path: Path, entries: List[EnvLine]) -> None:
    """原子写入 `.env`：使用随机临时文件（mkstemp）防并发碰撞，并在 POSIX 下赋予 0o600 权限。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = serialize_env(entries).encode("utf-8")

    fd, tmp_path_str = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        if os.name == "posix":
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass

        tmp_path.replace(path)

        if os.name == "posix":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
