"""Settings 单元测试"""

import os
import tempfile
from pathlib import Path

import pytest
from src.config.settings import Settings, APP_NAME, APP_VERSION


@pytest.fixture(autouse=True)
def _reset_settings():
    """每个测试前重置单例，确保测试隔离"""
    Settings._reset()
    yield
    Settings._reset()


class TestSettings:
    def test_app_constants(self):
        assert APP_NAME == "video2text"
        assert APP_VERSION == "1.4.0"

    def test_load_existing_config(self, tmp_path):
        config_file = tmp_path / "config.ini"
        config_file.write_text(
            "[transcription]\nmodel_path = large-v3\nbeam_size = 10\nlog_level = DEBUG\n",
            encoding="utf-8",
        )
        settings = Settings(config_path=str(config_file))
        assert settings.get("transcription.model_path") == "large-v3"
        assert settings.get_int("transcription.beam_size") == 10
        assert settings.get("transcription.log_level") == "DEBUG"

    def test_missing_config_uses_caller_defaults(self, tmp_path):
        config_file = tmp_path / "config.ini"
        settings = Settings(config_path=str(config_file))
        assert not config_file.exists()
        assert settings.get("transcription.model_path") is None
        assert settings.get("transcription.model_path", "large-v3") == "large-v3"
        assert settings.get_int("transcription.beam_size", 5) == 5

    def test_get_with_default(self, tmp_path):
        config_file = tmp_path / "config.ini"
        config_file.write_text(
            "[transcription]\nmodel_path = small\n", encoding="utf-8"
        )
        settings = Settings(config_path=str(config_file))
        assert settings.get("transcription.model_path") == "small"
        assert settings.get("transcription.missing", "fallback") == "fallback"
        assert settings.get("nonexistent.key", "default") == "default"

    def test_get_int(self, tmp_path):
        config_file = tmp_path / "config.ini"
        config_file.write_text(
            "[transcription]\nbeam_size = 10\ntemperature = 0.5\n",
            encoding="utf-8",
        )
        settings = Settings(config_path=str(config_file))
        assert settings.get_int("transcription.beam_size") == 10
        assert settings.get_float("transcription.temperature") == pytest.approx(0.5)

    def test_get_bool(self, tmp_path):
        config_file = tmp_path / "config.ini"
        config_file.write_text("[transcription]\nvad_filter = True\n", encoding="utf-8")
        settings = Settings(config_path=str(config_file))
        assert settings.get_bool("transcription.vad_filter") is True

    def test_get_list(self, tmp_path):
        config_file = tmp_path / "config.ini"
        config_file.write_text(
            "[output]\ntranscript_format = txt,srt,vtt\n",
            encoding="utf-8",
        )
        settings = Settings(config_path=str(config_file))
        result = settings.get_list("output.transcript_format")
        assert result == ["txt", "srt", "vtt"]

    def test_set_and_save(self, tmp_path):
        config_file = tmp_path / "config.ini"
        config_file.write_text("[transcription]\nbeam_size = 5\n", encoding="utf-8")
        settings = Settings(config_path=str(config_file))
        settings.set("transcription.beam_size", "15")
        settings.save()

        settings.reload()
        assert settings.get_int("transcription.beam_size") == 15

    def test_get_section(self, tmp_path):
        config_file = tmp_path / "config.ini"
        config_file.write_text(
            "[transcription]\nmodel_path = small\nbeam_size = 3\n",
            encoding="utf-8",
        )
        settings = Settings(config_path=str(config_file))
        section = settings.get_section("transcription")
        assert section["model_path"] == "small"
        assert section["beam_size"] == "3"

    def test_to_dict(self, tmp_path):
        config_file = tmp_path / "config.ini"
        config_file.write_text(
            "[transcription]\nmodel_path = small\n", encoding="utf-8"
        )
        settings = Settings(config_path=str(config_file))
        d = settings.to_dict()
        assert "transcription" in d
        assert d["transcription"]["model_path"] == "small"

    def test_update_from_dict(self, tmp_path):
        config_file = tmp_path / "config.ini"
        config_file.write_text("[transcription]\nbeam_size = 5\n", encoding="utf-8")
        settings = Settings(config_path=str(config_file))
        settings.update_from_dict({"transcription": {"beam_size": "20"}})
        assert settings.get_int("transcription.beam_size") == 20

    def test_missing_config_returns_none(self, tmp_path):
        config_file = tmp_path / "config.ini"
        settings = Settings(config_path=str(config_file))
        assert settings.get("transcription.model_path") is None
        assert settings.get("transcription.model_path", "large-v3") == "large-v3"
        assert settings.get_int("summarization.max_length", 5000) == 5000
