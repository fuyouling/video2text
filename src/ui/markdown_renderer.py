"""Markdown 渲染器 —— 从 ResultViewerWindow 提取的 Markdown→HTML 转换逻辑"""

import re
from typing import Optional

try:
    import markdown

    MARKDOWN_AVAILABLE = True
except ImportError:
    MARKDOWN_AVAILABLE = False

try:
    import pygments  # noqa: F401

    PYGMENTS_AVAILABLE = True
except ImportError:
    PYGMENTS_AVAILABLE = False


class MarkdownRenderer:
    """带缓存的 Markdown 渲染器，输出 QTextBrowser 兼容的 HTML。"""

    def __init__(self) -> None:
        self._cached_md_text: str = ""
        self._cached_html: str = ""

    def render(
        self,
        markdown_text: str,
        font_size: int = 14,
        theme_css: str = "",
        border_color: str = "#ccc",
        secondary_bg: str = "#f5f5f5",
    ) -> Optional[str]:
        """将 Markdown 文本渲染为带样式的 HTML。

        Returns:
            完整 HTML 字符串，若 markdown 库不可用则返回 None。
        """
        if not MARKDOWN_AVAILABLE:
            return None

        if markdown_text != self._cached_md_text:
            safe_text = self._sanitize_html(markdown_text)
            safe_text = self.preprocess_md_tables(safe_text)
            safe_text = self.preprocess_md_nested_lists(safe_text)

            try:
                extensions = ["tables", "fenced_code", "extra", "sane_lists"]
                if PYGMENTS_AVAILABLE:
                    extensions.append("codehilite")

                self._cached_html = markdown.markdown(safe_text, extensions=extensions)
                self._cached_md_text = markdown_text
            except Exception:
                return None

        final_html = self.fix_tables_for_qt(
            self._cached_html, border_color, secondary_bg
        )
        final_html = self.fix_nested_lists_for_qt(final_html)

        return f"""
        <style>
            {theme_css}
        </style>
        {final_html}
        """

    def invalidate_cache(self) -> None:
        """清除渲染缓存，下次调用 render() 时重新转换。"""
        self._cached_md_text = ""
        self._cached_html = ""

    @staticmethod
    def _sanitize_html(text: str) -> str:
        """移除 Markdown 源文本中可能的危险 HTML 标签（script/style/iframe 等）。"""
        safe_text = re.sub(
            r"<\s*(script|style|iframe|object|embed|form|input|textarea|button)[^>]*>.*?<\s*/\s*\1\s*>",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        safe_text = re.sub(
            r"<\s*/?\s*(script|style|iframe|object|embed|form|input|textarea|button)[^>]*>",
            "",
            safe_text,
            flags=re.IGNORECASE,
        )
        return safe_text

    @staticmethod
    def fix_tables_for_qt(
        html: str, border_color: str = "#ccc", secondary_bg: str = "#f5f5f5"
    ) -> str:
        html = re.sub(
            r"<table>",
            '<table border="1" cellspacing="0" cellpadding="6" '
            'style="border-collapse:collapse; width:100%;">',
            html,
        )
        html = re.sub(
            r"<th>",
            f'<th style="border:1px solid {border_color}; padding:6px 10px; '
            f'background:{secondary_bg}; font-weight:bold; text-align:left;">',
            html,
        )
        html = re.sub(
            r"<td>",
            f'<td style="border:1px solid {border_color}; padding:6px 10px;">',
            html,
        )
        return html

    @staticmethod
    def fix_nested_lists_for_qt(html: str) -> str:
        html = re.sub(
            r"(<li[^>]*>(?:(?!</li>).)*?)<(ul|ol)([\s>])",
            lambda m: (
                f'{m.group(1)}<{m.group(2)} style="margin-left:1.5em;"{m.group(3)}'
            ),
            html,
            flags=re.DOTALL,
        )
        return html

    @staticmethod
    def preprocess_md_nested_lists(text: str) -> str:
        lines = text.split("\n")
        result: list[str] = []
        in_code = False

        for line in lines:
            if line.lstrip().startswith("```"):
                in_code = not in_code
                result.append(line)
                continue

            if in_code:
                result.append(line)
                continue

            if re.match(r"^( +)([-*+]|\d+[.)])\s", line):
                result.append("  " + line)
            else:
                result.append(line)

        return "\n".join(result)

    @staticmethod
    def preprocess_md_tables(text: str) -> str:
        lines = text.split("\n")
        result: list[str] = []
        i = 0
        in_code = False

        while i < len(lines):
            line = lines[i]

            if line.lstrip().startswith("```"):
                in_code = not in_code
                result.append(line)
                i += 1
                continue

            if in_code:
                result.append(line)
                i += 1
                continue

            stripped = line.lstrip()
            indent = len(line) - len(stripped)

            if indent > 0 and stripped.startswith("|") and stripped.count("|") >= 2:
                table_block: list[str] = []
                j = i
                while j < len(lines):
                    s = lines[j].lstrip()
                    if s.startswith("|") and s.count("|") >= 2:
                        table_block.append(s)
                        j += 1
                    else:
                        break

                if len(table_block) >= 3 and re.match(
                    r"^\|[\s\-:|]+\|$", table_block[1].strip()
                ):
                    if result and result[-1].strip():
                        result.append("")
                    result.extend(table_block)
                    i = j
                    continue

                result.append(line)
                i += 1
            else:
                result.append(line)
                i += 1

        return "\n".join(result)
