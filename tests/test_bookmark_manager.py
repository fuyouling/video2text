"""BookmarkManager 单元测试：自动建目录、多实例并发防覆盖、磁盘状态感知"""

import threading
import time
from pathlib import Path
from src.storage.bookmark_manager import BookmarkItem, BookmarkManager


def test_bookmark_manager_mkdir_parents(tmp_path: Path):
    """测试保存前自动创建深层嵌套父目录。"""
    deep_path = tmp_path / "deep" / "nested" / "dir" / "bookmarks.json"
    bm = BookmarkManager(deep_path)
    item = BookmarkItem(
        video_name="vid1",
        content_type="transcript",
        position=10,
        text="片段内容",
    )
    bm.add(item)
    assert deep_path.exists()
    loaded = bm.get_all()
    assert len(loaded) == 1
    assert loaded[0].video_name == "vid1"
    assert loaded[0].text == "片段内容"


def test_bookmark_manager_multi_instance_concurrency(tmp_path: Path):
    """测试多实例并发操作时，书签不会相互覆盖丢失。"""
    file_path = tmp_path / "concurrent_bookmarks.json"
    num_threads = 10
    items_per_thread = 5
    barrier = threading.Barrier(num_threads)

    def worker(thread_idx: int):
        # 每个线程创建独立的 BookmarkManager 实例
        mgr = BookmarkManager(file_path)
        barrier.wait()
        for i in range(items_per_thread):
            item = BookmarkItem(
                video_name=f"vid_t{thread_idx}_{i}",
                content_type="transcript",
                position=i * 10,
                text=f"text_{thread_idx}_{i}",
            )
            mgr.add(item)

    threads = [
        threading.Thread(target=worker, args=(t,)) for t in range(num_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 验证最终数据
    final_mgr = BookmarkManager(file_path)
    all_items = final_mgr.get_all()
    assert len(all_items) == num_threads * items_per_thread
    names = {b.video_name for b in all_items}
    for t in range(num_threads):
        for i in range(items_per_thread):
            assert f"vid_t{t}_{i}" in names


def test_bookmark_manager_disk_state_sync(tmp_path: Path):
    """测试多实例间磁盘状态更新感知。"""
    file_path = tmp_path / "sync_bookmarks.json"
    bm1 = BookmarkManager(file_path)
    bm2 = BookmarkManager(file_path)

    bm1.add(BookmarkItem(video_name="v1", content_type="transcript", position=0, text="t1"))
    # bm2 应当感知磁盘变更并读取到 v1
    all_from_2 = bm2.get_all()
    assert len(all_from_2) == 1
    assert all_from_2[0].video_name == "v1"

    # bm2 添加 v2
    bm2.add(BookmarkItem(video_name="v2", content_type="summary", position=10, text="t2"))
    # bm1 应当感知磁盘变更并读取到 v1 和 v2
    all_from_1 = bm1.get_all()
    assert len(all_from_1) == 2
    assert {b.video_name for b in all_from_1} == {"v1", "v2"}

    # bm1 删除索引 0
    bm1.remove([0])
    all_after_del = bm2.get_all()
    assert len(all_after_del) == 1
    assert all_after_del[0].video_name == "v2"

    # bm2 清空
    bm2.clear()
    assert len(bm1.get_all()) == 0
