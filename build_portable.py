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
    # 纯 CPU 版本（排除 ONNX Runtime CUDA 提供者并跳过 libs/）
    python build_portable.py --cpu-only

    # 复制示例（部署到安装目录）
    python build_portable.py --copy C:\\dev\\windowsTools\\video2text
    # 完全重建后部署
    python build_portable.py --clean --copy C:\\dev\\windowsTools\\video2text

    # 构建后直接部署，不生成 ZIP
    python build_portable.py --no-zip --copy C:\\dev\\windowsTools\\video2text
    # 完全清理 + 构建 + 部署，不生成 ZIP
    python build_portable.py --clean --no-zip --not-copy-dll --copy  C:\\dev\\windowsTools\\video2text
    python build_portable.py --no-zip --not-copy-dll --copy  C:\\dev\\windowsTools\\video2text

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


def compute_build_hash(root, spec_file, cpu_only=False):
    """联合计算 spec 文件、关键配置、assets 与 src/ 目录下所有源码与资源文件的 sha256 哈希值。"""
    h = hashlib.sha256()
    if cpu_only:
        h.update(b"cpu_only=True")

    for extra in [spec_file, root / "requirements.txt", root / "config_release.ini"]:
        if extra.exists():
            h.update(extra.name.encode("utf-8"))
            h.update(get_file_hash(extra).encode())

    assets_dir = root / "assets"
    if assets_dir.is_dir():
        for p in sorted(assets_dir.glob("*")):
            if p.is_file():
                h.update(p.name.encode("utf-8"))
                h.update(get_file_hash(p).encode())

    src_dir = root / "src"
    if src_dir.is_dir():
        src_files = sorted(
            [
                p
                for p in src_dir.rglob("*")
                if p.is_file() and p.suffix.lower() in (".py", ".json", ".ini", ".ico", ".png")
            ],
            key=lambda p: str(p.relative_to(src_dir)).replace("\\", "/")
        )
        for fpath in src_files:
            rel_path = str(fpath.relative_to(src_dir)).replace("\\", "/")
            h.update(rel_path.encode("utf-8"))
            h.update(get_file_hash(fpath).encode())
    return h.hexdigest()


def run_cmd(cmd, check=True, verbose=False):
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
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
        encoding="utf-8",
        errors="replace",
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
        # 避免深入遍历被排除的子目录（如 models）
        dirs[:] = [d for d in dirs if d not in excluded_dirs]
        for f in files:
            all_files.append(Path(root_path) / f)

    total = len(all_files)
    total_size = sum(f.stat().st_size for f in all_files)
    log(f"  Packing {total} files ({total_size / (1024 * 1024):.1f} MB)...", "white")

    written = 0
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
    def _onerror(func, p, exc_info):
        import stat
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass

    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=lambda fn, p, err: _onerror(fn, p, None))
        else:
            shutil.rmtree(path, onerror=_onerror)
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


def sync_file(src_file: Path, dst_file: Path):
    """仅在目标文件不存在或大小/修改时间不一致时复制文件。"""
    if dst_file.exists():
        try:
            src_stat = src_file.stat()
            dst_stat = dst_file.stat()
            if src_stat.st_size == dst_stat.st_size and abs(src_stat.st_mtime - dst_stat.st_mtime) < 1.0:
                return False  # 内容与时间一致，跳过
            dst_file.unlink()
        except PermissionError:
            log(f"  [ERROR] Cannot overwrite locked file: {dst_file}", "red")
            log("  Please close video2text.exe or any programs using this file, then retry.", "red")
            sys.exit(1)
        except Exception:
            pass
    try:
        shutil.copy2(src_file, dst_file)
    except PermissionError:
        log(f"  [ERROR] Cannot write to locked file: {dst_file}", "red")
        log("  Please close video2text.exe or any programs using this file, then retry.", "red")
        sys.exit(1)
    return True


def sync_dir(src_dir: Path, dst_dir: Path):
    """增量镜像目录：Windows 下使用多线程 robocopy /MIR，其余平台回退。"""
    dst_dir.mkdir(parents=True, exist_ok=True)
    if platform.system().lower() == "windows":
        cmd = f'robocopy "{src_dir}" "{dst_dir}" /MIR /R:1 /W:1 /MT:16 /NP /NFL /NDL'
        # robocopy 退出码 0-7 均代表正常执行（0=无差异, 1=已复制, 2=已删除多余, 3=复制+删除）
        res = subprocess.run(cmd, shell=True, capture_output=True)
        if res.returncode >= 8:
            log(f"  [ERROR] Robocopy failed with code {res.returncode}", "red")
            return False
        return True
    else:
        shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
        return True


