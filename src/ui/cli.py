"""CLI命令定义"""

import sys
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from src.config.settings import Settings
from src.preprocessing.video_processor import VideoProcessor
from src.services.transcription_service import TranscriptionService
from src.services.summarization_service import SummarizationService
from src.storage.file_writer import FileWriter
from src.storage.output_formatter import OutputFormatter
from src.text_processing.segment_merger import SegmentMerger
from src.text_processing.text_cleaner import TextCleaner
from src.transcription.transcriber import Transcriber
from src.utils.exceptions import (
    Video2TextError,
    VideoFileError,
    SummarizationError,
)
from src.utils.logger import setup_logger, get_logger
from src.utils.validators import validate_executable_path

app = typer.Typer(help="Video2Text - 视频转文本工具")
console = Console()

SUPPORTED_TRANSCRIPT_FORMATS = {"txt", "srt", "vtt", "json"}


def get_settings() -> Settings:
    return Settings()


def get_transcript_output_formats(settings: Settings) -> list[str]:
    formats = [
        fmt.lower() for fmt in settings.get_list("output.transcript_format", ["txt"])
    ]
    formats = [fmt for fmt in formats if fmt in SUPPORTED_TRANSCRIPT_FORMATS]
    return formats if formats else ["txt"]


def get_model_path(settings: Settings, model_name: Optional[str] = None) -> str:
    if model_name is None:
        model_name = settings.get("transcription.model_path", "large-v3")

    models_dir = settings.get("paths.models_dir", "models")

    if Path(model_name).is_absolute():
        return model_name

    if Path(model_name).exists():
        return str(Path(model_name).resolve())

    model_path = Path(models_dir) / model_name
    if model_path.exists():
        return str(model_path)

    logger = get_logger(__name__)
    logger.warning(f"本地模型不存在: {model_path}，将尝试从Hugging Face下载")
    return model_name


def _init_common(
    settings: Settings, output_dir: str, verbose: bool = False
) -> tuple[VideoProcessor, FileWriter, list[str]]:
    """CLI 公共初始化：日志、FFmpeg路径、VideoProcessor、FileWriter"""
    log_level = "DEBUG" if verbose else settings.get("app.log_level", "INFO")
    setup_logger(
        "video2text",
        log_dir=settings.get("paths.logs_dir", "logs"),
        level=log_level,
    )

    ffmpeg_path = settings.get("preprocessing.ffmpeg_path", "ffmpeg")
    try:
        ffmpeg_path = validate_executable_path(ffmpeg_path, "FFmpeg")
    except Exception as e:
        raise VideoFileError(str(e)) from e

    video_processor = VideoProcessor(ffmpeg_path=ffmpeg_path)
    file_writer = FileWriter(output_dir)
    output_formats = get_transcript_output_formats(settings)

    return video_processor, file_writer, output_formats


@app.command()
def transcribe(
    input_path: str = typer.Argument(..., help="视频文件路径"),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", "-o", help="输出目录"
    ),
    language: Optional[str] = typer.Option(None, "--language", "-l", help="语言代码"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="转写模型"),
    device: Optional[str] = typer.Option(None, "--device", "-d", help="设备类型"),
    beam_size: Optional[int] = typer.Option(
        None, "--beam-size", help="beam search大小"
    ),
    temperature: Optional[float] = typer.Option(None, "--temperature", help="温度参数"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
):
    """转写视频为文本"""
    try:
        settings = get_settings()

        output_dir = output_dir or settings.get("output.output_dir", "output")
        language = language or settings.get("transcription.language", "auto")
        model = model or settings.get("transcription.model_path", "large-v3")
        device = device or settings.get("transcription.device", "auto")
        beam_size = (
            beam_size
            if beam_size is not None
            else settings.get_int("transcription.beam_size", 5)
        )
        temperature = (
            temperature
            if temperature is not None
            else settings.get_float("transcription.temperature", 0.0)
        )

        model_path = get_model_path(settings, model)

        video_processor, file_writer, output_formats = _init_common(
            settings, output_dir, verbose
        )

        console.print(Panel.fit("[bold blue]Video2Text 转写模式[/bold blue]"))
        console.print(f"输入文件: {input_path}")
        console.print(f"输出目录: {output_dir}")
        console.print(f"模型: {model_path}")
        console.print(f"设备: {device}")

        transcriber = Transcriber(
            model_path=model_path,
            device=device,
            compute_type=settings.get("transcription.compute_type", "float16"),
            num_workers=settings.get_int("transcription.num_workers", 1),
        )

        service = TranscriptionService(
            transcriber=transcriber,
            video_processor=video_processor,
            file_writer=file_writer,
            language=language,
            beam_size=beam_size,
            temperature=temperature,
            vad_filter=settings.get_bool("transcription.vad_filter", True),
            max_chunk_duration=settings.get_int(
                "preprocessing.max_chunk_duration", 300
            ),
            output_formats=output_formats,
            on_progress=lambda msg: console.print(f"  {msg}"),
        )

        service.transcriber.load_model()
        try:
            results = service.run([input_path], output_dir)
        finally:
            service.transcriber.unload_model()

        if results:
            console.print(Panel.fit("[bold green]转写成功！[/bold green]"))
            console.print(f"输出目录: {output_dir}")
            for r in results:
                for fmt in output_formats:
                    console.print(f"  - {r.video_name}.{fmt}")
        else:
            console.print("[bold red]转写失败[/bold red]")
            sys.exit(2)

    except Video2TextError as e:
        console.print(f"[bold red]错误: {e}[/bold red]")
        sys.exit(2)
    except Exception as e:
        console.print(f"[bold red]未知错误: {e}[/bold red]")
        sys.exit(1)


