"""模型下载工具 - 支持绿色版首次运行自动下载模型"""

import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from src.i18n import t
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

HF_MIRROR_BASE = "https://hf-mirror.com"

PARAFORMER_FILES = {
    # asr: speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx
    "asr/model_quant.onnx": "https://www.modelscope.cn/api/v1/models/damo/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx/repo?Revision=master&FilePath=model_quant.onnx",
    "asr/config.yaml": "https://www.modelscope.cn/api/v1/models/damo/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx/repo?Revision=master&FilePath=config.yaml",
    "asr/am.mvn": "https://www.modelscope.cn/api/v1/models/damo/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx/repo?Revision=master&FilePath=am.mvn",
    "asr/tokens.json": "https://www.modelscope.cn/api/v1/models/damo/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx/repo?Revision=master&FilePath=tokens.json",
    # vad: speech_fsmn_vad_zh-cn-16k-common-onnx
    "vad/model_quant.onnx": "https://www.modelscope.cn/api/v1/models/damo/speech_fsmn_vad_zh-cn-16k-common-onnx/repo?Revision=master&FilePath=model_quant.onnx",
    "vad/config.yaml": "https://www.modelscope.cn/api/v1/models/damo/speech_fsmn_vad_zh-cn-16k-common-onnx/repo?Revision=master&FilePath=config.yaml",
    "vad/am.mvn": "https://www.modelscope.cn/api/v1/models/damo/speech_fsmn_vad_zh-cn-16k-common-onnx/repo?Revision=master&FilePath=am.mvn",
    # punc: punc_ct-transformer_zh-cn-common-vocab272727-onnx
    "punc/model_quant.onnx": "https://www.modelscope.cn/api/v1/models/damo/punc_ct-transformer_zh-cn-common-vocab272727-onnx/repo?Revision=master&FilePath=model_quant.onnx",
    "punc/config.yaml": "https://www.modelscope.cn/api/v1/models/damo/punc_ct-transformer_zh-cn-common-vocab272727-onnx/repo?Revision=master&FilePath=config.yaml",
    "punc/tokens.json": "https://www.modelscope.cn/api/v1/models/damo/punc_ct-transformer_zh-cn-common-vocab272727-onnx/repo?Revision=master&FilePath=tokens.json",
}

PARAFORMER_CORE_FILES = list(PARAFORMER_FILES.keys())

# 核心模型文件最小合法大小下限（字节），防止被截断的网络残片或 0 字节文件被误判为完整
CORE_FILE_MIN_SIZES: Dict[str, int] = {
    "model.bin": 100 * 1024 * 1024,           # Whisper 核心权重通常在 1.5GB ~ 3GB，至少 >100MB
    "asr/model_quant.onnx": 50 * 1024 * 1024, # Paraformer ASR 模型至少 >50MB (约 200MB)
    "punc/model_quant.onnx": 50 * 1024 * 1024,# 标点模型至少 >50MB (约 200MB)
    "vad/model_quant.onnx": 100 * 1024,       # FSMN VAD INT8 量化模型约 500KB，至少 >100KB
}

MODEL_CONFIG = {
    "large-v3": {
        "base_url": LARGE_V3_BASE,
        "models_dir": "models/large-v3",
        "core_files": LARGE_V3_CORE_FILES,
        "optional_files": LARGE_V3_OPTIONAL_FILES,
        "all_files": LARGE_V3_ALL_FILES,
        "source": "huggingface",
        "display_name": "Faster-Whisper Large-v3 (通用多语种, 约3.0GB)",
    },
    "faster-whisper-large-v3-turbo-ct2": {
        "base_url": TURBO_CT2_BASE,
        "models_dir": "models/faster-whisper-large-v3-turbo-ct2",
        "core_files": LARGE_V3_CORE_FILES,
        "optional_files": LARGE_V3_OPTIONAL_FILES,
        "all_files": LARGE_V3_ALL_FILES,
        "source": "huggingface",
        "display_name": "Faster-Whisper Large-v3-Turbo (通用多语种, 约1.4GB)",
    },
    "paraformer-large-vad-punc": {
        "base_url": "",
        "models_dir": "models/paraformer-large-vad-punc",
        "core_files": PARAFORMER_CORE_FILES,
        "optional_files": [],
        "all_files": PARAFORMER_CORE_FILES,
        "file_urls": PARAFORMER_FILES,
        "source": "modelscope",
        "display_name": "FunASR Paraformer (中文离线高精度, 约540MB)",
    },
}


def get_model_display_name(model_name: str) -> str:
    """获取模型友好展示名称。"""
    if not model_name:
        return ""
    cfg = MODEL_CONFIG.get(model_name)
    if cfg and "display_name" in cfg:
        return cfg["display_name"]
    pure_name = Path(model_name).name
    cfg = MODEL_CONFIG.get(pure_name)
    if cfg and "display_name" in cfg:
        return cfg["display_name"]
    return model_name


