"""Video2Text 多语言国际化核心模块。

设计要点：
- 文案以 JSON 形式存放于 `locales/<lang>.json`，点分键命名空间。
- 语言注册表 `languages.json` 是唯一需要改动的「新增语言」入口。
- 缺失键按注册表 fallback 链逐级回退，最终回退到 zh-CN（基准语言）。
- 支持复数分段（如俄文三态），以及 {name}/{path}/{n} 等占位符。
- 同时可选加载 Qt 自带的 qtbase_<lang>.qm 以本地化原生控件。
"""

import json
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QLocale, QTranslator
from PySide6.QtWidgets import QApplication


class I18N:
    """国际化单例：负责加载、缓存、查找与兜底。"""

    _instance: Optional["I18N"] = None

    def __new__(cls) -> "I18N":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._lang = "zh-CN"
        self._registry: dict[str, dict] = {}
        self._catalogs: dict[str, dict] = {}
        self._load_registry()
        self._initialized = True

    # ── 路径 ────────────────────────────────────────────────

    @staticmethod
    def _base_dir() -> Path:
        """i18n 资源根目录：兼容开发态与 frozen（便携版）两种模式。

        在 PyInstaller 打包（frozen）环境下：
          - 资源文件通过 spec 的 datas 机制收集，存放在 sys._MEIPASS 下。
          - sys.executable 是 exe 本身（在 _internal 的父目录），
            而 sys._MEIPASS 指向 _internal/，这才是 datas 的根目录。
        """
        if getattr(sys, "frozen", False):
            return Path(sys._MEIPASS) / "i18n"
        return Path(__file__).resolve().parent

    def _load_registry(self) -> None:
        path = self._base_dir() / "languages.json"
        try:
            self._registry = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self._registry = {}

    # ── 语言注册表查询 ─────────────────────────────────────

    def available_languages(self) -> list[str]:
        """返回所有已启用语言代码（按注册表顺序）。"""
        return [
            code
            for code, meta in self._registry.items()
            if meta.get("enabled", True)
        ]

    def language_meta(self, code: str) -> dict:
        return self._registry.get(code, {})

    # ── 加载 ────────────────────────────────────────────────

    def load(self, lang: str) -> None:
        """加载 locales/<lang>.json 到内存（带缓存）。"""
        if lang in self._catalogs:
            return
        path = self._base_dir() / "locales" / f"{lang}.json"
        try:
            self._catalogs[lang] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self._catalogs[lang] = {}

    def set_lang(self, lang: str) -> None:
        if lang not in self._registry:
            lang = "zh-CN"
        self._lang = lang
        self.load(lang)

    @property
    def lang(self) -> str:
        return self._lang

    # ── 查找 ────────────────────────────────────────────────

    def _lookup_raw(self, lang: str, key: str) -> Optional[str]:
        self.load(lang)
        node = self._catalogs.get(lang, {})
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node if isinstance(node, str) else None

    def _fallback_lookup(self, key: str) -> Optional[str]:
        """按注册表 fallback 链逐级回退，最终到 zh-CN。"""
        seen: set[str] = set()
        lang = self._lang
        while lang and lang not in seen:
            seen.add(lang)
            val = self._lookup_raw(lang, key)
            if val is not None:
                return val
            lang = self._registry.get(lang, {}).get("fallback")
        if "zh-CN" not in seen:
            return self._lookup_raw("zh-CN", key)
        return None

    # ── 复数规则 ────────────────────────────────────────────

    def _plural_index(self, count: int) -> int:
        rule = self._registry.get(self._lang, {}).get("plural", "simple")
        if rule == "slavic":
            mod10 = count % 10
            mod100 = count % 100
            if mod10 == 1 and mod100 != 11:
                return 0
            if 2 <= mod10 <= 4 and not (12 <= mod100 <= 14):
                return 1
            return 2
        # simple（en/zh/ja/ko/fr/es/de …）：单数为 0，其余为 1
        return 0 if count == 1 else 1

    # ── 主入口 ───────────────────────────────────────────────

    def t(self, msg_key: str, count=None, **kwargs) -> str:
        text = self._fallback_lookup(msg_key)
        if text is None:
            return msg_key

        if count is not None and "|" in text:
            segments = text.split("|")
            idx = self._plural_index(count)
            text = segments[idx] if idx < len(segments) else segments[-1]

        fmt: dict = {}
        if count is not None:
            fmt["count"] = count
            fmt["n"] = count
        fmt.update(kwargs)
        if fmt:
            try:
                return text.format(**fmt)
            except (KeyError, IndexError, ValueError):
                return text
        return text


# ── 模块级便捷接口 ───────────────────────────────────────────

_i18n = I18N()


def t(msg_key: str, count=None, **kwargs) -> str:
    return _i18n.t(msg_key, count=count, **kwargs)


def set_lang(lang: str) -> None:
    _i18n.set_lang(lang)


def get_lang() -> str:
    return _i18n.lang


def available_languages() -> list[str]:
    return _i18n.available_languages()


def language_meta(code: str) -> dict:
    return _i18n.language_meta(code)


def resolve_language(explicit: Optional[str] = None) -> str:
    """按优先级解析语言：explicit → 环境变量 → 配置 → 系统区域 → zh-CN。

    explicit 一般来自 CLI 的 --lang 或 VIDEO2TEXT_LANG 已在调用前并入。
    """
    import os

    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("VIDEO2TEXT_LANG")
    if env:
        candidates.append(env)
    try:
        from src.config.settings import Settings

        cfg = Settings().get("app.ui_language", "zh-CN")
        if cfg and cfg != "auto":
            candidates.append(cfg)
    except Exception:
        pass
    try:
        # Windows: 优先用 Win32 API 获取真实的显示语言（而非系统区域格式）
        if sys.platform == "win32":
            import ctypes as _ctypes
            import locale as _locale

            win_lang = _locale.windows_locale.get(
                _ctypes.windll.kernel32.GetUserDefaultUILanguage(), ""
            )
            if win_lang:
                candidates.append(win_lang)
    except Exception:
        pass
    try:
        sys_name = QLocale.system().name()  # "en_US" / "zh_CN"
        candidates.append(sys_name)
        candidates.append(sys_name.split("_")[0])
    except Exception:
        pass
    candidates.append("zh-CN")

    codes = set(_i18n.available_languages()) | {"zh-CN"}
    norm_map = {c.replace("-", "_"): c for c in codes}
    base_map: dict[str, str] = {}
    for c in codes:
        base_map.setdefault(c.split("-")[0], c)

    for cand in candidates:
        cand_norm = cand.replace("-", "_")
        if cand_norm in norm_map:
            return norm_map[cand_norm]
        base = cand_norm.split("_")[0]
        if base in base_map:
            return base_map[base]
    return "zh-CN"


_QT_TRANSLATORS: list[QTranslator] = []


def install_qt_translator(app: QApplication, lang: Optional[str] = None) -> None:
    """加载 Qt 自带的 qtbase_<lang>.qm，本地化 QDialogButtonBox / QFileDialog 等。

    若对应 .qm 文件不存在（PySide6 未必打包），则静默跳过，不影响自有文案。
    """
    lang = lang or get_lang()
    qt_code = language_meta(lang).get("qt")
    if not qt_code:
        return
    translator = QTranslator()
    try:
        from PySide6.QtCore import QLibraryInfo

        tr_path = QLibraryInfo.path(
            QLibraryInfo.LibraryPath.TranslationsPath
        )
        if translator.load(f"qtbase_{qt_code}", tr_path):
            app.installTranslator(translator)
            _QT_TRANSLATORS.append(translator)
    except Exception:
        pass
