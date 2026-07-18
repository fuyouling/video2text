"""模型下载工具 - 支持绿色版首次运行自动下载模型"""

import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.utils.logger import get_logger
from src.utils.paths import get_base_dir

logger = get_logger("video2text")

LARGE_V3_CORE_FILES = [
    "config.json",
    "model.bin",
    "preprocessor_config.json",
    "tokenizer.json",
    "vocabulary.json",
]

LARGE_V3_OPTIONAL_FILES = [
    ".gitattributes",
    "README.md",
]

LARGE_V3_ALL_FILES = LARGE_V3_CORE_FILES + LARGE_V3_OPTIONAL_FILES

LARGE_V3_BASE = "https://huggingface.co/Systran/faster-whisper-large-v3/resolve/main"

TURBO_CT2_BASE = "https://huggingface.co/deepdml/faster-whisper-large-v3-turbo-ct2/resolve/main"

DEFAULT_MODEL_NAME = "faster-whisper-large-v3-turbo-ct2"

MODEL_CONFIG = {
    "large-v3": {
        "base_url": LARGE_V3_BASE,
        "models_dir": "models/large-v3",
        "core_files": LARGE_V3_CORE_FILES,
        "optional_files": LARGE_V3_OPTIONAL_FILES,
        "all_files": LARGE_V3_ALL_FILES,
    },
    "faster-whisper-large-v3-turbo-ct2": {
        "base_url": TURBO_CT2_BASE,
        "models_dir": "models/faster-whisper-large-v3-turbo-ct2",
        "core_files": LARGE_V3_CORE_FILES,
        "optional_files": LARGE_V3_OPTIONAL_FILES,
        "all_files": LARGE_V3_ALL_FILES,
    },
}


