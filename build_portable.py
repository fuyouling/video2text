#!/usr/bin/env python3
"""
Video2Text Portable Build Script (Python)
Requires: Python 3.8+, PyInstaller, requests

用法:
    # 构建 + 生成 ZIP
    python build_portable.py
    # 完全清理 + 构建 + 生成 ZIP
    python build_portable.py --clean
    # 仅构建，跳过 ZIP
    python build_portable.py --no-zip
    # 完全清理 + 仅构建
    python build_portable.py --clean --no-zip
    # 预览构建步骤，不实际执行
    python build_portable.py --dry-run
    # 显示详细输出
    python build_portable.py --verbose
    # ZIP 最快压缩
    python build_portable.py --fast-zip
    # ZIP 最大压缩
    python build_portable.py --best-zip
    # 仅将已有构建重新打包为 ZIP
    python build_portable.py --only-zip
    # 将构建结果复制到安装目录 DIR（DIR 必填）
    python build_portable.py --copy DIR
    # 不复制 DLL 文件到便携目录（仅在构建时有效）
    python build_portable.py --not-copy-dll

    # 复制示例（部署到安装目录）
    python build_portable.py --copy C:\\dev\\windowsTools\\video2text
    # 完全重建后部署
    python build_portable.py --clean --copy C:\\dev\\windowsTools\\video2text

    # 构建后直接部署，不生成 ZIP
    python build_portable.py --no-zip --copy C:\\dev\\windowsTools\\video2text
    # 完全清理 + 构建 + 部署，不生成 ZIP
    python build_portable.py --clean --no-zip --copy --not-copy-dll C:\\dev\\windowsTools\\video2text

    python build_portable.py --clean --no-zip --not-copy-dll
    python build_portable.py --clean --not-copy-dll

    # 仅复制已有构建到安装目录 DIR（不做构建/ZIP）
    python build_portable.py --only-copy DIR
    # 仅复制示例（部署到安装目录）
    python build_portable.py --only-copy C:\\dev\\windowsTools\\video2text

    # github Actions 构建示例：
    python build_portable.py --clean --not-copy-dll

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

TOTAL_STEPS = 7


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
    try:
        print(f"{prefix}{msg}{reset}")
    except UnicodeEncodeError:
        sys.stdout.reconfigure(encoding="utf-8")
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
            for kw in ["error", "fail"]
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
            arcname = Path("video2text_portable") / file_path.relative_to(source_dir)
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


def copy_dir_contents(src_dir, dst_dir):
    dst_dir.mkdir(parents=True, exist_ok=True)
    for item in src_dir.iterdir():
        dst = dst_dir / item.name
        # 仅删除与目标中同名的项，避免残留；不删除目标里其它目录/文件
        if dst.exists():
            if dst.is_dir():
                cmd = f'rmdir /s /q "{dst}"'
                log(f"  Command: {cmd}", "white")
                if not safe_rmtree(dst):
                    sys.exit(1)
                log(f"  Deleted dir (stale): {dst}", "yellow")
            else:
                cmd = f'del /q "{dst}"'
                log(f"  Command: {cmd}", "white")
                dst.unlink()
                log(f"  Deleted file (stale): {dst}", "yellow")
        if item.is_dir():
            cmd = f'robocopy "{item}" "{dst}" /E /R:1 /W:1'
            log(f"  Command: {cmd}", "white")
            shutil.copytree(item, dst, dirs_exist_ok=True)
            log(f"  Copied dir: {item.name} -> {dst}", "green")
        else:
            cmd = f'copy /y "{item}" "{dst}"'
            log(f"  Command: {cmd}", "white")
            shutil.copy2(item, dst)
            log(f"  Copied file: {item.name} -> {dst}", "green")
    return True


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
    parser.add_argument(
        "--only-zip", action="store_true", help="Only repackage existing build into ZIP"
    )
    parser.add_argument(
        "--copy",
        nargs=1,
        metavar="DIR",
        default=None,
        help="Copy built files to install directory DIR after build (DIR required)",
    )
    parser.add_argument(
        "--only-copy",
        nargs=1,
        metavar="DIR",
        default=None,
        help="Only copy existing build to install directory DIR (no build/ZIP, DIR required)",
    )
    parser.add_argument(
        "--not-copy-dll",
        action="store_true",
        help="Do not copy DLL files from libs/ into the portable directory",
    )
    args = parser.parse_args()
    install_dir_arg = args.copy[0] if args.copy else None
    only_copy_dir_arg = args.only_copy[0] if args.only_copy else None

    compress_level = 1 if args.fast_zip else (9 if args.best_zip else 6)
    build_start = time.time()
    root = Path(__file__).parent
    portable_dir = root / "dist" / "video2text_portable"
    cache_file = root / ".build_cache"
    spec_file = root / "video2text_portable.spec"
    main_py = root / "src" / "main.py"

    if args.only_zip:
        log("=" * 52, "cyan")
        log("Video2Text --only-zip Mode: repackaging existing build", "cyan")
        log("=" * 52, "cyan")
        print()

        version = read_version(root)
        plat = platform.system().lower()
        zip_name = f"video2text_portable_{plat}_v{version}.zip"
        zip_path = root / "dist" / zip_name

        if not portable_dir.exists():
            log(f"[ERROR] Portable directory not found: {portable_dir}", "red")
            log("Run a full build first before using --only-zip.", "red")
            sys.exit(1)

        step_log(6, f"Creating ZIP package ({zip_name})...")
        if zip_path.exists():
            zip_path.unlink()
        try:
            create_zip_with_progress(zip_path, portable_dir, compress_level)
            log(f"  Created: {zip_path}", "green")
        except Exception as e:
            log(f"  [ERROR] ZIP creation failed: {e}", "red")
            sys.exit(1)

        print()
        log("=" * 52, "cyan")
        log("Repackage Complete!", "green")
        log("=" * 52, "cyan")
        print()
        log(f"  - ZIP package: {zip_path}", "white")
        print()
        return

    if args.only_copy:
        install_dir = Path(only_copy_dir_arg)
        log("=" * 52, "cyan")
        log("Video2Text --only-copy Mode: copy existing build only", "cyan")
        log("=" * 52, "cyan")
        print()

        if not portable_dir.exists():
            log(f"[ERROR] Portable directory not found: {portable_dir}", "red")
            log("Run a full build first before using --only-copy.", "red")
            sys.exit(1)

        step_log(7, f"Copying build to install directory ({install_dir})...")
        cmd = f'robocopy "{portable_dir}" "{install_dir}" /E /R:1 /W:1'
        log(f"  Command: {cmd}", "white")
        try:
            copy_dir_contents(portable_dir, install_dir)
            log(f"  Copied to: {install_dir}", "green")
        except Exception as e:
            log(f"  [ERROR] Copy failed: {e}", "red")
            sys.exit(1)

        print()
        log("=" * 52, "cyan")
        log("Copy Complete!", "green")
        log("=" * 52, "cyan")
        print()
        log(f"  - Installed to: {install_dir}", "white")
        print()
        return

    log("=" * 52, "cyan")
    log("Video2Text Green Version Build Tool", "cyan")
    log("=" * 52, "cyan")
    print()

    # 步骤 1：检查 Python 环境
    step_log(1, "Checking Python environment...")
    py_ver = sys.version.split()[0]
    log(f"  Python found: {py_ver}", "green")

    # 步骤 2：清理旧构建
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

    # 步骤 3：检查依赖
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

    # 步骤 4：PyInstaller 打包
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
                "ERROR" if not args.verbose else "INFO",
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

    # 步骤 5：组装便携目录
    step_log(5, "Creating portable directory structure...")
    if args.dry_run:
        log("  [dry-run] Would create portable directory structure", "white")
        log("  [dry-run] Would copy: assets/, docs/", "white")
        log("  [dry-run] Would copy config_realease.ini -> config.ini (release defaults)", "white")
        log("  [dry-run] Would copy README.md", "white")
        log("  [dry-run] Would copy ffmpeg/ (内置 FFmpeg)", "white")
    else:
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

        config_src = root / "config_realease.ini"
        try:
            if config_src.exists():
                shutil.copy2(config_src, portable_dir / "config.ini")
                log("  Copied: config_realease.ini -> config.ini (release defaults)", "green")
            else:
                log("  Warning: config_realease.ini not found, skipped", "yellow")
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

        libs_src = root / "libs"
        if libs_src.is_dir():
            if args.not_copy_dll:
                log("  Skipped: libs/ (--not-copy-dll specified)", "yellow")
            else:
                dst = portable_dir / "libs"
                dst.mkdir(parents=True, exist_ok=True)
                for p in libs_src.iterdir():
                    if p.suffix.lower() == ".dll":
                        shutil.copy2(p, dst / p.name)
                log("  Copied: libs/ (CUDA/cuDNN DLLs, loaded at runtime)", "green")

        # 7za.exe：用于解压 BCJ2 压缩的 7z 包（py7zr 不支持 BCJ2）。
        # 将 7z/7za.exe 随包分发，确保 DLL 解压在目标机器上可靠运行。
        seven_zip_src = root / "7z" / "7za.exe"
        if seven_zip_src.is_file():
            try:
                shutil.copy2(seven_zip_src, portable_dir / "7za.exe")
                log("  Copied: 7za.exe", "green")
            except Exception as e:
                log(f"  Warning: Failed to copy 7za.exe: {e}", "yellow")
        else:
            log(
                "  Note: 7z/7za.exe 未找到，DLL 解压将失败"
                "（需 BCJ2 支持，py7zr 不可用）",
                "yellow",
            )

        ffmpeg_src = root / "ffmpeg"
        if ffmpeg_src.exists():
            dst = portable_dir / "ffmpeg"
            (dst / "bin").mkdir(parents=True, exist_ok=True)
            for name in ["ffmpeg.exe", "ffprobe.exe"]:
                src_file = ffmpeg_src / "bin" / name
                if src_file.exists():
                    shutil.copy2(src_file, dst / "bin" / name)
            presets_src = ffmpeg_src / "presets"
            if presets_src.exists():
                shutil.copytree(presets_src, dst / "presets", dirs_exist_ok=True)
            log("  Copied: ffmpeg", "green")

    # 步骤 6：打包 ZIP
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

    # 步骤 7：复制到安装目录
    install_dir = None
    if install_dir_arg:
        install_dir = Path(install_dir_arg)
        step_log(7, f"Copying build to install directory ({install_dir})...")
        if args.dry_run:
            log(f"  [dry-run] Would copy {portable_dir} -> {install_dir}", "white")
        else:
            if not portable_dir.exists():
                log(f"  [ERROR] Portable directory not found: {portable_dir}", "red")
                sys.exit(1)
            try:
                copy_dir_contents(portable_dir, install_dir)
                log(f"  Copied to: {install_dir}", "green")
            except Exception as e:
                log(f"  [ERROR] Copy failed: {e}", "red")
                sys.exit(1)

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
    if install_dir:
        log(f"  - Installed to: {install_dir}", "white")
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
    log("  - Use --only-zip to repackage existing build into ZIP", "white")
    log(
        "  - Use --copy DIR to deploy build to install dir DIR",
        "white",
    )
    print()


if __name__ == "__main__":
    main()