class ModelDownloader:
    """模型下载器 —— 从 HuggingFace 下载 faster-whisper 模型文件，支持代理与自动重试。"""

    def __init__(self, model_name: str = DEFAULT_MODEL_NAME):
        self.model_name = model_name
        self.model_config = MODEL_CONFIG.get(model_name)

        if not self.model_config:
            raise ValueError(t("model_downloader.unknown_model", model=model_name))

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
        """检查所有核心模型文件是否已存在且满足最小大小限制。"""
        for f in self.model_config["core_files"]:
            fp = self.models_dir / f
            min_size = CORE_FILE_MIN_SIZES.get(f, 1)
            try:
                if not fp.exists() or fp.stat().st_size < min_size:
                    return False
            except OSError:
                return False
        return True

    def get_model_size(self) -> str:
        total = 0
        for f in self.model_config["all_files"]:
            fp = self.models_dir / f
            if fp.exists():
                total += fp.stat().st_size
        if total == 0:
            return t("model_downloader.size_unknown")
        for unit in ["B", "KB", "MB", "GB"]:
            if total < 1024.0:
                return f"{total:.2f} {unit}"
            total /= 1024.0
        return f"{total:.2f} TB"

    def _get_proxy(self) -> str:
        try:
            from src.config.settings import Settings

            s = Settings()
            settings_proxy = s.get("app.proxy", "")
        except Exception:
            settings_proxy = ""
        from src.utils.proxy_detect import resolve_proxy

        return resolve_proxy(settings_proxy)

    def _proxy_source_hint(self) -> str:
        """返回代理来源说明（config.ini 或 系统自动探测），用于日志提示。"""
        try:
            from src.config.settings import Settings

            s = Settings()
            settings_proxy = s.get("app.proxy", "")
        except Exception:
            settings_proxy = ""
        if settings_proxy and settings_proxy.strip():
            return "config.ini [app] proxy"
        return t("model_downloader.proxy_source.system")

    def _get_session(self, proxy: str = ""):
        import requests
        from requests.adapters import HTTPAdapter

        if self._session is None:
            self._session = requests.Session()
            # 探测连通性时禁用自动重试，避免超时时间被 urllib3 放大数倍
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
        session = self._get_session(proxy)
        url = "https://huggingface.co"
        for attempt in range(2):
            try:
                r = session.get(
                    url,
                    timeout=(5, 4),
                    stream=True,
                    headers={"Cache-Control": "no-store"},
                )
                try:
                    return r.status_code < 400
                finally:
                    r.close()
            except Exception:
                if attempt == 0:
                    time.sleep(0.3)
        return False

    def _check_mirror_accessible(self, proxy: str = "") -> bool:
        try:
            session = self._get_session(proxy)
            url = HF_MIRROR_BASE
            r = session.get(
                url,
                timeout=(5, 4),
                stream=True,
                headers={"Cache-Control": "no-store"},
            )
            try:
                return r.status_code < 400
            finally:
                r.close()
        except Exception:
            return False

    def _check_modelscope_accessible(self, proxy: str = "") -> bool:
        try:
            session = self._get_session(proxy)
            url = "https://www.modelscope.cn"
            r = session.get(
                url,
                timeout=(5, 4),
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
        min_size: int = 1,
    ) -> bool:
        """下载单个文件到 .tmp 临时文件，下载并校验完成后原子替换到目标路径。"""
        import requests

        session = self._get_session(proxy)
        connect_timeout = 30
        read_timeout = 300

        tmp_dest = dest.with_name(dest.name + ".tmp")
        tmp_dest.parent.mkdir(parents=True, exist_ok=True)

        for attempt in range(1, max_retries + 1):
            response = None
            try:
                existing_size = tmp_dest.stat().st_size if tmp_dest.exists() else 0
                headers = {}
                resume_requested = existing_size > 0
                if resume_requested:
                    headers["Range"] = f"bytes={existing_size}-"
                    logger.info(t("model_downloader.download.resume_existing", size=self._fmt_size(existing_size)))

                response = session.get(
                    url,
                    stream=True,
                    timeout=(connect_timeout, read_timeout),
                    headers=headers,
                )

                if response.status_code == 416:
                    logger.info(t("model_downloader.download.downloaded_complete", size=self._fmt_size(existing_size)))
                    if existing_size >= min_size:
                        try:
                            tmp_dest.replace(dest)
                            return True
                        except OSError as e:
                            logger.error(f"Failed to replace {dest} with {tmp_dest}: {e}")
                            return False
                    else:
                        logger.warning(f"File {tmp_dest.name} on 416 is smaller than min_size {min_size}, restarting download.")
                        tmp_dest.unlink(missing_ok=True)
                        existing_size = 0
                        continue

                is_resume = response.status_code == 206
                if resume_requested and not is_resume:
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
                with open(tmp_dest, mode) as f:
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

                if file_progress_callback:
                    if total_size > 0 and last_reported < downloaded:
                        file_progress_callback(downloaded, total_size)
                    elif total_size == 0 and downloaded - last_reported >= throttle_step:
                        file_progress_callback(downloaded, 0)

                if total_size > 0 and downloaded != total_size:
                    logger.warning(
                        t("model_downloader.download.size_mismatch", expected=self._fmt_size(total_size), actual=self._fmt_size(downloaded)),
                    )
                    continue

                # 校验文件大小下限
                if tmp_dest.stat().st_size < min_size:
                    logger.warning(
                        f"Downloaded file {dest.name} size ({self._fmt_size(tmp_dest.stat().st_size)}) "
                        f"is below required minimum ({self._fmt_size(min_size)}). Retrying."
                    )
                    tmp_dest.unlink(missing_ok=True)
                    continue

                # 校验完成，原子替换到目标路径
                try:
                    tmp_dest.replace(dest)
                    return True
                except OSError as e:
                    logger.error(f"Failed to replace {dest} with temp file: {e}")
                    return False

            except requests.exceptions.Timeout:
                logger.warning(t("model_downloader.download.timeout_short", attempt=attempt, max_retries=max_retries))
            except requests.exceptions.ConnectionError:
                logger.warning(t("model_downloader.download.conn_failed", attempt=attempt, max_retries=max_retries))
            except Exception as e:
                logger.warning(t("model_downloader.download.fail_generic", attempt=attempt, max_retries=max_retries, error=e))
            finally:
                if response is not None:
                    response.close()

            if attempt < max_retries:
                wait = min(2**attempt, 30)
                logger.info(t("model_downloader.download.retry_wait", seconds=wait))
                time.sleep(wait)

        logger.error(t("model_downloader.download.file_failed", max_retries=max_retries, name=dest.name))
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

        if confirm_callback:
            try:
                confirmed = confirm_callback(self.model_name)
            except TypeError:
                confirmed = confirm_callback()
            if not confirmed:
                self.download_cancelled = True
                return False

        proxy = self._get_proxy()

        logger.info(t("model_downloader.download.start", model=self.model_name))
        logger.info(t("model_downloader.download.check_network"))

        is_modelscope = self.model_config.get("source") == "modelscope"
        if is_modelscope:
            if self._check_modelscope_accessible():
                logger.info(t("model_downloader.download.direct_ok", source="ModelScope"))
                proxy = ""
            elif proxy and self._check_modelscope_accessible(proxy=proxy):
                logger.info(t("model_downloader.download.proxy_ok"))
            else:
                logger.info("ModelScope 探测完成，尝试直连下载...")
                proxy = ""
        else:
            # HuggingFace 模型：依次尝试 直连 -> 代理 -> 国内镜像源 (hf-mirror.com)
            if self._check_hf_accessible():
                logger.info(t("model_downloader.download.direct_ok", source="HuggingFace"))
                proxy = ""
            elif proxy:
                logger.info(t("model_downloader.download.direct_fail_proxy", proxy=proxy))
                if self._check_hf_accessible(proxy=proxy):
                    logger.info(t("model_downloader.download.proxy_ok"))
                elif self._check_mirror_accessible():
                    logger.info(t("model_downloader.download.switch_mirror", mirror="hf-mirror.com"))
                    base_url = base_url.replace("https://huggingface.co", HF_MIRROR_BASE)
                    proxy = ""
                else:
                    logger.error(t("model_downloader.download.proxy_fail", source=self._proxy_source_hint()))
                    self._clean_incomplete_core_files()
                    return False
            elif self._check_mirror_accessible():
                logger.info(t("model_downloader.download.switch_mirror", mirror="hf-mirror.com"))
                base_url = base_url.replace("https://huggingface.co", HF_MIRROR_BASE)
                proxy = ""
            else:
                logger.error(t("model_downloader.download.no_access"))
                logger.error(t("model_downloader.download.config_hint"))
                # 清理可能残留的不完整核心文件，避免转写时 'model.bin is incomplete'
                self._clean_incomplete_core_files()
                return False

        total_downloaded = 0

        def _make_file_cb(filename: str, base_downloaded: int, file_index: int):
            def _file_cb(downloaded: int, file_total: int):
                if progress_callback:
                    progress_callback(
                        downloaded,
                        file_total if file_total > 0 else 0,
                        file_index,
                        len(files),
                    )

            return _file_cb

        failed: list[str] = []
        file_urls = self.model_config.get("file_urls", {})
        for file_index, filename in enumerate(files, start=1):
            if filename in file_urls:
                url = file_urls[filename]
            else:
                url = f"{base_url}/{filename}?download=true"
            dest = self.models_dir / filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            min_size = CORE_FILE_MIN_SIZES.get(filename, 1)

            if dest.exists() and dest.stat().st_size >= min_size:
                logger.info(t("model_downloader.download.file_exists", filename=filename, size=self._fmt_size(dest.stat().st_size)))
                total_downloaded += dest.stat().st_size
                continue
            else:
                logger.info(t("model_downloader.download.downloading", filename=filename))

            ok = self._download_file(
                url,
                dest,
                proxy=proxy,
                file_progress_callback=_make_file_cb(
                    filename, total_downloaded, file_index
                ),
                min_size=min_size,
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
                logger.warning(t("model_downloader.download.opt_failed", files=", ".join(opt_failed)))
            if core_failed:
                logger.error(t("model_downloader.download.core_failed", files=", ".join(core_failed)))
                # 清理可能残留的不完整核心文件，避免转写时 'model.bin is incomplete'
                self._clean_incomplete_core_files()
                self.close()
                return False

        self.close()
        logger.info(t("model_downloader.download.completed"))
        return True

    def _clean_incomplete_core_files(self) -> None:
        """删除不完整的残留核心文件（size < min_size 或空文件）。

        仅删除明显损坏的文件，保留已完整下载的文件，避免下次启动时
        因残留损坏文件导致转写阶段 'model.bin is incomplete' 崩溃。
        """
        for f in self.model_config["core_files"]:
            fp = self.models_dir / f
            try:
                min_size = CORE_FILE_MIN_SIZES.get(f, 1)
                if fp.exists() and fp.stat().st_size < min_size:
                    fp.unlink()
                    logger.warning(t("model_downloader.download.cleaned_empty", file=f))
            except OSError as e:
                logger.warning(t("model_downloader.download.clean_failed", file=f, error=e))

    def ensure_model_available(
        self, progress_callback=None, confirm_callback=None
    ) -> bool:
        if self.is_model_exists():
            total_size = self._fmt_size(
                sum(
                    (self.models_dir / cf).stat().st_size
                    for cf in self.model_config["core_files"]
                    if (self.models_dir / cf).exists()
                )
            )
            logger.info(t("model_downloader.download.completed_size", size=total_size))
            return True

        logger.info(t("model_downloader.download.model_incomplete", model=self.model_name))
        return self.download_model(progress_callback, confirm_callback)

    @staticmethod
    def get_model_path(model_name: str = DEFAULT_MODEL_NAME) -> Path:
        downloader = ModelDownloader(model_name)
        return downloader.model_path


def check_models_integrity(
    settings,
    progress_callback=None,
    confirm_callback=None,
    target_models: Optional[list] = None,
) -> bool:
    """启动时一次性检查模型文件完整性。

    仅当 config.ini 的 [app]is_check_model_file 为 true（或缺失）时执行。
    检查配置的模型或传入的 target_models：
    - 仅当全部模型完整（all_ok is True）时：将 is_check_model_file 置为 false。
    """
    do_check = settings.get_bool("app.is_check_model_file", True)
    if not do_check:
        logger.info(t("model_downloader.integrity.skipped"))
        return True

    logger.info(t("model_downloader.integrity.start"))

    if target_models:
        model_names = target_models
    else:
        engine = settings.get("transcription.engine", "faster-whisper").lower().strip()
        if engine == "funasr":
            model_names = ["paraformer-large-vad-punc"]
        else:
            model_names = [settings.get("transcription.model_path", DEFAULT_MODEL_NAME)]

    all_ok = True

    for model_name in model_names:
        if model_name not in MODEL_CONFIG:
            logger.warning(t("model_downloader.integrity.skip_unknown", key="model", model=model_name))
            continue

        try:
            downloader = ModelDownloader(model_name)
        except ValueError:
            continue

        if downloader.is_model_exists():
            logger.info(t("model_downloader.integrity.model_complete", model=model_name))
            continue

        logger.info(t("model_downloader.integrity.model_incomplete", model=model_name))
        ok = downloader.download_model(progress_callback, confirm_callback)
        if ok and downloader.is_model_exists():
            logger.info(t("model_downloader.integrity.repaired", model=model_name))
        else:
            all_ok = False
            logger.error(t("model_downloader.integrity.failed", model=model_name))

    # 仅当 all_ok is True 时才将 is_check_model_file 设置为 false
    try:
        if all_ok:
            settings.set("app.is_check_model_file", "false")
            logger.info(t("model_downloader.integrity.passed"))
        else:
            logger.info(t("model_downloader.integrity.not_passed"))
    except Exception as e:
        logger.warning(t("model_downloader.integrity.config_update_fail", error=e))

    return all_ok
