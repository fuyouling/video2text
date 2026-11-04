"""PromptManager unit tests"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.summarization.prompt_manager import PromptManager
from src.summarization.prompt_types import PromptTemplate, get_builtin_presets


@pytest.fixture(autouse=True)
def _reset_prompt_manager():
    PromptManager._reset()
    yield
    PromptManager._reset()


class TestPromptManagerPresets:
    def test_builtin_presets_exist(self):
        presets = get_builtin_presets()
        assert "default" in presets
        assert "meeting" in presets
        assert "lecture" in presets

        for p in presets.values():
            assert p.is_builtin is True
            assert len(p.name) > 0
            assert len(p.system_prompt) > 0

    def test_is_builtin(self, tmp_path):
        pm = PromptManager(base_dir=tmp_path)
        assert pm.is_builtin("default") is True
        assert pm.is_builtin("meeting") is True
        assert pm.is_builtin("non_existent_preset") is False

    def test_cannot_delete_builtin_preset(self, tmp_path):
        pm = PromptManager(base_dir=tmp_path)
        pm.delete_template("default")
        assert pm.get_template("default") is not None


class TestPromptManagerCustomTemplates:
    def test_add_and_get_custom_template(self, tmp_path):
        pm = PromptManager(base_dir=tmp_path)
        pm.set_template("my_template", "自定义指令", user_instruction="特别关注结论")

        assert "my_template" in pm.get_names()
        tmpl = pm.get_template("my_template")
        assert tmpl is not None
        assert tmpl.system_prompt == "自定义指令"
        assert tmpl.user_instruction == "特别关注结论"
        assert tmpl.is_builtin is False

    def test_delete_custom_template(self, tmp_path):
        pm = PromptManager(base_dir=tmp_path)
        pm.set_template("temp_template", "内容")
        assert "temp_template" in pm.get_names()

        pm.delete_template("temp_template")
        assert "temp_template" not in pm.get_names()
        assert pm.get_template("temp_template") is None

    def test_last_used_tracking(self, tmp_path):
        pm = PromptManager(base_dir=tmp_path)
        pm.set_last_used("meeting")
        assert pm.get_last_used() == "meeting"
        assert "会议" in pm.get_last_used_content() or "meeting" in pm.get_last_used_content()


class TestPromptManagerMigration:
    def test_migrate_from_legacy_prompts_json(self, tmp_path):
        legacy_file = tmp_path / "prompts.json"
        legacy_data = {
            "templates": {
                "旧模板1": "旧指令1",
                "旧模板2": "旧指令2",
            },
            "last_used": "旧模板1",
            "markdown_enabled": False,
        }
        legacy_file.write_text(json.dumps(legacy_data, ensure_ascii=False), encoding="utf-8")

        pm = PromptManager(base_dir=tmp_path)
        assert "旧模板1" in pm.get_names()
        assert "旧模板2" in pm.get_names()
        assert pm.get_content("旧模板1") == "旧指令1"
        assert pm.get_last_used() == "旧模板1"
        # 检查是否成功迁移并生成 summary_prompts.json
        new_file = tmp_path / "summary_prompts.json"
        assert new_file.exists()


class TestPromptManagerMarkdown:
    def test_custom_markdown_prompt_persistence(self, tmp_path):
        pm = PromptManager(base_dir=tmp_path)
        custom_md = "请使用严格的 Markdown 列表输出。"
        pm.set_custom_markdown_prompt(custom_md)

        assert pm.get_markdown_prompt() == custom_md

        # 重新创建实例验证持久化
        PromptManager._reset()
        pm2 = PromptManager(base_dir=tmp_path)
        assert pm2.get_markdown_prompt() == custom_md


class TestPromptManagerBuildMessages:
    def test_build_messages_structure(self, tmp_path):
        pm = PromptManager(base_dir=tmp_path)

        messages = pm.build_messages("待总结文本内容", user_instruction="提取要点")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

        # 默认预设 System message 显式包含 Markdown 排版指示
        assert "Markdown" in messages[0]["content"]
        # User message 包含待总结正文和用户指令
        assert "待总结文本内容" in messages[1]["content"]
        assert "提取要点" in messages[1]["content"]

    def test_build_messages_custom_prompt(self, tmp_path):
        pm = PromptManager(base_dir=tmp_path)

        messages = pm.build_messages("文本", custom_prompt="请扮演资深行业顾问")
        assert "请扮演资深行业顾问" in messages[0]["content"]
        # 自定义提示词不包含 Markdown 时，后台绝不暗自追加
        assert "Markdown" not in messages[0]["content"]
        assert "文本" in messages[1]["content"]

    def test_build_prompt_backwards_compatible(self, tmp_path):
        pm = PromptManager(base_dir=tmp_path)

        prompt_str = pm.build_prompt("测试文本", custom_prompt="自定义指令")
        assert isinstance(prompt_str, str)
        assert "自定义指令" in prompt_str
        assert "Markdown" not in prompt_str
        assert "测试文本" in prompt_str

        # 默认预设显式包含 Markdown 指示
        default_prompt = pm.build_prompt("测试文本")
        assert "Markdown" in default_prompt
        assert "测试文本" in default_prompt


class TestPromptManagerBuiltinProtectionAndCrossLanguage:
    def test_cannot_overwrite_builtin_preset(self, tmp_path):
        pm = PromptManager(base_dir=tmp_path)
        original_default = pm.get_content("default")
        pm.set_template("default", "恶意覆盖内容")
        # 自定义列表不应该包含 default
        assert "default" not in pm.get_names()
        # 内容依然保持为内置预设
        assert pm.get_content("default") == original_default

    def test_delete_lock_repair(self, tmp_path):
        pm = PromptManager(base_dir=tmp_path)
        # 模拟由于旧代码直接注入了同名自定义条目
        pm._custom_templates["meeting"] = PromptTemplate(
            id="meeting", name="meeting", system_prompt="残留冲突内容"
        )
        assert "meeting" in pm.get_names()
        # 执行删除：应当把残留的自定义条目清理掉，同时恢复使用内置预设
        pm.delete_template("meeting")
        assert "meeting" not in pm.get_names()
        assert pm.get_template("meeting") is not None
        assert "会议" in pm.get_content("meeting") or "Meeting" in pm.get_content("meeting")

    def test_cross_language_preset_resolution(self, tmp_path):
        pm = PromptManager(base_dir=tmp_path)
        # 英文预设名反查
        tmpl_en = pm.get_template("Meeting Minutes")
        assert tmpl_en is not None
        assert tmpl_en.id == "meeting"
        assert pm.is_builtin("Meeting Minutes") is True

        # 中文预设名反查
        tmpl_zh = pm.get_template("会议与访谈纪要")
        assert tmpl_zh is not None
        assert tmpl_zh.id == "meeting"
        assert pm.is_builtin("会议与访谈纪要") is True

        # 语言切换后即使以旧语言名称查询，提示词依然不为空
        content = pm.get_content("会议与访谈纪要")
        assert len(content) > 0

    def test_concurrent_access(self, tmp_path):
        import concurrent.futures
        pm = PromptManager(base_dir=tmp_path)

        def worker(i: int):
            name = f"custom_{i % 5}"
            pm.set_template(name, f"content_{i}")
            _ = pm.get_template(name)
            _ = pm.get_names()
            _ = pm.is_builtin(name)
            if i % 3 == 0:
                pm.delete_template(name)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, i) for i in range(50)]
            concurrent.futures.wait(futures)

        # 验证并发读写无崩溃
        assert isinstance(pm.get_names(), list)
