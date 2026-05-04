# Windows 安装教程 

本文档提供在 Windows 系统上安装打包好的 `video2text` 程序的完整步骤。

## 包含的文件

安装包体积较大，已上传至 123 云盘，内含以下组件：

- Ollama 安装包及模型文件
- ffmpeg 安装包
- video2text 绿色版 exe 程序包 video2text_portable_windows_20260426.zip
- large‑v3 模型文件

> 请使用支持保留目录结构的解压工具（如 7‑Zip）解压压缩包，确保文件夹结构完整。
> 连接:
```
『来自123云盘用户喵王龙的分享』video2text
链接：https://1840674647.share.123pan.cn/123pan/7CfNTd-SE7j3?pwd=viWa#
提取码：viWa
```

## 安装步骤

1. **安装 Ollama**
   - 运行 Ollama 的 exe 安装程序。
   - 将 `models.zip` 解压到 `C:\Users\%USERNAME%\.ollama` 目录下。
   - 开启Ollama,直接打开安装好的程序就行

2. **安装 ffmpeg**
   - 直接运行 ffmpeg 安装程序。
   - 完成后将 ffmpeg 所在目录添加到系统 `PATH` 环境变量，以便在命令行中直接调用。
   - 运行 `ffmpeg -version` 检查是否成功显示版本信息。

3. **部署 video2text**
   - 解压 `video2text` 绿色版 exe 程序包。
   - 将 `large‑v3` 模型文件解压到 `video2text.exe` 所在目录下的 `models` 子文件夹中。

完成以上步骤后，即可在 Windows 环境下运行 `video2text`，并使用 large‑v3 模型进行视频转文本。

---

## 使用说明

### 启动程序

双击 `video2text.exe` 即可打开图形界面（GUI）。也可以双击 `video2text.bat` 启动，它会自动将工作目录切换到程序所在位置。