@app.command()
def summarize(
    input_path: str = typer.Argument(..., help="转写文本文件路径"),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", "-o", help="输出目录"
    ),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="总结模型"),
    max_length: Optional[int] = typer.Option(None, "--max-length", help="最大长度"),
    temperature: Optional[float] = typer.Option(None, "--temperature", help="温度参数"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
):
    """总结转写文本"""
    try:
        settings = get_settings()

        output_dir = output_dir or settings.get("output.output_dir", "output")

        log_level = "DEBUG" if verbose else settings.get("app.log_level", "INFO")
        setup_logger(
            "video2text",
            log_dir=settings.get("paths.logs_dir", "logs"),
            level=log_level,
        )

        console.print(Panel.fit("[bold blue]Video2Text 总结模式[/bold blue]"))
        console.print(f"输入文件: {input_path}")
        console.print(f"输出目录: {output_dir}")

        text_path = Path(input_path)
        if not text_path.exists():
            raise VideoFileError(f"文件不存在: {input_path}")

        text = text_path.read_text(encoding="utf-8-sig")
        console.print(f"文本长度: {len(text)} 字符")

        file_writer = FileWriter(output_dir)
        video_name = text_path.stem

        service = SummarizationService(
            settings=settings,
            file_writer=file_writer,
            model_name=model,
            temperature=temperature,
            max_length=max_length,
            on_progress=lambda msg: console.print(f"  {msg}"),
        )

        try:
            if not service.check_connection():
                raise SummarizationError("无法连接到Ollama服务")

            if not service.check_model():
                raise SummarizationError(f"模型 {service.model_name} 不存在")

            service.summarize(text, video_name=video_name)

            console.print(Panel.fit("[bold green]总结成功！[/bold green]"))
            console.print(f"输出文件: {output_dir}/{video_name}_summary.txt")
        finally:
            service.close()

    except Video2TextError as e:
        console.print(f"[bold red]错误: {e}[/bold red]")
        sys.exit(2)
    except Exception as e:
        console.print(f"[bold red]未知错误: {e}[/bold red]")
        sys.exit(1)


