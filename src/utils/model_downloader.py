"""模型下载工具 - 支持绿色版首次运行自动下载模型"""

import os
import sys
from pathlib import Path
from src.utils.logger import get_logger

logger = get_logger(__name__)

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

    def _check_hf_accessible(self, proxy: str = "") -> bool:
        try:
            import requests

            url = "https://huggingface.co"
            kw = {"timeout": 10}
            if proxy:
                kw["proxies"] = {"http": proxy, "https": proxy}
            r = requests.head(url, **kw)
            return r.status_code < 400
        except Exception:
            return False

    def _download_file(
        self, url: str, dest: Path, proxy: str = "", progress_callback=None
    ) -> bool:
        try:
            import requests
        except ImportError:
            logger.error("需要安装 requests 库: pip install requests")
            return False

        kw = {"stream": True, "timeout": 300}
        if proxy:
            kw["proxies"] = {"http": proxy, "https": proxy}

        try:
            response = requests.get(url, **kw)
            response.raise_for_status()

            with open(dest, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            return True

        except requests.exceptions.Timeout:
            logger.error("下载超时: %s", dest.name)
            dest.unlink(missing_ok=True)
            return False
        except requests.exceptions.ConnectionError:
            logger.error("连接失败: %s", dest.name)
            dest.unlink(missing_ok=True)
            return False
        except Exception as e:
            logger.error("下载失败 %s: %s", dest.name, e)
            dest.unlink(missing_ok=True)
            return False

    def download_model(self, progress_callback=None) -> bool:  # noqa: ARG001
        try:
            import requests
        except ImportError:
            logger.error("需要安装 requests 库: pip install requests")
            return False

        base_url = self.model_config["base_url"]
        files = self.model_config["all_files"]
        core_files = set(self.model_config["core_files"])
        self.models_dir.mkdir(parents=True, exist_ok=True)

        proxy = self._get_proxy()

        logger.info("检查 HuggingFace 连接...")
        if self._check_hf_accessible():
            logger.info("HuggingFace 可直接访问")
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

        failed: list[str] = []
        for filename in files:
            url = f"{base_url}/{filename}?download=true"
            dest = self.models_dir / filename
            logger.info("下载: %s", filename)
            ok = self._download_file(url, dest, proxy=proxy)
            if ok:
                logger.info("完成: %s", filename)
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
                return False

        logger.info("全部模型文件下载完成: %s", self.models_dir)
        return True

    def ensure_model_available(self, progress_callback=None) -> bool:  # noqa: ARG001
        if self.is_model_exists():
            logger.info("模型文件已就绪: %s", self.models_dir)
            return True

        logger.info("模型文件不完整，开始下载...")
        return self.download_model()

    @staticmethod
    def get_model_path(model_name: str = "large-v3") -> Path:
        downloader = ModelDownloader(model_name)
        return downloader.model_path
