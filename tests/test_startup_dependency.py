"""测试启动依赖检测与下载逻辑，包括镜像源回退、串行下载不中断以及 i18n 输出。"""

from unittest.mock import MagicMock, patch
import pytest

from src.ui.startup_dependency_worker import StartupDependencyWorker
from src.utils.model_downloader import ModelDownloader, check_models_integrity, HF_MIRROR_BASE
from src.utils.dll_downloader import DllDownloader, DLL_DOWNLOAD_URL, DLL_MIRROR_URL
from src.i18n import t


def test_startup_dependency_worker_does_not_abort_dll_on_model_failure():
    """测试当模型下载失败时，StartupDependencyWorker 仍会继续执行 DLL 下载。"""
    worker = StartupDependencyWorker(
        download_model=True,
        download_dll=True,
        keep_archive=False,
    )

    phases = []
    worker.phase_changed.connect(lambda p: phases.append(p))
    results = []
    worker.finished.connect(lambda r: results.append(r))

    with patch("src.ui.startup_dependency_worker.check_models_integrity", return_value=False) as mock_model, \
         patch.object(worker, "_run_dll_phase", return_value=True) as mock_dll:
        worker.run()

        mock_model.assert_called_once()
        mock_dll.assert_called_once()
        assert phases == ["model", "dll"]
        # 模型失败，最终整体结果为 False，但两个阶段均得到执行
        assert results == [False]


def test_model_downloader_hf_mirror_fallback():
    """测试 HuggingFace 直连失败时自动回退到 hf-mirror.com。"""
    downloader = ModelDownloader("faster-whisper-large-v3-turbo-ct2")

    # 模拟直连失败、代理未配置、镜像源成功
    with patch.object(downloader, "_check_hf_accessible", return_value=False), \
         patch.object(downloader, "_check_mirror_accessible", return_value=True), \
         patch.object(downloader, "_get_proxy", return_value=""), \
         patch.object(downloader, "_download_file", return_value=True) as mock_download:

        ok = downloader.download_model()
        assert ok is True
        # 确认所有文件都是通过 hf-mirror.com 下载的
        for call_args in mock_download.call_args_list:
            url = call_args[0][0]
            assert url.startswith(HF_MIRROR_BASE)


def test_dll_downloader_mirror_fallback():
    """测试 GitHub 直连失败时自动回退到镜像源。"""
    downloader = DllDownloader()

    def mock_check(proxy="", url=DLL_DOWNLOAD_URL):
        # 只有镜像源返回 True
        return url == DLL_MIRROR_URL

    with patch.object(downloader, "is_dlls_complete", return_value=False), \
         patch.object(downloader, "_get_proxy", return_value=""), \
         patch.object(downloader, "_check_github_accessible", side_effect=mock_check), \
         patch.object(downloader, "_download_archive", return_value=True) as mock_archive, \
         patch.object(downloader, "_extract_archive", return_value=True), \
         patch("src.utils.dll_downloader.ensure_cuda_libs"):

        ok = downloader.download_and_extract()
        assert ok is True
        # 确认使用了镜像 URL
        mock_archive.assert_called_once()
        assert mock_archive.call_args[1].get("download_url") == DLL_MIRROR_URL


def test_i18n_direct_ok_formatting():
    """测试 direct_ok 支持格式化传入的 source。"""
    assert "HuggingFace" in t("model_downloader.download.direct_ok", source="HuggingFace")
    assert "ModelScope" in t("model_downloader.download.direct_ok", source="ModelScope")
    assert "hf-mirror.com" in t("model_downloader.download.mirror_ok", mirror="hf-mirror.com")
