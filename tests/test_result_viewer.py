"""ResultViewerWindow 结果查看窗口单元测试。

验证：
1. 首次打开能正确加载并展示转写和总结内容。
2. 重新总结生成后，再次调用 load_files（模拟点击「全屏查看」）能够自动刷新为最新内容，而非显示上次缓存内容。
3. 重新总结并切换格式（txt -> md）时，能自动定位并加载最新修改的摘要文件。
4. 多文件场景下，传入 target_video 能正确保持/选中目标文件并刷新。
5. 文件夹树模式下（folder_mode=True）重新加载也能正确选中并刷新内容。
6. reload_file_if_active 方法在当前文件被查看时能立即重新从磁盘载入最新内容。
"""

import os
from pathlib import Path
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from src.ui.result_viewer import ResultViewerWindow


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_result_viewer_initial_load(qapp, tmp_path: Path):
    """测试首次加载文件内容"""
    video_name = "sample_video"
    tx_file = tmp_path / f"{video_name}.txt"
    sum_file = tmp_path / f"{video_name}_summary.txt"
    tx_file.write_text("转写内容测试第1版", encoding="utf-8-sig")
    sum_file.write_text("总结内容测试第1版", encoding="utf-8-sig")

    viewer = ResultViewerWindow()
    viewer.load_files([video_name], str(tmp_path))

    assert "转写内容测试第1版" in viewer.transcript_view.toPlainText()
    assert "总结内容测试第1版" in viewer.summary_view.toPlainText()
    assert viewer._current_video_name == video_name
    viewer.close()
    viewer.deleteLater()


def test_result_viewer_resummarize_and_reopen_refreshes_content(qapp, tmp_path: Path):
    """关键问题修复验证：重新总结生成后，再次点击全屏查看自动刷新最新文本内容"""
    video_name = "test_resummarize"
    tx_file = tmp_path / f"{video_name}.txt"
    sum_file = tmp_path / f"{video_name}_summary.txt"

    tx_file.write_text("这是原始转写文本", encoding="utf-8-sig")
    sum_file.write_text("【版本1】这是初次总结内容", encoding="utf-8-sig")

    viewer = ResultViewerWindow()
    # 模拟第一次打开全屏查看
    viewer.load_files([video_name], str(tmp_path))
    assert "【版本1】" in viewer.summary_view.toPlainText()

    # 模拟用户在主界面重新总结生成了新内容，写入文件
    sum_file.write_text("【版本2】这是重新总结生成的最新摘要内容！", encoding="utf-8-sig")
    future_time = sum_file.stat().st_mtime + 5.0
    os.utime(sum_file, (future_time, future_time))

    # 模拟用户再次点击「全屏查看」按钮（调用 load_files）
    viewer.load_files([video_name], str(tmp_path), target_video=video_name)

    # 验证：必须自动刷新为版本2的最新内容，不能残留版本1的缓存
    latest_text = viewer.summary_view.toPlainText()
    assert "【版本2】" in latest_text
    assert "最新摘要内容" in latest_text
    assert "【版本1】" not in latest_text
    viewer.close()
    viewer.deleteLater()


def test_result_viewer_resummarize_with_different_format_refreshes(qapp, tmp_path: Path):
    """验证摘要格式从 txt 改为 md 重新生成时，也能自动读取最新 md 摘要"""
    video_name = "format_switch_video"
    tx_file = tmp_path / f"{video_name}.txt"
    sum_txt = tmp_path / f"{video_name}_summary.txt"

    tx_file.write_text("视频转写文本", encoding="utf-8-sig")
    sum_txt.write_text("旧版纯文本总结", encoding="utf-8-sig")

    viewer = ResultViewerWindow()
    viewer.load_files([video_name], str(tmp_path))
    assert "旧版纯文本总结" in viewer.summary_view.toPlainText()

    # 模拟重新总结并以 Markdown 格式保存
    sum_md = tmp_path / f"{video_name}_summary.md"
    sum_md.write_text("## 新版 Markdown 格式总结\n- 重点分析 A\n- 重点分析 B", encoding="utf-8-sig")
    future_time = sum_txt.stat().st_mtime + 5.0
    os.utime(sum_md, (future_time, future_time))

    # 再次打开/刷新
    viewer.load_files([video_name], str(tmp_path), target_video=video_name)

    latest_text = viewer.summary_view.toPlainText()
    assert "新版 Markdown 格式总结" in latest_text
    assert "重点分析 A" in latest_text
    assert "旧版纯文本总结" not in latest_text
    viewer.close()
    viewer.deleteLater()


