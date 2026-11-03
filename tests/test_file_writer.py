"""FileWriter 单元测试：原子写、换行符规范化、Windows 非法文件名清洗"""

from pathlib import Path
from src.storage.file_writer import FileWriter, FileLocator
from src.transcription.transcriber import TranscriptSegment
from src.text_processing.segment_merger import MergedSegment


def _make_segment(start: float, end: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(
        start=start, end=end, text=text, confidence=95.0, language="zh"
    )


def test_atomic_write_fsync_and_newline_normalization(tmp_path: Path):
    target = tmp_path / "test_norm.txt"
    # 模拟包含 Windows \r\n 换行符的文本
    raw_content = "line1\r\nline2\r\nline3\r\n"
    FileWriter._atomic_write(target, raw_content)

    assert target.exists()
    # 验证二进制读取时，换行符被严格规范为 \n，没有膨胀为 \r\r\n
    binary_data = target.read_bytes()
    assert b"\r\r\n" not in binary_data
    assert binary_data == b"line1\nline2\nline3\n"


def test_file_writer_sanitize_filename_all_methods(tmp_path: Path):
    writer = FileWriter(str(tmp_path))
    illegal_filename = 'invalid:file*name?test"1<2>3|end.'

    # 1. write_transcript
    segments = [_make_segment(0.0, 1.0, "测试转写")]
    out_tx = writer.write_transcript(segments, illegal_filename, fmt="txt")
    assert Path(out_tx).exists()
    assert ":" not in Path(out_tx).name
    assert "*" not in Path(out_tx).name
    assert "?" not in Path(out_tx).name

    # 2. write_merged_transcript
    merged_segments = [MergedSegment(start=0.0, end=1.0, text="合并转写", language="zh")]
    out_merged = writer.write_merged_transcript(merged_segments, illegal_filename)
    assert Path(out_merged).exists()

    # 3. write_summary
    out_sum = writer.write_summary("这是摘要内容", illegal_filename, fmt="md")
    assert Path(out_sum).exists()

    # 4. write_json
    out_json = writer.write_json({"key": "val"}, illegal_filename)
    assert Path(out_json).exists()

    # 5. write_text
    out_text = writer.write_text("纯文本内容", illegal_filename)
    assert Path(out_text).exists()

    # 6. write_keywords
    out_kw = writer.write_keywords(["关键词1", "关键词2"], illegal_filename)
    assert Path(out_kw).exists()

    # 7. FileLocator 寻址能够通过原始非法名找到清洗后的产物
    found_tx = FileLocator.find_transcript(str(tmp_path), illegal_filename)
    assert found_tx is not None
    assert found_tx.exists()

    found_sum = FileLocator.find_summary(str(tmp_path), illegal_filename)
    assert found_sum is not None
    assert found_sum.exists()
