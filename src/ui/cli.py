"""CLI命令定义"""

import sys
import time
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.panel import Panel
import subprocess
import shutil
import tempfile

from src.config.settings import Settings
from src.preprocessing.video_processor import VideoProcessor
from src.transcription.transcriber import Transcriber
from src.text_processing.text_cleaner import TextCleaner
from src.text_processing.segment_merger import SegmentMerger
from src.summarization.summarizer import Summarizer
from src.storage.file_writer import FileWriter
from src.storage.output_formatter import OutputFormatter
from src.utils.logger import setup_logger, get_logger
from src.utils.exceptions import (
    Video2TextError,
    VideoFileError,
    TranscriptionError,
    SummarizationError,
)
from src.ui.progress import ProgressTracker, SimpleProgress

app = typer.Typer(help="Video2Text - 视频转文本工具")
console = Console()

SUPPORTED_TRANSCRIPT_FORMATS = {"txt", "srt", "vtt", "json"}

if sys.platform == "win32":
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    CREATE_NO_WINDOW = 0


def get_settings() -> Settings:
    """获取配置"""
    return Settings()


def get_transcript_output_formats(settings: Settings) -> list[str]:
    """获取转写输出格式列表"""
    formats = [
        fmt.lower() for fmt in settings.get_list("output.transcript_format", ["txt"])
    ]
    formats = [fmt for fmt in formats if fmt in SUPPORTED_TRANSCRIPT_FORMATS]

    if not formats:
        return ["txt"]

    return formats


def write_transcript_outputs(
    file_writer: FileWriter,
    segments: list,
    video_name: str,
    formats: list[str],
) -> list[str]:
    """按指定格式写入转写结果"""
    output_paths = []

    for output_format in formats:
        output_paths.append(
            file_writer.write_transcript(segments, video_name, format=output_format)
        )

    return output_paths


