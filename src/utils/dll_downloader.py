"""DLL 依赖检测、下载、解压与清理工具。

faster-whisper 需要 cuBLAS / cuDNN 9 的 DLL 文件才能启用 GPU 加速。
本模块负责在启动时检测这些 DLL 是否完整，若不完整则从 GitHub 下载
cuBLAS.and.cuDNN_CUDA12_win_v3.7z 压缩包并解压到 libs/ 目录。
"""

import hashlib
import sys
import time
from pathlib import Path
from typing import Optional

from src.i18n import t
from src.utils.logger import get_logger
from src.utils.paths import ensure_cuda_libs, get_base_dir
from src.utils.subprocess_compat import get_no_window_kwargs

logger = get_logger("video2text")


def _fmt_size(size: int) -> str:
    """将字节数格式化为可读形式。"""
    if size <= 0:
        return "0B"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0:
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}TB"


DLL_REQUIRED_FILES = [
    "cublas64_12.dll",
    "cublasLt64_12.dll",
    "cudnn64_9.dll",
    "cudnn_adv64_9.dll",
    "cudnn_cnn64_9.dll",
    "cudnn_engines_precompiled64_9.dll",
    "cudnn_engines_runtime_compiled64_9.dll",
    "cudnn_graph64_9.dll",
    "cudnn_heuristic64_9.dll",
    "cudnn_ops64_9.dll",
]

DLL_DOWNLOAD_URL = (
    "https://github.com/Purfview/whisper-standalone-win/releases/download/libs/"
    "cuBLAS.and.cuDNN_CUDA12_win_v3.7z"
)

DLL_MIRROR_URL = (
    "https://ghfast.top/https://github.com/Purfview/whisper-standalone-win/releases/download/libs/"
    "cuBLAS.and.cuDNN_CUDA12_win_v3.7z"
)

DLL_ARCHIVE_NAME = "cuBLAS.and.cuDNN_CUDA12_win_v3.7z"

DLL_ARCHIVE_SHA256 = "ec00119dbeec2adff661302883becd004028784031a4547ea1749e7683ffb67f"


