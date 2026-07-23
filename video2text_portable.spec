# -*- mode: python ; coding: utf-8 -*-

import json
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules
from PyInstaller.building.datastruct import TOC

# CUDA 依赖库目录
# 注意：CUDA/cuDNN 的 DLL 不打包进 _internal，而是作为发布目录下的 libs/ 子目录
# 随附分发，由程序运行时通过 os.add_dll_directory() 显式加载（见 src/main.py）。
specpath = SPECPATH
_LIBS_DIR = Path(specpath) / 'libs'
_cuda_libs = []

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
        'ctranslate2_bins': [list(b) for b in collect_dynamic_libs('ctranslate2')],
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
ctranslate2_bins = [tuple(b) for b in _cache['ctranslate2_bins']]
ctranslate2_subs = [m for m in _cache['ctranslate2_subs'] if not m.startswith('ctranslate2.converters.')]
tokenizers_subs = _cache['tokenizers_subs']

# --- PySide6：仅收集实际使用的模块，避免全量拷贝 WebEngine/Quick/3D 等 ---
# 本应用只使用 QtCore / QtGui / QtWidgets。依赖交由 PyInstaller 的 PySide6 hook
# 自动解析（会带上必要的依赖 DLL 与 platforms 插件），随后在 Analysis 之后
# 通过黑名单过滤剔除 WebEngine/Quick/Qml/3D/Multimedia 等未使用的大体积文件。
pyside6_bins = []
pyside6_data = []
pyside6_subs = ['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets']

a = Analysis(
    ['src/main.py'],
    pathex=[],
    binaries=pyside6_bins + ctranslate2_bins,
    datas=[
        ('config.ini', '.'),
        ('src/ui/styles/voice_to_text.qss', 'src/ui/styles'),
        ('src/i18n/languages.json', 'i18n'),
        ('src/i18n/locales/de.json', 'i18n/locales'),
        ('src/i18n/locales/en.json', 'i18n/locales'),
        ('src/i18n/locales/es.json', 'i18n/locales'),
        ('src/i18n/locales/fr.json', 'i18n/locales'),
        ('src/i18n/locales/ja.json', 'i18n/locales'),
        ('src/i18n/locales/ko.json', 'i18n/locales'),
        ('src/i18n/locales/ru.json', 'i18n/locales'),
        ('src/i18n/locales/zh-CN.json', 'i18n/locales'),
        ('src/i18n/locales/zh-TW.json', 'i18n/locales'),
        # 7za.exe 用于解压 BCJ2 压缩的 7z 包（py7zr 不支持 BCJ2）
        ('7z/7za.exe', '.'),
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
        'cv2',
        'opencv-python',
        'opencv-python-headless',
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
        'torch',
        'torchvision',
        'torchaudio',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# --- 剔除未使用的 PySide6 大体积文件（WebEngine/Quick/Qml/3D/Multimedia 等）---
# 即使在 excludes 中声明了这些模块，PySide6 的 hook 仍可能收集其 DLL 与数据文件。
# 这里对 binaries / datas 按路径关键字做黑名单过滤，可显著减小发布体积。
_PYSIDE_DROP = (
    'qt6webengine', 'qtwebengine', 'webengine',
    'qt6quick', 'qtquick', 'qt6qml', 'qtqml', 'qmltooling',
    'qt6pdf', 'qt6designer', 'designercomponents',
    'qt63d', 'qt6multimedia', 'qtmultimedia',
    'qt6charts', 'qt6datavis', 'qt6graphs',
    'qt6sensors', 'qt6serialport', 'qt6sql', 'qt6svg',
    'qt6remoteobjects', 'qt6positioning', 'qt6nfc', 'qt6bluetooth',
    'qt6websockets', 'qt6webchannel', 'qt6test',
    'qt6shadertools', 'qt6quick3d',
    'opengl32sw.dll',
    'avcodec-', 'avformat-', 'avutil-', 'swscale-', 'swresample-',
    'qmlls.exe', 'qmlformat.exe', 'qmlprofiler', 'qml.exe', 'qmlscene',
)


def _drop_pyside(entry):
    dest = entry[0].replace('\\', '/').lower()
    if 'pyside6' not in dest and 'qt6' not in dest:
        return False
    # 保留 qml 相关中确属核心的？此处直接按关键字剔除
    return any(k in dest for k in _PYSIDE_DROP)


_before = (len(a.binaries), len(a.datas))
a.binaries = TOC([e for e in a.binaries if not _drop_pyside(e)])
a.datas = TOC([e for e in a.datas if not _drop_pyside(e)])
print(f'[spec] PySide6 filter: binaries {_before[0]}->{len(a.binaries)}, '
      f'datas {_before[1]}->{len(a.datas)}')

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
