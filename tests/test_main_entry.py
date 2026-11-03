import subprocess
import sys


def test_main_cli_help():
    proc = subprocess.run(
        [sys.executable, "src/main.py", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert proc.returncode == 0
    assert "video2text" in proc.stdout.lower() or "video2text" in proc.stderr.lower() or "usage" in proc.stdout.lower()

