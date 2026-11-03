import threading
from pathlib import Path
from src.storage.output_index import OutputIndex


def test_output_index_basic(tmp_path: Path):
    idx = OutputIndex(str(tmp_path))
    assert idx.load_manifest() == {}

    idx.record("video1", transcript_paths=[str(tmp_path / "video1.txt")])
    entries = idx.load_manifest()
    assert "video1" in entries
    assert entries["video1"]["transcripts"] == [str(tmp_path / "video1.txt")]

    idx.record("video1", summary_path=str(tmp_path / "video1_summary.md"))
    entries = idx.load_manifest()
    assert entries["video1"]["summary"] == str(tmp_path / "video1_summary.md")
    assert entries["video1"]["transcripts"] == [str(tmp_path / "video1.txt")]


def test_output_index_concurrent_records(tmp_path: Path):
    num_threads = 10
    barrier = threading.Barrier(num_threads)

    def worker(i: int):
        barrier.wait()
        idx = OutputIndex(str(tmp_path))
        idx.record(
            f"video_{i}",
            transcript_paths=[str(tmp_path / f"video_{i}.txt")],
            summary_path=str(tmp_path / f"video_{i}_summary.md"),
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final_idx = OutputIndex(str(tmp_path))
    entries = final_idx.load_manifest()
    assert len(entries) == num_threads
    for i in range(num_threads):
        assert f"video_{i}" in entries
        assert entries[f"video_{i}"]["summary"] == str(tmp_path / f"video_{i}_summary.md")
        assert entries[f"video_{i}"]["dir"] == str(tmp_path)


def test_output_index_dir_record(tmp_path: Path):
    """测试 record 自动根据 transcript_paths[0] 或 summary_path 记录真实父目录 dir。"""
    sub_dir = tmp_path / "mirrored_sub"
    sub_dir.mkdir(parents=True)
    idx = OutputIndex(str(tmp_path))

    # 1. 由 transcript_paths 写入
    idx.record("video_sub", transcript_paths=[str(sub_dir / "video_sub.txt")])
    entries = idx.load_manifest()
    assert entries["video_sub"]["dir"] == str(sub_dir)

    # 2. 仅由 summary_path 写入
    sub_dir2 = tmp_path / "another_sub"
    sub_dir2.mkdir(parents=True)
    idx.record("video_sub2", summary_path=str(sub_dir2 / "video_sub2_summary.md"))
    entries = idx.load_manifest()
    assert entries["video_sub2"]["dir"] == str(sub_dir2)

    # 3. build_name_map 优先使用真实 dir
    name_map = idx.build_name_map()
    assert name_map["video_sub"] == str(sub_dir)
    assert name_map["video_sub2"] == str(sub_dir2)


def test_output_index_scan_pruning_and_subdirs(tmp_path: Path):
    """测试 scan 单次 os.walk 剪枝隐藏目录与镜像子目录识别。"""
    # 隐藏目录
    dot_dir = tmp_path / ".v2t"
    dot_dir.mkdir(parents=True)
    (dot_dir / "index.json").write_text("{}", encoding="utf-8")
    (dot_dir / "hidden.txt").write_text("secret", encoding="utf-8")

    ckpt_dir = tmp_path / ".checkpoint"
    ckpt_dir.mkdir(parents=True)
    (ckpt_dir / "checkpoint.txt").write_text("cp", encoding="utf-8")

    # 根目录产物
    (tmp_path / "root_video.txt").write_text("root text", encoding="utf-8")
    (tmp_path / "root_video.srt").write_text("root srt", encoding="utf-8")
    (tmp_path / "root_video_summary.md").write_text("root summary", encoding="utf-8")
    (tmp_path / "root_video_keywords.txt").write_text("root kw", encoding="utf-8")

    # 嵌套子目录产物（镜像子目录）
    sub_dir = tmp_path / "nested" / "sub"
    sub_dir.mkdir(parents=True)
    (sub_dir / "sub_video.vtt").write_text("sub vtt", encoding="utf-8")
    (sub_dir / "sub_video_summary.txt").write_text("sub sum txt", encoding="utf-8")

    idx = OutputIndex(str(tmp_path))
    scanned = idx.scan()

    # 隐藏目录内的文件不应出现
    assert "hidden" not in scanned
    assert "checkpoint" not in scanned

    # 根目录产物
    assert "root_video" in scanned
    assert scanned["root_video"]["dir"] == str(tmp_path)
    assert any(p.endswith("root_video.txt") for p in scanned["root_video"]["transcripts"])
    assert any(p.endswith("root_video.srt") for p in scanned["root_video"]["transcripts"])
    assert scanned["root_video"]["summary"].endswith("root_video_summary.md")

    # 关键词派生文件不应被记录为转写文本
    assert "root_video_keywords" not in scanned

    # 子目录产物
    assert "sub_video" in scanned
    assert scanned["sub_video"]["dir"] == str(sub_dir)
    assert any(p.endswith("sub_video.vtt") for p in scanned["sub_video"]["transcripts"])
    assert scanned["sub_video"]["summary"].endswith("sub_video_summary.txt")