class ModelDownloader:
    """模型下载器 —— 从 HuggingFace 下载 faster-whisper 模型文件，支持代理与自动重试。"""

    def __init__(self, model_name: str = DEFAULT_MODEL_NAME):
        self.model_name = model_name
        self.model_config = MODEL_CONFIG.get(model_name)

        if not self.model_config:
            raise ValueError(f"未知的模型: {model_name}")

        self._base_dir = get_base_dir()

        self.models_dir = self._base_dir / self.model_config["models_dir"]
        self.model_path = self.models_dir / "model.bin"
        self._session = None
        self.download_cancelled = False

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def is_model_exists(self) -> bool:
        for f in self.model_config["core_files"]:
            fp = self.models_dir / f
            if not fp.exists() or fp.stat().st_size == 0:
                return False
        return True

    def get_model_size(self) -> str:
        total = 0
        for f in self.model_config["all_files"]:
            fp = self.models_dir / f
            if fp.exists():
                total += fp.stat().st_size
        if total == 0:
            return "未知"
        for unit in ["B", "KB", "MB", "GB"]:
            if total < 1024.0:
                return f"{total:.2f} {unit}"
            total /= 1024.0
        return f"{total:.2f} TB"

    def _get_proxy(self) -> str:
        try:
            from src.config.settings import Settings

            s = Settings()
            return s.get("app.proxy", "")
        except Exception:
            return ""

    def _get_session(self, proxy: str = ""):
        import requests
        from requests.adapters import HTTPAdapter

        if self._session is None:
            self._session = requests.Session()
            # 探测连通性时禁用自动重试，避免超时时间被 urllib3 放大数倍
            # （默认 Retry 会让 timeout=3 实际阻塞 ~10s，进而导致关闭窗口时线程仍
            # running 触发 "QThread: Destroyed while thread is still running"）。
            adapter = HTTPAdapter(max_retries=0, pool_connections=1, pool_maxsize=1)
            self._session.mount("http://", adapter)
            self._session.mount("https://", adapter)
        # 忽略 HTTP_PROXY/HTTPS_PROXY 等环境变量代理，确保 config.ini 的
        # [app] proxy 是唯一代理来源，避免失效的系统代理导致连不上/卡死。
        self._session.trust_env = False
        if proxy:
            self._session.proxies = {"http": proxy, "https": proxy}
        else:
            self._session.proxies = {}
        return self._session

    def _check_hf_accessible(self, proxy: str = "") -> bool:
        try:
            session = self._get_session(proxy)
            url = "https://huggingface.co"
            # 使用极短超时 + 禁用重试，仅用于快速探测连通性。
            # timeout=(connect, read) —— 连接 2s、读取 1s，任何一端超时都立即
            # 失败，避免卡住后台线程导致 closeEvent 等待窗口关闭。
            r = session.get(
                url,
                timeout=(2, 1),
                stream=True,
                headers={"Cache-Control": "no-store"},
            )
            try:
                return r.status_code < 400
            finally:
                r.close()
        except Exception:
            return False

    def _download_file(
        self,
        url: str,
        dest: Path,
        proxy: str = "",
        file_progress_callback=None,
        max_retries: int = 5,
    ) -> bool:
        import requests

        session = self._get_session(proxy)
        connect_timeout = 30
        read_timeout = 300

        for attempt in range(1, max_retries + 1):
            response = None
            try:
                existing_size = dest.stat().st_size if dest.exists() else 0
                headers = {}
                if existing_size > 0:
                    headers["Range"] = f"bytes={existing_size}-"
                    logger.info("  │  ├─ 续传: 已有 %s", self._fmt_size(existing_size))

                response = session.get(
                    url,
                    stream=True,
                    timeout=(connect_timeout, read_timeout),
                    headers=headers,
                )

                if response.status_code == 416:
                    logger.info("  │  └─ 已完整，跳过")
                    return True

                is_resume = response.status_code == 206
                if not is_resume and existing_size > 0:
                    existing_size = 0

                response.raise_for_status()

                if is_resume:
                    total_size = existing_size + int(
                        response.headers.get("content-length", 0)
                    )
                else:
                    total_size = int(response.headers.get("content-length", 0))

                downloaded = existing_size
                last_reported = downloaded
                throttle_step = max(1024 * 1024, 8192)

                mode = "ab" if is_resume else "wb"
                with open(dest, mode) as f:
                    for chunk in response.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if (
                                file_progress_callback
                                and total_size > 0
                                and downloaded - last_reported >= throttle_step
                            ):
                                file_progress_callback(downloaded, total_size)
                                last_reported = downloaded

                if (
                    file_progress_callback
                    and total_size > 0
                    and last_reported < downloaded
                ):
                    file_progress_callback(downloaded, total_size)

                if total_size > 0 and downloaded != total_size:
                    logger.warning(
                        "  │  └─ 大小不匹配: 期望 %s, 实际 %s",
                        self._fmt_size(total_size),
                        self._fmt_size(downloaded),
                    )
                    continue

                return True

            except requests.exceptions.Timeout:
                logger.warning("  │  ├─ 超时 (%d/%d)", attempt, max_retries)
            except requests.exceptions.ConnectionError:
                logger.warning("  │  ├─ 连接失败 (%d/%d)", attempt, max_retries)
            except Exception as e:
                logger.warning("  │  ├─ 失败 (%d/%d): %s", attempt, max_retries, e)
            finally:
                if response is not None:
                    response.close()

            if attempt < max_retries:
                wait = min(2**attempt, 30)
                logger.info("  │  ├─ %d 秒后重试...", wait)
                time.sleep(wait)

        logger.error("  │  └─ 下载失败 (已重试 %d 次)", max_retries)
        return False

    @staticmethod
    def _fmt_size(size: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} TB"

    def download_model(self, progress_callback=None, confirm_callback=None) -> bool:
        base_url = self.model_config["base_url"]
        files = self.model_config["all_files"]
        core_files = set(self.model_config["core_files"])
        self.models_dir.mkdir(parents=True, exist_ok=True)

        if confirm_callback and not confirm_callback():
            #logger.info("用户取消了模型下载")
            self.download_cancelled = True
            return False

        proxy = self._get_proxy()

        logger.info("模型下载 (%s)", self.model_name)
        logger.info("  ├─ 检查网络连接")

        if self._check_hf_accessible():
            logger.info("  │  └─ 直连 HuggingFace ... OK")
            proxy = ""
        elif proxy:
            logger.info("  │  ├─ 直连失败，尝试代理 %s", proxy)
            if self._check_hf_accessible(proxy=proxy):
                logger.info("  │  │  └─ 代理连接 ... OK")
            else:
                logger.error("  │  │  └─ 代理连接失败")
                logger.error("  │  │     请检查 config.ini 的 [app] proxy 是否可用")
                return False
        else:
            logger.error("  │  └─ 无法访问 HuggingFace（直连不通且未配置代理）")
            logger.error("  │     请在 config.ini 的 [app] 节设置 proxy 后重试")
            logger.error("  │     或从网盘下载已打包好的模型文件放入 models 目录")
            # 清理可能残留的不完整核心文件，避免转写时 'model.bin is incomplete'
            self._clean_incomplete_core_files()
            return False

        logger.info("  └─ 下载文件 (%d 个 -> %s)", len(files), self.models_dir)

        total_downloaded = 0

        def _make_file_cb(filename: str, base_downloaded: int):
            def _file_cb(downloaded: int, file_total: int):
                if progress_callback:
                    progress_callback(base_downloaded + downloaded, -1)

            return _file_cb

        failed: list[str] = []
        for i, filename in enumerate(files):
            is_last = i == len(files) - 1
            connector = "  └─" if is_last else "  ├─"
            branch = "     " if is_last else "  │  "

            url = f"{base_url}/{filename}?download=true"
            dest = self.models_dir / filename

            if dest.exists() and dest.stat().st_size > 0:
                logger.info(
                    "%s %s (%s, 已有)",
                    connector,
                    filename,
                    self._fmt_size(dest.stat().st_size),
                )
            else:
                logger.info("%s %s", connector, filename)

            ok = self._download_file(
                url,
                dest,
                proxy=proxy,
                file_progress_callback=_make_file_cb(filename, total_downloaded),
            )
            if ok:
                if dest.exists():
                    total_downloaded += dest.stat().st_size
            else:
                failed.append(filename)

        if failed:
            core_failed = [f for f in failed if f in core_files]
            opt_failed = [f for f in failed if f not in core_files]
            if opt_failed:
                logger.warning(
                    "可选文件下载失败（不影响使用）: %s", ", ".join(opt_failed)
                )
            if core_failed:
                logger.error("核心文件下载失败: %s", ", ".join(core_failed))
                # 清理可能残留的不完整核心文件，避免转写时 'model.bin is incomplete'
                self._clean_incomplete_core_files()
                self.close()
                return False

        self.close()
        logger.info("下载完成")
        return True

    def _clean_incomplete_core_files(self) -> None:
        """删除不完整的残留核心文件（size==0 或部分下载）。

        仅删除明显损坏的文件，保留已完整下载的文件，避免下次启动时
        因残留损坏文件导致转写阶段 'model.bin is incomplete' 崩溃。
        """
        for f in self.model_config["core_files"]:
            fp = self.models_dir / f
            try:
                if fp.exists() and fp.stat().st_size == 0:
                    fp.unlink()
                    logger.warning("  │  └─ 已清理空文件: %s", f)
            except OSError as e:
                logger.warning("  │  └─ 清理失败 %s: %s", f, e)

    def ensure_model_available(
        self, progress_callback=None, confirm_callback=None
    ) -> bool:
        if self.is_model_exists():
            logger.info("模型已就绪: %s", self.models_dir)
            return True

        logger.info("模型文件不完整，开始下载...")
        return self.download_model(progress_callback, confirm_callback)

    @staticmethod
    def get_model_path(model_name: str = DEFAULT_MODEL_NAME) -> Path:
        downloader = ModelDownloader(model_name)
        return downloader.model_path


