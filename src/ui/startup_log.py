"""启动依赖检测日志美化工具。

把分散在 gui / model_downloader / dll_downloader / settings 中的
「启动依赖检测」系列日志，统一成一致、对齐、带分组的树状输出。

设计目标：
- 输出集中在 ``video2text.dependency`` 子 logger，方便未来单独控制格式/级别。
- 文案前缀统一、缩进对齐：分组标题用 ``▸``，子项用 ``  ├─ `` / ``  └─ ``，
  与日志面板（log_panel.py 的树状正则）直接兼容，可正确着色。
- 不改动底层下载逻辑，只替换「提示性日志」的措辞与排版。

用法::

    from src.ui.startup_log import dlog
    dlog.phase_startup_check()
    dlog.model_missing(model_name)
"""

from __future__ import annotations

from src.utils.logger import setup_dependency_logger

_logger = setup_dependency_logger("video2text.dependency")


class _DependencyLog:
    """启动依赖检测日志的语义化封装。"""

    # ── 阶段分组（顶部大标题） ──────────────────────────
    @staticmethod
    def phase_startup_check() -> None:
        _logger.info("▸ 启动依赖检测：开始")

    @staticmethod
    def phase_download_thread(
        download_model: bool, download_dll: bool, keep_archive: bool
    ) -> None:
        _logger.info(
            "▸ 启动依赖下载：model=%s, dll=%s, keep_archive=%s",
            download_model, download_dll, keep_archive,
        )

    @staticmethod
    def phase_done(ok: bool) -> None:
        if ok:
            _logger.info("▸ 依赖检测 ✓ 通过，应用就绪")
        else:
            _logger.warning("▸ 依赖检测 ✗ 未通过")

    @staticmethod
    def config_saved() -> None:
        _logger.info("▸ 依赖检测：配置已保存")

    # ── 主线程检测结论（树状） ──────────────────────────
    @staticmethod
    def model_complete(model_name: str) -> None:
        _logger.info("  ├─ 模型 ✓ 已完整 (%s)", model_name)

    @staticmethod
    def model_missing(model_name: str) -> None:
        _logger.info("  ├─ 模型 ✗ 不完整 (%s)", model_name)

    @staticmethod
    def dll_complete() -> None:
        _logger.info("  ├─ DLL 依赖 ✓ 已完整")

    @staticmethod
    def dll_missing() -> None:
        _logger.info("  ├─ DLL 依赖 ✗ 不完整")

    @staticmethod
    def all_ready() -> None:
        _logger.info("  └─ 所有依赖已就绪，无需下载")

    @staticmethod
    def user_cancelled() -> None:
        _logger.info("  └─ 用户取消下载，已关闭后续自动检测")

    @staticmethod
    def user_confirmed() -> None:
        _logger.info("  └─ 用户确认下载，启动后台线程…")

    # ── 模型完整性检测 ──────────────────────────────────
    @staticmethod
    def model_check_start() -> None:
        _logger.info("▸ 模型完整性检测：启动")

    @staticmethod
    def model_check_complete(model_name: str) -> None:
        _logger.info("▸ 模型完整性检测 ✓ 已补齐 (%s)", model_name)

    @staticmethod
    def model_check_passed() -> None:
        _logger.info("▸ 模型完整性检测 ✓ 通过，已关闭后续检测")

    @staticmethod
    def model_check_skipped() -> None:
        _logger.info("▸ 模型完整性检测：已跳过 (is_check_model_file=false)")

    # ── DLL 依赖检测 ────────────────────────────────────
    @staticmethod
    def dll_use_proxy(proxy: str, source: str) -> None:
        _logger.info("▸ DLL 依赖：使用代理 %s (%s)", proxy, source)

    @staticmethod
    def dll_direct() -> None:
        _logger.info("▸ DLL 依赖：直连下载")

    @staticmethod
    def dll_start() -> None:
        _logger.info("▸ DLL 依赖：开始检测与补齐（下载 → 解压）")

    @staticmethod
    def dll_ready(count: int) -> None:
        _logger.info("▸ DLL 依赖 ✓ 全部就绪（共 %d 个 DLL）", count)

    @staticmethod
    def dll_already_complete() -> None:
        _logger.info("▸ DLL 依赖 ✓ 已完整，无需下载")


# 单例：供各模块 ``from src.ui.startup_log import dlog`` 使用
dlog = _DependencyLog()
