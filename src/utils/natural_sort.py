"""自然排序工具，复刻 Windows 资源管理器「名称」列的默认排序。

资源管理器按名称排序时：大小写不敏感；连续数字按整数数值比较
（如 ``file2 < file10 < file100``），而非逐字符字典序（``file10 < file2``）。
Windows 上优先调用系统 ``StrCmpLogicalW`` 以 100% 还原资源管理器顺序，
其它平台回退到纯 Python 的自然排序键，保证各列表顺序彼此一致。
"""

import re
import sys

_DIGIT_RE = re.compile(r"(\d+)")

try:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        _shlwapi = ctypes.windll.shlwapi  # type: ignore[attr-defined]
        _shlwapi.StrCmpLogicalW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        _shlwapi.StrCmpLogicalW.restype = ctypes.c_int
        _HAS_WIN_API = True
    else:
        _HAS_WIN_API = False
except Exception:  # pragma: no cover - 调用失败则回退纯 Python
    _HAS_WIN_API = False


def natural_sort_key(name: str) -> list:
    """供 ``sorted(key=...)`` 使用的排序键：大小写不敏感，数字按数值比较。"""
    key: list = []
    for part in _DIGIT_RE.split(name.lower()):
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return key


if _HAS_WIN_API:
    import functools

    def _win_cmp(a: str, b: str) -> int:
        return _shlwapi.StrCmpLogicalW(a, b)

    def natural_sorted(iterable, key=lambda x: x):
        """按资源管理器顺序排序（Windows 上用 ``StrCmpLogicalW`` 精确还原）。"""
        return sorted(
            iterable,
            key=functools.cmp_to_key(lambda a, b: _win_cmp(key(a), key(b))),
        )
else:
    def natural_sorted(iterable, key=lambda x: x):
        """按资源管理器顺序排序（纯 Python 自然排序回退）。"""
        return sorted(iterable, key=lambda x: natural_sort_key(key(x)))