> 也可以在命令行中不带参数运行 `video2text.exe` 来启动 GUI；带参数则进入命令行模式（见下方 [CLI 章节](#命令行使用cli)）。

### 主界面概览

程序启动后显示主窗口（默认 1200×800），从上到下分为以下区域：

| 区域 | 说明 |
|------|------|
| **菜单栏** | 设置（编辑配置）、帮助（关于） |
| **输入行** | 选择视频文件或文件夹的路径显示及操作按钮 |
| **输出行** | 输出目录设置、加载历史、暂停按钮 |
| **进度行** | 进度条、进度标签、三个操作按钮（仅转写/仅总结/转写+总结） |
| **左侧面板** | 日志输出（实时显示运行日志） |
| **右侧面板上部** | 结果查看（文件列表 + 转写文本/摘要标签页） |
| **右侧面板下部** | 提示词配置（自定义总结提示词及模板管理） |
| **状态栏** | 显示配置路径、操作反馈信息 |

---

### 基本操作流程

#### 1. 选择视频

- **选择文件**：点击「选择文件」按钮，在弹出的对话框中选择一个或多个视频文件（按住 `Ctrl` 或 `Shift` 多选）。
- **选择文件夹**：点击「选择文件夹」按钮，选择一个文件夹后程序会自动递归扫描其中所有支持格式的视频，并弹出选择对话框：
  - 对话框显示找到的所有视频文件，每个文件前有复选框，默认全部勾选
  - 可通过「全选」/「取消全选」按钮批量操作
  - 勾选需要处理的文件后点击「确定」

**支持的视频格式**（共 17 种）：
`.mp4` `.avi` `.mov` `.mkv` `.flv` `.wmv` `.webm` `.ts` `.mts` `.m4v` `.3gp` `.mpeg` `.mpg` `.vob` `.ogv` `.rm` `.rmvb`

#### 2. 设置输出目录

- 点击「浏览」按钮选择转写结果的保存位置。
- 默认输出目录为程序所在目录下的 `output` 文件夹。
- 如果是通过「选择文件夹」导入视频，程序会自动在 `output` 下创建以源文件夹命名的子目录。

#### 3. 执行操作

界面提供三种操作模式：

| 按钮 | 功能 | 说明 |
|------|------|------|
| **仅转写** | 语音 → 文字 | 使用 faster-whisper 模型将视频中的语音转为文字，结果保存到输出目录 |
| **仅总结** | 文字 → 摘要 | 对当前「文本内容」标签页中的文字进行 AI 摘要（需要 Ollama 或 NVIDIA API） |
| **转写+总结** | 语音 → 文字 → 摘要 | 先转写，完成后自动对每段转写文本进行摘要，一步完成全流程 |

操作过程中的说明：
- 进度条会实时更新，显示「已完成数/总数」。
- 日志面板会实时输出处理信息，包括每段视频的转写进度、耗时等。
- 转写过程中可点击「暂停」按钮暂停，暂停后按钮变为「继续」，点击继续恢复处理。暂停会在当前切片完成后生效。
- 任务完成或失败时，日志面板会显示统计信息（成功/失败数量）。

---

### 查看结果

#### 文件列表

操作完成后，右侧面板的文件列表会显示所有已处理的视频文件名。点击文件名可切换查看对应的转写文本和摘要。

#### 转写文本（「文本内容」标签页）

- 显示该视频的完整转写文本。
- **文本可编辑**：可以直接修改转写结果中的错别字或格式问题。
- 编辑后的文本可通过点击「仅总结」按钮重新生成摘要。

#### 摘要（「摘要」标签页）

- 显示 AI 生成的摘要内容。
- 摘要以 Markdown 格式渲染显示，支持表格、代码块、列表等格式。
- 内容为只读，不可直接编辑。

#### 右键菜单

在文件列表中右键点击某个文件，可选择：
- **重新转写**：对选中的视频重新执行转写（需要原始视频文件仍在原路径）。
- **重新总结**：对选中的视频重新执行总结（需要已存在转写文件）。

---

### 全屏结果查看器

点击主界面的「全屏查看」按钮（或在结果查看窗口中按 `F11`），可打开独立的结果查看窗口，提供更舒适的浏览体验。

#### 工具栏

| 控件 | 说明 |
|------|------|
| **字体大小** | 数值调节框，也可用 `Ctrl+滚轮` 或 `Ctrl++`/`Ctrl+-` 调节（范围 8-32pt，默认 14pt），`Ctrl+0` 重置为默认 |
| **主题** | 下拉框切换「浅色」/「深色」主题，选择会自动保存 |
| **全屏** | 按 `F11` 切换全屏，`Esc` 退出全屏 |
| **添加书签** | `Ctrl+B` 在当前位置添加书签 |
| **书签面板** | `Ctrl+Shift+B` 显示/隐藏书签侧栏 |
| **文件夹模式** | `Ctrl+D` 切换文件夹模式（树形视图） |
| **关闭** | `Ctrl+W` 关闭窗口 |

#### 搜索功能

- 按 `Ctrl+F` 聚焦搜索框，输入关键词后自动搜索（300ms 防抖）。
- 按 `F3` 或 `Ctrl+G` 跳转到下一个匹配项。
- 按 `Shift+F3` 或 `Ctrl+Shift+G` 跳转到上一个匹配项。
- 搜索栏右侧显示匹配计数（如「3/15」）。
- 当前匹配项高亮为橙色，其他匹配项高亮为黄色。

#### 书签系统

- 在转写文本或摘要中定位到需要标记的位置，按 `Ctrl+B` 添加书签。
- 按 `Ctrl+Shift+B` 打开书签面板，显示所有已添加的书签。
- 书签列表中显示 `[转写/摘要] 文件名 + 预览文本`。
- 双击书签条目可跳转到对应文件和位置。
- 书签支持按关键词过滤，也可单独删除或清空。
- 书签数据跨会话自动保存。

#### 文件夹模式

- 按 `Ctrl+D` 切换到文件夹模式，左侧文件列表变为树形结构。
- 按子目录分层展示，目录节点加粗并显示子视频数量（如 `subfolder (3)`）。
- 子文件夹默认折叠，点击展开。

---

### 提示词配置

主界面底部的「提示词配置」区域用于自定义总结时使用的提示词。

#### 使用方法

1. 在文本框中输入自定义提示词（如「请用中文总结以下内容的要点，重点关注技术细节」）。
2. 如果留空，将使用默认提示词：「你是一个专业的文本总结助手，擅长提取关键信息并生成简洁准确的总结。」
3. 点击「仅总结」或「转写+总结」时，会将提示词与转写文本组合后发送给 AI 模型。

#### 模板管理

- **保存模板**：输入提示词后，从下拉框中输入新模板名称，点击「保存提示词」即可保存。
- **加载模板**：从下拉框中选择已保存的模板名称，提示词会自动填充到文本框。
- **删除模板**：选择要删除的模板，点击「删除提示词」并确认。
- 程序会自动记住上次使用的模板，下次启动时自动恢复。

#### 输出格式

提示词中已内置 Markdown 格式指令，摘要会自动以要点标题 + 内容的 Markdown 格式输出，确保结构清晰。

---

### 设置与配置

#### 图形界面配置

通过菜单「设置 → 编辑配置」打开配置编辑对话框，所有配置项按标签页分类展示：

**转写标签页：**

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| 模型路径 | Whisper 模型名称或路径 | `large-v3` |
| 设备 | 计算设备（`auto`/`cpu`/`cuda`/`mps`） | `cuda` |
| 语言 | 转写语言（`auto`/`zh`/`en`/`ja` 等） | `zh` |
| 束搜索宽度 | beam search 大小 | `5` |
| 候选数量 | best_of 参数 | `5` |
| 温度 | 转写温度参数 | `0.0` |
| 计算类型 | 精度（`float16`/`int8`/`float32`） | `float16` |
| 工作线程数 | 并行线程数 | `1` |
| VAD 过滤 | 是否启用语音活动检测 | `True` |

**总结标签页：**

支持两种总结服务提供商，通过单选按钮切换：

*本地 Ollama 模型：*

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| Ollama 服务地址 | Ollama API 地址 | `http://127.0.0.1:11434` |
| 模型名称 | 已安装的 Ollama 模型（可下拉选择或手动输入） | `qwen2.5:7b-instruct-q4_K_M` |
| 最大长度 | 最大生成长度 | `5000` |
| 温度 | 生成温度 | `0.7` |
| 超时时间 | 请求超时（秒） | `300` |

对话框中还提供以下 Ollama 管理按钮：
- **启动服务**：自动启动 `ollama serve` 进程，等待最多 10 秒
- **关闭服务**：关闭由本程序启动的 Ollama 服务（外部启动的无法关闭）
- **测试连接**：检测 Ollama 服务是否可访问，成功显示绿色提示，失败显示红色提示
- **刷新模型列表**：从 Ollama 获取已安装的模型列表，更新下拉框

*在线 NVIDIA 模型：*

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| NVIDIA API 地址 | API 端点 | `https://integrate.api.nvidia.com/v1/chat/completions` |
| NVIDIA 模型 | 模型名称 | `openai/gpt-oss-120b` |
| 最大 Token 数 | 最大生成 token 数 | `100000` |
| 温度 | 生成温度 | `1.0` |
| Top P | 核采样参数 | `1.0` |
| 频率惩罚 | 重复惩罚 | `0.0` |
| 存在惩罚 | 话题惩罚 | `0.0` |

提供「测试连接」按钮验证 API Key 和网络连通性。

> 使用 NVIDIA 模型需设置 `NVIDIA_API_KEY` 环境变量或在 `.env` 文件中配置。

**其他标签页：**

| 标签页 | 主要配置项 |
|--------|-----------|
| 预处理 | FFmpeg 路径、音频采样率（16000Hz）、音频声道数（1）、最大分段时长（300秒）、支持的视频格式 |
| 输出 | 输出目录、转写格式（txt/srt/vtt/json，可逗号分隔多选）、摘要格式（txt/md） |
| 网络 | 代理地址（用于 HuggingFace 模型下载） |
| 路径 | 模型目录、日志目录、视频目录 |

> 路径类配置项右侧有「浏览」按钮，可直接选择目录。

#### 配置文件手动编辑

程序目录下的 `config.ini` 可直接用文本编辑器修改，完整配置如下：

```ini
[app]
log_level = INFO              # 日志级别: DEBUG/INFO/WARNING/ERROR

[transcription]
model_path = large-v3         # 转写模型名称或路径
device = cuda                 # 设备: auto/cpu/cuda/mps
language = zh                 # 语言: auto/zh/en/ja/...
beam_size = 5                 # beam search 大小
best_of = 5                   # 候选数量
temperature = 0.0             # 温度参数
compute_type = float16        # 计算类型: float16/int8/float32
num_workers = 1               # 工作线程数
vad_filter = True             # VAD 过滤

[summarization]
provider = ollama             # 服务商: ollama/nvidia
ollama_url = http://127.0.0.1:11434  # Ollama 服务地址
model_name = qwen2.5:7b-instruct-q4_K_M  # 模型名称
max_length = 10000            # 最大生成长度
temperature = 0.7             # 温度参数
timeout = 600                 # 请求超时时间（秒）
custom_prompt =               # 自定义提示词
nvidia_api_url = https://integrate.api.nvidia.com/v1/chat/completions
nvidia_model = openai/gpt-oss-120b
nvidia_max_tokens = 100000
nvidia_temperature = 1.0
nvidia_top_p = 1.0
nvidia_frequency_penalty = 0.0
nvidia_presence_penalty = 0.0

[preprocessing]
ffmpeg_path = ffmpeg          # FFmpeg 路径
audio_sample_rate = 16000     # 音频采样率
audio_channels = 1            # 音频声道数
max_chunk_duration = 300      # 最大切片时长（秒）
supported_video_formats = .mp4,.avi,.mov,.mkv,.flv,.wmv,.webm,.ts,.mts,.m4v,.3gp,.mpeg,.mpg,.vob,.ogv,.rm,.rmvb

[output]
output_dir = output           # 默认输出目录
transcript_format = txt       # 转写格式（可逗号分隔: txt,srt,vtt,json）
summary_format = md           # 摘要格式: txt/md

[network]
proxy =                       # 代理地址（用于 HuggingFace 模型下载）

[paths]
models_dir = models           # 模型目录
logs_dir = logs               # 日志目录
video_dir = video             # 视频目录
```

也可通过环境变量 `VIDEO2TEXT_CONFIG` 指定自定义配置文件路径。

---

### 输出文件说明

#### 文件命名

| 类型 | 文件名格式 | 示例 |
|------|-----------|------|
| 转写文件 | `{视频名}.{格式}` | `video1.txt`、`video1.srt` |
| 摘要文件 | `{视频名}_summary.{格式}` | `video1_summary.md` |

#### 转写格式

| 格式 | 说明 |
|------|------|
| `txt` | 可读文本，每行 `[HH:MM:SS - HH:MM:SS] 文本` |
| `srt` | SRT 字幕格式，可用于视频播放器加载 |
| `vtt` | WebVTT 字幕格式，适用于网页播放器 |
| `json` | JSON 数组，每项包含 `start`、`end`、`text`、`confidence`、`language` 字段 |

> 可在 `config.ini` 中设置 `transcript_format = txt,srt,json` 同时输出多种格式。

#### 摘要格式

| 格式 | 说明 |
|------|------|
| `txt` | 纯文本格式 |
| `md` | Markdown 格式（默认），支持标题、列表、加粗等 |

---

### 命令行使用（CLI）

除图形界面外，也可在命令行中直接使用。在程序所在目录打开终端，使用 `video2text.exe` 加子命令：

#### 转写命令

```powershell
.\video2text.exe transcribe <视频文件路径> [选项]
```

| 选项 | 缩写 | 说明 | 默认值 |
|------|------|------|--------|
| `--output-dir` | `-o` | 输出目录 | `output` |
| `--language` | `-l` | 语言代码 | `zh` |
| `--model` | `-m` | 转写模型 | `large-v3` |
| `--device` | `-d` | 设备类型 | `cuda` |
| `--beam-size` | - | beam search 大小 | `5` |
| `--temperature` | - | 温度参数 | `0.0` |
| `--verbose` | `-v` | 详细输出 | 关闭 |

示例：
```powershell
.\video2text.exe transcribe "D:\videos\lecture.mp4" -o output -l zh -m large-v3 -d cuda
```

#### 总结命令

```powershell
.\video2text.exe summarize <转写文本文件路径> [选项]
```

| 选项 | 缩写 | 说明 | 默认值 |
|------|------|------|--------|
| `--output-dir` | `-o` | 输出目录 | `output` |
| `--model` | `-m` | 总结模型 | `qwen2.5:7b-instruct-q4_K_M` |
| `--max-length` | - | 最大长度 | `5000` |
| `--temperature` | - | 温度参数 | `0.7` |
| `--verbose` | `-v` | 详细输出 | 关闭 |

示例：
```powershell
.\video2text.exe summarize output\lecture.txt -o output -m qwen2.5:7b-instruct-q4_K_M
```

#### 完整流程命令

```powershell
.\video2text.exe run-pipeline <视频文件路径> [选项]
```

| 选项 | 缩写 | 说明 | 默认值 |
|------|------|------|--------|
| `--output-dir` | `-o` | 输出目录 | `output` |
| `--language` | `-l` | 语言代码 | `zh` |
| `--transcription-model` | - | 转写模型 | `large-v3` |
| `--summarization-model` | - | 总结模型 | `qwen2.5:7b-instruct-q4_K_M` |
| `--device` | `-d` | 设备类型 | `cuda` |
| `--beam-size` | - | beam search 大小 | `5` |
| `--temperature` | - | 转写温度 | `0.0` |
| `--summary-temperature` | - | 总结温度 | `0.7` |
| `--max-length` | - | 最大长度 | `5000` |
| `--verbose` | `-v` | 详细输出 | 关闭 |

示例：
```powershell
.\video2text.exe run-pipeline "D:\videos\meeting.mp4" -o output -l zh --transcription-model large-v3 --summarization-model qwen2.5:7b-instruct-q4_K_M
```

#### 其他命令

```powershell
# 查看版本
.\video2text.exe version

# 查看所有命令及用法
.\video2text.exe --help
.\video2text.exe help
```

---

### 高级功能

#### 断点续传

对于长视频（超过 300 秒），程序会自动将音频切片分段转写。每完成一个切片会保存检查点，如果任务中断（如程序崩溃、手动关闭），下次重新运行相同视频时会自动跳过已完成的切片，从中断处继续。

#### 模型自动下载

首次运行时如果 `models/large-v3/` 目录下没有模型文件（或文件不完整），程序会自动从 HuggingFace 下载（约 3GB）。下载支持：
- 代理设置（在 `config.ini` 的 `[network] proxy` 中配置）
- 失败自动重试（最多 3 次，指数退避）
- 下载进度显示在日志面板

#### GPU 显存管理

- 转写模型加载到 GPU 后会缓存复用，避免重复加载。
- 关闭程序时会自动卸载模型并释放 GPU 显存。
- 如果 GPU 显存不足（CUDA OOM），程序会自动降级：`float16` → `int8` → `float32` → `int8_float16`，最终回退到 CPU 模式。

#### 日志系统

程序运行日志保存在 `logs/` 目录下：

| 日志文件 | 级别 | 说明 |
|----------|------|------|
| `app.log` | INFO | 常规运行日志，5MB 轮转，保留 7 份 |
| `debug.log` | DEBUG | 详细调试日志，10MB 轮转，保留 3 份 |
| `error.log` | ERROR | 错误日志，10MB 轮转，保留 30 份 |

失败的任务会额外记录到 `logs/fail_log.log`，包含时间戳、操作模式、视频名称和错误信息。

#### 加载历史结果

点击「加载历史」按钮可以扫描输出目录中已有的转写和总结文件，加载到文件列表中查看。适用于之前处理过的视频需要再次查看结果的场景。

---

### 注意事项

- **Ollama 服务**：使用总结功能前需确保 Ollama 已启动。程序可自动启动 Ollama 服务（在配置对话框中点击「启动服务」），也可手动运行 `ollama serve`。如果使用 Ollama，需先拉取模型：`ollama pull qwen2.5:7b-instruct-q4_K_M`。
- **GPU 加速**：如无 NVIDIA 显卡，请在配置中将「设备」改为 `cpu`，转写速度会明显较慢。
- **模型文件**：`large-v3` 模型约 3GB，必须放置在 `models/large-v3/` 目录下。首次运行时如未找到模型会自动下载（需联网）。
- **ffmpeg 路径**：确保 `ffmpeg` 已添加到系统 `PATH`，或在配置中设置 FFmpeg 路径为完整路径（如 `C:\ffmpeg\bin\ffmpeg.exe`）。
- **代理设置**：如果网络环境需要代理才能访问 HuggingFace，请在网络标签页中配置代理地址。
- **关闭程序**：关闭程序时会自动取消运行中的任务、恢复暂停状态、停止本程序启动的 Ollama 服务、释放 GPU 显存。
- **API Key**：使用 NVIDIA 总结服务需设置环境变量 `NVIDIA_API_KEY`；使用带认证的 Ollama 服务需设置 `OLLAMA_API_KEY`。可通过系统环境变量或程序目录下的 `.env` 文件配置。