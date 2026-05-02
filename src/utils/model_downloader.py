"""模型下载工具 - 支持绿色版首次运行自动下载模型"""

import sys
import time
from pathlib import Path
from src.utils.logger import get_logger

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
    """模型下载器"""

    def __init__(self, model_name: str = "large-v3"):
        self.model_name = model_name
        self.model_config = MODEL_CONFIG.get(model_name)

        if not self.model_config:
            raise ValueError(f"未知的模型: {model_name}")

        if getattr(sys, "frozen", False):
            self._base_dir = Path(sys.executable).parent
        else:
            self._base_dir = Path(__file__).resolve().parent.parent.parent

        self.models_dir = self._base_dir / self.model_config["models_dir"]
        self.model_path = self.models_dir / "model.bin"
        self._session = None

    def close(self) -> None:
        """关闭底层 HTTP Session。"""
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
        """获取或创建 requests Session（带代理支持）。"""
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
        max_retries: int = 3,
    ) -> bool:
        """下载单个文件，支持自动重试。

        Args:
            url: 下载地址
            dest: 目标路径
            proxy: 代理地址
            file_progress_callback: 单文件进度回调 (downloaded_bytes, file_total_bytes)
            max_retries: 最大重试次数
        """
        import requests

        session = self._get_session(proxy)

        for attempt in range(1, max_retries + 1):
            response = None
            try:
                response = session.get(url, stream=True, timeout=300)
                response.raise_for_status()

                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0
                last_reported = 0
                throttle_step = max(1024 * 1024, 8192)

                with open(dest, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
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
                        "文件大小不匹配 %s: 期望 %d, 实际 %d",
                        dest.name,
                        total_size,
                        downloaded,
                    )
                    dest.unlink(missing_ok=True)
                    continue

                return True

            except requests.exceptions.Timeout:
                logger.warning(
                    "下载超时 %s (尝试 %d/%d)", dest.name, attempt, max_retries
                )
                dest.unlink(missing_ok=True)
            except requests.exceptions.ConnectionError:
                logger.warning(
                    "连接失败 %s (尝试 %d/%d)", dest.name, attempt, max_retries
                )
                dest.unlink(missing_ok=True)
            except Exception as e:
                logger.warning(
                    "下载失败 %s (尝试 %d/%d): %s", dest.name, attempt, max_retries, e
                )
                dest.unlink(missing_ok=True)
            finally:
                if response is not None:
                    response.close()

            if attempt < max_retries:
                wait = 2**attempt
                logger.info("%d 秒后重试...", wait)
                time.sleep(wait)

        logger.error("下载最终失败: %s (已重试 %d 次)", dest.name, max_retries)
        return False

    def download_model(self, progress_callback=None) -> bool:
        """下载模型文件。

        Args:
            progress_callback: 进度回调，接收 (downloaded_bytes, total_bytes)。
                downloaded_bytes 为已完成的文件总大小 + 当前文件已下载大小，
                total_bytes 为所有文件总大小。
        """
        base_url = self.model_config["base_url"]
        files = self.model_config["all_files"]
        core_files = set(self.model_config["core_files"])
        self.models_dir.mkdir(parents=True, exist_ok=True)

        proxy = self._get_proxy()

        logger.info("检查 HuggingFace 连接...")
        if self._check_hf_accessible():
            logger.info("HuggingFace 可直接访问")
            proxy = ""
        elif proxy:
            logger.info("尝试通过代理 %s 连接 HuggingFace...", proxy)
            if self._check_hf_accessible(proxy=proxy):
                logger.info("代理连接 HuggingFace 成功")
            else:
                logger.error("通过代理仍无法访问 HuggingFace，请检查网络或代理设置")
                return False
        else:
            logger.warning("无法直接访问 HuggingFace")
            logger.warning("请在 config.ini 的 [network] 节设置 proxy，例如:")
            logger.warning("  proxy = http://127.0.0.1:7890")
            return False

        logger.info("开始下载模型文件 (%d 个)", len(files))
        logger.info("目标目录: %s", self.models_dir)

        total_downloaded = 0

        def _make_file_cb(filename: str, base_downloaded: int):
            def _file_cb(downloaded: int, file_total: int):
                if progress_callback:
                    progress_callback(base_downloaded + downloaded, -1)

            return _file_cb

        failed: list[str] = []
        for filename in files:
            url = f"{base_url}/{filename}?download=true"
            dest = self.models_dir / filename
            logger.info("下载: %s", filename)
            ok = self._download_file(
                url,
                dest,
                proxy=proxy,
                file_progress_callback=_make_file_cb(filename, total_downloaded),
            )
            if ok:
                logger.info("完成: %s", filename)
                if dest.exists():
                    total_downloaded += dest.stat().st_size
            else:
                logger.error("失败: %s", filename)
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
        logger.info("全部模型文件下载完成: %s", self.models_dir)
        return True

    def ensure_model_available(self, progress_callback=None) -> bool:
        if self.is_model_exists():
            logger.info("模型文件已就绪: %s", self.models_dir)
            return True

        logger.info("模型文件不完整，开始下载...")
        return self.download_model(progress_callback)

    @staticmethod
    def get_model_path(model_name: str = "large-v3") -> Path:
        downloader = ModelDownloader(model_name)
        return downloader.model_path
