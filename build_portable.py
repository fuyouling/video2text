#!/usr/bin/env python3
"""
Video2Text Portable Build Script (Python)
Requires: Python 3.8+, PyInstaller, requests

Usage:
  python build_portable.py                  # Build + create ZIP
  python build_portable.py --clean          # Full clean + build + create ZIP
  python build_portable.py --no-zip         # Build only, skip ZIP
  python build_portable.py --clean --no-zip # Full clean + build only
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def log(msg, color="white"):
    colors = {
        "cyan": "\033[96m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "red": "\033[91m",
        "white": "\033[97m",
    }
    reset = "\033[0m"
    prefix = colors.get(color, colors["white"])
    print(f"{prefix}{msg}{reset}")


def get_file_hash(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def run_cmd(cmd, check=True):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        log(f"[ERROR] Command failed: {' '.join(cmd)}", "red")
        if result.stderr:
            log(result.stderr.strip(), "red")
        sys.exit(1)
    return result


def main():
    parser = argparse.ArgumentParser(description="Video2Text Portable Build Tool")
    parser.add_argument(
        "--clean", action="store_true", help="Full clean (delete build/ and cache)"
    )
    parser.add_argument("--no-zip", action="store_true", help="Skip ZIP packaging")
    args = parser.parse_args()

    build_start = time.time()
    root = Path(__file__).parent
    portable_dir = root / "dist" / "video2text_portable"
    cache_file = root / ".build_cache"
    spec_file = root / "video2text_portable.spec"

    log("=" * 52, "cyan")
    log("Video2Text Green Version Build Tool", "cyan")
    log("=" * 52, "cyan")
    print()

    # Step 1: Check Python
    log("[1/6] Checking Python environment...", "yellow")
    py_ver = sys.version.split()[0]
    log(f"  Python found: {py_ver}", "green")

    # Step 2: Clean old builds
    log("[2/6] Cleaning old builds...", "yellow")
    if args.clean:
        log("  Full clean mode (--clean specified)", "yellow")
        for p in [
            root / "build",
            root / "dist",
            root / ".build_cache",
            root / "_spec_cache.json",
        ]:
            if p.is_dir():
                shutil.rmtree(p)
            elif p.is_file():
                p.unlink()
    else:
        if portable_dir.exists():
            shutil.rmtree(portable_dir)
        log("  Preserved build/ cache (use --clean for full clean)", "green")

    # Step 3: Install dependencies
    log("[3/6] Checking dependencies...", "yellow")
    for pkg in ["pyinstaller", "requests"]:
        result = run_cmd([sys.executable, "-m", "pip", "show", pkg], check=False)
        if result.returncode != 0:
            log(f"  Installing {pkg}...", "yellow")
            run_cmd([sys.executable, "-m", "pip", "install", pkg])
        else:
            log(f"  {pkg} already installed", "green")

    # Step 4: Build with PyInstaller
    log("[4/6] Building executable...", "yellow")
    needs_rebuild = False

    if args.clean:
        needs_rebuild = True
    elif spec_file.exists():
        current_hash = get_file_hash(spec_file)
        if cache_file.exists():
            cached_hash = cache_file.read_text().strip()
            if current_hash != cached_hash:
                needs_rebuild = True
        else:
            needs_rebuild = True
    else:
        needs_rebuild = True

    exe_path = portable_dir / "video2text.exe"
    if needs_rebuild or not exe_path.exists():
        log("  Rebuilding...", "yellow")
        run_cmd([sys.executable, "-m", "PyInstaller", str(spec_file)])
        if spec_file.exists():
            cache_file.write_text(get_file_hash(spec_file))
        log("  Build complete (cached for next run)", "green")
    else:
        log("  Skipped (using previous build)", "green")

    # Step 5: Create directory structure and copy files
    log("[5/6] Creating portable directory structure...", "yellow")

    for dirname in ["logs", "output", "video", "models"]:
        d = portable_dir / dirname
        d.mkdir(parents=True, exist_ok=True)
        log(f"  Created: {dirname}", "green")
        src_readme = root / dirname / "readme.md"
        if src_readme.exists():
            shutil.copy2(src_readme, d / "readme.md")
            log(f"  Copied: {dirname}/readme.md", "green")

    # Copy assets
    assets_src = root / "assets"
    if assets_src.exists():
        shutil.copytree(assets_src, portable_dir / "assets", dirs_exist_ok=True)
        log("  Copied: assets/ (for icons)", "green")

    # Copy docs
    docs_src = root / "docs"
    if docs_src.exists():
        shutil.copytree(docs_src, portable_dir / "docs", dirs_exist_ok=True)
        log("  Copied: docs/ (documentation)", "green")

    # Copy config.ini
    config_src = root / "config.ini"
    if config_src.exists():
        shutil.copy2(config_src, portable_dir / "config.ini")
        log("  Copied: config.ini", "green")

    # Create README
    readme_text = """\
