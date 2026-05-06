"""DirectoryManager 单元测试"""

import json
import tempfile
from pathlib import Path

import pytest

from src.config.directory_manager import DirectoryManager


@pytest.fixture
def tmp_json(tmp_path: Path):
    return tmp_path / "favorite_dirs.json"


@pytest.fixture
def mgr(tmp_json: Path):
    return DirectoryManager(tmp_json)


class TestDirectoryManager:
    def test_init_empty(self, mgr: DirectoryManager):
        assert mgr.get_input_dirs() == []
        assert mgr.get_output_dirs() == []

    def test_add_input_dir(self, mgr: DirectoryManager):
        mgr.add_input_dir("/videos/a")
        assert mgr.get_input_dirs() == ["/videos/a"]

    def test_add_output_dir(self, mgr: DirectoryManager):
        mgr.add_output_dir("/output/b")
        assert mgr.get_output_dirs() == ["/output/b"]

    def test_add_duplicate_moved_to_front(self, mgr: DirectoryManager):
        mgr.add_input_dir("/a")
        mgr.add_input_dir("/b")
        mgr.add_input_dir("/a")
        dirs = mgr.get_input_dirs()
        assert dirs == ["/a", "/b"]
        assert dirs[0] == "/a"

    def test_add_preserves_order(self, mgr: DirectoryManager):
        mgr.add_input_dir("/first")
        mgr.add_input_dir("/second")
        mgr.add_input_dir("/third")
        assert mgr.get_input_dirs() == ["/third", "/second", "/first"]

    def test_remove_input_dir(self, mgr: DirectoryManager):
        mgr.add_input_dir("/a")
        mgr.add_input_dir("/b")
        mgr.remove_input_dir("/a")
        assert mgr.get_input_dirs() == ["/b"]

    def test_remove_output_dir(self, mgr: DirectoryManager):
        mgr.add_output_dir("/x")
        mgr.remove_output_dir("/x")
        assert mgr.get_output_dirs() == []

    def test_remove_nonexistent(self, mgr: DirectoryManager):
        mgr.add_input_dir("/a")
        mgr.remove_input_dir("/nonexistent")
        assert mgr.get_input_dirs() == ["/a"]

    def test_clear_input_dirs(self, mgr: DirectoryManager):
        mgr.add_input_dir("/a")
        mgr.add_input_dir("/b")
        mgr.clear_input_dirs()
        assert mgr.get_input_dirs() == []

    def test_clear_output_dirs(self, mgr: DirectoryManager):
        mgr.add_output_dir("/x")
        mgr.clear_output_dirs()
        assert mgr.get_output_dirs() == []

    def test_persistence(self, tmp_json: Path):
        mgr1 = DirectoryManager(tmp_json)
        mgr1.add_input_dir("/in1")
        mgr1.add_input_dir("/in2")
        mgr1.add_output_dir("/out1")

        mgr2 = DirectoryManager(tmp_json)
        assert mgr2.get_input_dirs() == ["/in2", "/in1"]
        assert mgr2.get_output_dirs() == ["/out1"]

    def test_empty_file(self, tmp_json: Path):
        tmp_json.write_text("{}", encoding="utf-8")
        mgr = DirectoryManager(tmp_json)
        assert mgr.get_input_dirs() == []
        assert mgr.get_output_dirs() == []

    def test_corrupted_file(self, tmp_json: Path):
        tmp_json.write_text("not json", encoding="utf-8")
        mgr = DirectoryManager(tmp_json)
        assert mgr.get_input_dirs() == []
        assert mgr.get_output_dirs() == []

    def test_json_format(self, tmp_json: Path, mgr: DirectoryManager):
        mgr.add_input_dir("/a")
        mgr.add_output_dir("/b")
        data = json.loads(tmp_json.read_text(encoding="utf-8"))
        assert data == {"input_dirs": ["/a"], "output_dirs": ["/b"]}

    def test_get_returns_copy(self, mgr: DirectoryManager):
        mgr.add_input_dir("/a")
        dirs = mgr.get_input_dirs()
        dirs.append("/modified")
        assert mgr.get_input_dirs() == ["/a"]