def test_result_viewer_multiple_videos_selection_persistence_and_refresh(qapp, tmp_path: Path):
    """测试多视频场景下，重新总结特定视频后打开，优先选中并刷新该视频"""
    v1 = "video_01"
    v2 = "video_02"
    for v in (v1, v2):
        (tmp_path / f"{v}.txt").write_text(f"{v} 转写", encoding="utf-8-sig")
        (tmp_path / f"{v}_summary.txt").write_text(f"{v} 初次总结", encoding="utf-8-sig")

    viewer = ResultViewerWindow()
    viewer.load_files([v1, v2], str(tmp_path), target_video=v1)
    assert viewer._current_video_name == v1

    # 重新总结 v2
    v2_sum = tmp_path / f"{v2}_summary.txt"
    v2_sum.write_text("video_02 重新总结后的内容", encoding="utf-8-sig")
    future_time = v2_sum.stat().st_mtime + 5.0
    os.utime(v2_sum, (future_time, future_time))

    # 用户在主窗口选中 v2 点击全屏查看
    viewer.load_files([v1, v2], str(tmp_path), target_video=v2)
    assert viewer._current_video_name == v2
    assert "video_02 重新总结后的内容" in viewer.summary_view.toPlainText()
    viewer.close()
    viewer.deleteLater()


def test_result_viewer_folder_mode_refresh(qapp, tmp_path: Path):
    """测试文件夹树模式下（folder_mode=True）重新总结后刷新"""
    sub_dir = tmp_path / "course1"
    sub_dir.mkdir(parents=True, exist_ok=True)
    video_name = "lecture_01"
    (sub_dir / f"{video_name}.txt").write_text("课程1 转写", encoding="utf-8-sig")
    (sub_dir / f"{video_name}_summary.txt").write_text("课程1 初版总结", encoding="utf-8-sig")

    viewer = ResultViewerWindow()
    viewer.load_files(
        [video_name],
        str(tmp_path),
        folder_mode=True,
        name_to_dir={video_name: str(sub_dir)},
        target_video=video_name,
    )
    assert "课程1 初版总结" in viewer.summary_view.toPlainText()

    # 重新生成总结
    lec_sum = sub_dir / f"{video_name}_summary.txt"
    lec_sum.write_text("课程1 重新生成的详细知识点总结", encoding="utf-8-sig")
    future_time = lec_sum.stat().st_mtime + 5.0
    os.utime(lec_sum, (future_time, future_time))

    # 再次加载
    viewer.load_files(
        [video_name],
        str(tmp_path),
        folder_mode=True,
        name_to_dir={video_name: str(sub_dir)},
        target_video=video_name,
    )
    assert "课程1 重新生成的详细知识点总结" in viewer.summary_view.toPlainText()
    assert "初版总结" not in viewer.summary_view.toPlainText()
    viewer.close()
    viewer.deleteLater()


def test_result_viewer_reload_file_if_active(qapp, tmp_path: Path):
    """测试 reload_file_if_active 即时刷新当前查看的文件"""
    video_name = "active_stream_video"
    tx_file = tmp_path / f"{video_name}.txt"
    sum_file = tmp_path / f"{video_name}_summary.txt"

    tx_file.write_text("初始文本", encoding="utf-8-sig")
    sum_file.write_text("初始总结", encoding="utf-8-sig")

    viewer = ResultViewerWindow()
    viewer.load_files([video_name], str(tmp_path))
    assert "初始总结" in viewer.summary_view.toPlainText()

    # 磁盘文件更新
    sum_file.write_text("实时重新生成的总结文本", encoding="utf-8-sig")

    # 对非活跃文件调用不影响
    viewer.reload_file_if_active("other_inactive_file")
    assert "初始总结" in viewer.summary_view.toPlainText()

    # 对当前活跃文件调用，立即刷新
    viewer.reload_file_if_active(video_name)
    assert "实时重新生成的总结文本" in viewer.summary_view.toPlainText()
    viewer.close()
    viewer.deleteLater()

