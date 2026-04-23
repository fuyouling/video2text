# Video2Text

视频转文本工具 - 使用 faster_whisper 进行语音转写，使用 Ollama + Qwen2.5 进行文本总结。

## 功能特性

- 🎬 支持多种视频格式（MP4, AVI, MOV, MKV等）
- 🎤 高质量语音转写（基于 faster_whisper）
- 🤖 智能文本总结（基于 Ollama + Qwen2.5）
- 📝 多种输出格式（TXT, SRT, VTT, JSON）
- ⚡ GPU加速支持
- 🌍 多语言支持

## 安装

### 1. 创建虚拟环境

```bash
conda create -n video2text python=3.12.8
conda activate video2text
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 安装 FFmpeg

**Windows:**
```bash
# 下载 FFmpeg 并添加到 PATH
# https://ffmpeg.org/download.html
```

**macOS:**
```bash
brew install ffmpeg
```

**Linux:**
```bash
sudo apt-get install ffmpeg
```

### 4. 启动 Ollama 服务

```bash
# 安装 Ollama
# https://ollama.com/download

# 启动服务
ollama serve

# 拉取总结模型
ollama pull qwen2.5:7b-instruct-q4_K_M
```

## 使用方法

### 转写视频

```bash
python -m src.main transcribe video/sample.mp4 --output-dir output
```

### 总结文本

```bash
python -m src.main summarize output/sample.txt --output-dir output
```

### 完整管道（转写+总结）

```bash
python -m src.main run-pipeline video/sample.mp4 --output-dir output
```

### 命令行参数

#### 转写命令 (transcribe)

- `--input, -i`: 视频文件路径（必需）
- `--output-dir, -o`: 输出目录（默认: output）
- `--language, -l`: 语言代码（默认: auto）
- `--model, -m`: 转写模型（默认: large-v3）
- `--device, -d`: 设备类型（默认: auto）
- `--beam-size`: beam search大小（默认: 5）
- `--temperature`: 温度参数（默认: 0.0）
- `--verbose, -v`: 详细输出

#### 总结命令 (summarize)

- `--input, -i`: 转写文本文件路径（必需）
- `--output-dir, -o`: 输出目录（默认: output）
- `--model, -m`: 总结模型（默认: qwen2.5:7b-instruct-q4_K_M）
- `--max-length`: 最大长度（默认: 500）
- `--temperature`: 温度参数（默认: 0.7）
- `--verbose, -v`: 详细输出

#### 完整管道命令 (run-pipeline)

包含所有转写和总结的参数。

## 配置文件

配置文件 `config.ini` 包含以下部分：

```ini
[app]
name = video2text
version = 1.0.0
log_level = INFO

[transcription]
model_path = large-v3
device = cuda
language = zh
beam_size = 5
best_of = 5
temperature = 0.0

[summarization]
ollama_url = http://127.0.0.1:11434
model_name = qwen2.5:7b-instruct-q4_K_M
max_length = 500
temperature = 0.7

[preprocessing]
ffmpeg_path = ffmpeg
audio_sample_rate = 16000
audio_channels = 1
supported_video_formats = .mp4,.avi,.mov,.mkv,.flv,.wmv,.webm

[output]
output_dir = output
transcript_format = txt,srt,vtt
summary_format = txt
json_output = true

[paths]
models_dir = models
logs_dir = logs
video_dir = video
```

## 输出格式

转写输出由 `config.ini` 中的 `output.transcript_format` 控制，支持以下格式：

- `txt` - 转写文本（可读格式）
- `srt` - SRT 字幕格式
- `vtt` - VTT 字幕格式
- `json` - 转写分段结果的 JSON 数据

例如：

```ini
[output]
transcript_format = txt,srt,vtt
json_output = true
```

以上配置表示：

- 生成 `{video_name}.txt`
- 生成 `{video_name}.srt`
- 生成 `{video_name}.vtt`
- 在运行完整管道 `run-pipeline` 时，额外生成 `{video_name}_full.json`

如果你希望转写结果本身也输出为 JSON，可将配置改为：

```ini
[output]
transcript_format = txt,srt,vtt,json
json_output = true
```

此时会额外生成：

- `{video_name}.json` - 转写分段结果 JSON

说明：

- `transcript_format` 只控制转写结果文件
- `json_output` 控制完整管道输出的 `{video_name}_full.json`
- `{video_name}_summary.txt` 为摘要输出，由 `summarize` 或 `run-pipeline` 生成

## 项目结构

```
video2text/
├── config.ini              # 配置文件
├── requirements.txt         # 依赖文件
├── README.md              # 说明文档
├── src/                   # 源代码
│   ├── __init__.py
│   ├── main.py            # 程序入口
│   ├── config/            # 配置管理模块
│   ├── ui/                # 用户界面模块
│   ├── preprocessing/     # 视频预处理模块
│   ├── transcription/     # 转写引擎模块
│   ├── text_processing/   # 文本处理模块
│   ├── summarization/     # 总结引擎模块
│   ├── storage/           # 输出存储模块
│   └── utils/             # 工具模块
├── models/                # 模型目录
├── logs/                  # 日志目录
├── output/                # 输出目录
├── video/                 # 视频目录
└── tests/                 # 测试目录
```

## 技术栈

- **CLI框架**: Typer
- **转写引擎**: faster-whisper
- **总结引擎**: Ollama + Qwen2.5-7B
- **视频处理**: FFmpeg
- **日志系统**: Python logging
- **配置管理**: Python configparser

## 常见问题

### 1. FFmpeg 未找到

确保 FFmpeg 已安装并添加到系统 PATH 环境变量中。

### 2. GPU 不可用

检查 CUDA 是否正确安装，或使用 `--device cpu` 参数强制使用 CPU。

### 3. Ollama 连接失败

确保 Ollama 服务正在运行：`ollama serve`

## 许可证

MIT License