def get_model_path(settings: Settings, model_name: Optional[str] = None) -> str:
    """获取完整的模型路径

    Args:
        settings: 配置对象
        model_name: 模型名称（可选）

    Returns:
        完整的模型路径
    """
    if model_name is None:
        model_name = settings.get("transcription.model_path", "large-v3")

    models_dir = settings.get("paths.models_dir", "models")

    # 如果是绝对路径，直接使用
    if Path(model_name).is_absolute():
        return model_name

    # 如果是相对路径且已存在，直接使用
    if Path(model_name).exists():
        return str(Path(model_name).resolve())

    # 否则，拼接models_dir
    model_path = Path(models_dir) / model_name

    # 检查模型是否存在
    if model_path.exists():
        return str(model_path)

    # 如果本地不存在，返回模型名称（让faster_whisper去下载）
    logger = get_logger(__name__)
    logger.warning(f"本地模型不存在: {model_path}，将尝试从Hugging Face下载")
    return model_name


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

        # 从配置文件获取默认值
        output_dir = output_dir or settings.get("output.output_dir", "output")
        language = language or settings.get("transcription.language", "auto")
        model = model or settings.get("transcription.model_path", "large-v3")
        device = device or settings.get("transcription.device", "auto")
        beam_size = beam_size or settings.get_int("transcription.beam_size", 5)
        temperature = (
            temperature
            if temperature is not None
            else settings.get_float("transcription.temperature", 0.0)
        )

        # 获取完整模型路径
        model_path = get_model_path(settings, model)

        # 设置日志
        log_level = "DEBUG" if verbose else settings.get("app.log_level", "INFO")
        setup_logger(
            "video2text",
            log_dir=settings.get("paths.logs_dir", "logs"),
            level=log_level,
        )

        console.print(Panel.fit(f"[bold blue]Video2Text 转写模式[/bold blue]"))
        console.print(f"输入文件: {input_path}")
        console.print(f"输出目录: {output_dir}")
        console.print(f"模型: {model_path}")
        console.print(f"设备: {device}")

        progress = ProgressTracker(5, "转写进度")

        video_processor = VideoProcessor(
            ffmpeg_path=settings.get("preprocessing.ffmpeg_path", "ffmpeg")
        )
        progress.update(1, "验证视频文件")
        video_processor.validate_video(input_path)

        video_info = video_processor.get_video_info(input_path)
        console.print(f"视频时长: {video_info.duration:.2f}秒")

        progress.update(1, "提取音频")
        audio_path = Path(output_dir) / "temp_audio.wav"
        video_processor.extract_audio(
            input_path,
            str(audio_path),
            sample_rate=settings.get_int("preprocessing.audio_sample_rate", 16000),
            channels=settings.get_int("preprocessing.audio_channels", 1),
        )

        progress.update(1, "加载转写模型")
        transcriber = Transcriber(
            model_path=model_path,
            device=device,
            compute_type=settings.get("transcription.compute_type", "float16"),
            num_workers=settings.get_int("transcription.num_workers", 1),
        )
        transcriber.load_model()

        progress.update(1, "执行转写")
        # 最大音频块时长（秒），可在配置中自定义
        max_chunk_duration = settings.get_int("preprocessing.max_chunk_duration", 300)
        if video_info.duration > max_chunk_duration:
            # 使用临时目录存放切片文件
            chunk_dir_path = tempfile.mkdtemp(prefix="audio_chunks_", dir=output_dir)
            chunk_dir = Path(chunk_dir_path)
            try:
                split_cmd = [
                    video_processor.ffmpeg_path,
                    "-i",
                    str(audio_path),
                    "-f",
                    "segment",
                    "-segment_time",
                    str(max_chunk_duration),
                    "-acodec",
                    "pcm_s16le",
                    "-reset_timestamps",
                    "1",
                    str(chunk_dir / "chunk_%03d.wav"),
                ]
                try:
                    subprocess.run(
                        split_cmd,
                        capture_output=True,
                        text=True,
                        check=True,
                        creationflags=CREATE_NO_WINDOW,
                        encoding="utf-8",
                        errors="ignore",
                    )
                except subprocess.CalledProcessError as e:
                    raise Video2TextError(
                        f"音频切片失败: {e.stderr.strip() or e.stdout.strip()}"
                    )
                chunk_files = sorted(chunk_dir.glob("chunk_*.wav"))
                # 初始化切片转写进度条
                chunk_progress = ProgressTracker(len(chunk_files), "切片转写进度")
                all_segments: list = []
                cumulative_offset = 0.0
                for idx, chunk_path in enumerate(chunk_files):
                    chunk_segments = transcriber.transcribe(
                        str(chunk_path),
                        language=language,
                        beam_size=beam_size,
                        temperature=temperature,
                        vad_filter=settings.get_bool("transcription.vad_filter", True),
                    )
                    # 更新切片转写进度
                    chunk_progress.update(1, f"转写块 {idx + 1}/{len(chunk_files)}")
                    # 调整时间戳
                    for seg in chunk_segments:
                        seg.start += cumulative_offset
                        seg.end += cumulative_offset
                    # 更新累积偏移量，以实际块时长为准
                    if chunk_segments:
                        cumulative_offset += max(s.end for s in chunk_segments)
                    all_segments.extend(chunk_segments)
                chunk_progress.complete("切片转写完成")
                segments = all_segments
            finally:
                # 确保临时文件被删除
                shutil.rmtree(chunk_dir, ignore_errors=True)
        else:
            segments = transcriber.transcribe(
                str(audio_path),
                language=language,
                beam_size=beam_size,
                temperature=temperature,
                vad_filter=settings.get_bool("transcription.vad_filter", True),
            )

        progress.update(1, "保存结果")
        file_writer = FileWriter(output_dir)
        video_name = Path(input_path).stem
        output_formats = get_transcript_output_formats(settings)

        write_transcript_outputs(file_writer, segments, video_name, output_formats)

        progress.complete(f"转写完成，共 {len(segments)} 个段落")

        console.print(Panel.fit(f"[bold green]转写成功！[/bold green]"))
        console.print(f"输出目录: {output_dir}")
        for output_format in output_formats:
            console.print(f"  - {video_name}.{output_format}")

        audio_path.unlink(missing_ok=True)

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

        # 从配置文件获取默认值
        output_dir = output_dir or settings.get("output.output_dir", "output")
        model = model or settings.get(
            "summarization.model_name", "qwen2.5:7b-instruct-q4_K_M"
        )
        max_length = max_length or settings.get_int("summarization.max_length", 500)
        temperature = (
            temperature
            if temperature is not None
            else settings.get_float("summarization.temperature", 0.7)
        )

        # 设置日志
        log_level = "DEBUG" if verbose else settings.get("app.log_level", "INFO")
        setup_logger(
            "video2text",
            log_dir=settings.get("paths.logs_dir", "logs"),
            level=log_level,
        )

        console.print(Panel.fit(f"[bold blue]Video2Text 总结模式[/bold blue]"))
        console.print(f"输入文件: {input_path}")
        console.print(f"输出目录: {output_dir}")

        progress = ProgressTracker(3, "总结进度")

        progress.update(1, "读取转写文本")
        text_path = Path(input_path)
        if not text_path.exists():
            raise VideoFileError(f"文件不存在: {input_path}")

        with open(text_path, "r", encoding="utf-8") as f:
            text = f.read()

        console.print(f"文本长度: {len(text)} 字符")

        progress.update(1, "生成摘要")
        summarizer = Summarizer(
            model_name=model,
            ollama_url=settings.get(
                "summarization.ollama_url", "http://127.0.0.1:11434"
            ),
            temperature=temperature,
            max_length=max_length,
        )

        if not summarizer.check_connection():
            raise SummarizationError("无法连接到Ollama服务")

        if not summarizer.check_model():
            console.print(f"[yellow]警告: 模型 {model} 不存在[/yellow]")
            console.print(
                "[yellow]请运行: ollama pull qwen2.5:7b-instruct-q4_K_M[/yellow]"
            )
            raise SummarizationError(f"模型 {model} 不存在")

        summary = summarizer.summarize(text, max_length=max_length)

        progress.update(1, "保存结果")
        file_writer = FileWriter(output_dir)
        video_name = text_path.stem
        file_writer.write_summary(summary, video_name)

        progress.complete("总结完成")

        console.print(Panel.fit(f"[bold green]总结成功！[/bold green]"))
        console.print(f"输出文件: {output_dir}/{video_name}_summary.txt")

    except Video2TextError as e:
        console.print(f"[bold red]错误: {e}[/bold red]")
        sys.exit(4)
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

        # 从配置文件获取默认值
        output_dir = output_dir or settings.get("output.output_dir", "output")
        language = language or settings.get("transcription.language", "auto")
        transcription_model = transcription_model or settings.get(
            "transcription.model_path", "large-v3"
        )
        summarization_model = summarization_model or settings.get(
            "summarization.model_name", "qwen2.5:7b-instruct-q4_K_M"
        )
        device = device or settings.get("transcription.device", "auto")
        beam_size = beam_size or settings.get_int("transcription.beam_size", 5)
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
        max_length = max_length or settings.get_int("summarization.max_length", 500)

        # 获取完整模型路径
        model_path = get_model_path(settings, transcription_model)

        # 设置日志
        log_level = "DEBUG" if verbose else settings.get("app.log_level", "INFO")
        setup_logger(
            "video2text",
            log_dir=settings.get("paths.logs_dir", "logs"),
            level=log_level,
        )

        console.print(Panel.fit(f"[bold blue]Video2Text 完整管道模式[/bold blue]"))
        console.print(f"输入文件: {input_path}")
        console.print(f"输出目录: {output_dir}")
        console.print(f"转写模型: {model_path}")
        console.print(f"总结模型: {summarization_model}")

        start_time = time.time()
        progress = ProgressTracker(7, "处理进度")

        video_processor = VideoProcessor(
            ffmpeg_path=settings.get("preprocessing.ffmpeg_path", "ffmpeg")
        )
        progress.update(1, "验证视频文件")
        video_processor.validate_video(input_path)

        video_info = video_processor.get_video_info(input_path)
        console.print(f"视频时长: {video_info.duration:.2f}秒")

        progress.update(1, "提取音频")
        audio_path = Path(output_dir) / "temp_audio.wav"
        video_processor.extract_audio(
            input_path,
            str(audio_path),
            sample_rate=settings.get_int("preprocessing.audio_sample_rate", 16000),
            channels=settings.get_int("preprocessing.audio_channels", 1),
        )

        progress.update(1, "加载转写模型")
        transcriber = Transcriber(
            model_path=model_path,
            device=device,
            compute_type=settings.get("transcription.compute_type", "float16"),
            num_workers=settings.get_int("transcription.num_workers", 1),
        )
        transcriber.load_model()

        progress.update(1, "执行转写")
        # 最大音频块时长（秒），可在配置中自定义
        max_chunk_duration = settings.get_int("preprocessing.max_chunk_duration", 300)
        if video_info.duration > max_chunk_duration:
            # 使用临时目录存放切片文件
            chunk_dir_path = tempfile.mkdtemp(prefix="audio_chunks_", dir=output_dir)
            chunk_dir = Path(chunk_dir_path)
            try:
                split_cmd = [
                    video_processor.ffmpeg_path,
                    "-i",
                    str(audio_path),
                    "-f",
                    "segment",
                    "-segment_time",
                    str(max_chunk_duration),
                    "-acodec",
                    "pcm_s16le",
                    "-reset_timestamps",
                    "1",
                    str(chunk_dir / "chunk_%03d.wav"),
                ]
                try:
                    subprocess.run(
                        split_cmd,
                        capture_output=True,
                        text=True,
                        check=True,
                        creationflags=CREATE_NO_WINDOW,
                        encoding="utf-8",
                        errors="ignore",
                        
                    )
                except subprocess.CalledProcessError as e:
                    raise Video2TextError(
                        f"音频切片失败: {e.stderr.strip() or e.stdout.strip()}"
                    )
                chunk_files = sorted(chunk_dir.glob("chunk_*.wav"))
                # 初始化切片转写进度条
                chunk_progress = ProgressTracker(len(chunk_files), "切片转写进度")
                all_segments: list = []
                cumulative_offset = 0.0
                for idx, chunk_path in enumerate(chunk_files):
                    chunk_segments = transcriber.transcribe(
                        str(chunk_path),
                        language=language,
                        beam_size=beam_size,
                        temperature=temperature,
                        vad_filter=settings.get_bool("transcription.vad_filter", True),
                    )
                    # 更新切片转写进度
                    chunk_progress.update(1, f"转写块 {idx + 1}/{len(chunk_files)}")
                    # 调整时间戳
                    for seg in chunk_segments:
                        seg.start += cumulative_offset
                        seg.end += cumulative_offset
                    # 更新累积偏移量，以实际块时长为准
                    if chunk_segments:
                        cumulative_offset += max(s.end for s in chunk_segments)
                    all_segments.extend(chunk_segments)
                chunk_progress.complete("切片转写完成")
                segments = all_segments
            finally:
                # 确保临时文件被删除
                shutil.rmtree(chunk_dir, ignore_errors=True)
        else:
            segments = transcriber.transcribe(
                str(audio_path),
                language=language,
                beam_size=beam_size,
                temperature=temperature,
                vad_filter=settings.get_bool("transcription.vad_filter", True),
            )

        progress.update(1, "处理文本")
        text_cleaner = TextCleaner()
        segment_merger = SegmentMerger(
            max_gap=settings.get_float("text_processing.max_gap", 2.0),
            min_length=settings.get_int("text_processing.min_length", 50),
        )
        merged_segments = segment_merger.merge_segments(segments)
        processed_text = segment_merger.format_segments_as_text(
            merged_segments, include_timestamps=False
        )

        progress.update(1, "生成摘要")
        summarizer = Summarizer(
            model_name=summarization_model,
            ollama_url=settings.get(
                "summarization.ollama_url", "http://127.0.0.1:11434"
            ),
            temperature=summary_temperature,
            max_length=max_length,
        )

        if not summarizer.check_connection():
            console.print("[yellow]警告: 无法连接到Ollama服务，跳过总结[/yellow]")
            summary = "总结不可用"
        elif not summarizer.check_model():
            console.print(
                f"[yellow]警告: 模型 {summarization_model} 不存在，跳过总结[/yellow]"
            )
            console.print(
                "[yellow]请运行: ollama pull qwen2.5:7b-instruct-q4_K_M[/yellow]"
            )
            summary = "总结不可用"
        else:
            summary = summarizer.summarize(processed_text, max_length=max_length)

        progress.update(1, "保存结果")
        file_writer = FileWriter(output_dir)
        formatter = OutputFormatter()
        video_name = Path(input_path).stem
        output_formats = get_transcript_output_formats(settings)

        write_transcript_outputs(file_writer, segments, video_name, output_formats)
        file_writer.write_summary(summary, video_name)

        output_data = formatter.create_output_data(
            video_name=video_name,
            video_path=input_path,
            duration=video_info.duration,
            transcript_segments=segments,
            processed_text=processed_text,
            summary=summary,
            processing_time=time.time() - start_time,
        )
        if settings.get_bool("output.json_output", False):
            file_writer.write_output_data(output_data, video_name)

        progress.complete(f"处理完成，共 {len(segments)} 个段落")

        console.print(Panel.fit(f"[bold green]处理成功！[/bold green]"))
        console.print(f"输出目录: {output_dir}")
        for output_format in output_formats:
            console.print(f"  - {video_name}.{output_format} (转写结果)")
        console.print(f"  - {video_name}_summary.txt (摘要)")
        if settings.get_bool("output.json_output", False):
            console.print(f"  - {video_name}_full.json (完整数据)")

        audio_path.unlink(missing_ok=True)

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
    version = settings.get("app.version", "unknown")

    console.print(f"Video2Text v{version}")


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