Video2Text Portable Version - User Manual
========================================

1. Extract and run video2text.exe directly
2. Config file config.ini can be edited directly
3. Model will auto-download on first run (about 3GB, requires internet)
4. Ensure FFmpeg is installed and added to PATH
5. Ensure Ollama service is running (for summarization)

For detailed documentation, see README.md
"""
    (portable_dir / "README_PORTABLE.txt").write_text(readme_text, encoding="utf-8")
    log("  Created: README_PORTABLE.txt", "green")

    # Copy README.md
    readme_src = root / "README.md"
    if readme_src.exists():
        shutil.copy2(readme_src, portable_dir / "README.md")
        log("  Copied: README.md", "green")

    # Create launcher script
    bat_content = """\
@echo off
cd /d "%~dp0"
start "" "%~dp0video2text.exe" %*
"""
    (portable_dir / "video2text.bat").write_text(bat_content, encoding="ascii")
    log("  Created: video2text.bat", "green")

    # Step 6: Create ZIP package
    zip_path = None
    if args.no_zip:
        log("[6/6] Skipping ZIP package (--no-zip specified)", "yellow")
    else:
        log("[6/6] Creating ZIP package (excluding models)...", "yellow")
        # Read version from src/config/settings.py
        version = "unknown"
        settings_py = root / "src" / "config" / "settings.py"
        if settings_py.exists():
            for line in settings_py.read_text(encoding="utf-8").splitlines():
                if line.startswith("APP_VERSION"):
                    version = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        zip_name = f"video2text_portable_windows_v{version}.zip"
        zip_path = root / "dist" / zip_name

        # Create temp dir without models
        temp_dir = root / "dist" / "video2text_portable_temp"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        shutil.copytree(portable_dir, temp_dir)

        # Create ZIP with retry logic
        for attempt in range(1, 4):
            try:
                shutil.make_archive(
                    str(zip_path).replace(".zip", ""), "zip", str(temp_dir)
                )
                log(f"  Created: {zip_path} (models excluded)", "green")
                break
            except Exception as e:
                if attempt == 3:
                    log(
                        f"  Warning: ZIP creation failed after 3 attempts: {e}",
                        "yellow",
                    )
                    log(f"  You can manually zip: {temp_dir}", "yellow")
                else:
                    log(f"  Retry {attempt}/3...", "yellow")
                    time.sleep(2)

        # Cleanup temp dir
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

    elapsed = time.time() - build_start
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)

    # Summary
    print()
    log("=" * 52, "cyan")
    log("Build Complete!", "green")
    log("=" * 52, "cyan")
    print()
    log(f"Time elapsed: {minutes}m {seconds}s", "white")
    print()
    log("Output files:", "yellow")
    log(f"  - Directory: {portable_dir}", "white")
    if zip_path:
        log(f"  - ZIP package: {zip_path}", "white")
    print()
    log("Green version features:", "yellow")
    log("  [√] No installation required, extract and use", "green")
    log("  [√] Can directly edit config.ini", "green")
    log("  [√] No registry writes, pure green software", "green")
    log("  [√] Auto-downloads model on first run (excluded from ZIP)", "green")
    log("  [√] Uses cache to speed up rebuilds", "green")
    print()
    log("Tips:", "yellow")
    log(
        "  - Use --clean flag for full rebuild: python build_portable.py --clean",
        "white",
    )
    log(
        "  - Use --no-zip to skip ZIP packaging: python build_portable.py --no-zip",
        "white",
    )
    log("  - Combine flags: python build_portable.py --clean --no-zip", "white")
    log("  - Incremental build (default) skips unchanged steps", "white")
    print()

    input("Press Enter to exit")


if __name__ == "__main__":
    main()
