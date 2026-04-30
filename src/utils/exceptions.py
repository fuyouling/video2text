"""自定义异常类"""


class Video2TextError(Exception):
    """基础异常类"""

    pass


class VideoFileError(Video2TextError):
    """视频文件错误"""

    pass


class TranscriptionError(Video2TextError):
    """转写错误"""

    pass


class SummarizationError(Video2TextError):
    """总结错误"""

    pass


class ConfigurationError(Video2TextError):
    """配置错误"""

    pass


class ExternalServiceError(Video2TextError):
    """外部服务错误"""

    pass


class OutputError(Video2TextError):
    """输出文件错误"""

    pass