def copy_dir_contents(src_dir, dst_dir):
    dst_dir.mkdir(parents=True, exist_ok=True)
    for item in src_dir.iterdir():
        dst = dst_dir / item.name
        if dst.exists():
            if dst.is_file() and item.is_dir():
                dst.unlink()
            elif dst.is_dir() and item.is_file():
                if not safe_rmtree(dst):
                    sys.exit(1)

        if item.is_dir():
            log(f"  Syncing dir: {item.name} -> {dst}", "white")
            if not sync_dir(item, dst):
                sys.exit(1)
            log(f"  Synced dir: {item.name} -> {dst}", "green")
        else:
            if sync_file(item, dst):
                log(f"  Copied file: {item.name} -> {dst}", "green")
            else:
                log(f"  Unchanged file (skipped): {item.name}", "green")
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
    parser.add_argument(
        "--cpu-only",
        action="store_true",
        help="Build CPU-only package (excludes ONNX Runtime CUDA provider and skips copying libs/)",
    )
    args = parser.parse_args()
    if args.cpu_only:
        args.not_copy_dll = True
        os.environ["V2T_EXCLUDE_CUDA"] = "1"
    else:
        os.environ.pop("V2T_EXCLUDE_CUDA", None)
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
        log("  Preserved build/ and dist/ cache (use --clean for full clean)", "green")

    # 步骤 3：检查依赖
    step_log(3, "Checking dependencies...")
    import importlib.util
    required_pkgs = {"PyInstaller": "pyinstaller", "requests": "requests"}
    missing = [pip_name for mod_name, pip_name in required_pkgs.items() if importlib.util.find_spec(mod_name) is None]
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

    # 检查 ffmpeg.exe 与 ffprobe.exe 存在性
    ffmpeg_bin = root / "ffmpeg" / "bin"
    ffmpeg_exe = ffmpeg_bin / "ffmpeg.exe"
    ffprobe_exe = ffmpeg_bin / "ffprobe.exe"
    missing_ffmpeg = []
    if not ffmpeg_exe.is_file():
        missing_ffmpeg.append("ffmpeg.exe")
    if not ffprobe_exe.is_file():
        missing_ffmpeg.append("ffprobe.exe")
    if missing_ffmpeg:
        log(f"  [ERROR] Missing required FFmpeg binary: {', '.join(missing_ffmpeg)} in {ffmpeg_bin}", "red")
        log("  Please ensure ffmpeg.exe and ffprobe.exe are placed in ffmpeg/bin/ before building.", "red")
        sys.exit(1)
    else:
        log("  FFmpeg binaries verified (ffmpeg.exe, ffprobe.exe)", "green")

    # 步骤 4：PyInstaller 打包
    step_log(4, "Building executable...")
    if not spec_file.exists():
        log(f"  [ERROR] Spec file not found: {spec_file}", "red")
        sys.exit(1)

    needs_rebuild = False
    if args.clean:
        needs_rebuild = True
    else:
        current_build_hash = compute_build_hash(root, spec_file, cpu_only=args.cpu_only)
        if cache_file.exists():
            cached_hash = cache_file.read_text(encoding="utf-8").strip()
            if current_build_hash != cached_hash:
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
            current_build_hash = compute_build_hash(root, spec_file, cpu_only=args.cpu_only)
            cache_file.write_text(current_build_hash, encoding="utf-8")
            log("  Build complete (cached for next run)", "green")
    else:
        log("  Skipped (using previous build)", "green")

    # 步骤 5：组装便携目录
    step_log(5, "Creating portable directory structure...")
    if args.dry_run:
        log("  [dry-run] Would create portable directory structure", "white")
        log("  [dry-run] Would copy: assets/, docs/", "white")
        log("  [dry-run] Would copy config_release.ini -> config.ini (release defaults)", "white")
        log("  [dry-run] Would copy README.md", "white")
        log("  [dry-run] Would copy ffmpeg/ (bundled FFmpeg)", "white")
    else:
        assets_src = root / "assets"
        if assets_src.exists():
            try:
                sync_dir(assets_src, portable_dir / "assets")
                log("  Copied: assets/ (for icons)", "green")
            except Exception as e:
                log(f"  Warning: Failed to copy assets/: {e}", "yellow")

        docs_src = root / "docs"
        if docs_src.exists():
            try:
                sync_dir(docs_src, portable_dir / "docs")
                log("  Copied: docs/ (documentation)", "green")
            except Exception as e:
                log(f"  Warning: Failed to copy docs/: {e}", "yellow")

        config_release = root / "config_release.ini"
        try:
            if config_release.exists():
                sync_file(config_release, portable_dir / "config.ini")
                log(f"  Copied: {config_release.name} -> config.ini (release defaults)", "green")
            else:
                log("  Warning: config_release.ini not found, skipped", "yellow")
        except Exception as e:
            log(f"  Warning: Failed to copy config.ini: {e}", "yellow")

        readme_src = root / "README.md"
        if readme_src.exists():
            try:
                sync_file(readme_src, portable_dir / "README.md")
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

        if not args.not_copy_dll and not args.cpu_only:
            dst = portable_dir / "libs"
            dst.mkdir(parents=True, exist_ok=True)
            copied_count = 0

            # 1. 优先复制项目本地 libs/ 目录下的 DLL（如果有）
            libs_src = root / "libs"
            if libs_src.is_dir():
                for p in libs_src.iterdir():
                    if p.suffix.lower() == ".dll":
                        if sync_file(p, dst / p.name):
                            copied_count += 1
                if copied_count > 0:
                    log(f"  Copied {copied_count} DLL(s) from local libs/", "green")

            # 2. 从当前 Python 环境的 site-packages/nvidia/*/bin 提取 cudart*.dll, cufft*.dll, nvJitLink*.dll
            import fnmatch
            import site
            import sysconfig

            site_dirs = set()
            try:
                for sp in site.getsitepackages():
                    site_dirs.add(Path(sp))
                user_site = site.getusersitepackages()
                if user_site:
                    site_dirs.add(Path(user_site))
            except Exception:
                pass
            try:
                for name in ("purelib", "platlib"):
                    p = sysconfig.get_path(name)
                    if p:
                        site_dirs.add(Path(p))
            except Exception:
                pass

            nvidia_patterns = ["cudart*.dll", "cufft*.dll", "nvjitlink*.dll"]
            nvidia_count = 0
            for sp in site_dirs:
                nvidia_dir = sp / "nvidia"
                if nvidia_dir.is_dir():
                    for bin_dir in nvidia_dir.glob("*/bin"):
                        if bin_dir.is_dir():
                            for dll in bin_dir.glob("*.dll"):
                                dll_lower = dll.name.lower()
                                if any(fnmatch.fnmatch(dll_lower, pat.lower()) for pat in nvidia_patterns):
                                    if sync_file(dll, dst / dll.name):
                                        log(f"  Extracted NVIDIA DLL: {dll.name}", "green")
                                    nvidia_count += 1

            if copied_count == 0 and nvidia_count == 0:
                log("  Notice: No CUDA DLLs found in libs/ or site-packages/nvidia/*/bin", "yellow")
            else:
                log(f"  Copied: libs/ ({copied_count + nvidia_count} total DLLs, loaded at runtime)", "green")
        else:
            libs_dst = portable_dir / "libs"
            if libs_dst.exists():
                safe_rmtree(libs_dst)
            log("  Skipped: libs/ (--not-copy-dll or --cpu-only specified)", "yellow")

        # 7za.exe：由 spec 的 datas ('7z/7za.exe', '.') 已打包进 _internal/，
        # 运行时由 dll_downloader 通过 sys._MEIPASS / _internal 查找，故此处
        # 不再重复复制到根目录（避免根目录与 _internal 各存一份）。

        ffmpeg_bin = root / "ffmpeg" / "bin"
        ffmpeg_exe = ffmpeg_bin / "ffmpeg.exe"
        ffprobe_exe = ffmpeg_bin / "ffprobe.exe"
        missing_ffmpeg = []
        if not ffmpeg_exe.is_file():
            missing_ffmpeg.append("ffmpeg.exe")
        if not ffprobe_exe.is_file():
            missing_ffmpeg.append("ffprobe.exe")
        if missing_ffmpeg:
            log(f"  [ERROR] Missing required FFmpeg binaries: {', '.join(missing_ffmpeg)} in {ffmpeg_bin}", "red")
            sys.exit(1)

        dst = portable_dir / "ffmpeg"
        (dst / "bin").mkdir(parents=True, exist_ok=True)
        sync_file(ffmpeg_exe, dst / "bin" / "ffmpeg.exe")
        sync_file(ffprobe_exe, dst / "bin" / "ffprobe.exe")
        presets_src = root / "ffmpeg" / "presets"
        if presets_src.exists():
            sync_dir(presets_src, dst / "presets")
        log("  Copied: ffmpeg (ffmpeg.exe, ffprobe.exe, presets)", "green")

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
