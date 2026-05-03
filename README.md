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

### 5. 模型文件下载
```
models/large-v3/model.bin 文件比较大不好上传github,下载地址
https://huggingface.co/Systran/faster-whisper-large-v3/resolve/main/model.bin?download=true
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

### GUI
```bash
python -m src.ui.gui
```

#### 主界面

主界面采用左右分栏布局，左侧为实时日志面板，右侧为结果查看与 Ollama 配置面板。

**输入与输出：**
- 支持选择单个视频文件或整个文件夹（自动递归扫描子目录中的视频）
- 文件夹选择时弹出对话框，可勾选需要处理的视频
- 可自定义输出目录，并支持加载历史转写/总结文件

**任务操作：**
- **仅转写** — 仅执行语音转写，不进行摘要总结
- **仅总结** — 对「文本内容」标签页中的文字进行摘要总结（可粘贴任意文本）
- **转写+总结** — 先转写后自动总结的完整管道
- 支持暂停/继续转写任务
- 实时进度条显示处理进度，完成后显示成功/失败统计

**结果查看：**
- 左侧文件列表展示已完成的视频，点击切换查看不同视频的转写和摘要
- 「文本内容」标签页可直接编辑转写文本，编辑后点击「仅总结」即可重新生成摘要
- 「摘要」标签页展示 Ollama 生成的摘要结果
- 支持流式输出，摘要生成过程中实时显示文本

**Ollama 配置面板：**
- 配置 Ollama 服务地址、模型名称（下拉框选择或手动输入）
- 一键刷新可用模型列表、启动/关闭/测试 Ollama 服务
- 调整温度、最大长度等参数
- 提示词模板管理：保存、加载、删除自定义提示词模板
- 配置一键保存到 `config.ini`

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
# PowerShell
.\build_portable.ps1

# 或手动执行
pyinstaller video2text_portable.spec
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

- `--output-dir, -o`: 输出目录（默认: output）
- `--language, -l`: 语言代码（默认: auto）
- `--transcription-model`: 转写模型（默认: large-v3）
- `--summarization-model`: 总结模型（默认: qwen2.5:7b-instruct-q4_K_M）
- `--device, -d`: 设备类型（默认: auto）
- `--beam-size`: beam search大小（默认: 5）
- `--temperature`: 转写温度参数（默认: 0.0）
- `--summary-temperature`: 总结温度参数（默认: 0.7）
- `--max-length`: 最大长度（默认: 500）
- `--verbose, -v`: 详细输出

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

## 项目结构

```
video2text/
├── config.ini                # 配置文件
├── prompts.json              # 提示词模板
├── requirements.txt          # 依赖文件
├── README.md                 # 说明文档
├── LICENSE                   # MIT 许可证
├── build_portable.ps1        # 便携版打包脚本
├── video2text_portable.spec  # PyInstaller 打包配置
├── src/                      # 源代码
│   ├── __init__.py
│   ├── main.py               # 程序入口
│   ├── config/               # 配置管理模块
│   │   └── settings.py
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
│   │   └── summarizer.py
│   ├── services/             # 业务服务模块
│   │   ├── transcription_service.py
│   │   └── summarization_service.py
│   ├── storage/              # 输出存储模块
│   │   ├── file_writer.py
│   │   └── output_formatter.py
│   └── utils/                # 工具模块
│       ├── exceptions.py
│       ├── logger.py
│       ├── model_downloader.py
│       ├── output_validator.py
│       ├── time_format.py
│       └── validators.py
├── models/                   # 模型目录
├── logs/                     # 日志目录
├── output/                   # 输出目录
├── video/                    # 视频目录
├── assets/                   # 资源文件（图标等）
├── docs/                     # 项目文档
├── tests/                    # 测试目录
└── .github/workflows/        # GitHub Actions CI/CD
```

## 技术栈

- **CLI框架**: Typer + Rich
- **GUI框架**: PySide6
- **转写引擎**: faster-whisper
- **总结引擎**: Ollama + Qwen2.5-7B
- **视频处理**: FFmpeg
- **日志系统**: Python logging
- **配置管理**: Python configparser
- **数据校验**: Pydantic
- **打包工具**: PyInstaller

## 常见问题

### 1. FFmpeg 未找到

确保 FFmpeg 已安装并添加到系统 PATH 环境变量中。

### 2. GPU 不可用

检查 CUDA 是否正确安装，或使用 `--device cpu` 参数强制使用 CPU。

### 3. Ollama 连接失败

确保 Ollama 服务正在运行：`ollama serve`

## 许可证

GNU GENERAL PUBLIC LICENSE

## 讨论小组
QQ群: 296875960
