"""自定义异常类"""


class Video2TextError(Exception):
    """基础异常类"""

    pass


class VideoFileError(Video2TextError):
    """音视频文件错误"""

    pass


class TranscriptionError(Video2TextError):
    """转写错误"""

    pass


class DownloadCancelledError(TranscriptionError):
    """用户取消了模型下载"""

    pass


class TranscriptionCancelledError(TranscriptionError):
    """用户在转写过程中取消了任务（用于中断正在进行的转写）"""

    pass


class SummarizationError(Video2TextError):
    """总结错误"""

    pass


class ConfigurationError(Video2TextError):
    """配置错误"""

    pass


class OutputError(Video2TextError):
    """输出文件错误"""

    pass
