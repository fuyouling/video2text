# -*- mode: python ; coding: utf-8 -*-

import json
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

block_cipher = None

SPEC_CACHE_FILE = '_spec_cache.json'

def load_or_collect():
    """Cache collect_* results to _spec_cache.json to speed up incremental builds."""
    if os.path.exists(SPEC_CACHE_FILE):
        try:
            with open(SPEC_CACHE_FILE, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            print(f'[spec] Loaded collect cache from {SPEC_CACHE_FILE}')
            return cached
        except (json.JSONDecodeError, KeyError):
            pass

    print('[spec] Collecting module data (first run, this may take a while)...')
    data = {
        'faster_whisper_data': collect_data_files('faster_whisper'),
        'ctranslate2_data': collect_data_files('ctranslate2'),
        'tokenizers_data': collect_data_files('tokenizers'),
        'huggingface_hub_data': collect_data_files('huggingface_hub'),
        'pyside6_data': collect_data_files('PySide6'),
        'pyside6_bins': [list(b) for b in collect_dynamic_libs('PySide6')],
        'ctranslate2_bins': [list(b) for b in collect_dynamic_libs('ctranslate2')],
        'pyside6_subs': collect_submodules('PySide6'),
        'ctranslate2_subs': collect_submodules('ctranslate2'),
        'tokenizers_subs': collect_submodules('tokenizers'),
    }
    with open(SPEC_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    print(f'[spec] Cached collect results to {SPEC_CACHE_FILE}')
    return data

_cache = load_or_collect()

# Reconstruct tuples for binaries/datas (JSON stores as lists)
faster_whisper_data = _cache['faster_whisper_data']
ctranslate2_data = _cache['ctranslate2_data']
tokenizers_data = _cache['tokenizers_data']
huggingface_hub_data = _cache['huggingface_hub_data']
pyside6_data = _cache['pyside6_data']
pyside6_bins = [tuple(b) for b in _cache['pyside6_bins']]
ctranslate2_bins = [tuple(b) for b in _cache['ctranslate2_bins']]
pyside6_subs = _cache['pyside6_subs']
ctranslate2_subs = _cache['ctranslate2_subs']
tokenizers_subs = _cache['tokenizers_subs']

a = Analysis(
    ['src/main.py'],
    pathex=[],
    binaries=pyside6_bins + ctranslate2_bins,
    datas=[
        ('config.ini', '.'),
        ('logs/readme.md', 'logs'),
        ('models/readme.md', 'models'),
        ('output/readme.md', 'output'),
        ('video/readme.md', 'video'),
    ] + pyside6_data + faster_whisper_data + ctranslate2_data + tokenizers_data + huggingface_hub_data,
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
    ] + pyside6_subs + ctranslate2_subs + tokenizers_subs,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'tkinter',
        'IPython',
        'jupyter',
        'notebook',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DRender',
        'PySide6.Qt3DAnimation',
        'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic',
        'PySide6.Qt3DExtras',
        'PySide6.QtBluetooth',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtNfc',
        'PySide6.QtPositioning',
        'PySide6.QtQuick',
        'PySide6.QtQuick3D',
        'PySide6.QtQuickWidgets',
        'PySide6.QtRemoteObjects',
        'PySide6.QtSensors',
        'PySide6.QtSerialPort',
        'PySide6.QtSql',
        'PySide6.QtSvg',
        'PySide6.QtSvgWidgets',
        'PySide6.QtTest',
        'PySide6.QtWebChannel',
        'PySide6.QtWebEngine',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebSockets',
        'PySide6.QtXml',
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
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
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
    upx=False,
    upx_exclude=[],
    name='video2text_portable',
)
