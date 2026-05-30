# Video2Text

视频转文本工具 - 使用 faster_whisper 进行语音转写，默认使用 Ollama + Qwen2.5 或 NVIDIA API 进行文本总结，语音转写和文本总结模型可自行更换。

## 功能特性

- 🎬 支持多种视频和音频格式（16种视频 + 7种音频：MP3, WAV, FLAC, AAC, OGG, M4A, WMA）
- 🎤 高质量语音转写（基于 faster_whisper）
- 🤖 智能文本总结（支持本地 Ollama + Qwen2.5 和在线 NVIDIA API 两种模式）
- 📝 多种输出格式（TXT, SRT, VTT, JSON）
- ⚡ GPU加速支持
- 🌍 多语言支持
- 🔄 长音频自动分段转写 + 断点续传（基于 checkpoint 机制）
- 🧵 多线程并发总结（NVIDIA multi 模式，支持速率限制与自动重试）
- 📂 收藏目录管理（常用输入/输出目录一键切换）
- 🔍 查找替换（`Ctrl+F` 快速定位文本）
- ⚙️ 图形化配置编辑器（可视化编辑所有配置项）

## 安装

> **Windows 用户**：如果希望免去源码安装步骤，可以直接下载打包好的 exe 绿色版程序，解压即用。详细安装教程请参阅 [Windows 安装教程（Wiki）](https://github.com/fuyouling/video2text/wiki)。
> 或者直接查看文件 docs\install_windows.md

### 1. 创建虚拟环境

```bash
conda create -n video2text python=3.12.8
conda activate video2text
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 转写模型文件下载

模型下载地址: [large-v3](https://huggingface.co/Systran/faster-whisper-large-v3/resolve/main)


### 4. 总结模型安装

支持两种总结服务，按需选择其一即可。

#### 4.1 NVIDIA 在线（使用在线 NVIDIA 模型总结）

需要先在 [NVIDIA Build](https://build.nvidia.com/) 注册账号并创建 API Key（目前大部分模型免费使用）。获取 Key 后在项目根目录创建 `.env` 文件填入：

```
# NVIDIA API Key（使用在线 NVIDIA 模型总结时需要）
NVIDIA_API_KEY=nvapi-你的API密钥
```

然后在GUI界面或者 `config.ini` 中将 `provider` 改为 `nvidia`：

```ini
[summarization]
provider = nvidia
```

#### 4.2 安装 Ollama（使用本地模型总结）

```bash
# 安装 Ollama
# https://ollama.com/download

# 启动服务
ollama serve

# 拉取总结模型
ollama pull qwen2.5:7b-instruct-q4_K_M
```

## 使用方法

### 转写视频/音频

```bash
python -m src.main transcribe video/sample.mp4 --output-dir output
```

音频文件使用相同命令，支持 MP3, WAV, FLAC, AAC, OGG, M4A, WMA 格式：

```bash
python -m src.main transcribe audio/sample.mp3 --output-dir output
```

### 总结文本

```bash
python -m src.main summarize output/sample.txt --output-dir output
```

### 完整管道（转写总结）

```bash
python -m src.main run-pipeline video/sample.mp4 --output-dir output
```

### GUI
```bash
python -m src.ui.gui
```

#### 主界面

主界面采用左右分栏布局，左侧为实时日志面板，右侧为结果查看与 Ollama 配置面板。

**输入与输出：**
- 支持选择单个视频/音频文件或整个文件夹（自动递归扫描子目录中的媒体文件）
- 文件夹选择时弹出对话框，可勾选需要处理的文件
- 可自定义输出目录，并支持加载历史转写/总结文件
- 收藏目录管理：将常用输入/输出目录添加到收藏，一键快速切换

**任务操作：**
- **仅转写** — 仅执行语音转写，不进行摘要总结
- **仅总结** — 对「文本内容」标签页中的文字进行摘要总结（可粘贴任意文本）
- **转写总结** — 先转写后自动总结的完整管道
- 支持暂停/继续转写任务
- 实时进度条显示处理进度，完成后显示成功/失败统计

**结果查看：**
- 左侧文件列表展示已完成的视频，点击切换查看不同视频的转写和摘要
- 「文本内容」标签页可直接编辑转写文本，编辑后点击「仅总结」即可重新生成摘要
- 「摘要」标签页展示 Ollama 生成的摘要结果
- 支持流式输出，摘要生成过程中实时显示文本

**总结配置面板：**
- 支持切换「本地 Ollama 模型」和「在线 NVIDIA 模型」两种总结服务
- Ollama：配置服务地址、模型名称（下拉框选择或手动输入）、一键启动/关闭/测试服务
- NVIDIA：配置 API 地址、模型名称、Token 数、温度等参数、测试连接
- NVIDIA 多线程模式：`nvidia_mode=multi` 时使用线程池并发处理，支持配置线程数（`nvidia_thread_count`），内置速率限制与 429 自动重试
- 调整温度、最大长度等参数
- 提示词模板管理：保存、加载、删除自定义提示词模板
- 配置一键保存到 `config.ini`（`Ctrl+S` 快捷键）

**查找替换：**
- 主窗口支持 `Ctrl+F` 打开查找替换栏，快速定位转写文本中的关键词

**配置编辑器：**
- 通过菜单「设置 → 编辑配置」打开图形化配置编辑器
- 可视化编辑 `config.ini` 中所有配置段（转写、总结、预处理、输出、网络、文本处理等）
- 修改后即时生效，无需手动编辑文件

#### 结果查看面板

点击主界面的「全屏查看」按钮可打开独立的结果查看窗口，适合大屏阅读和多文件浏览。

**浏览与导航：**
- 左侧文件列表支持关键词过滤，快速定位目标文件
- 文件夹模式（`Ctrl+D`）：以树形结构按子目录分层展示，自动统计每个文件夹下的视频数量
- 双标签页切换查看「转写文本」和「摘要」

**Markdown 渲染：**
- 摘要内容支持 Markdown 格式渲染（标题、列表、表格、代码块、引用等）
- 自动适配主题样式，支持代码语法高亮（需安装 `pygments`）

**搜索功能：**
- 关键词搜索（`Ctrl+F`）并高亮所有匹配项，当前匹配项以不同颜色标识
- 支持上一个/下一个导航（`F3` / `Ctrl+G`）
- 实时显示匹配计数（如 `3/15`）
- 搜索带防抖处理，大文件下依然流畅

**书签系统：**
- 添加书签（`Ctrl+B`）标记当前阅读位置，自动保存文件名、内容类型和光标位置
- 书签面板（`Ctrl+Shift+B`）支持过滤、删除、清空操作
- 双击书签自动跳转到对应文件和位置
- 书签数据持久化存储，跨会话保留

**显示控制：**
- 浅色/深色主题切换，自动适配所有界面元素
- 字体大小调节（`Ctrl+滚轮` 或 `Ctrl+/Ctrl-`，`Ctrl+0` 重置）
- 全屏模式（`F11` / `Esc` 退出）
- 窗口大小、分割器位置等状态自动保存和恢复

### 便携版打包

使用 PyInstaller 打包为 Windows 便携版：

```bash
python build_portable.py
```

### 命令行参数

#### 转写命令 (transcribe)

- `input_path`: 视频/音频文件路径（必需，位置参数）
- `--output-dir, -o`: 输出目录（默认: output）
- `--verbose, -v`: 详细输出

#### 总结命令 (summarize)

- `input_path`: 转写文本文件路径（必需，位置参数）
- `--output-dir, -o`: 输出目录（默认: output）
- `--verbose, -v`: 详细输出

#### 完整管道命令 (run-pipeline)

- `input_path`: 视频/音频文件路径（必需，位置参数）
- `--output-dir, -o`: 输出目录（默认: output）
- `--verbose, -v`: 详细输出

> 所有转写和总结参数（模型、语言、设备、温度等）均通过 `config.ini` 配置，详见下方配置参考章节。

#### 其他命令

- `version`: 显示版本信息
- `help`: 显示所有命令的详细用法


## 输出格式

转写输出由 `config.ini` 中的 `output.transcript_format` 控制，支持以下格式：

- `txt` - 转写文本（可读格式）
- `srt` - SRT 字幕格式
- `vtt` - VTT 字幕格式
- `json` - 转写分段结果的 JSON 数据

例如：

```ini
[output]
transcript_format = txt,srt,json
json_output = true
```

以上配置表示：

- 生成 `{video_name}.txt`
- 生成 `{video_name}.srt`
- 生成 `{video_name}.json`（转写分段结果）
- 在运行完整管道 `run-pipeline` 时，额外生成 `{video_name}_full.json`

如果你希望添加 VTT 格式输出，可将配置改为：

```ini
[output]
transcript_format = txt,srt,vtt,json
json_output = true
```

此时会额外生成：

- `{video_name}.vtt` - VTT 字幕格式

说明：

- `transcript_format` 只控制转写结果文件
- `json_output` 控制完整管道输出的 `{video_name}_full.json`
- `{video_name}_summary.txt` 为摘要输出，由 `summarize` 或 `run-pipeline` 生成

### 摘要输出格式

摘要输出格式由 `config.ini` 中的 `output.summary_format` 控制：

- `txt` - 纯文本格式（`{video_name}_summary.txt`）
- `md` - Markdown 格式（`{video_name}_summary.md`，默认值）

```ini
[output]
summary_format = md
```

## 项目结构

```
video2text/
├── config.ini                # 配置文件
├── prompts.json              # 提示词模板
├── requirements.txt          # 依赖文件
├── README.md                 # 说明文档
├── LICENSE                   # GPL v3 许可证
├── build_portable.py         # 便携版打包脚本
├── video2text_portable.spec  # PyInstaller 打包配置
├── src/                      # 源代码
│   ├── __init__.py
│   ├── main.py               # 程序入口
│   ├── config/               # 配置管理模块
│   │   ├── settings.py
│   │   └── directory_manager.py  # 收藏目录管理
│   ├── ui/                   # 用户界面模块
│   │   ├── cli.py            # CLI 命令定义
│   │   ├── gui.py            # GUI 主窗口
│   │   ├── gui_dialogs.py    # GUI 对话框
│   │   ├── gui_workers.py    # GUI 后台任务
│   │   └── result_viewer.py  # 结果查看器
│   ├── preprocessing/        # 视频预处理模块
│   │   ├── ffmpeg.py
│   │   └── video_processor.py
│   ├── transcription/        # 转写引擎模块
│   │   └── transcriber.py
│   ├── text_processing/      # 文本处理模块
│   │   ├── segment_merger.py
│   │   └── text_cleaner.py
│   ├── summarization/        # 总结引擎模块
│   │   ├── ollama_client.py
│   │   ├── summarizer.py
│   │   ├── providers.py      # 总结服务提供者工厂
│   │   └── nvidia_client.py  # NVIDIA API 客户端
│   ├── services/             # 业务服务模块
│   │   ├── transcription_service.py
│   │   └── summarization_service.py
│   ├── storage/              # 输出存储模块
│   │   ├── file_writer.py
│   │   ├── output_formatter.py
│   │   └── bookmark_manager.py  # 书签管理
│   └── utils/                # 工具模块
│       ├── exceptions.py
│       ├── logger.py
│       ├── model_downloader.py
│       ├── output_validator.py
│       ├── time_format.py
│       └── validators.py
├── models/                   # 模型目录
│   └── large-v3/             # faster-whisper large-v3 模型
│       ├── config.json              # 模型配置文件
│       ├── gitattributes            # Git 属性文件
│       ├── model.bin                # 模型权重文件
│       ├── preprocessor_config.json # 预处理器配置
│       ├── README.md                # 模型说明文档
│       ├── tokenizer.json           # 分词器配置
│       └── vocabulary.json          # 词表文件
├── ffmpeg/                   # 内置 FFmpeg（bin + presets）
│   ├── bin/
│   │   ├── ffmpeg.exe
│   │   └── ffprobe.exe
│   └── presets/
├── logs/                     # 日志目录
├── output/                   # 输出目录
├── video/                    # 视频目录
├── assets/                   # 资源文件（图标等）
├── docs/                     # 项目文档
└── tests/                    # 测试目录
```

## 技术栈

- **CLI框架**: Typer + Rich
- **GUI框架**: PySide6
- **转写引擎**: faster-whisper
- **总结引擎**: Ollama / NVIDIA API
- **视频处理**: FFmpeg
- **日志系统**: Python logging
- **配置管理**: Python configparser
- **打包工具**: PyInstaller

## 配置参考

以下为 `config.ini` 中各配置段的关键参数说明。完整配置请参考项目中的 `config.ini` 文件。

### [transcription] 转写配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `model_path` | `large-v3` | 转写模型路径 |
| `device` | `cuda` | 计算设备（`cuda` / `cpu`） |
| `language` | `zh` | 转写语言（`auto` 为自动检测） |
| `beam_size` | `5` | Beam search 大小 |
| `best_of` | `5` | 候选数量，从多个候选中选择最佳结果 |
| `temperature` | `0.0` | 采样温度，0 表示贪心解码 |
| `compute_type` | `float16` | 计算精度类型（`float16` / `float32` / `int8`） |
| `num_workers` | `1` | 转写工作线程数 |
| `vad_filter` | `True` | 启用 VAD 语音活动检测过滤静音段 |
| `condition_on_previous_text` | `True` | 基于前文上下文进行条件转写 |
| `word_timestamps` | `False` | 启用词级时间戳（会增加处理时间） |

### [summarization] 总结配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `provider` | `ollama` | 总结服务提供者（`ollama` / `nvidia`） |
| `ollama_url` | `http://127.0.0.1:11434` | Ollama 服务地址 |
| `model_name` | `qwen2.5:7b-instruct-q4_K_M` | Ollama 模型名称 |
| `max_length` | `10000` | Ollama 最大输出长度 |
| `temperature` | `0.7` | Ollama 采样温度 |
| `timeout` | `600` | Ollama 请求超时时间（秒） |
| `custom_prompt` | （空） | 自定义提示词模板 |
| `nvidia_api_url` | NVIDIA API 端点 | NVIDIA API 请求地址 |
| `nvidia_model` | `openai/gpt-oss-120b` | NVIDIA 模型名称 |
| `nvidia_max_tokens` | `100000` | NVIDIA 最大 Token 数 |
| `nvidia_temperature` | `1.0` | NVIDIA 采样温度 |
| `nvidia_top_p` | `1.0` | NVIDIA 核采样参数 |
| `nvidia_frequency_penalty` | `0.0` | NVIDIA 频率惩罚 |
| `nvidia_presence_penalty` | `0.0` | NVIDIA 存在惩罚 |
| `nvidia_mode` | `multi` | NVIDIA 执行模式（`single` 串行流式 / `multi` 并发） |
| `nvidia_thread_count` | `5` | NVIDIA 多线程并发数（仅 `multi` 模式生效） |
| `nvidia_stream` | `true` | NVIDIA 流式输出（仅 `single` 模式生效，`multi` 模式下始终关闭） |

### [preprocessing] 预处理配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `audio_sample_rate` | `16000` | 音频采样率（Hz） |
| `audio_channels` | `1` | 音频声道数 |
| `max_chunk_duration` | `300` | 长音频分段时长阈值（秒），超过此值自动分段 |
| `supported_video_formats` | `.mp4,.avi,...` | 支持的视频格式列表（共 16 种） |
| `supported_audio_formats` | `.mp3,.wav,...` | 支持的音频格式列表（共 7 种） |

### [output] 输出配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `output_dir` | `output` | 默认输出目录 |
| `transcript_format` | `txt` | 转写输出格式（`txt` / `srt` / `vtt` / `json`，可逗号分隔多选） |
| `summary_format` | `md` | 摘要输出格式（`txt` / `md`） |

### [network] 网络配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `proxy` | `http://127.0.0.1:7890` | HTTP 代理地址 |

### [text_processing] 文本处理配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_gap` | `2.0` | 分段合并最大间隔（秒） |
| `min_length` | `50` | 分段最小字符长度 |
| `filler_words` | `嗯,啊,呃,...` | 填充词列表（逗号分隔），自动清理 |


## 许可证

GNU GENERAL PUBLIC LICENSE

## 讨论小组
QQ群: 296875960
