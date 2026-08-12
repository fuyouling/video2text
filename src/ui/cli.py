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
from src.storage.output_formatter import OutputFormatter
from src.utils.exceptions import (
    Video2TextError,
    VideoFileError,
    SummarizationError,
)
from src.utils.logger import setup_logger
from src.i18n import t, set_lang, resolve_language

set_lang(resolve_language())

app = typer.Typer(help=t("cli.app_help"))
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

    from src.utils.model_downloader import check_models_integrity

    check_models_integrity(settings)
    settings.save()

    return video_processor, file_writer


@app.command(help=t("cli.cmd_transcribe_desc"))
def transcribe(
    input_path: str = typer.Argument(..., help=t("cli.transcribe_arg")),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", "-o", help=t("cli.output_dir")
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help=t("cli.verbose")),
):
    """转写音视频为文本"""
    try:
        settings = get_settings()

        output_dir = output_dir or settings.get("output.output_dir", "output")

        video_processor, file_writer = _init_common(settings, output_dir, verbose)

        cfg = _load_tx_config(settings)
        num_workers = settings.get_int("transcription.num_workers", 1)

        console.print(Panel.fit(t("cli.transcribe_panel")))
        console.print(t("cli.input_file_label", path=input_path))
        console.print(t("cli.output_dir_label", dir=output_dir))
        console.print(t("cli.model_label", model=cfg.model_path))
        console.print(t("cli.device_label", device=cfg.device))

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
            vad_filter=cfg.vad_filter,
            vad_parameters=cfg.vad_parameters,
            initial_prompt=cfg.initial_prompt,
            hotwords=cfg.hotwords,
            compression_ratio_threshold=cfg.compression_ratio_threshold,
            log_prob_threshold=cfg.log_prob_threshold,
            no_speech_threshold=cfg.no_speech_threshold,
            repetition_penalty=cfg.repetition_penalty,
            no_repeat_ngram_size=cfg.no_repeat_ngram_size,
            max_chunk_duration=cfg.max_chunk_duration,
            output_formats=cfg.output_formats,
            on_segment=lambda name, seg: console.print(
                OutputFormatter.format_transcript([seg], include_timestamps=True),
                highlight=False,
            ),
        )

        service.transcriber.load_model()
        try:
            results = service.run([input_path], output_dir)
        finally:
            service.transcriber.unload_model()

        if results:
            console.print(Panel.fit(t("cli.transcribe_success")))
            console.print(t("cli.output_dir_label", dir=output_dir))
            for r in results:
                for fmt in cfg.output_formats:
                    console.print(t("cli.transcript_result", name=r.video_name, fmt=fmt))
        else:
            console.print(t("cli.transcribe_fail"))
            sys.exit(2)

    except Video2TextError as e:
        console.print(t("cli.error", error=e))
        sys.exit(2)
    except Exception as e:
        console.print(t("cli.unknown_error", error=e))
        sys.exit(1)


@app.command(help=t("cli.cmd_summarize_desc"))
def summarize(
    input_path: str = typer.Argument(..., help=t("cli.summarize_arg")),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", "-o", help=t("cli.output_dir")
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help=t("cli.verbose")),
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

        console.print(Panel.fit(t("cli.summarize_panel")))
        console.print(t("cli.input_file_label", path=input_path))
        console.print(t("cli.output_dir_label", dir=output_dir))

        text_path = Path(input_path)
        if not text_path.exists():
            raise VideoFileError(t("cli.file_not_found", path=input_path))

        text = text_path.read_text(encoding="utf-8-sig")
        console.print(t("cli.text_length", count=len(text)))

        file_writer = FileWriter(output_dir)
        video_name = text_path.stem

        provider_inst = create_provider(settings)
        service = None

        try:
            if not provider_inst.check_connection():
                provider_name = settings.get("summarization.provider", "ollama")
                raise SummarizationError(
                    t("cli.cannot_connect", provider=provider_name)
                )

            service = SummarizationService(
                settings=settings,
                file_writer=file_writer,
                provider=provider_inst,
            )

            service.summarize(text, video_name=video_name, index=1, total=1)

            summary_fmt = settings.get("output.summary_format", "txt").lower().strip()
            console.print(Panel.fit(t("cli.summarize_success")))
            console.print(
                t("cli.output_file_label", dir=output_dir, name=video_name, fmt=summary_fmt)
            )
        finally:
            if service is not None:
                service.close()
            else:
                provider_inst.close()

    except Video2TextError as e:
        console.print(t("cli.error", error=e))
        sys.exit(2)
    except Exception as e:
        console.print(t("cli.unknown_error", error=e))
        sys.exit(1)