@app.command()
def run_pipeline(
    input_path: str = typer.Argument(..., help="视频文件路径"),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", "-o", help="输出目录"
    ),
    language: Optional[str] = typer.Option(None, "--language", "-l", help="语言代码"),
    transcription_model: Optional[str] = typer.Option(
        None, "--transcription-model", help="转写模型"
    ),
    summarization_model: Optional[str] = typer.Option(
        None, "--summarization-model", help="总结模型"
    ),
    device: Optional[str] = typer.Option(None, "--device", "-d", help="设备类型"),
    beam_size: Optional[int] = typer.Option(
        None, "--beam-size", help="beam search大小"
    ),
    temperature: Optional[float] = typer.Option(
        None, "--temperature", help="转写温度参数"
    ),
    summary_temperature: Optional[float] = typer.Option(
        None, "--summary-temperature", help="总结温度参数"
    ),
    max_length: Optional[int] = typer.Option(None, "--max-length", help="最大长度"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
):
    """运行完整处理管道"""
    try:
        settings = get_settings()

        output_dir = output_dir or settings.get("output.output_dir", "output")
        language = language or settings.get("transcription.language", "auto")
        transcription_model = transcription_model or settings.get(
            "transcription.model_path", "large-v3"
        )
        summarization_model = summarization_model or settings.get(
            "summarization.model_name", "qwen2.5:7b-instruct-q4_K_M"
        )
        device = device or settings.get("transcription.device", "auto")
        beam_size = (
            beam_size
            if beam_size is not None
            else settings.get_int("transcription.beam_size", 5)
        )
        temperature = (
            temperature
            if temperature is not None
            else settings.get_float("transcription.temperature", 0.0)
        )
        summary_temperature = (
            summary_temperature
            if summary_temperature is not None
            else settings.get_float("summarization.temperature", 0.7)
        )
        max_length = (
            max_length
            if max_length is not None
            else settings.get_int("summarization.max_length", 5000)
        )

        model_path = get_model_path(settings, transcription_model)

        video_processor, file_writer, output_formats = _init_common(
            settings, output_dir, verbose
        )

        console.print(Panel.fit("[bold blue]Video2Text 完整管道模式[/bold blue]"))
        console.print(f"输入文件: {input_path}")
        console.print(f"输出目录: {output_dir}")
        console.print(f"转写模型: {model_path}")
        console.print(f"总结模型: {summarization_model}")

        start_time = time.time()
        sum_service = None

        transcriber = Transcriber(
            model_path=model_path,
            device=device,
            compute_type=settings.get("transcription.compute_type", "float16"),
            num_workers=settings.get_int("transcription.num_workers", 1),
        )

        # 转写阶段
        tx_service = TranscriptionService(
            transcriber=transcriber,
            video_processor=video_processor,
            file_writer=file_writer,
            language=language,
            beam_size=beam_size,
            temperature=temperature,
            vad_filter=settings.get_bool("transcription.vad_filter", True),
            max_chunk_duration=settings.get_int(
                "preprocessing.max_chunk_duration", 300
            ),
            output_formats=output_formats,
            on_progress=lambda msg: console.print(f"  {msg}"),
        )

        tx_service.transcriber.load_model()
        try:
            tx_results = tx_service.run([input_path], output_dir)

            if not tx_results:
                console.print("[bold red]转写失败，无法继续[/bold red]")
                sys.exit(2)

            # 总结阶段
            segment_merger = SegmentMerger(
                max_gap=settings.get_float("text_processing.max_gap", 2.0),
                min_length=settings.get_int("text_processing.min_length", 50),
            )
            text_cleaner = TextCleaner()

            sum_service = SummarizationService(
                settings=settings,
                file_writer=file_writer,
                model_name=summarization_model,
                temperature=summary_temperature,
                max_length=max_length,
                on_progress=lambda msg: console.print(f"  {msg}"),
            )

            summary_map: dict[str, tuple[str, str]] = {}
            for tx_result in tx_results:
                merged = segment_merger.merge_segments(tx_result.segments)
                processed_text = segment_merger.format_segments_as_text(
                    merged, include_timestamps=False
                )
                processed_text = text_cleaner.clean(processed_text)
                summary_map[tx_result.video_name] = (processed_text, "总结不可用")

            sum_available = sum_service.check_connection()
            if not sum_available:
                console.print("[yellow]警告: 无法连接到Ollama服务，跳过总结[/yellow]")
            elif not sum_service.check_model():
                console.print(
                    f"[yellow]警告: 模型 {summarization_model} 不存在，跳过总结[/yellow]"
                )
                sum_available = False
            else:
                for tx_result in tx_results:
                    processed_text = summary_map[tx_result.video_name][0]
                    try:
                        summary = sum_service.summarize(
                            processed_text, video_name=tx_result.video_name
                        )
                        summary_map[tx_result.video_name] = (
                            processed_text,
                            summary or "总结不可用",
                        )
                    except Exception as e:
                        console.print(
                            f"[yellow]警告: {tx_result.video_name} 总结失败: {e}[/yellow]"
                        )

            # 保存完整数据
            json_output = settings.get_bool("output.json_output", False)
            if json_output:
                video_info = video_processor.get_video_info(input_path)
                formatter = OutputFormatter()
                for tx_result in tx_results:
                    video_name = tx_result.video_name
                    processed_text, summary = summary_map.get(
                        video_name, ("", "总结不可用")
                    )
                    output_data = formatter.create_output_data(
                        video_name=video_name,
                        video_path=input_path,
                        duration=video_info.duration,
                        transcript_segments=tx_result.segments,
                        processed_text=processed_text,
                        summary=summary,
                        processing_time=time.time() - start_time,
                    )
                    file_writer.write_output_data(output_data, video_name)

            console.print(Panel.fit("[bold green]处理成功！[/bold green]"))
            console.print(f"输出目录: {output_dir}")
            for tx_result in tx_results:
                for fmt in output_formats:
                    console.print(f"  - {tx_result.video_name}.{fmt} (转写结果)")
                console.print(f"  - {tx_result.video_name}_summary.txt (摘要)")
                if settings.get_bool("output.json_output", False):
                    console.print(f"  - {tx_result.video_name}_full.json (完整数据)")
        finally:
            tx_service.transcriber.unload_model()
            if sum_service is not None:
                sum_service.close()

    except Video2TextError as e:
        console.print(f"[bold red]错误: {e}[/bold red]")
        sys.exit(2)
    except Exception as e:
        console.print(f"[bold red]未知错误: {e}[/bold red]")
        sys.exit(1)


