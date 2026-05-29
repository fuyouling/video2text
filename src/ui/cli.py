"""CLI命令定义"""

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from src.config.settings import Settings
from src.config.transcription_config import _load_tx_config
from src.config.version import APP_VERSION
from src.preprocessing.video_processor import VideoProcessor
from src.services.transcription_service import TranscriptionService
from src.services.summarization_service import SummarizationService
from src.storage.file_writer import FileWriter
from src.summarization.providers import create_provider
from src.text_processing.segment_merger import SegmentMerger
from src.text_processing.text_cleaner import TextCleaner
from src.transcription.transcriber import Transcriber
from src.utils.exceptions import (
    Video2TextError,
    VideoFileError,
    SummarizationError,
)
from src.utils.logger import setup_logger

app = typer.Typer(help="Video2Text - 媒体转文本工具")
console = Console()


def get_settings() -> Settings:
    """获取全局配置单例。"""
    return Settings()


def _init_common(
    settings: Settings, output_dir: str, verbose: bool = False
) -> tuple[VideoProcessor, FileWriter]:
    """CLI 公共初始化：日志、VideoProcessor、FileWriter"""
    log_level = "DEBUG" if verbose else settings.get("app.log_level", "INFO")
    setup_logger(
        "video2text",
        log_dir=settings.get("paths.logs_dir", "logs"),
        level=log_level,
    )

    video_processor = VideoProcessor()
    file_writer = FileWriter(output_dir)

    return video_processor, file_writer


