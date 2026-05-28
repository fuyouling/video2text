"""模型下载工具 - 支持绿色版首次运行自动下载模型"""

import sys
import time
from pathlib import Path
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

MODEL_CONFIG = {
    "large-v3": {
        "base_url": LARGE_V3_BASE,
        "models_dir": "models/large-v3",
        "core_files": LARGE_V3_CORE_FILES,
        "optional_files": LARGE_V3_OPTIONAL_FILES,
        "all_files": LARGE_V3_ALL_FILES,
    },
}


class ModelDownloader:
    """模型下载器 —— 从 HuggingFace 下载 faster-whisper 模型文件，支持代理与自动重试。"""

    def __init__(self, model_name: str = "large-v3"):
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
            if not (self.models_dir / f).exists():
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
            return s.get("network.proxy", "")
        except Exception:
            return ""

    def _get_session(self, proxy: str = ""):
        import requests

        if self._session is None:
            self._session = requests.Session()
        if proxy:
            self._session.proxies = {"http": proxy, "https": proxy}
        else:
            self._session.proxies = {}
        return self._session

    def _check_hf_accessible(self, proxy: str = "") -> bool:
        try:
            session = self._get_session(proxy)
            url = "https://huggingface.co"
            r = session.get(url, timeout=10, stream=True)
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
                logger.info("  │  └─ 代理连接 ... OK")
            else:
                logger.error("  │  └─ 代理连接失败")
                return False
        else:
            logger.error("  │  └─ 无法访问 HuggingFace")
            logger.error("请在 config.ini 的 [network] 节设置 proxy")
            return False

        logger.info("  ├─ 下载文件 (%d 个 -> %s)", len(files), self.models_dir)

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
                self.close()
                return False

        self.close()
        logger.info("下载完成")
        return True

    def ensure_model_available(
        self, progress_callback=None, confirm_callback=None
    ) -> bool:
        if self.is_model_exists():
            logger.info("模型已就绪: %s", self.models_dir)
            return True

        logger.info("模型文件不完整，开始下载...")
        return self.download_model(progress_callback, confirm_callback)

    @staticmethod
    def get_model_path(model_name: str = "large-v3") -> Path:
        downloader = ModelDownloader(model_name)
        return downloader.model_path
