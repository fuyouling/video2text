"""validators 单元测试"""

import pytest
from src.utils.validators import (
    validate_file_path,
    validate_directory,
    validate_language,
    validate_device,
    validate_positive_int,
    validate_float_range,
    validate_executable_path,
)
from src.utils.exceptions import VideoFileError, ConfigurationError


class TestValidateFilePath:
    def test_valid_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = validate_file_path(str(f))
        assert result == f

    def test_nonexistent_file(self):
        with pytest.raises(VideoFileError, match="不存在"):
            validate_file_path("/nonexistent/file.txt")

    def test_allowed_extensions(self, tmp_path):
        f = tmp_path / "test.mp4"
        f.write_text("fake")
        result = validate_file_path(str(f), allowed_extensions=[".mp4", ".avi"])
        assert result == f

    def test_disallowed_extension(self, tmp_path):
        f = tmp_path / "test.xyz"
        f.write_text("fake")
        with pytest.raises(VideoFileError, match="不支持"):
            validate_file_path(str(f), allowed_extensions=[".mp4"])


class TestValidateDirectory:
    def test_existing_dir(self, tmp_path):
        result = validate_directory(str(tmp_path))
        assert result == tmp_path

    def test_create_dir(self, tmp_path):
        new_dir = tmp_path / "new"
        result = validate_directory(str(new_dir), create=True)
        assert result.exists()

    def test_nonexistent_no_create(self, tmp_path):
        with pytest.raises(ConfigurationError, match="不存在"):
            validate_directory(str(tmp_path / "nonexistent"))


class TestValidateLanguage:
    def test_auto(self):
        assert validate_language("auto", ["zh", "en"]) == "auto"

    def test_valid(self):
        assert validate_language("zh", ["zh", "en"]) == "zh"

    def test_invalid(self):
        with pytest.raises(ConfigurationError, match="不支持"):
            validate_language("fr", ["zh", "en"])


class TestValidateDevice:
    def test_valid_devices(self):
        assert validate_device("auto") == "auto"
        assert validate_device("cpu") == "cpu"
        assert validate_device("cuda") == "cuda"

    def test_invalid_device(self):
        with pytest.raises(ConfigurationError, match="不支持"):
            validate_device("tpu")


class TestValidatePositiveInt:
    def test_valid(self):
        assert validate_positive_int(5, "test") == 5

    def test_zero(self):
        with pytest.raises(ConfigurationError, match="正整数"):
            validate_positive_int(0, "test")

    def test_negative(self):
        with pytest.raises(ConfigurationError, match="正整数"):
            validate_positive_int(-1, "test")


class TestValidateFloatRange:
    def test_valid(self):
        assert validate_float_range(0.5, "test", 0.0, 1.0) == 0.5

    def test_out_of_range(self):
        with pytest.raises(ConfigurationError, match="之间"):
            validate_float_range(1.5, "test", 0.0, 1.0)


class TestValidateExecutablePath:
    def test_empty_path(self):
        with pytest.raises(ConfigurationError, match="不能为空"):
            validate_executable_path("")

    def test_dangerous_chars(self):
        with pytest.raises(ConfigurationError, match="不安全"):
            validate_executable_path("ffmpeg & rm -rf /")

    def test_semicolon(self):
        with pytest.raises(ConfigurationError, match="不安全"):
            validate_executable_path("ffmpeg;rm")

    def test_known_executable(self):
        # python should be findable
        result = validate_executable_path("python")
        assert result is not None
        assert len(result) > 0
