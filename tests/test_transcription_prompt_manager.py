"""TranscriptionPromptManager 单元测试"""

import json
from pathlib import Path
import pytest

from src.transcription.transcription_prompt_manager import TranscriptionPromptManager
from src.transcription.transcription_prompt_types import (
    TranscriptionPromptTemplate,
    get_builtin_tx_presets,
)


@pytest.fixture(autouse=True)
def _reset_tx_prompt_manager():
    TranscriptionPromptManager._reset()
    yield
    TranscriptionPromptManager._reset()


class TestTranscriptionPromptPresets:
    def test_builtin_presets_exist(self):
        presets = get_builtin_tx_presets()
        for expected in ["general", "meeting"]:
            assert expected in presets
            p = presets[expected]
            assert p.is_builtin is True
            assert len(p.name) > 0
            assert len(p.initial_prompt) > 0 or len(p.hotwords) > 0

    def test_is_builtin(self, tmp_path):
        pm = TranscriptionPromptManager(base_dir=tmp_path)
        assert pm.is_builtin("general") is True
        assert pm.is_builtin("meeting") is True
        assert pm.is_builtin("non_existent_preset") is False

    def test_cannot_delete_builtin_preset(self, tmp_path):
        pm = TranscriptionPromptManager(base_dir=tmp_path)
        pm.delete_template("general")
        assert pm.get_template("general") is not None

    def test_reset_builtin_preset(self, tmp_path):
        pm = TranscriptionPromptManager(base_dir=tmp_path)
        tmpl = pm.reset_template("meeting")
        assert tmpl is not None
        assert tmpl.is_builtin is True
        assert len(tmpl.initial_prompt) > 0


class TestTranscriptionPromptCustomTemplates:
    def test_add_and_get_custom_template(self, tmp_path):
        pm = TranscriptionPromptManager(base_dir=tmp_path)
        pm.set_template(
            name="我的电商",
            initial_prompt="电商直播转写",
            hotwords="SKU GMV 淘系",
            description="专属电商词汇",
        )

        assert "我的电商" in pm.get_names()
        tmpl = pm.get_template("我的电商")
        assert tmpl is not None
        assert tmpl.name == "我的电商"
        assert tmpl.initial_prompt == "电商直播转写"
        assert tmpl.hotwords == "SKU GMV 淘系"
        assert tmpl.is_builtin is False
        assert pm.get_initial_prompt("我的电商") == "电商直播转写"
        assert pm.get_hotwords("我的电商") == "SKU GMV 淘系"

    def test_delete_custom_template(self, tmp_path):
        pm = TranscriptionPromptManager(base_dir=tmp_path)
        pm.set_template("临时模板", "测试内容", "热词1")
        assert "临时模板" in pm.get_names()

        pm.delete_template("临时模板")
        assert "临时模板" not in pm.get_names()
        assert pm.get_template("临时模板") is None

    def test_forbid_creating_builtin_preset_id(self, tmp_path):
        pm = TranscriptionPromptManager(base_dir=tmp_path)
        with pytest.raises(ValueError, match="禁止创建与系统内置预设 ID 重名"):
            pm.set_template("general", "提示词", "热词")

        with pytest.raises(ValueError, match="禁止创建与系统内置预设 ID 重名"):
            pm.set_template("meeting", "提示词", "热词")

    def test_delete_shadowed_builtin_unlocks(self, tmp_path):
        pm = TranscriptionPromptManager(base_dir=tmp_path)
        # 模拟通过底层或历史配置文件注入了同名覆盖模板
        pm._custom_templates["general"] = TranscriptionPromptTemplate(
            id="general",
            name="general",
            initial_prompt="覆盖提示词",
            hotwords="覆盖热词",
            is_builtin=False,
        )
        assert pm.get_initial_prompt("general") == "覆盖提示词"

        # 修复后执行删除，解除覆盖而不锁死
        pm.delete_template("general")
        assert "general" not in pm._custom_templates
        # 恢复内置预设内容
        restored = pm.get_template("general")
        assert restored is not None
        assert restored.is_builtin is True
        assert restored.initial_prompt != "覆盖提示词"

    def test_last_used_tracking(self, tmp_path):
        pm = TranscriptionPromptManager(base_dir=tmp_path)
        pm.set_last_used("meeting")
        assert pm.get_last_used() == "meeting"

        # 持久化验证
        TranscriptionPromptManager._reset()
        pm2 = TranscriptionPromptManager(base_dir=tmp_path)
        assert pm2.get_last_used() == "meeting"

    def test_get_all_templates(self, tmp_path):
        pm = TranscriptionPromptManager(base_dir=tmp_path)
        pm.set_template("自定义1", "提示词1", "热词1")
        all_tmpls = pm.get_all_templates()
        assert "general" in all_tmpls
        assert "meeting" in all_tmpls
        assert "自定义1" in all_tmpls


class TestTranscriptionPromptMigration:
    def test_migrate_from_v1_json(self, tmp_path):
        v1_file = tmp_path / "transcription_prompts.json"
        v1_data = {
            "templates": {
                "旧电商模板": {
                    "initial_prompt": "旧初始提示词",
                    "hotwords": "旧热词1 旧热词2",
                },
                "旧会议模板": {
                    "initial_prompt": "旧会议提示词",
                    "hotwords": "决议 议题",
                },
            },
            "last_used": "旧电商模板",
        }
        v1_file.write_text(json.dumps(v1_data, ensure_ascii=False), encoding="utf-8")

        pm = TranscriptionPromptManager(base_dir=tmp_path)
        assert "旧电商模板" in pm.get_names()
        assert "旧会议模板" in pm.get_names()
        assert pm.get_initial_prompt("旧电商模板") == "旧初始提示词"
        assert pm.get_hotwords("旧电商模板") == "旧热词1 旧热词2"
        assert pm.get_last_used() == "旧电商模板"

        # 检查是否成功持久化升级为 v2 格式
        updated_data = json.loads(v1_file.read_text(encoding="utf-8"))
        assert updated_data.get("version") == 2
        assert "旧电商模板" in updated_data.get("templates", {})


class TestTranscriptionPromptValidation:
    def test_validate_limits_safe(self):
        res = TranscriptionPromptManager.validate_limits(
            initial_prompt="这是一段简短提示词",
            hotwords="词汇1 词汇2 词汇3",
        )
        assert res["safe"] is True
        assert res["prompt_warning"] is None
        assert res["hotwords_warning"] is None

    def test_validate_limits_exceeded(self):
        long_prompt = "超长测试提示词" * 50  # 350 字
        many_hotwords = " ".join([f"热词{i}" for i in range(60)])  # 60 词
        res = TranscriptionPromptManager.validate_limits(
            initial_prompt=long_prompt,
            hotwords=many_hotwords,
        )
        assert res["safe"] is False
        assert res["prompt_warning"] is not None
        assert res["hotwords_warning"] is not None

    def test_validate_limits_chinese_delimiters(self):
        # 顿号、中文分号、英文分号、全角空格容错
        hotwords = "苹果、香蕉；橙子;西瓜\u3000葡萄,哈密瓜，草莓"
        res = TranscriptionPromptManager.validate_limits(
            initial_prompt="测试",
            hotwords=hotwords,
        )
        assert res["safe"] is True
        assert res["hotwords_count"] == 7