class DllDownloader:
    """DLL 依赖检测与下载器。

    职责：检测 DLL 完整性 → 下载 7z 压缩包 → 解压到 libs/ → 清理。
    """

    def __init__(self):
        self._base_dir = get_base_dir()
        self.libs_dir = self._base_dir / "libs"
        self.libs_dir.mkdir(parents=True, exist_ok=True)
        self.archive_path = self.libs_dir / DLL_ARCHIVE_NAME
        self._session = None

    # ── 检测 ──────────────────────────────────────────────

    def is_dlls_exists(self) -> bool:
        """检查所有必需的 DLL 文件是否已存在于 libs/ 目录（仅判存在）。"""
        for name in DLL_REQUIRED_FILES:
            if not (self.libs_dir / name).exists():
                return False
        return True

    def is_dlls_complete(self) -> bool:
        """检查所有必需的 DLL 文件是否完整（存在且大小 > 0）。"""
        for name in DLL_REQUIRED_FILES:
            fp = self.libs_dir / name
            try:
                if not fp.exists() or fp.stat().st_size == 0:
                    return False
            except OSError:
                return False
        return True

    def check_dlls_in_use(self) -> list[str]:
        """检查目标 libs/ 目录下的 DLL 是否被正在运行的进程占用。"""
        locked = []
        for name in DLL_REQUIRED_FILES:
            fp = self.libs_dir / name
            if fp.exists():
                try:
                    # Windows 下若文件被运行态进程加载占用，以写模式打开会抛 PermissionError
                    with open(fp, "r+b"):
                        pass
                except (PermissionError, OSError):
                    locked.append(name)
        return locked

    @staticmethod
    def compute_sha256(file_path: Path) -> str:
        """计算给定文件的 SHA-256 哈希值。"""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    def verify_archive(self, expected_hash: Optional[str] = DLL_ARCHIVE_SHA256) -> bool:
        """校验下载的压缩包完整性（SHA-256）。"""
        if not self.archive_path.exists():
            return False
        if not expected_hash:
            return True
        try:
            actual_hash = self.compute_sha256(self.archive_path)
            return actual_hash.lower() == expected_hash.lower()
        except OSError as e:
            logger.warning(f"Failed to read archive for SHA-256 check: {e}")
            return False

    # ── 代理 / Session 管理 ───────────────────────────────

    def _get_proxy(self) -> str:
        """从 config.ini 读取代理配置；未配置时自动探测本机系统代理。"""
        from src.config.settings import Settings
        from src.utils.proxy_detect import resolve_proxy

        try:
            settings_proxy = Settings().get("app.proxy", "")
        except Exception:
            settings_proxy = ""
        return resolve_proxy(settings_proxy)

    def _proxy_source_hint(self) -> str:
        """返回代理来源说明（config.ini 或 系统自动探测），用于日志提示。"""
        from src.config.settings import Settings

        try:
            settings_proxy = Settings().get("app.proxy", "")
        except Exception:
            settings_proxy = ""
        if settings_proxy and settings_proxy.strip():
            return "config.ini [app] proxy"
        return t("dll_downloader.proxy_source.system")

    def _get_session(self):
        """获取（或创建）requests Session，仅在首次调用时初始化。"""
        import requests
        from requests.adapters import HTTPAdapter

        if self._session is None:
            self._session = requests.Session()
            self._session.trust_env = False
            adapter = HTTPAdapter(max_retries=0, pool_connections=1, pool_maxsize=1)
            self._session.mount("http://", adapter)
            self._session.mount("https://", adapter)
        return self._session

    def _apply_proxy(self, proxy: str) -> None:
        """将代理应用到 session。空字符串表示清除代理（直连）。"""
        if proxy:
            self._session.proxies = {"http": proxy, "https": proxy}
        else:
            self._session.proxies = {}

    # ── 网络探测 ──────────────────────────────────────────

    def _check_github_accessible(self, proxy: str = "", url: str = DLL_DOWNLOAD_URL) -> bool:
        """探测 GitHub release 下载链接是否可达。"""
        try:
            session = self._get_session()
            self._apply_proxy(proxy)
            r = session.get(
                url,
                timeout=(5, 5),
                stream=True,
                headers={"Cache-Control": "no-store"},
            )
            try:
                return r.status_code < 400
            finally:
                r.close()
        except Exception:
            return False

    # ── 下载 ──────────────────────────────────────────────

    def _download_archive(
        self,
        progress_callback=None,
        proxy: str = "",
        download_url: str = DLL_DOWNLOAD_URL,
        expected_sha256: Optional[str] = DLL_ARCHIVE_SHA256,
    ) -> bool:
        """下载 7z 压缩包到基目录，支持断点续传、严格对齐与 SHA-256 完整性校验。"""
        import requests

        self._get_session()
        self._apply_proxy(proxy)

        connect_timeout = 30
        read_timeout = 300
        max_retries = 5

        logger.info(t("dll_downloader.download.start", archive=DLL_ARCHIVE_NAME))

        for attempt in range(1, max_retries + 1):
            response = None
            try:
                # 循环内动态重新计算 existing_size
                existing_size = (
                    self.archive_path.stat().st_size
                    if self.archive_path.exists()
                    else 0
                )

                headers = {}
                resume_requested = existing_size > 0
                if resume_requested:
                    headers["Range"] = f"bytes={existing_size}-"
                    logger.info(t("dll_downloader.download.existing", archive=DLL_ARCHIVE_NAME, size=_fmt_size(existing_size)))

                response = self._get_session().get(
                    download_url,
                    stream=True,
                    timeout=(connect_timeout, read_timeout),
                    headers=headers,
                )

                # 416 Range Not Satisfiable：文件已完整或超出范围
                if response.status_code == 416:
                    logger.info(t("dll_downloader.download.complete_416"))
                    if self.verify_archive(expected_sha256):
                        return True
                    logger.warning("Existing archive failed SHA-256 check on 416; removing and retrying.")
                    self.archive_path.unlink(missing_ok=True)
                    continue

                is_resume = response.status_code == 206

                if is_resume:
                    # 断点续传（206）时 offset 严格对齐
                    content_range = response.headers.get("content-range", "")
                    if content_range.lower().startswith("bytes "):
                        try:
                            range_spec = content_range.split()[1].split("/")[0]
                            start_byte = int(range_spec.split("-")[0])
                            if start_byte != existing_size:
                                logger.warning(
                                    f"Range offset mismatch: expected {existing_size}, server returned {start_byte}. Aligning offset."
                                )
                                existing_size = start_byte
                        except Exception:
                            pass
                else:
                    # 非 206 时重置 offset 为 0，杜绝数据重复拼接
                    if resume_requested:
                        logger.warning(t("dll_downloader.download.no_resume"))
                    existing_size = 0

                if response.status_code >= 400:
                    if 400 <= response.status_code < 500:
                        logger.error(t("dll_downloader.download.http_error_no_retry", code=response.status_code))
                        return False
                    logger.warning(t("dll_downloader.download.http_error", code=response.status_code, attempt=attempt, max_retries=max_retries))
                    continue

                if is_resume:
                    logger.info(t("dll_downloader.download.resuming", attempt=attempt))
                elif attempt > 1:
                    logger.info(t("dll_downloader.download.attempt", attempt=attempt, max_retries=max_retries))

                content_length = response.headers.get("content-length")
                if content_length is not None:
                    total_size = existing_size + int(content_length)
                    if attempt == 1:
                        logger.info(t("dll_downloader.download.total_size", size=_fmt_size(total_size)))
                elif is_resume:
                    logger.warning(t("dll_downloader.download.no_content_length"))
                    existing_size = 0
                    total_size = 0
                    is_resume = False
                else:
                    total_size = 0

                downloaded = existing_size

                if is_resume and self.archive_path.exists():
                    with open(self.archive_path, "r+b") as f:
                        f.seek(existing_size)
                        f.truncate(existing_size)
                        for chunk in response.iter_content(chunk_size=65536):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                if progress_callback:
                                    progress_callback(
                                        downloaded,
                                        total_size if total_size > 0 else 0,
                                        1,
                                        1,
                                    )
                else:
                    existing_size = 0
                    downloaded = 0
                    with open(self.archive_path, "wb") as f:
                        for chunk in response.iter_content(chunk_size=65536):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                if progress_callback:
                                    progress_callback(
                                        downloaded,
                                        total_size if total_size > 0 else 0,
                                        1,
                                        1,
                                    )

                # 校验下载大小完整性
                if total_size > 0 and downloaded != total_size:
                    logger.warning(t("dll_downloader.download.size_mismatch", expected=_fmt_size(total_size), actual=_fmt_size(downloaded)))
                    continue

                # 增加 SHA-256 完整性哈希校验
                if expected_sha256:
                    if not self.verify_archive(expected_sha256):
                        logger.warning(f"Archive SHA-256 verification failed on attempt {attempt}. Removing corrupted file.")
                        self.archive_path.unlink(missing_ok=True)
                        continue

                logger.info(t("dll_downloader.download.completed", size=_fmt_size(downloaded)))
                return True

            except requests.exceptions.Timeout:
                logger.warning(t("dll_downloader.download.timeout", attempt=attempt, max_retries=max_retries))
            except requests.exceptions.ConnectionError:
                logger.warning(t("dll_downloader.download.connection_error", attempt=attempt, max_retries=max_retries))
            except Exception as e:
                logger.warning(f"Download attempt {attempt} error: {e}")
            finally:
                if response is not None:
                    response.close()

            if attempt < max_retries:
                wait = min(2**attempt, 30)
                logger.info(t("dll_downloader.download.retry_in", seconds=wait))
                time.sleep(wait)

        logger.error(t("dll_downloader.download.failed_retries", max_retries=max_retries))
        return False

    # ── 解压 ──────────────────────────────────────────────

    def _find_7z_binary(self) -> Optional[str]:
        """查找可用的 7z 解压二进制。"""
        candidates = []
        if getattr(sys, "_MEIPASS", ""):
            candidates.append(Path(sys._MEIPASS) / "7za.exe")
        candidates += [
            self._base_dir / "7za.exe",
            self._base_dir / "_internal" / "7za.exe",
            self._base_dir / "7z.exe",
            self._base_dir / "7z" / "7za.exe",
            self._base_dir / "assets" / "7za.exe",
            self._base_dir / "bin" / "7za.exe",
        ]
        for c in candidates:
            if c.is_file():
                return str(c)
        import shutil

        for name in ("7za.exe", "7z.exe", "7za", "7z"):
            found = shutil.which(name)
            if found:
                return found
        return None

    def _extract_with_7z(self, binary: str) -> bool:
        """使用外部 7z 二进制解压，规避 Windows 含空格路径的外层引号解析错误。"""
        import subprocess

        self.libs_dir.mkdir(parents=True, exist_ok=True)
        libs_dir_str = str(self.libs_dir.resolve()).rstrip(r"\/")
        archive_str = str(self.archive_path.resolve())
        binary_str = str(Path(binary).resolve())

        logger.info(t("dll_downloader.extract.extracting", archive=DLL_ARCHIVE_NAME, binary=binary))

        try:
            if sys.platform == "win32":
                # Windows 下通过字符串命令行传递 -o"{libs_dir}" 形式，规避 list2cmdline 将 "-o..." 整段包裹导致 7z 误解析
                cmd_str = f'"{binary_str}" x -y -o"{libs_dir_str}" "{archive_str}"'
                proc = subprocess.run(
                    cmd_str,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    **get_no_window_kwargs(),
                )
            else:
                cmd = [
                    binary_str,
                    "x",
                    "-y",
                    f"-o{libs_dir_str}",
                    archive_str,
                ]
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    **get_no_window_kwargs(),
                )
        except Exception:
            logger.error(t("dll_downloader.extract.call_failed"))
            return False

        if proc.returncode != 0:
            logger.error(t("dll_downloader.extract.failed", error=(proc.stderr or "").strip()[-500:]))
            return False
        logger.info(t("dll_downloader.extract.done"))
        return True

    def _extract_archive(self) -> bool:
        """解压 7z 压缩包到 libs/ 目录。解压前检查目标 DLL 是否被运行态占用。"""
        # 解压前检查目标 DLL 是否被运行态占用
        locked_dlls = self.check_dlls_in_use()
        if locked_dlls:
            logger.error(
                f"Cannot extract DLLs: files are locked by running process: {', '.join(locked_dlls)}. "
                "Please close running instances and retry."
            )
            return False

        seven_zip = self._find_7z_binary()
        if not seven_zip:
            logger.error(t("dll_downloader.extract.no_7z"))
            return False
        return self._extract_with_7z(seven_zip)

    # ── 对外接口 ──────────────────────────────────────────

    def download_and_extract(
        self, progress_callback=None, confirm_callback=None
    ) -> bool:
        """统一入口：检测 → 下载 → 解压。"""
        if self.is_dlls_complete():
            logger.info(t("dll_downloader.status.already_complete"))
            return True

        if confirm_callback and not confirm_callback():
            logger.info(t("dll_downloader.status.cancelled"))
            return False

        proxy = self._get_proxy()
        download_url = DLL_DOWNLOAD_URL
        self._get_session()
        if self._check_github_accessible():
            logger.info(t("dll_downloader.status.direct_ok"))
            proxy = ""
        elif proxy:
            logger.info(t("dll_downloader.status.direct_fail_proxy", proxy=proxy))
            if self._check_github_accessible(proxy=proxy):
                logger.info(t("dll_downloader.status.proxy_ok"))
            elif self._check_github_accessible(url=DLL_MIRROR_URL):
                logger.info("GitHub 直连未通，切换至国内镜像源下载 DLL...")
                download_url = DLL_MIRROR_URL
                proxy = ""
            else:
                logger.error(t("dll_downloader.status.proxy_fail"))
                return False
        elif self._check_github_accessible(url=DLL_MIRROR_URL):
            logger.info("GitHub 直连未通，切换至国内镜像源下载 DLL...")
            download_url = DLL_MIRROR_URL
            proxy = ""
        else:
            logger.error(t("dll_downloader.status.no_access"))
            logger.error(t("dll_downloader.status.config_hint"))
            return False

        if proxy:
            logger.info(t("dll_downloader.status.using_proxy", proxy=proxy, source=self._proxy_source_hint()))
        else:
            logger.info(t("dll_downloader.status.direct_download"))

        logger.info(t("dll_downloader.status.check_and_fill"))
        ok = self._download_archive(progress_callback, proxy=proxy, download_url=download_url)
        if not ok:
            logger.error(t("dll_downloader.status.download_fail"))
            return False

        ok = self._extract_archive()
        if not ok:
            logger.error(t("dll_downloader.status.extract_fail"))
            return False

        ensure_cuda_libs()
        logger.info(t("dll_downloader.status.all_ready", count=len(DLL_REQUIRED_FILES)))
        return True

    def cleanup_archive(self) -> None:
        """删除已下载的 7z 压缩包。"""
        if self.archive_path.exists():
            try:
                self.archive_path.unlink()
            except OSError:
                pass