@app.command()
def version():
    """显示版本信息"""
    settings = get_settings()
    ver = settings.get("app.version", "unknown")
    console.print(f"Video2Text v{ver}")


@app.command()
def help_command():
    """显示所有命令的详细用法"""
    console.print(Panel.fit("[bold blue]Video2Text 命令帮助[/bold blue]"))
    console.print("\n[bold]可用命令:[/bold]\n")

    commands = [
        {
            "name": "transcribe",
            "description": "转写视频为文本",
            "usage": "video2text transcribe <视频文件路径> [选项]",
            "options": [
                ("--output-dir, -o", "输出目录"),
                ("--language, -l", "语言代码 (如: zh, en, auto)"),
                ("--model, -m", "转写模型 (如: large-v3, base)"),
                ("--device, -d", "设备类型 (如: auto, cpu, cuda)"),
                ("--beam-size", "beam search大小"),
                ("--temperature", "温度参数"),
                ("--verbose, -v", "详细输出"),
            ],
        },
        {
            "name": "summarize",
            "description": "总结转写文本",
            "usage": "video2text summarize <转写文本文件路径> [选项]",
            "options": [
                ("--output-dir, -o", "输出目录"),
                ("--model, -m", "总结模型 (如: qwen2.5:7b-instruct-q4_K_M)"),
                ("--max-length", "最大长度"),
                ("--temperature", "温度参数"),
                ("--verbose, -v", "详细输出"),
            ],
        },
        {
            "name": "run-pipeline",
            "description": "运行完整处理管道（转写+总结）",
            "usage": "video2text run-pipeline <视频文件路径> [选项]",
            "options": [
                ("--output-dir, -o", "输出目录"),
                ("--language, -l", "语言代码"),
                ("--transcription-model", "转写模型"),
                ("--summarization-model", "总结模型"),
                ("--device, -d", "设备类型"),
                ("--beam-size", "beam search大小"),
                ("--temperature", "转写温度参数"),
                ("--summary-temperature", "总结温度参数"),
                ("--max-length", "最大长度"),
                ("--verbose, -v", "详细输出"),
            ],
        },
        {
            "name": "version",
            "description": "显示版本信息",
            "usage": "video2text version",
            "options": [],
        },
        {
            "name": "--help",
            "description": "显示所有命令的详细用法",
            "usage": "video2text help",
            "options": [],
        },
    ]

    for cmd in commands:
        console.print(f"[bold cyan]{cmd['name']}[/bold cyan] - {cmd['description']}")
        console.print(f"  用法: {cmd['usage']}")
        if cmd["options"]:
            console.print("  选项:")
            for opt, desc in cmd["options"]:
                console.print(f"    {opt:<30} {desc}")
        console.print()

    console.print("[bold]示例:[/bold]")
    console.print("  video2text transcribe video.mp4 -o output -l zh")
    console.print("  video2text summarize transcript.txt -o output")
    console.print("  video2text run-pipeline video.mp4 -o output")
    console.print("\n[bold]提示:[/bold] 1. 使用 --help 查看单个命令的详细选项")
    console.print(
        "      2. powershell使用全路径调用可执行文件，如: .\\video2text.exe transcribe video.mp4 -o output -l zh   "
    )


if __name__ == "__main__":
    app()
