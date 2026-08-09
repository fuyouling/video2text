"""API Key 存储工具 —— 解析 / 序列化 / 读写 .env 文件（与 UI 完全解耦）。

设计目标：
- 纯 Python、不依赖 Qt，便于独立单测与复用（CLI / 将来迁移存储格式互不牵连）。
- 保留 `.env` 中的注释与空行顺序，仅改写 / 追加 ``KEY=VALUE`` 行。
- 保存采用「先写临时文件再 ``Path.replace``」的原子写入，避免写到一半崩溃损坏文件。

解析约定：
- ``# ...`` 行             -> 注释，原样保留
- 空行                    -> 空行，原样保留
- 含 ``=`` 的行            -> 键值对（自动去掉值两侧包裹的引号）
- ``export FOO=bar``       -> 去掉前缀 ``export `` 后按键值对处理
- 其它无法识别的行          -> 按注释原样保留
"""

from __future__ import annotations

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
            body = line
            # 兼容 `export FOO=bar` 写法：去掉前缀 `export `（保留可能的空格）
            if stripped.startswith("export"):
                body = stripped[len("export"):].lstrip()
            key, _, value = body.partition("=")
            key = key.strip()
            value = value.strip()
            # 去掉值两侧包裹的引号，序列化时按需再加回
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            entries.append(EnvLine(kind="kv", key=key, value=value))
        else:
            # 形如环境变量赋值之外的行：原样保留，避免丢信息
            entries.append(EnvLine(kind="comment", raw=line))
    return entries


def serialize_env(entries: List[EnvLine]) -> str:
    """把条目列表还原为 `.env` 文本（跳过被删除的条目）。"""
    lines: List[str] = []
    for entry in entries:
        if entry.kind == "deleted":
            continue
        if entry.kind == "kv":
            val = entry.value
            # 值含空格 / 特殊字符或为空时加双引号，避免歧义
            if val == "" or any(c in val for c in " #\"'="):
                val = '"' + val.replace('"', '\\"') + '"'
            lines.append(f"{entry.key}={val}")
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
    """原子写入 `.env`：先写 ``<file>.tmp`` 再 ``replace``，避免半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(serialize_env(entries), encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