@app.command()
def transcribe(
    input_path: str = typer.Argument(..., help="媒体文件路径（视频或音频）"),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", "-o", help="输出目录"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
):
    """转写媒体为文本"""
    try:
        settings = get_settings()

        output_dir = output_dir or settings.get("output.output_dir", "output")

        video_processor, file_writer = _init_common(settings, output_dir, verbose)

        cfg = _load_tx_config(settings)
        num_workers = settings.get_int("transcription.num_workers", 1)
        vad_filter = settings.get_bool("transcription.vad_filter", True)

        console.print(Panel.fit("[bold blue]Video2Text 转写模式[/bold blue]"))
        console.print(f"输入文件: {input_path}")
        console.print(f"输出目录: {output_dir}")
        console.print(f"模型: {cfg.model_path}")
        console.print(f"设备: {cfg.device}")

        transcriber = Transcriber(
            model_path=cfg.model_path,
            device=cfg.device,
            compute_type=cfg.compute_type,
            num_workers=num_workers,
        )

        service = TranscriptionService(
            transcriber=transcriber,
            video_processor=video_processor,
            file_writer=file_writer,
            language=cfg.language,
            beam_size=cfg.beam_size,
            best_of=cfg.best_of,
            temperature=cfg.temperature,
            condition_on_previous_text=cfg.condition_on_previous_text,
            word_timestamps=cfg.word_timestamps,
            vad_filter=vad_filter,
            max_chunk_duration=cfg.max_chunk_duration,
            output_formats=cfg.output_formats,
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
                for fmt in cfg.output_formats:
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

        provider_inst = create_provider(settings)
        service = None

        try:
            if not provider_inst.check_connection():
                provider_name = settings.get("summarization.provider", "ollama")
                raise SummarizationError(
                    f"无法连接到{provider_name}总结服务，请检查配置"
                )

            service = SummarizationService(
                settings=settings,
                file_writer=file_writer,
                provider=provider_inst,
                on_progress=lambda msg: console.print(f"  {msg}"),
            )

            service.summarize(text, video_name=video_name, index=1, total=1)

            summary_fmt = settings.get("output.summary_format", "txt").lower().strip()
            console.print(Panel.fit("[bold green]总结成功！[/bold green]"))
            console.print(f"输出文件: {output_dir}/{video_name}_summary.{summary_fmt}")
        finally:
            if service is not None:
                service.close()
            else:
                provider_inst.close()

    except Video2TextError as e:
        console.print(f"[bold red]错误: {e}[/bold red]")
        sys.exit(2)
    except Exception as e:
        console.print(f"[bold red]未知错误: {e}[/bold red]")
        sys.exit(1)


@app.command()
def run_pipeline(
    input_path: str = typer.Argument(..., help="媒体文件路径（视频或音频）"),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", "-o", help="输出目录"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
):
    """运行完整处理管道"""
    try:
        settings = get_settings()

        output_dir = output_dir or settings.get("output.output_dir", "output")

        video_processor, file_writer = _init_common(settings, output_dir, verbose)

        cfg = _load_tx_config(settings)
        num_workers = settings.get_int("transcription.num_workers", 1)
        vad_filter = settings.get_bool("transcription.vad_filter", True)

        console.print(Panel.fit("[bold blue]Video2Text 完整管道模式[/bold blue]"))
        console.print(f"输入文件: {input_path}")
        console.print(f"输出目录: {output_dir}")
        console.print(f"转写模型: {cfg.model_path}")
        console.print(f"设备: {cfg.device}")

        transcriber = Transcriber(
            model_path=cfg.model_path,
            device=cfg.device,
            compute_type=cfg.compute_type,
            num_workers=num_workers,
        )

        tx_service = TranscriptionService(
            transcriber=transcriber,
            video_processor=video_processor,
            file_writer=file_writer,
            language=cfg.language,
            beam_size=cfg.beam_size,
            best_of=cfg.best_of,
            temperature=cfg.temperature,
            condition_on_previous_text=cfg.condition_on_previous_text,
            word_timestamps=cfg.word_timestamps,
            vad_filter=vad_filter,
            max_chunk_duration=cfg.max_chunk_duration,
            output_formats=cfg.output_formats,
            on_progress=lambda msg: console.print(f"  {msg}"),
        )

        tx_service.transcriber.load_model()
        try:
            tx_results = tx_service.run([input_path], output_dir)

            if not tx_results:
                console.print("[bold red]转写失败，无法继续[/bold red]")
                sys.exit(2)

            segment_merger = SegmentMerger(
                max_gap=settings.get_float("text_processing.max_gap", 2.0),
                min_length=settings.get_int("text_processing.min_length", 50),
            )
            text_cleaner = TextCleaner(
                {
                    "filler_words": settings.get_list("text_processing.filler_words"),
                }
            )

            summary_map: dict[str, tuple[str, str]] = {}
            for tx_result in tx_results:
                merged = segment_merger.merge_segments(tx_result.segments)
                processed_text = segment_merger.format_segments_as_text(
                    merged, include_timestamps=False
                )
                processed_text = text_cleaner.clean(processed_text)
                summary_map[tx_result.video_name] = (processed_text, "总结不可用")

            provider_inst = create_provider(settings)
            sum_available = provider_inst.check_connection()
            if not sum_available:
                provider_name = settings.get("summarization.provider", "ollama")
                console.print(
                    f"[yellow]警告: 无法连接到{provider_name}总结服务，跳过总结[/yellow]"
                )
                provider_inst.close()
            else:
                sum_service = None
                try:
                    sum_service = SummarizationService(
                        settings=settings,
                        file_writer=file_writer,
                        provider=provider_inst,
                        on_progress=lambda msg: console.print(f"  {msg}"),
                    )
                    for idx, tx_result in enumerate(tx_results):
                        processed_text = summary_map[tx_result.video_name][0]
                        try:
                            summary = sum_service.summarize(
                                processed_text,
                                video_name=tx_result.video_name,
                                index=idx + 1,
                                total=len(tx_results),
                            )
                            summary_map[tx_result.video_name] = (
                                processed_text,
                                summary or "总结不可用",
                            )
                        except Exception as e:
                            console.print(
                                f"[yellow]警告: {tx_result.video_name} 总结失败: {e}[/yellow]"
                            )
                finally:
                    if sum_service is not None:
                        sum_service.close()
                    else:
                        provider_inst.close()

            console.print(Panel.fit("[bold green]处理成功！[/bold green]"))
            console.print(f"输出目录: {output_dir}")
            summary_fmt = settings.get("output.summary_format", "txt").lower().strip()
            for tx_result in tx_results:
                for fmt in cfg.output_formats:
                    console.print(f"  - {tx_result.video_name}.{fmt} (转写结果)")
                console.print(
                    f"  - {tx_result.video_name}_summary.{summary_fmt} (摘要)"
                )
        finally:
            tx_service.transcriber.unload_model()

    except Video2TextError as e:
        console.print(f"[bold red]错误: {e}[/bold red]")
        sys.exit(2)
    except Exception as e:
        console.print(f"[bold red]未知错误: {e}[/bold red]")
        sys.exit(1)


@app.command()
def version():
    """显示版本信息"""
    console.print(f"Video2Text v{APP_VERSION}")


@app.command()
def help_command():
    """显示所有命令的详细用法"""
    console.print(Panel.fit("[bold blue]Video2Text 命令帮助[/bold blue]"))
    console.print("\n[bold]可用命令:[/bold]\n")

    commands = [
        {
            "name": "transcribe",
            "description": "转写媒体为文本",
            "usage": "video2text transcribe <媒体文件路径> [选项]",
            "options": [
                ("--output-dir, -o", "输出目录"),
                ("--verbose, -v", "详细输出"),
            ],
        },
        {
            "name": "summarize",
            "description": "总结转写文本",
            "usage": "video2text summarize <转写文本文件路径> [选项]",
            "options": [
                ("--output-dir, -o", "输出目录"),
                ("--verbose, -v", "详细输出"),
            ],
        },
        {
            "name": "run-pipeline",
            "description": "运行完整处理管道（转写总结）",
            "usage": "video2text run-pipeline <媒体文件路径> [选项]",
            "options": [
                ("--output-dir, -o", "输出目录"),
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
    console.print("  video2text transcribe video.mp4 -o output")
    console.print("  video2text summarize transcript.txt -o output")
    console.print("  video2text run-pipeline video.mp4 -o output")
    console.print("\n[bold]提示:[/bold] 1. 使用 --help 查看单个命令的详细选项")
    console.print(
        "      2. 所有转写和总结参数（模型、语言、设备、温度等）均通过 config.ini 配置"
    )
    console.print(
        "      3. powershell使用全路径调用可执行文件，如: .\\video2text.exe transcribe video.mp4 -o output"
    )


if __name__ == "__main__":
    app()