def check_models_integrity(
    settings,
    progress_callback=None,
    confirm_callback=None,
) -> bool:
    """启动时一次性检查模型文件完整性。

    仅当 config.ini 的 [app]is_check_model_file 为 true（或缺失）时执行。
    检查 [transcription] 中配置的模型：
    - 文件已完整：标记通过，将 is_check_model_file 置为 false 并保存。
    - 文件不完整：尝试下载，下载成功同样置为 false 并保存。
    - 文件不完整且下载失败（如 HuggingFace 不可直连）：同样置为 false 并保存，
      避免每次启动都卡在「检查网络连接」反复失败；用户可通过网盘获取模型后
      将 is_check_model_file 重新设为 true 再检测。

    检查通过（含文件完整或被成功补齐）返回 True，否则返回 False。

    注意：配置保存（settings.save()）由调用方负责。本函数只设置内存中的
    配置值（settings.set），不写磁盘 —— 避免后台线程写配置与主线程读取
    config.ini 产生线程安全问题（configparser 的 write 不是线程安全的）。
    GUI 调用方应在主线程的 finished 信号槽中执行 save()。
    """
    do_check = settings.get_bool("app.is_check_model_file", True)
    if not do_check:
        logger.info("模型完整性检测: 已跳过 (is_check_model_file=false)")
        return True

    logger.info("模型完整性检测: 启动检测")

    model_keys = ["transcription.model_path"]
    all_ok = True

    for key in model_keys:
        model_name = settings.get(key, DEFAULT_MODEL_NAME)
        if model_name not in MODEL_CONFIG:
            logger.warning("模型完整性检测: 跳过未知模型 (%s=%s)", key, model_name)
            continue

        try:
            downloader = ModelDownloader(model_name)
        except ValueError:
            continue

        if downloader.is_model_exists():
            logger.info("模型完整性检测: ✓ 完整 (%s)", model_name)
            continue

        logger.info("模型完整性检测: 文件不完整 (%s)，尝试下载...", model_name)
        ok = downloader.download_model(progress_callback, confirm_callback)
        if ok and downloader.is_model_exists():
            logger.info("模型完整性检测: ✓ 已补齐 (%s)", model_name)
        else:
            all_ok = False
            logger.error("模型完整性检测: ✗ 失败 (%s)", model_name)

    # 无论本次检测结果如何，只标记内存中的配置值，不写磁盘。
    # 配置保存由调用方在主线程中执行，避免 configparser 跨线程写
    # 导致主线程读取到损坏的配置数据进而引发崩溃。
    try:
        settings.set("app.is_check_model_file", "false")
        if all_ok:
            logger.info("模型完整性检测: ✓ 通过，已关闭后续检测")
        else:
            logger.info(
                "模型完整性检测: 未通过（多为网络不可达），已关闭后续自动检测。"
                "主界面不受影响，可正常使用；请配置代理或从网盘补齐模型后重开。"
            )
    except Exception as e:
        logger.warning("模型完整性检测: 更新配置失败 (不影响使用): %s", e)

    return all_ok


class StartupModelCheckWorker(QObject):
    """后台线程执行模型下载（仅下载阶段），不包含确认对话框逻辑。

    确认对话框已在主线程中提前完成。worker 负责下载并将进度通过信号上报主线程。
    """

    finished = Signal(bool)
    progress_updated = Signal(int, int)

    def __init__(self, settings) -> None:
        super().__init__()
        self._settings = settings
        self._cancelled = False

    def cancel(self) -> None:
        """取消当前下载（线程安全）。"""
        self._cancelled = True

    def run(self) -> None:
        """在后台线程执行模型下载。"""
        ok = False
        try:
            if not self._cancelled:
                ok = check_models_integrity(
                    self._settings,
                    progress_callback=self._make_progress_cb(),
                )
        except Exception:  # noqa: BLE001
            logger.exception("模型完整性检测异常")
            ok = False
        self.finished.emit(ok)

    def _make_progress_cb(self):
        def _progress(downloaded: int, total: int) -> None:
            self.progress_updated.emit(downloaded, total)

        return _progress
