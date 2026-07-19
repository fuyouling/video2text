"""测试 DLL 7z 解压（使用 7z/7za.exe，不依赖 py7zr）。

验证点：
- 能找到随仓库分发的 7za.exe（7z/7za.exe）。
- 能下载并解压真实的 cuBLAS/cuDNN 7z 压缩包（含 BCJ2 算法）。
- 解压后必需 DLL 文件存在（嵌套 lib/ 会被提升到 libs/ 根目录）。
"""

import os
import shutil
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.dll_downloader import (
    DLL_REQUIRED_FILES,
    DllDownloader,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_7ZA = _REPO_ROOT / "7z" / "7za.exe"


@unittest.skipUnless(
    _7ZA.is_file(),
    "未找到 7z/7za.exe，跳过真实解压测试",
)
class TestSevenZipExtraction(unittest.TestCase):
    def setUp(self) -> None:
        self.downloader = DllDownloader()
        # 使用临时 libs 目录，避免污染仓库 libs/
        self.work_libs = _REPO_ROOT / "libs_test_tmp"
        self.work_libs.mkdir(parents=True, exist_ok=True)
        self.downloader.libs_dir = self.work_libs
        self.downloader.archive_path = _REPO_ROOT / "cuBLAS_test_tmp.7z"

    def tearDown(self) -> None:
        for p in (self.work_libs, self.downloader.archive_path):
            if p.exists():
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    try:
                        p.unlink()
                    except OSError:
                        pass

    def test_find_7z_binary(self) -> None:
        found = self.downloader._find_7z_binary()
        self.assertIsNotNone(found, "应在 7z/ 下找到 7za.exe")
        self.assertTrue(Path(found).is_file())

    def test_download_and_extract_real_archive(self) -> None:
        # 真实下载（依赖网络，约数百 MB），默认跳过以免拖慢常规测试。
        # 设置环境变量 RUN_REAL_DLL_DOWNLOAD=1 可启用端到端验证。
        if not os.environ.get("RUN_REAL_DLL_DOWNLOAD"):
            self.skipTest("设置 RUN_REAL_DLL_DOWNLOAD=1 以执行真实下载解压测试")
        ok = self.downloader._download_archive()
        self.assertTrue(ok, "7z 压缩包下载失败")
        self.assertTrue(self.downloader.archive_path.exists())

        ok = self.downloader._extract_archive()
        self.assertTrue(ok, "7z 解压失败（BCJ2 不支持？）")

        missing = [
            name for name in DLL_REQUIRED_FILES
            if not (self.work_libs / name).exists()
        ]
        self.assertEqual(
            missing, [], f"解压后缺少必需 DLL: {missing}"
        )

    def test_extract_flat_no_subdir(self) -> None:
        # 压缩包为扁平结构（无子目录），验证 DLL 直接解压到 libs/ 根目录
        src = _REPO_ROOT / "libs_flat_src"
        src.mkdir(parents=True, exist_ok=True)
        (src / "dummy_cublas64_12.dll").write_bytes(b"x" * 1024)
        archive = _REPO_ROOT / "flat_test.7z"
        subprocess_run = __import__("subprocess").run
        proc = subprocess_run(
            [str(_7ZA), "a", "-y", str(archive), str(src / "dummy_cublas64_12.dll")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        shutil.rmtree(src, ignore_errors=True)

        self.downloader.archive_path = archive
        self.assertTrue(self.downloader._extract_archive())
        self.assertTrue(
            (self.work_libs / "dummy_cublas64_12.dll").exists(),
            "扁平压缩包的 DLL 未被解压到 libs/ 根目录",
        )
        self.assertFalse(
            (self.work_libs / "lib").exists(), "不应出现多余的 lib/ 子目录"
        )
        archive.unlink(missing_ok=True)


class TestDllIntegrity(unittest.TestCase):
    """DLL 完整性检测（仅判文件存在与大小，不依赖网络/7z）。"""

    def setUp(self) -> None:
        self.downloader = DllDownloader()
        self.work_libs = _REPO_ROOT / "libs_integrity_tmp"
        self.work_libs.mkdir(parents=True, exist_ok=True)
        self.downloader.libs_dir = self.work_libs

    def tearDown(self) -> None:
        if self.work_libs.exists():
            shutil.rmtree(self.work_libs, ignore_errors=True)

    def _write(self, name: str, size: int) -> None:
        p = self.work_libs / name
        if size > 0:
            p.write_bytes(b"x" * size)
        else:
            p.write_bytes(b"")

    def test_missing_file_incomplete(self) -> None:
        self.assertFalse(self.downloader.is_dlls_complete())

    def test_empty_file_incomplete(self) -> None:
        for name in DLL_REQUIRED_FILES:
            self._write(name, 0)
        self.assertFalse(self.downloader.is_dlls_complete())

    def test_all_nonempty_complete(self) -> None:
        for name in DLL_REQUIRED_FILES:
            self._write(name, 1024)
        self.assertTrue(self.downloader.is_dlls_complete())


if __name__ == "__main__":
    unittest.main(verbosity=2)
