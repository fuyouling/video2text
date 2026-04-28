# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

block_cipher = None

# 收集 faster_whisper 及其依赖的所有模块和数据
faster_whisper_data = collect_data_files('faster_whisper')
ctranslate2_data = collect_data_files('ctranslate2')
tokenizers_data = collect_data_files('tokenizers')
huggingface_hub_data = collect_data_files('huggingface_hub')

a = Analysis(
    ['src/main.py'],
    pathex=[],
    binaries=collect_dynamic_libs('PySide6') + collect_dynamic_libs('ctranslate2'),
    datas=[
        ('config.ini', '.'),
        ('logs/readme.md', 'logs'),
        ('models/readme.md', 'models'),
        ('output/readme.md', 'output'),
        ('video/readme.md', 'video'),
    ] + collect_data_files('PySide6') + faster_whisper_data + ctranslate2_data + tokenizers_data + huggingface_hub_data,
    hiddenimports=[
        'typing_extensions',
        'pydantic',
        'typer',
        'rich',
        'faster_whisper',
        'ctranslate2',
        'ctranslate2.models',
        'tokenizers',
        'tokenizers.models',
        'huggingface_hub',
        'PySide6',
        'requests',
    ] + collect_submodules('PySide6') + collect_submodules('ctranslate2') + collect_submodules('tokenizers'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'tkinter',
        'IPython',
        'jupyter',
        'notebook',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='video2text',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI模式，设为True可显示控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(Path('assets/video2text_logo.ico')),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='video2text_portable',
)