@app.command(help=t("cli.cmd_pipeline_desc"))
def run_pipeline(
    input_path: str = typer.Argument(..., help=t("cli.transcribe_arg")),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", "-o", help=t("cli.output_dir")
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help=t("cli.verbose")),
):
    """运行完整处理管道"""
    try:
        settings = get_settings()

        output_dir = output_dir or settings.get("output.output_dir", "output")

        video_processor, file_writer = _init_common(settings, output_dir, verbose)

        cfg = _load_tx_config(settings)
        num_workers = settings.get_int("transcription.num_workers", 1)

        console.print(Panel.fit(t("cli.pipeline_panel")))
        console.print(t("cli.input_file_label", path=input_path))
        console.print(t("cli.output_dir_label", dir=output_dir))
        console.print(t("cli.tx_model_label", model=cfg.model_path))
        console.print(t("cli.device_label", device=cfg.device))

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
            vad_filter=cfg.vad_filter,
            vad_parameters=cfg.vad_parameters,
            initial_prompt=cfg.initial_prompt,
            hotwords=cfg.hotwords,
            compression_ratio_threshold=cfg.compression_ratio_threshold,
            log_prob_threshold=cfg.log_prob_threshold,
            no_speech_threshold=cfg.no_speech_threshold,
            repetition_penalty=cfg.repetition_penalty,
            no_repeat_ngram_size=cfg.no_repeat_ngram_size,
            max_chunk_duration=cfg.max_chunk_duration,
            output_formats=cfg.output_formats,
            on_segment=lambda name, seg: console.print(
                OutputFormatter.format_transcript([seg], include_timestamps=True),
                highlight=False,
            ),
        )

        tx_service.transcriber.load_model()
        try:
            tx_results = tx_service.run([input_path], output_dir)

            if not tx_results:
                console.print(t("cli.transcribe_fail_abort"))
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
                summary_map[tx_result.video_name] = (processed_text, t("cli.summary_unavailable"))

            provider_inst = create_provider(settings)
            sum_available = provider_inst.check_connection()
            if not sum_available:
                provider_name = settings.get("summarization.provider", "ollama")
                console.print(t("cli.warn_cannot_connect", provider=provider_name))
                provider_inst.close()
            else:
                sum_service = None
                try:
                    sum_service = SummarizationService(
                        settings=settings,
                        file_writer=file_writer,
                        provider=provider_inst,
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
                                summary or t("cli.summary_unavailable"),
                            )
                        except Exception as e:
                            console.print(
                                t(
                                    "cli.warn_summarize_fail",
                                    name=tx_result.video_name,
                                    error=e,
                                )
                            )
                finally:
                    if sum_service is not None:
                        sum_service.close()
                    else:
                        provider_inst.close()

            console.print(Panel.fit(t("cli.pipeline_success")))
            console.print(t("cli.output_dir_label", dir=output_dir))
            summary_fmt = settings.get("output.summary_format", "txt").lower().strip()
            for tx_result in tx_results:
                for fmt in cfg.output_formats:
                    console.print(
                        t("cli.transcript_result", name=tx_result.video_name, fmt=fmt)
                    )
                console.print(
                    t(
                        "cli.summary_result",
                        name=tx_result.video_name,
                        fmt=summary_fmt,
                    )
                )
        finally:
            tx_service.transcriber.unload_model()

    except Video2TextError as e:
        console.print(t("cli.error", error=e))
        sys.exit(2)
    except Exception as e:
        console.print(t("cli.unknown_error", error=e))
        sys.exit(1)


@app.command(help=t("cli.cmd_version_desc"))
def version():
    """显示版本信息"""
    console.print(t("cli.version", ver=APP_VERSION))


@app.command(help=t("cli.cmd_help_desc"))
def help_command():
    """显示所有命令的详细用法"""
    console.print(Panel.fit(t("cli.help_title")))
    console.print(t("cli.available_commands"))

    commands = [
        {
            "name": "transcribe",
            "description": t("cli.cmd_transcribe_desc"),
            "usage": t("cli.cmd_transcribe_usage"),
            "options": [
                ("--output-dir, -o", t("cli.output_dir")),
                ("--verbose, -v", t("cli.verbose")),
            ],
        },
        {
            "name": "summarize",
            "description": t("cli.cmd_summarize_desc"),
            "usage": t("cli.cmd_summarize_usage"),
            "options": [
                ("--output-dir, -o", t("cli.output_dir")),
                ("--verbose, -v", t("cli.verbose")),
            ],
        },
        {
            "name": "run-pipeline",
            "description": t("cli.cmd_pipeline_desc"),
            "usage": t("cli.cmd_pipeline_usage"),
            "options": [
                ("--output-dir, -o", t("cli.output_dir")),
                ("--verbose, -v", t("cli.verbose")),
            ],
        },
        {
            "name": "version",
            "description": t("cli.cmd_version_desc"),
            "usage": "video2text version",
            "options": [],
        },
        {
            "name": "--help",
            "description": t("cli.cmd_help_desc"),
            "usage": "video2text help",
            "options": [],
        },
    ]

    for cmd in commands:
        console.print(f"[bold cyan]{cmd['name']}[/bold cyan] - {cmd['description']}")
        console.print(f"  {t('cli.usage_label')}: {cmd['usage']}")
        if cmd["options"]:
            console.print(t("cli.options_label"))
            for opt, desc in cmd["options"]:
                console.print(f"    {opt:<30} {desc}")
        console.print()

    console.print(t("cli.examples_label"))
    console.print("  video2text transcribe video.mp4 -o output")
    console.print("  video2text summarize transcript.txt -o output")
    console.print("  video2text run-pipeline video.mp4 -o output")
    console.print(f"\n{t('cli.tips_label')} {t('cli.tip1')}")
    console.print(f"      {t('cli.tip2')}")
    console.print(f"      {t('cli.tip3')}")


if __name__ == "__main__":
    app()
