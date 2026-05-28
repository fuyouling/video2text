#!/usr/bin/env python3
"""
Video2Text Portable Build Script (Python)
Requires: Python 3.8+, PyInstaller, requests

Usage:
  python build_portable.py                  # Build + create ZIP
  python build_portable.py --clean          # Full clean + build + create ZIP
  python build_portable.py --no-zip         # Build only, skip ZIP
  python build_portable.py --clean --no-zip # Full clean + build only
  python build_portable.py --dry-run        # Preview build steps without executing
  python build_portable.py --verbose        # Show detailed output
  python build_portable.py --fast-zip       # ZIP with fastest compression
  python build_portable.py --best-zip       # ZIP with best compression
"""

import argparse
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

TOTAL_STEPS = 6


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


def step_log(step_num, msg):
    log(f"[{step_num}/{TOTAL_STEPS}] {msg}", "yellow")


def get_file_hash(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def run_cmd(cmd, check=True, verbose=False):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if verbose and result.stdout:
        print(result.stdout)
    if check and result.returncode != 0:
        log(f"[ERROR] Command failed: {' '.join(cmd)}", "red")
        if result.stderr:
            log(result.stderr.strip(), "red")
        sys.exit(1)
    return result


def run_cmd_stream(cmd, verbose=False):
    log(f"  Running: {' '.join(cmd)}", "white")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    for line in proc.stdout:
        stripped = line.rstrip()
        if verbose:
            print(stripped)
        elif any(
            kw in stripped.lower()
            for kw in ["error", "warning", "fail", "building", "completed", "info:"]
        ):
            print(stripped)
    proc.wait()
    if proc.returncode != 0:
        log(f"[ERROR] Command failed with exit code {proc.returncode}", "red")
        sys.exit(1)
    return proc.returncode


def read_version(root):
    version_py = root / "src" / "config" / "version.py"
    if not version_py.exists():
        return "unknown"
    content = version_py.read_text(encoding="utf-8")
    match = re.search(r'APP_VERSION\s*=\s*["\'](.+?)["\']', content)
    return match.group(1) if match else "unknown"


def create_zip_with_progress(zip_path, source_dir, compress_level):
    excluded_dirs = {"models"}
    all_files = []
    for root_path, dirs, files in os.walk(source_dir):
        rel = Path(root_path).relative_to(source_dir)
        if rel.parts and rel.parts[0] in excluded_dirs:
            continue
        for f in files:
            all_files.append(Path(root_path) / f)

    total = len(all_files)
    total_size = sum(f.stat().st_size for f in all_files)
    log(f"  Packing {total} files ({total_size / (1024 * 1024):.1f} MB)...", "white")

    written = 0
    compressed_size = 0
    with zipfile.ZipFile(
        zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=compress_level
    ) as zf:
        for file_path in all_files:
            arcname = file_path.relative_to(source_dir)
            zf.write(file_path, arcname)
            written += 1
            if written % 500 == 0 or written == total:
                pct = written * 100 // total
                log(f"  Progress: {written}/{total} ({pct}%)", "white")

    final_size = zip_path.stat().st_size
    log(
        f"  ZIP created: {final_size / (1024 * 1024):.1f} MB "
        f"(ratio {final_size * 100 // total_size if total_size else 0}%)",
        "green",
    )


def safe_rmtree(path):
    try:
        shutil.rmtree(path)
        return True
    except PermissionError:
        log(f"  [ERROR] Cannot delete: {path}", "red")
        log(
            "  Please close video2text.exe and any programs using files in this directory, then retry.",
            "red",
        )
        return False
    except Exception as e:
        log(f"  [ERROR] Failed to delete {path}: {e}", "red")
        return False


def main():
    os.system("")

    parser = argparse.ArgumentParser(description="Video2Text Portable Build Tool")
    parser.add_argument(
        "--clean", action="store_true", help="Full clean (delete build/ and cache)"
    )
    parser.add_argument("--no-zip", action="store_true", help="Skip ZIP packaging")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview build steps without executing"
    )
    parser.add_argument("--verbose", action="store_true", help="Show detailed output")
    parser.add_argument(
        "--fast-zip", action="store_true", help="Use fastest ZIP compression (level 1)"
    )
    parser.add_argument(
        "--best-zip", action="store_true", help="Use best ZIP compression (level 9)"
    )
    args = parser.parse_args()

    compress_level = 1 if args.fast_zip else (9 if args.best_zip else 6)

    build_start = time.time()
    root = Path(__file__).parent
    portable_dir = root / "dist" / "video2text_portable"
    cache_file = root / ".build_cache"
    spec_file = root / "video2text_portable.spec"
    main_py = root / "src" / "main.py"

    log("=" * 52, "cyan")
    log("Video2Text Green Version Build Tool", "cyan")
    log("=" * 52, "cyan")
    print()

    # Step 1: Check Python
    step_log(1, "Checking Python environment...")
    py_ver = sys.version.split()[0]
    log(f"  Python found: {py_ver}", "green")

    # Step 2: Clean old builds
    step_log(2, "Cleaning old builds...")
    if args.dry_run:
        log("  [dry-run] Would clean build artifacts", "white")
    elif args.clean:
        log("  Full clean mode (--clean specified)", "yellow")
        for p in [
            root / "build",
            root / "dist",
            root / ".build_cache",
            root / "_spec_cache.json",
        ]:
            if p.is_dir():
                if not safe_rmtree(p):
                    sys.exit(1)
            elif p.is_file():
                p.unlink()
    else:
        if portable_dir.exists():
            if not safe_rmtree(portable_dir):
                sys.exit(1)
        log("  Preserved build/ cache (use --clean for full clean)", "green")

    # Step 3: Install dependencies
    step_log(3, "Checking dependencies...")
    required_pkgs = ["pyinstaller", "requests"]
    result = run_cmd(
        [sys.executable, "-m", "pip", "freeze"], check=False, verbose=args.verbose
    )
    installed = {line.split("==")[0].lower() for line in result.stdout.splitlines()}
    missing = [pkg for pkg in required_pkgs if pkg.lower() not in installed]
    if missing:
        if args.dry_run:
            log(f"  [dry-run] Would install: {', '.join(missing)}", "white")
        else:
            log(f"  Installing: {', '.join(missing)}", "yellow")
            run_cmd(
                [sys.executable, "-m", "pip", "install"] + missing, verbose=args.verbose
            )
    else:
        log("  All dependencies satisfied", "green")

    # Step 4: Build with PyInstaller
    step_log(4, "Building executable...")
    if not spec_file.exists():
        log(f"  [ERROR] Spec file not found: {spec_file}", "red")
        sys.exit(1)

    needs_rebuild = False
    if args.clean:
        needs_rebuild = True
    else:
        current_spec_hash = get_file_hash(spec_file)
        current_main_hash = get_file_hash(main_py) if main_py.exists() else ""
        combined_hash = hashlib.sha256(
            (current_spec_hash + current_main_hash).encode()
        ).hexdigest()
        if cache_file.exists():
            cached_hash = cache_file.read_text().strip()
            if combined_hash != cached_hash:
                needs_rebuild = True
        else:
            needs_rebuild = True

    exe_path = portable_dir / "video2text.exe"
    if needs_rebuild or not exe_path.exists():
        if args.dry_run:
            log("  [dry-run] Would run PyInstaller", "white")
        else:
            log("  Rebuilding (this may take several minutes)...", "yellow")
            pyinstaller_cmd = [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--log-level",
                "WARN" if not args.verbose else "INFO",
                str(spec_file),
            ]
            run_cmd_stream(pyinstaller_cmd, verbose=args.verbose)
            current_spec_hash = get_file_hash(spec_file)
            current_main_hash = get_file_hash(main_py) if main_py.exists() else ""
            combined_hash = hashlib.sha256(
                (current_spec_hash + current_main_hash).encode()
            ).hexdigest()
            cache_file.write_text(combined_hash)
            log("  Build complete (cached for next run)", "green")
    else:
        log("  Skipped (using previous build)", "green")

    # Step 5: Create directory structure and copy files
    step_log(5, "Creating portable directory structure...")
    if args.dry_run:
        log("  [dry-run] Would create dirs: logs, output, video, models", "white")
        log("  [dry-run] Would copy: assets/, docs/, config.ini, README.md", "white")
    else:
        for dirname in ["logs", "output", "video", "models"]:
            d = portable_dir / dirname
            d.mkdir(parents=True, exist_ok=True)
            log(f"  Created: {dirname}", "green")
            src_readme = root / dirname / "readme.md"
            if src_readme.exists():
                try:
                    shutil.copy2(src_readme, d / "readme.md")
                    log(f"  Copied: {dirname}/readme.md", "green")
                except Exception as e:
                    log(f"  Warning: Failed to copy {dirname}/readme.md: {e}", "yellow")

        assets_src = root / "assets"
        if assets_src.exists():
            try:
                shutil.copytree(assets_src, portable_dir / "assets", dirs_exist_ok=True)
                log("  Copied: assets/ (for icons)", "green")
            except Exception as e:
                log(f"  Warning: Failed to copy assets/: {e}", "yellow")

        docs_src = root / "docs"
        if docs_src.exists():
            try:
                shutil.copytree(docs_src, portable_dir / "docs", dirs_exist_ok=True)
                log("  Copied: docs/ (documentation)", "green")
            except Exception as e:
                log(f"  Warning: Failed to copy docs/: {e}", "yellow")

        config_src = root / "config.ini"
        if config_src.exists():
            try:
                shutil.copy2(config_src, portable_dir / "config.ini")
                log("  Copied: config.ini", "green")
            except Exception as e:
                log(f"  Warning: Failed to copy config.ini: {e}", "yellow")

        readme_src = root / "README.md"
        if readme_src.exists():
            try:
                shutil.copy2(readme_src, portable_dir / "README.md")
                log("  Copied: README.md", "green")
            except Exception as e:
                log(f"  Warning: Failed to copy README.md: {e}", "yellow")

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
        step_log(6, "Skipping ZIP package (--no-zip specified)")
    else:
        version = read_version(root)
        plat = platform.system().lower()
        zip_name = f"video2text_portable_{plat}_v{version}.zip"
        zip_path = root / "dist" / zip_name
        step_log(6, f"Creating ZIP package ({zip_name})...")
        if args.dry_run:
            log(f"  [dry-run] Would create {zip_path}", "white")
        else:
            if zip_path.exists():
                zip_path.unlink()
            try:
                create_zip_with_progress(zip_path, portable_dir, compress_level)
                log(f"  Created: {zip_path}", "green")
            except Exception as e:
                log(f"  [ERROR] ZIP creation failed: {e}", "red")
                log(f"  You can manually zip: {portable_dir}", "yellow")
                zip_path = None

    elapsed = time.time() - build_start
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)

    print()
    log("=" * 52, "cyan")
    log("Build Complete!" if not args.dry_run else "Dry Run Complete!", "green")
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
    log("  - Use --dry-run to preview without executing", "white")
    log("  - Use --verbose for detailed PyInstaller output", "white")
    print()


if __name__ == "__main__":
    main()
