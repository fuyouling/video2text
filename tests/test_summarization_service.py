"""SummarizationService 单元测试"""

from unittest.mock import MagicMock, patch

import pytest

from src.services.summarization_service import SummarizationService
from src.utils.exceptions import SummarizationError


@pytest.fixture
def mock_provider():
    p = MagicMock()
    p.summarize.return_value = "这是总结结果"
    return p


@pytest.fixture
def mock_file_writer():
    return MagicMock()


@pytest.fixture
def service(mock_provider, mock_file_writer):
    settings = MagicMock()
    settings.get.return_value = "txt"
    return SummarizationService(
        settings=settings,
        file_writer=mock_file_writer,
        provider=mock_provider,
    )


class TestSummarizationService:
    def test_summarize_basic(self, service, mock_provider):
        result = service.summarize("一些文本内容", video_name="test_video")
        assert result == "这是总结结果"
        mock_provider.summarize.assert_called_once()

    def test_summarize_empty_text_raises(self, service):
        with pytest.raises(SummarizationError, match="输入文本为空"):
            service.summarize("")

    def test_summarize_whitespace_only_raises(self, service):
        with pytest.raises(SummarizationError, match="输入文本为空"):
            service.summarize("   ")

    def test_summarize_empty_result_raises(self, service, mock_provider):
        mock_provider.summarize.return_value = ""
        with pytest.raises(SummarizationError, match="模型返回空总结"):
            service.summarize("some text")

    def test_summarize_saves_file_when_video_name_given(
        self, service, mock_file_writer
    ):
        service.summarize("text", video_name="video1")
        mock_file_writer.write_summary.assert_called_once_with(
            "这是总结结果", "video1", fmt="txt"
        )

    def test_summarize_no_save_without_video_name(self, service, mock_file_writer):
        service.summarize("text")
        mock_file_writer.write_summary.assert_not_called()

    def test_summarize_with_custom_prompt(self, service, mock_provider):
        service.custom_prompt = "自定义提示词"
        service.summarize("text", video_name="v1")
        call_kwargs = mock_provider.summarize.call_args
        assert call_kwargs[1]["custom_prompt"] == "自定义提示词"

    def test_summarize_stream_callback(self, service, mock_provider):
        tokens = []

        def on_token(t):
            tokens.append(t)

        service.on_stream_token = on_token

        def fake_summarize(
            text, custom_prompt="", stream=False, on_token=None, cancel_check=None
        ):
            if stream and on_token:
                on_token("token1")
                on_token("token2")
            return "full result"

        mock_provider.summarize.side_effect = fake_summarize
        service.summarize("text", stream=True)
        assert tokens == ["token1", "token2"]

    def test_close(self, service, mock_provider):
        service.close()
        mock_provider.close.assert_called_once()
