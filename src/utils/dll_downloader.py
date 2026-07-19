"""DLL 依赖检测、下载、解压与清理工具。

faster-whisper 需要 cuBLAS / cuDNN 9 的 DLL 文件才能启用 GPU 加速。
本模块负责在启动时检测这些 DLL 是否完整，若不完整则从 GitHub 下载
cuBLAS.and.cuDNN_CUDA12_win_v3.7z 压缩包并解压到 libs/ 目录。
"""

import time
from typing import Optional

from src.ui.startup_log import dlog
from src.utils.logger import get_logger
from src.utils.paths import ensure_cuda_libs, get_base_dir

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

DLL_ARCHIVE_NAME = "cuBLAS.and.cuDNN_CUDA12_win_v3.7z"


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
        """检查所有必需的 DLL 文件是否完整（存在且大小 > 0）。

        与模型下载器一致：将 size == 0 视为不完整，触发重新下载与解压。
        仅以「存在且非空」作为完整判据，不硬编码每个 DLL 的期望字节数，
        避免版本更新导致期望大小变化而产生误判。
        """
        for name in DLL_REQUIRED_FILES:
            fp = self.libs_dir / name
            try:
                if not fp.exists() or fp.stat().st_size == 0:
                    return False
            except OSError:
                return False
        return True

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
        return "本机系统代理(自动探测)"

    def _get_session(self):
        """获取（或创建）requests Session，仅在首次调用时初始化。

        代理由调用方在获取 session 后自行设置，_get_session 不修改 proxies，
        避免后续调用（如 _download_archive）意外清空已配置的代理。
        """
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

    def _check_github_accessible(self) -> bool:
        """探测 GitHub release 下载链接是否可达。

        直接使用 DLL_DOWNLOAD_URL 而非 github.com 首页，因为 release 下载
        走不同的 CDN，主站可达不代表下载链接可达。
        """
        try:
            session = self._get_session()
            r = session.head(
                DLL_DOWNLOAD_URL,
                timeout=(5, 5),
                headers={"Cache-Control": "no-store"},
            )
            try:
                # 允许 2xx/3xx（含 302 重定向到 CDN 下载节点），
                # 不允许 4xx（404 URL 不存在 / 403 拒绝访问等）
                return r.status_code < 400
            finally:
                r.close()
        except Exception:
            return False

    # ── 下载 ──────────────────────────────────────────────

    def _download_archive(self, progress_callback=None) -> bool:
        """下载 7z 压缩包到基目录，支持断点续传和重试。

        重试策略：只对网络层异常（超时、连接错误、5xx）重试；
        客户端错误（4xx 如 404）直接返回 False，不重试。
        """
        import requests

        connect_timeout = 30
        read_timeout = 300
        max_retries = 5

        existing_size = (
            self.archive_path.stat().st_size
            if self.archive_path.exists()
            else 0
        )
        if existing_size > 0:
            logger.info(
                "DLL 下载: 检测到已有 %s，尝试断点续传", _fmt_size(existing_size)
            )
        else:
            logger.info("DLL 下载: 开始下载 %s", DLL_ARCHIVE_NAME)

        for attempt in range(1, max_retries + 1):
            response = None
            try:
                headers = {}
                resume_requested = existing_size > 0
                if resume_requested:
                    headers["Range"] = f"bytes={existing_size}-"

                response = self._get_session().get(
                    DLL_DOWNLOAD_URL,
                    stream=True,
                    timeout=(connect_timeout, read_timeout),
                    headers=headers,
                )

                # 416 Range Not Satisfiable：文件已完整
                if response.status_code == 416:
                    logger.info("DLL 下载: 文件已完整（HTTP 416），无需下载")
                    return True

                is_resume = response.status_code == 206

                if resume_requested and not is_resume:
                    # 服务器不支持断点续传 → 放弃已有部分，从头下载
                    logger.warning("DLL 下载: 服务器不支持断点续传，从头下载")
                    existing_size = 0

                if response.status_code >= 400:
                    if 400 <= response.status_code < 500:
                        # 4xx 不可重试（如 404 资源不存在）
                        logger.error(
                            "DLL 下载失败: HTTP %s（不可重试）", response.status_code
                        )
                        return False
                    # 5xx：服务器临时错误，进入下一次重试
                    logger.warning(
                        "DLL 下载: HTTP %s（第 %d/%d 次尝试）",
                        response.status_code,
                        attempt,
                        max_retries,
                    )
                    continue

                if is_resume:
                    logger.info("DLL 下载: 断点续传中（第 %d 次尝试）", attempt)
                elif attempt > 1:
                    logger.info("DLL 下载: 第 %d/%d 次尝试", attempt, max_retries)

                content_length = response.headers.get("content-length")
                if content_length is not None:
                    total_size = existing_size + int(content_length)
                    if attempt == 1:
                        logger.info(
                            "DLL 下载: 总大小 %s", _fmt_size(total_size)
                        )
                elif is_resume:
                    # 断点续传但没有 content-length → 无法校验完整性，
                    # 放弃已下载部分用 wb 模式从头下载
                    logger.warning(
                        "DLL 下载: 断点续传响应缺失 content-length，从头下载"
                    )
                    existing_size = 0
                    total_size = 0
                    is_resume = False
                else:
                    total_size = 0

                downloaded = existing_size

                mode = "ab" if is_resume else "wb"
                with open(self.archive_path, mode) as f:
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

                # 校验下载完整性（仅当已知总量时）
                if total_size > 0 and downloaded != total_size:
                    logger.warning(
                        "DLL 下载: 大小校验失败 (预期 %s, 实际 %s)，重试",
                        _fmt_size(total_size),
                        _fmt_size(downloaded),
                    )
                    continue
                logger.info(
                    "DLL 下载: ✓ 完成，共 %s", _fmt_size(downloaded)
                )
                return True

            except requests.exceptions.Timeout:
                logger.warning(
                    "DLL 下载: 超时（第 %d/%d 次尝试）", attempt, max_retries
                )
            except requests.exceptions.ConnectionError:
                logger.warning(
                    "DLL 下载: 连接错误（第 %d/%d 次尝试）", attempt, max_retries
                )
            except Exception:
                logger.exception(
                    "DLL 下载: 未知错误（第 %d/%d 次尝试）", attempt, max_retries
                )
            finally:
                if response is not None:
                    response.close()

            if attempt < max_retries:
                wait = min(2**attempt, 30)
                logger.info("DLL 下载: %d 秒后重试…", wait)
                time.sleep(wait)

        logger.error("DLL 下载: ✗ 失败（已重试 %d 次）", max_retries)
        return False

    # ── 解压 ──────────────────────────────────────────────

    def _find_7z_binary(self) -> Optional[str]:
        """查找可用的 7z 解压二进制。

        优先使用随应用打包的 7za.exe（体积小、支持 BCJ2 等全部 7z 压缩算法，
        py7zr 不支持 BCJ2 会导致解压失败）。其次从系统 PATH 查找 7z/7za。
        返回可执行路径，未找到返回 None。
        """
        candidates = [
            self._base_dir / "7za.exe",
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
        """使用外部 7z 二进制解压（支持 BCJ2 等 py7zr 不支持的算法）。

        压缩包内为扁平结构（无子目录），DLL 直接解压到 libs/ 根目录。
        """
        import subprocess

        self.libs_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            binary,
            "x",
            "-y",
            f"-o{self.libs_dir}",
            str(self.archive_path),
        ]
        logger.info("DLL 解压: 使用 %s 解压 %s → %s", binary, DLL_ARCHIVE_NAME, self.libs_dir)
        # CREATE_NO_WINDOW 隐藏打包 exe 运行时弹出的黑窗口（仅 Windows 有效）
        startup_flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            startup_flags = subprocess.CREATE_NO_WINDOW
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=startup_flags,
            )
        except Exception:
            logger.exception("DLL 解压: 调用 7z 失败")
            return False
        if proc.returncode != 0:
            logger.error("DLL 解压(7z)失败: %s", (proc.stderr or "").strip()[-500:])
            return False
        logger.info("DLL 解压: ✓ 完成")
        return True

    def _extract_archive(self) -> bool:
        """解压 7z 压缩包到 libs/ 目录。

        统一使用外部 7z 二进制（7za.exe / 7z.exe，支持 BCJ2 等全部算法）。
        py7zr 不支持 BCJ2 压缩的 7z 包，故不再使用。若找不到 7z 二进制，
        日志会给出明确提示（将 7za.exe 放到程序根目录 / 7z/ 或安装 7-Zip）。
        """
        seven_zip = self._find_7z_binary()
        if not seven_zip:
            logger.error(
                "DLL 解压失败: 未找到 7z 解压器（7za.exe / 7z.exe）。"
                "请将 7za.exe 放到程序根目录、7z/ 子目录或安装 7-Zip 后重试。"
            )
            return False
        return self._extract_with_7z(seven_zip)

    # ── 对外接口 ──────────────────────────────────────────

    def download_and_extract(
        self, progress_callback=None, confirm_callback=None
    ) -> bool:
        """统一入口：检测 → 下载 → 解压。

        返回 True 表示 DLL 已就绪（完整或已补齐），False 表示失败。
        """
        if self.is_dlls_complete():
            dlog.dll_already_complete()
            return True

        if confirm_callback and not confirm_callback():
            logger.info("DLL 依赖: 用户取消下载")
            return False

        proxy = self._get_proxy()
        # 先创建 session 并应用代理（后续 _download_archive 复用同一 session）
        self._get_session()
        self._apply_proxy(proxy)
        if proxy:
            dlog.dll_use_proxy(proxy, self._proxy_source_hint())
        else:
            dlog.dll_direct()

        # 网络探测（使用已设置的代理）
        if not self._check_github_accessible():
            logger.error("DLL 依赖: ✗ 无法访问下载地址（GitHub），下载终止")
            return False

        dlog.dll_start()
        ok = self._download_archive(progress_callback)
        if not ok:
            logger.error("DLL 依赖: ✗ 下载失败")
            return False

        ok = self._extract_archive()
        if not ok:
            logger.error("DLL 依赖: ✗ 解压失败")
            return False

        # DLL 已补齐，立即把 libs/ 加入 DLL 搜索路径，
        # 这样后续 ctranslate2 加载 CUDA/cuDNN 时无需重启即可生效。
        ensure_cuda_libs()

        dlog.dll_ready(len(DLL_REQUIRED_FILES))
        return True

    def cleanup_archive(self) -> None:
        """删除已下载的 7z 压缩包。"""
        if self.archive_path.exists():
            try:
                self.archive_path.unlink()
            except OSError:
                pass
