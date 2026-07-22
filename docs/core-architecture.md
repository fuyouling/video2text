# Video2Text 核心逻辑与实现方式

## 目录

- [1. 项目概述](#1-项目概述)
- [2. 整体架构](#2-整体架构)
- [3. 程序入口与启动流程](#3-程序入口与启动流程)
- [4. 配置管理模块](#4-配置管理模块)
- [5. 视频预处理模块](#5-视频预处理模块)
- [6. 语音转写引擎](#6-语音转写引擎)
- [7. 文本处理模块](#7-文本处理模块)
- [8. 文本总结引擎](#8-文本总结引擎)
- [9. 业务服务层](#9-业务服务层)
- [10. 存储与输出模块](#10-存储与输出模块)
- [11. 用户界面层](#11-用户界面层)
- [12. 工具模块](#12-工具模块)
- [13. 关键设计模式与技术决策](#13-关键设计模式与技术决策)
- [14. 数据流全景图](#14-数据流全景图)

---

## 1. 项目概述

Video2Text 是一个视频/音频转文本工具，核心功能包括：

- **语音转写**：基于 `faster_whisper`（OpenAI Whisper 的 CTranslate2 优化实现）将视频/音频中的语音转换为文本
- **智能总结**：通过本地 Ollama（Qwen2.5）或在线 NVIDIA API 对转写文本进行摘要总结
- **多格式输出**：支持 TXT、SRT、VTT、JSON 四种转写输出格式，以及 TXT/MD 两种摘要格式
- **双界面**：CLI（Typer + Rich）和 GUI（PySide6）两种使用方式

**技术栈**：

| 层级 | 技术 |
|------|------|
| CLI 框架 | Typer + Rich |
| GUI 框架 | PySide6 (Qt for Python) |
| 语音转写 | faster_whisper (CTranslate2) |
| 文本总结 | Ollama HTTP API / NVIDIA OpenAI 兼容 API |
| 视频处理 | FFmpeg / ffprobe（子进程调用） |
| 配置管理 | Python configparser |
| 日志系统 | Python logging + RotatingFileHandler |
| 打包工具 | PyInstaller |

---

## 2. 整体架构

项目采用**分层架构**，从底向上分为以下层次：

```
┌─────────────────────────────────────────────────────┐
│ 用户界面层 (UI) │
│ src/ui/cli.py | src/ui/gui.py │
│ src/ui/result_viewer.py │
├─────────────────────────────────────────────────────┤
│ 业务服务层 (Services) │
│ src/services/transcription_service.py │
│ src/services/summarization_service.py │
│ src/services/voice_recorder.py │
│ src/services/voice_transcription.py │
├─────────────────────────────────────────────────────┤
│ 核心引擎层 (Engines) │
│ src/transcription/ │ src/text_processing/ │
│ src/preprocessing/ │ src/summarization/ │
├─────────────────────────────────────────────────────┤
│ 基础设施层 (Infrastructure) │
│ src/config/ │ src/storage/ │ src/utils/ │
└─────────────────────────────────────────────────────┘
```

**模块职责划分**：

| 模块 | 路径 | 职责 |
|------|------|------|
| 配置管理 | `src/config/` | 读写 config.ini，路径解析，转写配置加载，版本信息，收藏目录管理 |
| 预处理 | `src/preprocessing/` | FFmpeg 路径管理，音视频验证，音频提取 |
| 转写引擎 | `src/transcription/` | faster_whisper 模型加载、转写、OOM 降级 |
| 文本处理 | `src/text_processing/` | 段落合并、文本清理（去填充词、标点修复） |
| 总结引擎 | `src/summarization/` | Ollama/NVIDIA 客户端，Provider 抽象层，提示词管理 |
| 业务服务 | `src/services/` | 编排预处理→转写→总结的完整流程，断点续传 |
| 存储输出 | `src/storage/` | 文件写入（原子写入）、格式化（OutputFormatter）、书签管理、对话存储 |
| 用户界面 | `src/ui/` | CLI 命令定义、GUI 主窗口、Worker 线程、结果查看器、主题管理、摘要标签页 |
| 工具 | `src/utils/` | 异常体系、日志、模型下载、校验器、时间格式化、路径、JSON 读写、限流、环境变量、子进程兼容 |

---

## 3. 程序入口与启动流程

### 3.1 入口文件 `src/main.py`

```python
def main():
    if getattr(sys, "frozen", False) and len(sys.argv) <= 1:
        # 打包模式（PyInstaller）且无参数 → 启动 GUI
        from src.ui.gui import main as gui_main
        gui_main()
    else:
        # 源码模式或有参数 → 启动 CLI（Typer）
        from src.ui.cli import app
        app()
```

**启动决策逻辑**：
1. 检测 `sys.frozen` 属性判断是否为 PyInstaller 打包环境
2. 打包模式下无命令行参数时默认启动 GUI
3. 其他情况启动 CLI（Typer 框架解析命令）
4. 启动异常时在打包模式下将 traceback 写入 `logs/error_startup.log`

### 3.2 环境变量加载

程序启动时首先通过 `python-dotenv` 加载 `.env` 文件：

```python
load_dotenv(get_base_dir() / ".env", override=True)
```

主要用于读取 `NVIDIA_API_KEY`、`OLLAMA_API_KEY` 等敏感配置。

---

## 4. 配置管理模块

### 4.1 Settings 单例类 (`src/config/settings.py`)

**设计要点**：
- **单例模式**：通过 `__new__` + 线程锁实现，同一进程内只加载一次配置文件
- **原子写入**：保存配置时先写入临时文件，再通过 `os.replace` 原子替换，防止崩溃导致配置损坏
- **路径自动解析**：`PATH_KEYS` 中定义的配置项（如 `paths.models_dir`）在读取时自动基于程序目录解析为绝对路径
- **绿色版支持**：支持通过环境变量 `VIDEO2TEXT_CONFIG` 指定配置路径，兼容便携版部署

**核心 API**：

```python
settings = Settings()
value = settings.get("section.key", default="默认值")         # 获取字符串
num = settings.get_int("section.key", default=0)              # 获取整数
flag = settings.get_bool("section.key", default=False)        # 获取布尔值
float_val = settings.get_float("section.key", default=0.0)    # 获取浮点数
items = settings.get_list("section.key", default=[])          # 获取逗号分隔列表
section = settings.get_section("section")                     # 获取整个配置节
settings.set("section.key", "new_value")                      # 设置值
settings.save()                                               # 持久化到磁盘
settings.reload()                                             # 从磁盘重新加载
```

**配置文件结构** (`config.ini`)：

| 配置段 | 用途 |
|--------|------|
| `[app]` | 应用级设置（日志级别、代理、背景图片） |
| `[transcription]` | 转写引擎参数（模型、设备、beam_size 等） |
| `[summarization]` | 总结引擎参数（provider、模型、温度等） |
| `[preprocessing]` | 预处理参数（采样率、分段阈值） |
| `[output]` | 输出参数（目录、格式） |
| `[text_processing]` | 文本处理参数（合并间隔、填充词） |
| `[paths]` | 路径配置（模型、日志、视频目录） |
| `[tools]` | 预留 |

### 4.2 TranscriptionConfig (`src/config/transcription_config.py`)

**职责**：从 Settings 加载转写参数，返回 `TranscriptionConfig` dataclass。

```python
@dataclass
class TranscriptionConfig:
    language: str
    model_path: str
    device: str
    compute_type: str
    beam_size: int
    best_of: int
    temperature: float
    condition_on_previous_text: bool
    word_timestamps: bool
    max_chunk_duration: int
    output_formats: list[str]
```

工厂函数 `_load_tx_config(settings)` 从 config.ini 读取并构造 `TranscriptionConfig`，供 `TranscriptionService` 和 CLI/GUI 使用。

### 4.3 版本信息 (`src/config/version.py`)

```python
APP_NAME = "video2text"
APP_VERSION = "2.4.2"
```

### 4.4 DirectoryManager (`src/config/directory_manager.py`)

管理用户收藏的常用输入/输出目录，数据存储在 `favorite_dirs.json` 中：

```json
{
    "input_dirs": ["C:/Videos", "D:/Media"],
    "output_dirs": ["C:/Output"]
}
```

**特性**：
- 零 Qt 依赖，纯 Python 标准库实现
- 线程安全（内部加锁）
- 路径去重（基于 `os.path.normpath` + `os.path.normcase`）
- 新添加的目录插入列表头部（最近使用优先）
- 原子写入 JSON 文件

---

## 5. 视频预处理模块

### 5.1 FFmpeg 管理器 (`src/preprocessing/ffmpeg.py`)

**职责**：返回项目内置 FFmpeg/ffprobe 的绝对路径，不存在则报错。

**实现要点**：
- **内置路径**：从项目根目录下的 `ffmpeg/bin/` 查找可执行文件
- **打包兼容**：PyInstaller 打包模式下基于 `sys.executable` 所在目录查找
- **路径缓存**：使用全局变量缓存已解析的路径，避免重复文件系统检查
- **ffprobe 独立**：`ensure_ffprobe()` 与 `ensure_ffmpeg()` 分别查找，路径结构相同

### 5.2 VideoProcessor (`src/preprocessing/video_processor.py`)

**职责**：音视频文件验证、信息提取、音频提取。

**核心数据结构**：

```python
@dataclass
class VideoInfo:
    duration: float          # 时长（秒）
    width: int               # 视频宽度
    height: int              # 视频高度
    fps: float               # 帧率
    codec: str               # 视频编码
    audio_codec: str         # 音频编码
    audio_sample_rate: int   # 音频采样率
    has_audio: bool          # 是否包含音轨
```

**音频提取流程**：

```
输入音视频文件
    │
    ├─ 是音频文件？ → 直接转换为 WAV
    │
    └─ 是视频文件？
        │
        ├─ 检查是否有音轨（ffprobe）
        │   └─ 无音轨 → 抛出 VideoFileError
        │
        ├─ 优先使用 pcm_s16le 编码提取（无损 WAV）
        │
        └─ 失败？ → 回退方案：先提取为 MP3，再转为 WAV
```

**音频提取命令**：

```bash
# 主方案：直接提取为 WAV
ffmpeg -i input.mp4 -vn -acodec pcm_s16le -ar 16000 -ac 1 -y output.wav

# 回退方案：先提取 MP3 再转 WAV
ffmpeg -i input.mp4 -vn -acodec libmp3lame -ar 16000 -ac 1 -q:a 2 -y temp.mp3
ffmpeg -i temp.mp3 -acodec pcm_s16le -ar 16000 -ac 1 -y output.wav
```

**支持的格式**（从 `config.ini` 的 `[preprocessing]` 节读取，可自定义）：
- 视频默认：17 种（mp4, avi, mov, mkv, flv, wmv, webm, ts, mts, m4v, 3gp, mpeg, mpg, vob, ogv, rm, rmvb）
- 音频默认：7 种（mp3, wav, flac, aac, ogg, m4a, wma）

---

## 6. 语音转写引擎

### 6.1 Transcriber 类 (`src/transcription/transcriber.py`)

**核心职责**：封装 `faster_whisper.WhisperModel`，提供线程安全的模型加载与转写。

**模型缓存机制**：

```python
_MAX_MODEL_CACHE = 2  # 最多缓存 2 个模型实例
_model_cache: OrderedDict[str, "Transcriber"] = OrderedDict()  # LRU 缓存

def get_cached_transcriber(model_path, device, compute_type, num_workers, download_root):
    cache_key = f"{model_path}|{device}|{compute_type}|{num_workers}"
    # 命中缓存 → 移到末尾（最近使用）并返回
    # 未命中 → 淘汰最旧条目 → 创建新实例
```

**模型加载策略（含 OOM 降级）**：

```
1. 检查 _loaded 标志，已加载则直接返回
2. 检查核心文件是否完整（model.bin, config.json, tokenizer.json 等）
   └─ 不完整 → 触发自动下载（ModelDownloader）
3. 使用用户配置的 device/compute_type 加载
4. 若 CUDA OOM：
    ├─ 尝试 int8 → float32 → int8_float16（同 device）
    └─ 仍失败 → 回退到 CPU + int8 → float32（最终兜底）
5. 每次失败后调用 torch.cuda.empty_cache() 释放显存
```

**转写数据结构**：

```python
@dataclass
class TranscriptSegment:
    start: float       # 开始时间（秒）
    end: float         # 结束时间（秒）
    text: str          # 转写文本
    confidence: float  # 置信度（0~100%）
    language: str      # 检测到的语言
```

**置信度计算**：

```python
def _logprob_to_confidence(avg_logprob: float) -> float:
    """将 avg_logprob（负值）转换为 0~100% 的置信度"""
    return round(max(0.0, min(100.0, math.exp(avg_logprob) * 100)), 2)
```

### 6.2 模型下载器 (`src/utils/model_downloader.py`)

**功能**：从 HuggingFace 自动下载 faster-whisper 模型文件。

**核心特性**：
- **断点续传**：通过 HTTP Range 请求头实现，已下载部分不重复下载
- **代理支持**：自动检测 HuggingFace 可访问性，不可用时尝试配置的代理
- **自动重试**：最多重试 5 次，指数退避（2^n 秒，最大 30 秒）
- **核心/可选文件区分**：核心文件下载失败则整体失败，可选文件失败仅警告

**模型文件清单**（large-v3）：

| 文件 | 类型 | 说明 |
|------|------|------|
| `model.bin` | 核心 | 模型权重（约 3GB） |
| `config.json` | 核心 | 模型配置 |
| `tokenizer.json` | 核心 | 分词器 |
| `preprocessor_config.json` | 核心 | 预处理配置 |
| `vocabulary.json` | 核心 | 词表 |

---

## 7. 文本处理模块

### 7.1 SegmentMerger (`src/text_processing/segment_merger.py`)

**职责**：将转写引擎输出的细粒度段落合并为更易阅读的段落。

**合并策略**：

| 策略 | 方法 | 规则 |
|------|------|------|
| 默认合并 | `merge_segments()` | 相邻段落时间间隔 ≤ max_gap 且语言相同 → 合并 |
| 按长度合并 | `merge_by_length()` | 当前段文本长度 < target_length 且语言相同 → 合并 |
| 按时间合并 | `merge_by_time()` | 段落起始时间间隔 < interval 且语言相同 → 合并 |
| 过滤短段落 | `filter_short_segments()` | 移除文本长度 < min_length 的段落 |

**通用合并算法**：

```python
def _merge(segments, should_merge, label):
    merged = []
    current = None
    for segment in segments:
        if current is None:
            current = MergedSegment(start=segment.start, end=segment.end, text=segment.text)
        elif should_merge(current, segment):
            current.end = segment.end
            current.text += " " + segment.text  # 拼接文本
        else:
            merged.append(current)
            current = MergedSegment(...)
    if current:
        merged.append(current)
    return merged
```

### 7.2 TextCleaner (`src/text_processing/text_cleaner.py`)

**职责**：清理转写文本中的噪声，提升可读性和总结质量。

**构造参数**：

```python
TextCleaner(config={
    "filler_words": ["嗯", "啊", "呃", "嗯嗯", "啊啊"],  # 填充词列表
    "normalize_punctuation": False,  # 是否将中文标点转换为英文标点
})
```

**处理流程**：

```
原始文本
    │
    ├─ 1. remove_extra_whitespace()    # 移除多余空白、规范化换行
    │
    ├─ 2. remove_fillers()             # 移除填充词（嗯、啊、呃 等）
    │
    ├─ 3. fix_punctuation()            # 修复标点符号
    │   ├─ normalize_punctuation=True → 中文标点转为英文标点
    │   ├─ 移除标点前的空格
    │   ├─ 规范化连续标点（。。。→ 。。。）
    │   └─ 移除重复标点
    │
    ├─ 4. normalize_quotes()           # 规范化引号（"" → "）
    │
    └─ 5. remove_repeated_chars()      # 移除重复字符
        ├─ 英文连续 3+ 字符 → 2 字符
        └─ 中文连续 5+ 字符 → 3 字符
```

**填充词处理**：
- 填充词列表从配置读取，默认：`嗯, 啊, 呃, 嗯嗯, 啊啊`
- 英文填充词使用 `\b` 词边界匹配，中文直接正则匹配
- 按长度降序排列，避免短词误匹配长词的前缀

---

## 8. 文本总结引擎

### 8.1 Provider 抽象层 (`src/summarization/providers.py`)

**设计模式**：策略模式 + 工厂模式

```python
class SummarizationProvider(Protocol):
    """总结提供商协议 —— 所有 Provider 必须实现这三个方法"""
    def check_connection(self) -> bool: ...
    def summarize(self, text, custom_prompt="", stream=False,
                  on_token=None, cancel_check=None, pause_event=None) -> str: ...
    def close(self) -> None: ...

class OllamaProvider:
    """本地 Ollama 模型总结"""
    ...

class NvidiaProvider:
    """在线 NVIDIA API 总结"""
    ...

def create_provider(settings: Settings) -> SummarizationProvider:
    """工厂函数 —— 根据配置创建对应的 Provider 实例"""
    provider_name = settings.get("summarization.provider", "ollama")
    if provider_name == "nvidia":
        return NvidiaProvider(settings)
    if provider_name != "ollama":
        logger.warning("未知的总结提供商 '%s'，回退到 Ollama", provider_name)
    return OllamaProvider(settings)
```

### 8.2 OllamaClient (`src/summarization/ollama_client.py`)

**职责**：管理 Ollama HTTP 通信与服务进程生命周期。

**服务进程管理**：

```
start_service(url)
    │
    ├─ HTTP 探测 /api/tags → 200？ → 服务已在运行（外部启动，不管理进程）
    │
    ├─ 已启动的进程仍存活？ → 返回 True
    │
    └─ 启动 ollama serve 子进程
        └─ 轮询等待最多 5 秒 → HTTP 探测成功 → 返回 True

stop_service()
    └─ Windows: taskkill /T /F /PID (终止进程树)
       Linux: proc.terminate() → proc.wait(5s) → proc.kill()
```

**HTTP 通信**：
- 使用 `requests.Session` 维持连接池
- 支持 API Key 认证（`OLLAMA_API_KEY` 环境变量）
- 带重试的 POST 请求（指数退避，最多 3 次）
- 支持流式输出（逐行解析 JSON 响应）

**API 端点**：

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/tags` | GET | 列出可用模型 |
| `/api/generate` | POST | 生成文本（流式/非流式） |

### 8.3 NvidiaClient (`src/summarization/nvidia_client.py`)

**职责**：通过 OpenAI 兼容接口调用 NVIDIA 模型。

**API 端点**：`https://integrate.api.nvidia.com/v1/chat/completions`

**请求格式**：

```json
{
    "model": "openai/gpt-oss-120b",
    "messages": [{"role": "user", "content": "提示词"}],
    "max_tokens": 100000,
    "temperature": 1.0,
    "top_p": 1.0,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "stream": false
}
```

**特性**：
- **429 限流处理**：自动读取 `Retry-After` 响应头，指数退避重试（最多 5 次）
- **流式输出**：解析 SSE（Server-Sent Events）格式，逐 token 回调
- **API Key 来源**：优先环境变量 `NVIDIA_API_KEY`，其次 `.env` 文件

### 8.5 PromptManager (`src/summarization/prompt_manager.py`)

**职责**：管理用户自定义提示词模板，支持持久化。

**数据存储**（`prompts.json`）：

```json
{
    "templates": {
        "学术论文": "请以学术论文的风格总结以下内容...",
        "会议纪要": "请提取会议的关键决策和行动项..."
    },
    "last_used": "会议纪要",
    "markdown_prompt": "\n请将总结内容以Markdown格式输出...",
    "markdown_enabled": true
}
```

**提示词构建流程**：

```python
def build_prompt(self, text: str, custom_prompt: str = "") -> str:
    if custom_prompt and custom_prompt.strip():
        base = custom_prompt.strip()
    else:
        base = "你是一个专业的文本总结助手，擅长提取关键信息并生成简洁准确的总结，只输出总结正文，**禁止添加任何开头语、结尾说明、解释性语句、备注**，**不要额外修饰、补充话术，纯输出总结内容**。"

    if self._markdown_enabled:
        md_prompt = self._markdown_prompt
        if md_prompt.strip():
            return f"{base}\n\n{md_prompt}\n\n文本内容：\n{text}"
    return f"{base}\n\n文本内容：\n{text}"
```

---

## 9. 业务服务层

### 9.1 TranscriptionService (`src/services/transcription_service.py`)

**职责**：统一 CLI / GUI 的转写逻辑，编排 验证→提取音频→转写→保存 的完整流程。

**构造函数参数**：

```python
TranscriptionService(
    transcriber, video_processor, file_writer,
    language="auto", beam_size=5, best_of=5, temperature=0.0,
    condition_on_previous_text=True, word_timestamps=False,
    vad_filter=True, max_chunk_duration=300,
    output_formats=["txt"],
    input_folder=None,                # 镜像目录输入文件夹
    mirror_depth=1,                   # 镜像目录深度
    on_video_done=None,               # 单文件完成回调
    on_video_error=None,              # 单文件错误回调
    cancel_check=None,                # 取消检查
)
```

**核心流程**：

```
video_files: List[str]
    │
    ├─ 初始化断点目录 (.checkpoint/)
    ├─ 清理过期断点文件
    │
    └─ 遍历每个视频文件：
        │
        ├─ 检查取消信号
        ├─ 检查暂停状态（_wait_if_paused）
        │
        ├─ validate_input()          # 验证音视频文件
        ├─ get_video_info()          # 获取音视频信息
        ├─ extract_audio()           # 提取音频为 WAV
        │
        ├─ 时长 > max_chunk_duration？
        │   ├─ 是 → _transcribe_chunked()  # 长音频切片转写
        │   └─ 否 → transcriber.transcribe()  # 直接转写
        │
        ├─ file_writer.write_transcript()  # 保存结果
        │
        └─ on_video_done 回调通知调用方（支持镜像目录: input_folder + mirror_depth）
```

**长音频切片转写（断点续传）**：

这是项目的核心特性之一，处理流程：

```
1. 使用 FFmpeg segment 分割音频为 chunk_000.wav, chunk_001.wav, ...
   ffmpeg -i audio.wav -f segment -segment_time 300 -acodec pcm_s16le
          -reset_timestamps 1 chunk_%03d.wav

2. 计算断点文件路径（基于视频路径哈希）：
   .checkpoint/{video_name}_{sha256_hash[:12]}_chunks.json

3. 加载已有断点数据，跳过已完成的切片

4. 逐个切片转写：
   ├─ 已完成（断点中有记录）→ 恢复 segments，调整时间戳偏移
   ├─ 之前失败（断点中有 error 标记）→ 重试
   └─ 新切片 → 转写 → 保存到断点文件

5. 时间戳调整：每个切片的 segments.start/end += cumulative_offset

6. 全部完成 → 删除断点文件
   有失败 → 保留断点文件供下次重试
```

**断点文件格式**：

```json
{
    "chunk_000": {
        "duration": 300.0,
        "segments": [
            {"start": 0.0, "end": 5.2, "text": "...", "confidence": 95.5, "language": "zh"}
        ]
    },
    "chunk_001": {
        "duration": 300.0,
        "segments": [],
        "error": "转写失败: ..."
    }
}
```

**暂停/继续机制**：

```python
self._pause_event = threading.Event()
self._pause_event.set()  # 初始状态：非暂停

def pause(self):
    self._pause_event.clear()  # 阻塞 _wait_if_paused

def resume(self):
    self._pause_event.set()    # 解除阻塞

def _wait_if_paused(self):
    while not self._pause_event.wait(timeout=0.5):  # 每 0.5 秒检查取消信号
        if self.cancel_check and self.cancel_check():
            break
```

**转写时间估算**：

基于历史记录的加权中位数速度估算：
1. 加载同模型/设备的历史记录
2. 计算每条记录的速度（transcribe_time / audio_duration）
3. 时间衰减加权（半衰期 30 天）：`weight = exp(-decay_factor * age)`
4. 计算加权中位数速度
5. 预估时间 = 音频时长 × 中位数速度
6. 样本不足 3 条时返回 None

### 9.2 SummarizationService (`src/services/summarization_service.py`)

**职责**：统一 CLI / GUI 的总结逻辑，支持流式输出、并发总结、暂停/继续与回调通知。

**构造函数参数**：

```python
SummarizationService(
    settings, file_writer, provider,
    custom_prompt="",
    on_stream_token=None,          # 流式 token → GUI
    on_item_started=None,           # 每个文件开始总结
    on_item_done=None,              # 每个文件总结完成
    on_item_error=None,             # 每个文件总结失败
    cancel_check=None,              # 取消检查
    pause_event=None,               # 暂停控制
    rate_limiter=None,              # 限速器（并发模式）
)
```

**方法**：

| 方法 | 说明 |
|------|------|
| `summarize(text, video_name, stream, index, total)` | 单文件总结 |
| `summarize_batch(items, stream, max_workers)` | 批量总结（串行或并发） |
| `close()` | 释放 Provider 资源 |
| `pause()` / `resume()` | 暂停/继续总结 |

**批量总结（并发模式）**：

当 provider 为 `nvidia` 且对应 `_mode=multi` 时，使用线程池并发处理：

```python
def _summarize_batch_concurrent(self, items, stream, total, max_workers):
    # 每个线程创建独立的 Provider 实例（避免共享连接）
    # 使用 RateLimiter 控制请求频率（默认间隔 1.5 秒）
    # 不支持流式输出（并发模式下流式输出无意义）

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for idx, item in enumerate(items):
            future = executor.submit(_process_item, idx, item)
            futures[future] = idx

        for future in as_completed(futures):
            idx, summary = future.result()
            results[idx] = summary
```

---

## 10. 存储与输出模块

### 10.1 FileWriter (`src/storage/file_writer.py`)

**职责**：将转写结果和总结结果写入文件。

**原子写入机制**：

```python
@staticmethod
def _atomic_write(file_path, content, encoding="utf-8"):
    fd, tmp_path = tempfile.mkstemp(dir=file_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
        try:
            os.replace(tmp_path, str(file_path)) # 原子替换
        except OSError:
            import shutil
            shutil.move(tmp_path, str(file_path)) # 跨设备 fallback
    except BaseException:
        try:
            os.unlink(tmp_path) # 失败时清理临时文件
        except OSError:
            pass
        raise
```

**格式化委派**：FileWriter 内部持有 `OutputFormatter` 实例（`src/storage/output_formatter.py`），调用其格式化方法：

| 格式 | 方法 | 说明 |
|------|------|------|
| TXT | `OutputFormatter.format_transcript()` | `[HH:MM:SS - HH:MM:SS] 文本` 每行一段 |
| SRT | `OutputFormatter.format_srt()` | 标准 SRT 字幕格式（序号 + 时间戳 + 文本） |
| VTT | `OutputFormatter.format_vtt()` | WebVTT 格式（WEBVTT 头 + 时间戳 + 文本） |
| JSON | `json.dumps()` | TranscriptSegment 的 dataclass 字典列表 |
| Summary TXT/MD | `OutputFormatter.format_summary()` | 原样返回（不做额外处理） |

**FileWriter 其他方法**：`write_merged_transcript()`（合并后段落）、`write_json()`、`write_text()`、`write_keywords()`、`find_transcript_file()`、`find_summary_file()`。

**输出校验**（`src/utils/output_validator.py`）：

写入后自动校验：
- 文件存在性、最小大小、编码正确性
- SRT：序号连续性、时间戳格式、start < end
- VTT：WEBVTT 头、时间戳格式、start < end
- JSON：可解析性、必要字段（start, end, text）

### 10.2 BookmarkManager (`src/storage/bookmark_manager.py`)

**职责**：管理结果查看器中的书签数据。

**数据结构**：

```python
class BookmarkItem:
    video_name: str # 视频名称
    content_type: str # 'transcript' 或 'summary'
    position: int # 文本位置（字符偏移）
    text: str # 书签处的文本片段（前 100 字符）
    file_path: str # 完整文件路径
    relative_path: str # 相对路径
    created_at: str # 创建时间
    note: str # 用户备注
```

数据持久化到 `bookmarks.json`，线程安全，原子写入。

### 10.3 VoiceConversationStore (`src/storage/voice_conversation_store.py`)

**职责**：管理语音对话的持久化存储，支持上下文记忆和对话历史。

**数据结构**：

```python
class ConversationItem:
    video_name: str # 视频名称
    conversation_id: str # 对话唯一标识
    timestamp: str # 对话时间
    transcript: str # 转写文本
    summary: str # 总结文本
    metadata: dict # 额外元数据（如模型、参数等）
```

数据持久化到 `voice_conversations.json`，线程安全，原子写入。支持按视频、按时间、按关键词检索。

---

## 11. 用户界面层

### 11.1 CLI (`src/ui/cli.py`)

基于 **Typer** 框架定义命令，**Rich** 库美化输出。

**命令定义**：

| 命令 | 功能 | 关键参数 |
|------|------|----------|
| `transcribe` | 仅转写 | input_path, --output-dir, -o, --verbose, -v |
| `summarize` | 仅总结 | input_path, --output-dir, -o, --verbose, -v |
| `run-pipeline` | 转写总结 | input_path, --output-dir, -o, --verbose, -v |
| `version` | 版本信息 | 无 |
| `help` | 命令帮助 | 无 |

> 所有模型、语言、设备、温度等参数均通过 `config.ini` 的 `[transcription]` 和 `[summarization]` 配置，CLI 命令行不暴露这些参数。

**公共初始化流程** (`_init_common`)：

```python
def _init_common(settings, output_dir, verbose):
    # 1. 初始化日志系统
    setup_logger("video2text", log_dir=..., level=...)
    # 2. 创建 VideoProcessor（自动查找内置 FFmpeg）
    video_processor = VideoProcessor()
    # 3. 创建 FileWriter
    file_writer = FileWriter(output_dir)
    return video_processor, file_writer
```

> 输出格式、模型路径等参数通过 `_load_tx_config(settings)` 从 `config.ini` 加载，在创建 `TranscriptionService` 时传入。

### 11.2 GUI (`src/ui/gui.py`)

基于 **PySide6** (Qt for Python) 构建的图形界面。

**主界面布局**：

```
┌─────────────────────────────────────────────────────────┐
│ 菜单栏：文件 | 设置 | 帮助 │
├─────────────────────────────────────────────────────────┤
│ 工具栏：输入文件/文件夹 | 输出目录 | 收藏目录 │
├──────────────────────┬──────────────────────────────────┤
│ │ │
│ 实时日志面板 │ 结果查看面板 │
│ (LogPanel) │ ├─ 文件列表 │
│ │ ├─ 文本内容标签页 │
│ │ └─ 摘要标签页 │
│ │ │
├──────────────────────┴──────────────────────────────────┤
│ 操作按钮：仅转写 | 仅总结 | 转写总结 | 暂停/继续 │
├─────────────────────────────────────────────────────────┤
│ 总结配置面板：Provider 切换 | 模型配置 | 提示词管理 │
├─────────────────────────────────────────────────────────┤
│ 状态栏：进度条 | 成功/失败统计 │
└─────────────────────────────────────────────────────────┘
```

**后台任务** (`src/ui/gui_workers.py`)：

GUI 使用 QObject 子类 + QThread 后台线程执行耗时操作，避免阻塞 UI：

```python
class TranscribeWorker(QObject):
    """转写后台线程"""
    video_done = Signal(str, int, list) # (video_name, segment_count, output_paths)
    video_error = Signal(str, str) # (video_name, error_msg)
    progress = Signal(int, int) # (current, total)
    error = Signal(str)
    finished = Signal()
    confirm_download = Signal()

class PipelineWorker(QObject):
    """完整管道线程（转写 + 总结）"""
    transcribe_done = Signal(str, int, list)
    transcribe_error = Signal(str, str)
    summarize_started = Signal(str)
    summarize_done = Signal(str, str)
    summarize_error = Signal(str, str)
    stream_token = Signal(str) # 流式 token
    progress = Signal(int, int)
    error = Signal(str)
    finished = Signal()
    confirm_download = Signal()
    phase_changed = Signal(str) # "transcribe" | "summarize"
```

### 11.3 结果查看器 (`src/ui/result_viewer.py`)

独立的全屏结果查看窗口，支持：
- 文件列表过滤与树形目录模式
- Markdown 渲染（标题、列表、表格、代码块）
- 关键词搜索与高亮（防抖处理）
- 书签系统（添加、跳转、过滤、删除）
- 浅色/深色主题切换
- 字体大小调节（Ctrl+滚轮）
- 全屏模式（F11）

### 11.4 UI 组件扩展

**新增 UI 组件**：

| 组件 | 路径 | 说明 |
|------|------|------|
| LogPanel | `src/ui/log_panel.py` | 实时日志显示面板，支持颜色编码和过滤 |
| ThemeManager | `src/ui/theme_manager.py` | 深色/浅色主题切换，支持自定义颜色 |
| SummarizationTab | `src/ui/summarization_tab.py` | 总结配置面板，包含 Provider 切换、模型选择、提示词管理 |
| FavoriteDirHelper | `src/ui/favorite_dir_helper.py` | 收藏目录管理，支持添加/删除/导航 |
| DonateDialog | `src/ui/donate_dialog.py` | 捐赠提示对话框 |
| SearchController | `src/ui/search_controller.py` | 关键词搜索控制器，支持高亮和防抖 |
| UILogBridge | `src/ui/ui_log_bridge.py` | 连接日志系统与 GUI 日志面板 |
| BackgroundContent | `src/ui/background_content.py` | 后台内容处理，支持异步加载 |
| VoiceToTextWidget | `src/ui/voice_to_text_widget.py` | 语音转文本显示控件，支持流式输出 |
| MarkdownRenderer | `src/ui/markdown_renderer.py` | Markdown 渲染引擎，支持 LaTeX 数学公式 |
| ResultViewer | `src/ui/result_viewer.py` | 独立结果查看窗口（全屏模式） |

---

## 12. 工具模块

### 12.1 异常体系 (`src/utils/exceptions.py`)

```
Video2TextError (基础异常)
├─ VideoFileError # 音视频文件错误
├─ TranscriptionError # 转写错误
│ └─ DownloadCancelledError # 用户取消模型下载
├─ SummarizationError # 总结错误
├─ ConfigurationError # 配置错误
└─ OutputError # 输出文件错误
```

### 12.2 日志系统 (`src/utils/logger.py`)

**日志文件**：

| 文件 | 级别 | 大小限制 | 备份数 |
|------|------|----------|--------|
| `app.log` | INFO+ | 5MB | 7 |
| `debug.log` | DEBUG+ | 10MB | 3 |
| `error.log` | ERROR+ | 10MB | 30 |

**特性**：
- 同名 logger 只配置一次（通过 `_CONFIGURED_LOGGERS` 集合跟踪）
- 使用 `RotatingFileHandler` 自动轮转
- 格式：`%(asctime)s - %(name)s - %(levelname)s - %(message)s`

### 12.3 时间格式化 (`src/utils/time_format.py`)

```python
format_time_hms(1234.5) # → "00:20:34" (HH:MM:SS)
format_time_srt(1234.5) # → "00:20:34,500" (HH:MM:SS,mmm)
format_time_vtt(1234.5) # → "00:20:34.500" (HH:MM:SS.mmm)
```

所有函数都会将输入钳位到 `[0, 99:59:59]` 范围，处理 inf/NaN/负数。

### 12.4 路径工具 (`src/utils/paths.py`)

```python
get_base_dir() # 获取项目基目录（frozen → sys.executable 所在目录，源码 → 项目根目录）
```

### 12.5 环境变量加载 (`src/utils/env_loader.py`)

自动从 `.env` 文件加载 API Key（无需手动 export）：
```python
ensure_env_loaded() # 加载 .env 到 os.environ
get_api_key("NVIDIA_API_KEY") # 获取指定 key
```

### 12.6 JSON 工具 (`src/utils/json_utils.py`)

提供统一的 JSON 安全读写：
```python
safe_read_json(file_path, default=None) # 安全读取，失败返回默认值
atomic_write_json(file_path, data) # 原子写入
```

### 12.7 速率限制 (`src/utils/rate_limit.py`)

```python
RateLimiter(min_interval=1.5) # 限速器，确保两次操作间隔不低于 min_interval 秒
is_rate_limit(response) # 判断 HTTP 429
get_retry_after(headers) # 解析 Retry-After 头
exponential_backoff(attempt) # 指数退避
```

### 12.8 子进程兼容 (`src/utils/subprocess_compat.py`)

```python
CREATE_NO_WINDOW # Windows 下隐藏控制台窗口，非 Windows 为 0
```

### 12.9 验证器 (`src/utils/validators.py`)

提供参数验证函数：
- `validate_file_path()` — 文件存在性和格式验证
- `validate_directory()` — 目录验证（可选自动创建）
- `validate_language()` — 语言代码验证
- `validate_device()` — 设备类型验证（auto/cpu/cuda/mps）
- `validate_executable_path()` — 可执行文件路径安全性验证（防 shell 注入）

### 12.10 模型下载器 (`src/utils/model_downloader.py`)

**功能**：从 HuggingFace 自动下载 faster-whisper 模型文件。

**核心特性**：
- **断点续传**：通过 HTTP Range 请求头实现，已下载部分不重复下载
- **代理支持**：自动检测 HuggingFace 可访问性，不可用时尝试配置的代理
- **自动重试**：最多重试 5 次，指数退避（2^n 秒，最大 30 秒）
- **核心/可选文件区分**：核心文件下载失败则整体失败，可选文件失败仅警告

**模型文件清单**（large-v3）：

| 文件 | 类型 | 说明 |
|------|------|------|
| `model.bin` | 核心 | 模型权重（约 3GB） |
| `config.json` | 核心 | 模型配置 |
| `tokenizer.json` | 核心 | 分词器 |
| `preprocessor_config.json` | 核心 | 预处理配置 |
| `vocabulary.json` | 核心 | 词表 |

### 12.11 输出校验器 (`src/utils/output_validator.py`)

**职责**：验证输出文件的格式和内容完整性。

**校验规则**：
- 文件存在性、最小大小、编码正确性
- SRT：序号连续性、时间戳格式、start < end
- VTT：WEBVTT 头、时间戳格式、start < end
- JSON：可解析性、必要字段（start, end, text）

### 12.12 生成图标 (`src/utils/generate_icon.py`)

**职责**：生成应用程序图标资源文件。

**功能**：
- 从 SVG 源文件生成多尺寸图标（PNG, ICO）
- 用于 PyInstaller 打包和 GUI 应用程序
- 支持 Windows 和 macOS 图标格式

### 12.13 转写提示词管理器 (`src/transcription/transcription_prompt_manager.py`)

**职责**：管理转写阶段的提示词模板，支持自定义。

**数据存储**（`transcription_prompts.json`）：

```json
{
  "templates": {
    "通用": "请准确转写以下音频内容，保持原始语义，不要添加任何解释或补充。",
    "会议": "请准确转写会议内容，保留发言者身份，标记停顿和语气词。",
    "访谈": "请准确转写访谈内容，保留问答结构，标记沉默和背景音。"
  },
  "last_used": "通用",
  "include_timestamps": true
}
```

**提示词构建流程**：

```python
def build_prompt(self, language: str, custom_prompt: str = "") -> str:
    if custom_prompt and custom_prompt.strip():
        base = custom_prompt.strip()
    else:
        base = "请准确转写以下音频内容，保持原始语义，不要添加任何解释或补充。"

    if self._include_timestamps:
        return f"{base}\n\n请在每句文本前添加时间戳，格式为 [HH:MM:SS]。"
    return base
```

---

## 13. 关键设计模式与技术决策

### 13.1 设计模式

| 模式 | 应用位置 | 说明 |
|------|----------|------|
| **单例模式** | Settings, PromptManager | 全局配置和提示词管理器，进程内唯一实例 |
| **策略模式** | SummarizationProvider | Ollama/NVIDIA 可互换的总结后端 |
| **工厂模式** | `create_provider()` | 根据配置（ollama/nvidia）动态创建 Provider 实例 |
| **观察者模式** | 回调函数（on_progress, on_token） | 服务层通过回调通知 UI 层 |
| **LRU 缓存** | Transcriber 模型缓存 | 最多缓存 2 个模型实例，淘汰最久未使用的 |
| **原子写入** | Settings, FileWriter, 所有 JSON 写入 | 先写临时文件再原子替换，防止崩溃损坏 |

### 13.2 线程安全策略

| 组件 | 同步机制 | 说明 |
|------|----------|------|
| Settings | `threading.Lock` | 单例创建和配置读写 |
| Transcriber | `threading.Lock` | 模型加载和推理 |
| DirectoryManager | `threading.Lock` | 目录列表读写 |
| BookmarkManager | `threading.Lock` | 书签数据读写 |
| FFmpeg 缓存 | `threading.Lock` | 路径缓存读写 |
| 暂停/继续 | `threading.Event` | 协作式暂停控制 |

### 13.3 错误处理策略

1. **异常层次化**：所有自定义异常继承自 `Video2TextError`，便于上层统一捕获
2. **OOM 自动降级**：转写引擎检测到显存不足时自动降级计算精度，最终回退到 CPU
3. **音频提取回退**：pcm_s16le 失败时自动回退到 libmp3lame + 转码
4. **断点续传**：长音频切片转写时持久化中间结果，失败后可从断点恢复
5. **429 限流重试**：NVIDIA API 遇到限流时自动等待并重试
6. **优雅降级**：总结服务不可用时跳过总结，不中断转写流程

### 13.4 便携版支持

- 通过 `get_base_dir()` 统一解析程序根目录（打包模式取 `sys.executable` 所在目录，源码模式取项目根目录）
- 所有路径配置支持相对路径（基于程序目录解析）
- 配置文件路径可通过环境变量 `VIDEO2TEXT_CONFIG` 覆盖
- PyInstaller 打包配置在 `video2text_portable.spec`

---

## 14. 数据流全景图

### 14.1 完整管道（run-pipeline）数据流

完整管道是项目最核心的数据流，串联了 预处理→转写→文本处理→总结→输出 的全部阶段。CLI 和 GUI 共享同一套服务层代码，区别仅在于入口和回调方式。

#### 14.1.1 CLI 入口：`run_pipeline()` (`cli.py:269`)

```python
@app.command()
def run_pipeline(
    input_path: str = typer.Argument(..., help="音视频文件路径（视频或音频）"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="输出目录"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
):
    """运行完整处理管道"""
    # 所有转写/总结参数均通过 config.ini 配置
    # 包括：model_path、device、language、beam_size、temperature 等
```

**CLI 执行流程**：

```
run_pipeline()
    │
    ├─ 1. 参数解析：命令行参数（仅 input_path, output_dir, verbose）> config.ini > 默认值
    │     转写/总结参数全部通过 _load_tx_config(settings) 从 config.ini 加载
    │
    ├─ 2. 公共初始化
    │   └─ _init_common(settings, output_dir, verbose)
    │       ├─ setup_logger()                    # 初始化日志系统
    │       ├─ VideoProcessor()                  # 创建音视频处理器（自动查找内置 FFmpeg）
    │       └─ FileWriter(output_dir)            # 创建文件写入器
    │
    ├─ 3. 加载配置并创建 Transcriber
    │   ├─ cfg = _load_tx_config(settings)       # 从 config.ini 加载转写参数
    │   └─ Transcriber(cfg.model_path, cfg.device, cfg.compute_type, num_workers)
    │
    ├─ 4. 转写阶段
    │   ├─ tx_service = TranscriptionService(transcriber, ..., language=cfg.language, ...)
    │   ├─ tx_service.transcriber.load_model()
    │   └─ tx_results = tx_service.run([input_path], output_dir)
    │
    ├─ 5. 文本处理阶段
    │   ├─ segment_merger = SegmentMerger(max_gap=..., min_length=...)
    │   ├─ text_cleaner = TextCleaner(filler_words=...)
    │   ├─ segment_merger.merge_segments(result.segments)
    │   ├─ segment_merger.format_segments_as_text(merged, include_timestamps=False)
    │   └─ text_cleaner.clean(processed_text)
    │
    ├─ 6. 总结阶段
    │   ├─ provider_inst = create_provider(settings)
    │   ├─ provider_inst.check_connection()
    │   └─ sum_service.summarize(processed_text, video_name=...)
    │
    └─ 7. 清理
        └─ tx_service.transcriber.unload_model()  # 释放 GPU 显存
```

#### 14.1.2 阶段一：预处理

**入口**：`TranscriptionService._transcribe_single()` (`transcription_service.py:174`)

```
video_path: str (如 "video/sample.mp4")
    │
    ▼
_transcribe_single(video_path, output_dir)
    │
    ├─ video_name = Path(video_path).stem  → "sample"
    │
    ├─ 步骤 1: 验证音视频文件
    │   └─ self.video_processor.validate_input(video_path)  (video_processor.py:69)
    │       ├─ 检查文件存在性: path.exists()
    │       ├─ 检查文件格式: path.suffix in supported_input_formats
    │       └─ ffprobe 验证完整性:
    │           ffprobe -v error -show_entries format=format_name -of csv=p=0 <file>
    │           └─ returncode != 0 → raise VideoFileError("音视频文件损坏")
    │
    ├─ 步骤 2: 获取音视频信息
    │   └─ video_info = self.video_processor.get_video_info(video_path)  (video_processor.py:131)
    │       ├─ ffprobe -v quiet -print_format json -show_format -show_streams <file>
    │       ├─ 解析 JSON 输出，提取：
    │       │   ├─ duration (时长) ← format.duration
    │       │   ├─ width, height ← video_stream
    │       │   ├─ fps ← video_stream.r_frame_rate (解析 "30/1" → 30.0)
    │       │   ├─ codec ← video_stream.codec_name
    │       │   ├─ audio_codec ← audio_stream.codec_name
    │       │   └─ has_audio ← audio_stream 是否存在
    │       └─ return VideoInfo(duration, width, height, fps, codec, ...)
    │
    └─ 步骤 3: 提取音频
        └─ self.video_processor.extract_audio(video_path, output_wav, 16000, 1, video_info)
            (video_processor.py:227)
            │
            ├─ 输入是音频文件？ → 直接转换
            │   └─ is_audio_file(video_path) → True → 跳过音轨检查
            │
            ├─ 输入是视频文件？
            │   └─ video_info.has_audio == False → raise VideoFileError("没有音轨")
            │
            ├─ 主方案: pcm_s16le 无损提取
            │   └─ ffmpeg -i input.mp4 -vn -acodec pcm_s16le -ar 16000 -ac 1 -y output.wav
            │       ├─ 成功 → return output.wav
            │       └─ 失败 → 回退
            │
            └─ 回退方案: libmp3lame → 转码
                ├─ ffmpeg -i input.mp4 -vn -acodec libmp3lame -ar 16000 -ac 1 -q:a 2 -y temp.mp3
                ├─ ffmpeg -i temp.mp3 -acodec pcm_s16le -ar 16000 -ac 1 -y output.wav
                └─ finally: temp.mp3.unlink()
```

#### 14.1.3 阶段二：语音转写

**入口**：`TranscriptionService._transcribe_single()` 的转写分支 (`transcription_service.py:209`)

```
temp_audio.wav
    │
    ├─ video_info.duration > max_chunk_duration (默认 300 秒)？
    │   │
    │   ├─ 否 → 直接转写
    │   │   └─ self.transcriber.transcribe(
    │   │           audio_path, language, beam_size, temperature,
    │   │           vad_filter, progress_callback
    │   │       )
    │   │
    │   └─ 是 → 切片转写 (断点续传)
    │       └─ self._transcribe_chunked(temp_audio, video_name, video_path, output_dir)
    │
    ▼
Transcriber.transcribe() (transcriber.py:336)
    │
    ├─ 1. 模型检查: self._loaded ? → 否则 self.load_model()
    │
    ├─ 2. 调用 faster_whisper
    │   └─ segments, info = model.transcribe(
    │           audio_path,
    │           language=language,          # None if "auto"
    │           beam_size=beam_size,        # 默认 5
    │           best_of=5,
    │           temperature=temperature,    # 0.0 = 贪心解码
    │           vad_filter=vad_filter,      # True = 过滤静音段
    │           word_timestamps=False,
    │           condition_on_previous_text=True,
    │       )
    │
    ├─ 3. 检测语言
    │   └─ detected_language = info.language  (如 "zh", "en")
    │
    ├─ 4. 遍历 segments，构建 TranscriptSegment 列表
    │   └─ for segment in segments:
    │       TranscriptSegment(
    │           start=segment.start,
    │           end=segment.end,
    │           text=segment.text.strip(),
    │           confidence=_logprob_to_confidence(segment.avg_logprob),  # exp(avg_logprob)*100
    │           language=detected_language,
    │       )
    │
    └─ 5. progress_callback(segment.start, segment.end, count)  # 每段回调
```

**长音频切片转写** `_transcribe_chunked()` (`transcription_service.py:248`)：

```
audio_path: Path, video_name: str
    │
    ├─ 1. 计算断点文件路径
    │   ├─ hash_input = f"{video_path}:chunk={max_chunk_duration}"
    │   ├─ path_hash = sha256(hash_input)[:12]
    │   └─ checkpoint_file = .checkpoint/{video_name}_{path_hash}_chunks.json
    │
    ├─ 2. FFmpeg 切片
    │   └─ ffmpeg -i audio.wav -f segment -segment_time 300
    │          -acodec pcm_s16le -reset_timestamps 1 chunk_%03d.wav
    │       └─ chunk_dir/chunk_000.wav, chunk_001.wav, ...
    │
    ├─ 3. 加载断点数据
    │   └─ done_chunks = safe_read_json(checkpoint_file)
    │       └─ {"chunk_000": {"duration": 300, "segments": [...]}, ...}
    │
    ├─ 4. 逐片处理
    │   └─ for idx, chunk_path in enumerate(chunk_files):
    │       │
    │       ├─ 检查取消信号: cancel_check()
    │       ├─ 检查暂停: _wait_if_paused()
    │       │
    │       ├─ 断点命中？
    │       │   ├─ 有 error 标记 → 重试（删除旧记录）
    │       │   ├─ 有 segments → 恢复，调整时间戳偏移
    │       │   │   └─ seg.start += cumulative_offset, seg.end += cumulative_offset
    │       │   └─ 跳过当前切片
    │       │
    │       └─ 断点未命中 → 转写
    │           ├─ chunk_segments = self.transcriber.transcribe(chunk_path, ...)
    │           ├─ chunk_duration = _get_chunk_duration(chunk_path)
    │           │   ├─ 优先: ffprobe 获取精确时长
    │           │   ├─ 回退: max(seg.end for seg in segments)
    │           │   ├─ 回退: 文件大小 / byte_rate 估算
    │           │   └─ 最终兜底: max_chunk_duration 默认值
    │           ├─ 保存到断点: done_chunks[chunk_key] = {duration, segments}
    │           ├─ atomic_write_json(checkpoint_file, done_chunks)
    │           └─ cumulative_offset += chunk_duration
    │
    ├─ 5. 完成处理
    │   ├─ 全部成功 → checkpoint_file.unlink()  # 删除断点文件
    │   └─ 有失败 → 保留断点文件供下次重试
    │
    └─ 6. 清理: shutil.rmtree(chunk_dir)
```

#### 14.1.4 阶段三：文本处理

**入口**：CLI `run_pipeline()` 中的文本处理代码 (`cli.py:263`)，GUI `prepare_transcript_text()` (`gui_workers.py:183`)

```
segments: List[TranscriptSegment]  (转写引擎输出)
    │
    ├─ 步骤 1: 段落合并
    │   └─ segment_merger.merge_segments(segments)  (segment_merger.py:88)
    │       │
    │       ├─ SegmentMerger(max_gap=2.0, min_length=50)
    │       │
    │       ├─ 合并规则: 相邻段落 时间间隔 ≤ max_gap 且 语言相同 → 合并
    │       │   └─ gap = seg.start - current.end
    │       │       return gap <= 2.0 and seg.language == current.language
    │       │
    │       └─ 输出: List[MergedSegment(start, end, text, language)]
    │           └─ 合并后的文本: current.text += " " + segment.text
    │
    ├─ 步骤 2: 格式化为纯文本
    │   └─ segment_merger.format_segments_as_text(merged, include_timestamps=False)
    │       (segment_merger.py:162)
    │       └─ "\n\n".join(seg.text for seg in merged)
    │
    └─ 步骤 3: 文本清理
        └─ text_cleaner.clean(processed_text)  (text_cleaner.py:41)
            │
            ├─ remove_extra_whitespace()     # \r\n→\n, 连续空行→单空行
            ├─ remove_fillers()              # 嗯、啊、呃 等填充词
            ├─ fix_punctuation()             # 修复标点：连续标点、空格位置
            ├─ normalize_quotes()            # ""→"
            └─ remove_repeated_chars()       # 英文 3+→2, 中文 5+→3
```

**CLI 中的文本处理代码**：

```python
# cli.py:383-389
summary_map: dict[str, tuple[str, str]] = {}
for tx_result in tx_results:
    merged = segment_merger.merge_segments(tx_result.segments)
    processed_text = segment_merger.format_segments_as_text(
        merged, include_timestamps=False
    )
    processed_text = text_cleaner.clean(processed_text)
    summary_map[tx_result.video_name] = (processed_text, "总结不可用")
```

**GUI 中的文本处理代码**（`prepare_transcript_text`）：

```python
# gui_workers.py:183-198
def prepare_transcript_text(
    transcript_path_or_result, segment_merger=None, text_cleaner=None
) -> str:
    if isinstance(transcript_path_or_result, Path):
        text = transcript_path_or_result.read_text(encoding="utf-8-sig")
        return text
    else:
        result = transcript_path_or_result
        if segment_merger and text_cleaner:
            merged = segment_merger.merge_segments(result.segments)
            processed = segment_merger.format_segments_as_text(
                merged, include_timestamps=False
            )
            return text_cleaner.clean(processed)
        return result.text
```

#### 14.1.5 阶段四：文本总结

**入口**：CLI `run_pipeline()` 中的总结代码 (`cli.py:397`)，GUI `PipelineWorker._summarize_results_serial/multi()` (`gui_workers.py:736/792`)

```
processed_text: str  (文本处理后的干净文本)
    │
    ├─ 步骤 1: 检查总结服务可用性
    │   └─ provider_inst = create_provider(settings)  (providers.py:130)
    │       ├─ settings.get("summarization.provider") == "nvidia" → NvidiaProvider
    │       └─ 其他 → OllamaProvider
    │
    │   └─ provider_inst.check_connection()
    │       ├─ OllamaProvider: client.check_connection() + client.check_model(model_name)
    │       │   └─ GET /api/tags → 200 ? → 检查模型是否在列表中
    │       └─ NvidiaProvider: client.check_connection()
    │           └─ POST /v1/chat/completions {"messages":[{"role":"user","content":"hi"}], "max_tokens":1}
    │               └─ 200 ? → API Key 有效且服务可达
    │
    ├─ 步骤 2: 构建提示词
    │   └─ PromptManager().build_prompt(text, custom_prompt)  (prompt_manager.py:136)
    │       │
    │       ├─ custom_prompt 非空？ → base = custom_prompt
    │       └─ 否则 → base = "你是一个专业的文本总结助手，擅长提取关键信息并生成简洁准确的总结。"
    │
    │       └─ markdown_enabled ?
    │           ├─ True  → f"{base}\n\n{markdown_prompt}\n\n文本内容：\n{text}"
    │           └─ False → f"{base}\n\n文本内容：\n{text}"
    │
    ├─ 步骤 3: 调用总结 API
    │   └─ service = SummarizationService(provider, file_writer, custom_prompt, ...)
    │       └─ service.summarize(processed_text, video_name, stream, index, total)
    │           (summarization_service.py:56)
    │           │
    │           └─ self.provider.summarize(text, custom_prompt, stream, on_token)
    │               │
    │               ├─ OllamaProvider.summarize() (providers.py:56)
    │               │   └─ self._client.generate(model, prompt, temperature, max_tokens, stream, on_token)
    │               │       (ollama_client.py:423)
    │               │       ├─ POST /api/generate
    │               │       │   {"model": "qwen2.5:...", "prompt": "...", "stream": true/false}
    │               │       ├─ 流式: iter_lines() → JSON.loads(line) → on_token(token)
    │               │       └─ 非流式: response.json()["response"]
    │               │
    │               └─ NvidiaProvider.summarize() (providers.py:106)
    │                   └─ self._client.generate(model, prompt, temperature, max_tokens, ...)
    │                       (nvidia_client.py:89)
    │                       ├─ POST /v1/chat/completions
    │                       │   {"model": "...", "messages": [{"role":"user","content":"..."}], "stream": true/false}
    │                       ├─ 流式: SSE 解析 → data: {"choices":[{"delta":{"content":"token"}}]}
    │                       └─ 非流式: response.json()["choices"][0]["message"]["content"]
    │                       └─ 429 限流: Retry-After 头 → 指数退避重试（最多 5 次）
    │
    └─ 步骤 4: 保存总结结果
        └─ file_writer.write_summary(summary, video_name, fmt=summary_format)
            (file_writer.py:152)
            │
            ├─ fmt = settings.get("output.summary_format", "txt")  → "txt" 或 "md"
            ├─ output_path = output_dir/{video_name}_summary.{fmt}
            └─ _atomic_write(output_path, content)  # 原子写入
```

**CLI 总结代码**：

```python
# cli.py:281-307
sum_service = SummarizationService(
    settings=settings,
    file_writer=file_writer,
    provider=provider_inst,
)
for idx, tx_result in enumerate(tx_results):
    processed_text = summary_map[tx_result.video_name][0]
    try:
        summary = sum_service.summarize(
            processed_text,
            video_name=tx_result.video_name,
            index=idx + 1,
            total=len(tx_results),
        )
        summary_map[tx_result.video_name] = (
            processed_text,
            summary or "总结不可用",
        )
    except Exception as e:
        console.print(f"[yellow]警告: {tx_result.video_name} 总结失败: {e}[/yellow]")
```

**GUI 总结代码**（`PipelineWorker.run` 使用 `SummarizationService.summarize_batch`）：

```python
# gui_workers.py:688-730
mode = _get_online_cfg(self.settings, "mode", "single")
max_workers = (
    _get_online_cfg(self.settings, "thread_count", 5)
    if provider_name == "nvidia" and mode == "multi"
    else 1
)
stream = self.stream and max_workers <= 1
rate_limiter = RateLimiter(1.5) if max_workers > 1 else None

provider_inst = create_provider(self.settings)
sum_service = SummarizationService(
    settings=self.settings,
    file_writer=file_writer,
    provider=provider_inst,
    custom_prompt=self.custom_prompt,
    on_stream_token=lambda token: self.stream_token.emit(token) if stream else None,
    cancel_check=lambda: self._cancelled,
    pause_event=self._sum_pause_ctrl.get_event(),
    rate_limiter=rate_limiter,
    on_item_started=lambda name: self.summarize_started.emit(name),
    on_item_done=lambda name, summary: self.summarize_done.emit(name, summary),
    on_item_error=lambda name, err: self.summarize_error.emit(name, err),
)
sum_service.summarize_batch(items, stream=stream, max_workers=max_workers)
```

串行模式（`max_workers=1`）下逐文件调用 `provider.summarize()`，每个文件完成后立即保存；并发模式（`max_workers > 1`）使用 `ThreadPoolExecutor` 并行处理，每线程创建独立 Provider 实例以避免共享连接。并发模式下流式输出自动关闭。

#### 14.1.6 阶段五：输出文件

```
转写结果 (TranscriptionService._transcribe_single 中保存)
    │
    └─ for fmt in self.output_formats:   # ["txt", "srt", "vtt", "json"]
        └─ file_writer.write_transcript(segments, video_name, fmt=fmt)
            (file_writer.py:56)
            │
            ├─ fmt == "txt"
            │   └─ OutputFormatter.format_transcript(segments, include_timestamps=True)
            │       └─ "[00:00:00 - 00:00:05] 你好，欢迎...\n[00:00:05 - 00:00:10] 今天..."
            │
            ├─ fmt == "srt"
            │   └─ OutputFormatter.format_srt(segments)
            │       └─ "1\n00:00:00,000 --> 00:00:05,000\n你好，欢迎...\n\n2\n..."
            │
            ├─ fmt == "vtt"
            │   └─ OutputFormatter.format_vtt(segments)
            │       └─ "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\n你好，欢迎...\n"
            │
            └─ fmt == "json"
                └─ json.dumps([asdict(seg) for seg in segments])
                    └─ [{"start":0.0,"end":5.2,"text":"...","confidence":95.5,"language":"zh"}, ...]

总结结果 (SummarizationService.summarize 中保存)
    │
    └─ file_writer.write_summary(summary, video_name, fmt=summary_format)
        (file_writer.py:152)
        │
        ├─ fmt == "txt" → output_dir/{video_name}_summary.txt
        └─ fmt == "md"  → output_dir/{video_name}_summary.md

输出校验 (每次写入后自动执行)
    │
    ├─ validate_output_file(path)         # 文件存在、非空、编码正确
    └─ validate_output_content(path, fmt) # 格式合规性
        ├─ SRT: 序号连续、时间戳格式、start < end
        ├─ VTT: WEBVTT 头、时间戳格式
        ├─ JSON: 可解析、必要字段
        └─ TXT: 非空
```

#### 14.1.7 完整数据流时序图

```
CLI: run_pipeline(input_path)                    GUI: PipelineWorker.run()
     │                                                │
     │  ┌─────────────────────────────────────────┐   │  ┌─────────────────────────────────────────┐
     │  │ 阶段一: 预处理                            │   │  │ 阶段一: 预处理                            │
     │  │                                         │   │  │                                         │
     │  │ VideoProcessor.validate_input()         │   │  │ VideoProcessor.validate_input()         │
     │  │ VideoProcessor.get_video_info()         │   │  │ VideoProcessor.get_video_info()         │
     │  │ VideoProcessor.extract_audio()          │   │  │ VideoProcessor.extract_audio()          │
     │  │   → temp_{name}.wav                     │   │  │   → temp_{name}.wav                     │
     │  └─────────────────────────────────────────┘   │  └─────────────────────────────────────────┘
     │                                                │
     │  ┌─────────────────────────────────────────┐   │  ┌─────────────────────────────────────────┐
     │  │ 阶段二: 转写                              │   │  │ 阶段二: 转写                              │
     │  │                                         │   │  │                                         │
     │  │ duration > 300s?                        │   │  │ duration > 300s?                        │
     │  │  ├─ 否 → Transcriber.transcribe()       │   │  │  ├─ 否 → Transcriber.transcribe()       │
     │  │  └─ 是 → _transcribe_chunked()          │   │  │  └─ 是 → _transcribe_chunked()          │
     │  │       ├─ FFmpeg segment 切片             │   │  │       ├─ FFmpeg segment 切片             │
     │  │       ├─ 加载断点 .checkpoint/*.json     │   │  │       ├─ 加载断点 .checkpoint/*.json     │
     │  │       ├─ 逐片转写 + 断点保存              │   │  │       ├─ 逐片转写 + 断点保存              │
     │  │       └─ 合并所有切片 segments            │   │  │       └─ 合并所有切片 segments            │
     │  │                                         │   │  │                                         │
     │  │ → List[TranscriptSegment]               │   │  │ → List[TranscriptSegment]               │
     │  │                                         │   │  │                                         │
     │  │ file_writer.write_transcript()          │   │  │ file_writer.write_transcript()          │
     │  │   → {name}.txt / .srt / .vtt / .json    │   │  │   → {name}.txt / .srt / .vtt / .json    │
     │  │                                         │   │  │                                         │
     │  │ console.print("转写完成")                 │   │  │ self.transcribe_done.emit(...)          │
     │  └─────────────────────────────────────────┘   │  └─────────────────────────────────────────┘
     │                                                │
     │  ┌─────────────────────────────────────────┐   │  ┌─────────────────────────────────────────┐
     │  │ 阶段三: 文本处理                          │   │  │ 阶段三: 文本处理                          │
     │  │                                         │   │  │                                         │
     │  │ for tx_result in tx_results:            │   │  │ _prepare_text(result, merger, cleaner)  │
     │  │   segment_merger.merge_segments()       │   │  │   segment_merger.merge_segments()       │
     │  │   segment_merger.format_segments_as_text│   │  │   segment_merger.format_segments_as_text│
     │  │   text_cleaner.clean()                  │   │  │   text_cleaner.clean()                  │
     │  │                                         │   │  │                                         │
     │  │ → processed_text: str                   │   │  │ → processed_text: str                   │
     │  └─────────────────────────────────────────┘   │  └─────────────────────────────────────────┘
     │                                                │
     │  ┌─────────────────────────────────────────┐   │  ┌─────────────────────────────────────────┐
     │  │ 阶段四: 总结                              │   │  │ 阶段四: 总结                              │
     │  │                                         │   │  │                                         │
     │  │ create_provider(settings)               │   │  │ 串行: create_provider → service.summarize│
     │  │ provider.check_connection()             │   │  │   stream=True → on_stream_token → emit  │
     │  │                                         │   │  │ 多线程: ThreadPoolExecutor + RateLimiter │
     │  │ PromptManager.build_prompt(text)        │   │  │   每线程独立 Provider，stream=False       │
     │  │   base + markdown_prompt + text          │   │  │                                         │
     │  │                                         │   │  │ PromptManager.build_prompt(text)        │
     │  │ service.summarize(processed_text)       │   │  │ service.summarize(processed_text)       │
     │  │   → provider.summarize()                │   │  │   → provider.summarize()                │
     │  │     ├─ Ollama: POST /api/generate       │   │  │     ├─ Ollama: POST /api/generate       │
     │  │     └─ NVIDIA: POST /v1/chat/completions│   │  │     └─ NVIDIA: POST /v1/chat/completions│
     │  │                                         │   │  │                                         │
     │  │ → summary: str                          │   │  │ → summary: str                          │
     │  │                                         │   │  │                                         │
     │  │ file_writer.write_summary()             │   │  │ file_writer.write_summary()             │
     │  │   → {name}_summary.txt / .md            │   │  │   → {name}_summary.txt / .md            │
     │  │                                         │   │  │                                         │
     │  │ console.print("处理成功")                 │   │  │ self.summarize_done.emit(name, summary) │
     │  └─────────────────────────────────────────┘   │  └─────────────────────────────────────────┘
     │                                                │
     │  ┌─────────────────────────────────────────┐   │  ┌─────────────────────────────────────────┐
     │  │ 阶段五: 清理                              │   │  │ 阶段五: 清理                              │
     │  │                                         │   │  │                                         │
     │  │ transcriber.unload_model()              │   │  │ transcriber 不卸载（缓存复用）             │
     │  │   → del model, empty_cache()            │   │  │ temp_audio.unlink(missing_ok=True)      │
     │  │                                         │   │  │ finished.emit()                         │
     │  └─────────────────────────────────────────┘   │  └─────────────────────────────────────────┘
```

#### 14.1.8 关键方法索引

| 阶段 | 方法 | 文件:行号 | 说明 |
|------|------|-----------|------|
| **CLI 入口** | `run_pipeline()` | `cli.py:269` | CLI 命令入口，参数解析与服务编排 |
| **公共初始化** | `_init_common()` | `cli.py:71` | 日志、VideoProcessor、FileWriter |
| **配置加载** | `_load_tx_config()` | `transcription_config.py:35` | 从 Settings 构建 TranscriptionConfig |
| **音视频验证** | `VideoProcessor.validate_input()` | `video_processor.py:69` | ffprobe 验证文件完整性 |
| **音视频信息** | `VideoProcessor.get_video_info()` | `video_processor.py:131` | 提取时长/编码/采样率 |
| **音频提取** | `VideoProcessor.extract_audio()` | `video_processor.py:227` | FFmpeg 提取 WAV（含回退） |
| **模型缓存** | `get_cached_transcriber()` | `transcriber.py:25` | LRU 缓存，避免重复加载 |
| **模型加载** | `Transcriber.load_model()` | `transcriber.py:171` | 加载 + OOM 降级 |
| **转写服务入口** | `TranscriptionService.run()` | `transcription_service.py:120` | 批量转写主循环 |
| **单文件转写** | `_transcribe_single()` | `transcription_service.py:174` | 验证→提取→转写→保存 |
| **长音频转写** | `_transcribe_chunked()` | `transcription_service.py:248` | 切片 + 断点续传 |
| **切片时长获取** | `_get_chunk_duration()` | `transcription_service.py:428` | ffprobe → 回退估算 |
| **转写引擎** | `Transcriber.transcribe()` | `transcriber.py:336` | faster_whisper 调用 |
| **段落合并** | `SegmentMerger.merge_segments()` | `segment_merger.py:88` | 时间间隔 + 语言相同 → 合并 |
| **文本清理** | `TextCleaner.clean()` | `text_cleaner.py:41` | 去填充词、修复标点、去重复 |
| **Provider 工厂** | `create_provider()` | `providers.py:130` | 根据配置创建 Ollama/NVIDIA |
| **提示词构建** | `PromptManager.build_prompt()` | `prompt_manager.py:136` | system prompt + markdown + text |
| **总结服务** | `SummarizationService.summarize()` | `summarization_service.py:56` | 总结 + 保存 |
| **转写文件写入** | `FileWriter.write_transcript()` | `file_writer.py:56` | TXT/SRT/VTT/JSON 格式化 + 原子写入 |
| **摘要文件写入** | `FileWriter.write_summary()` | `file_writer.py:152` | TXT/MD 格式化 + 原子写入 |
| **输出校验** | `validate_output_content()` | `output_validator.py:276` | SRT/VTT/JSON/TXT 格式校验 |
| **历史记录** | `_save_history_record()` | `transcription_service.py:533` | 保存转写耗时用于时间估算 |

### 14.2 GUI 交互数据流

GUI 基于 PySide6 的信号-槽机制实现异步通信。所有耗时操作在 QThread 后台线程执行，通过 Qt Signal 通知主线程更新 UI。

#### 14.2.1 Worker 线程架构

```
┌───────────────────────────────────────────────────────────────────┐
│  MainWindow (主线程 / GUI 线程)                                     │
│  ├─ _worker_thread: QThread        # 当前活跃的工作线程              │
│  ├─ _worker: QObject               # 当前活跃的 worker 对象         │
│  └─ 所有 UI 操作必须在主线程执行                                      │
├───────────────────────────────────────────────────────────────────┤
│  Worker 对象（通过 moveToThread 在后台线程执行）                      │
│  ├─ TranscribeWorker(QObject)      # 仅转写                        │
│  ├─ SummarizeWorker(QObject)       # 仅总结                        │
│  ├─ PipelineWorker(QObject)        # 转写总结管道                  │
│  ├─ ScanFilesWorker(QObject)       # 文件夹扫描                    │
│  ├─ OllamaCheckWorker(QObject)     # Ollama 连接检查               │
│  └─ NvidiaCheckWorker(QObject)     # NVIDIA API 连接检查           │
└───────────────────────────────────────────────────────────────────┘
```

**通用线程启动方法** `_start_worker()` (`gui.py:818`)：

```python
def _start_worker(self, thread: QThread, worker) -> None:
    """启动 worker 线程并连接通用信号"""
    # 1. 如果有正在运行的旧 worker，先取消并等待
    if self._worker_thread is not None and self._worker_thread.isRunning():
        self._worker.cancel()           # 设置 _cancelled = True
        self._worker_thread.quit()      # 退出事件循环
        self._worker_thread.wait(3000)  # 等待最多 3 秒
        if self._worker_thread.isRunning():
            self._worker_thread.terminate()  # 强制终止
            self._worker_thread.wait(1000)

    # 2. 将 worker 移到新线程
    worker.moveToThread(thread)

    # 3. 连接信号
    thread.started.connect(worker.run)           # 线程启动 → 执行 run()
    worker.finished.connect(thread.quit)         # worker 完成 → 退出线程
    thread.finished.connect(self._on_thread_finished)  # 线程结束 → 恢复 UI

    # 4. 启动线程
    thread.start()
```

**通用线程结束处理** `_on_thread_finished()` (`gui.py:1150`)：

```python
def _on_thread_finished(self) -> None:
    self._set_busy_state(False)         # 启用所有按钮
    self.progress_bar.setValue(maximum)  # 进度条拉满
    # 根据模式显示统计信息
    # "转写完成 — 成功: N, 失败: M"
    # "总结完成 — 成功: N, 失败: M"
    self._save_fail_records()           # 保存失败记录到 logs/fail_log.log
```

#### 14.2.2 选择输入文件/文件夹

```
用户点击"选择文件"
    │
    ▼
_select_input_files() (gui.py:617)
    │
    ├─ QFileDialog.getOpenFileNames()
    │   └─ 过滤器: _get_input_filter_str() → "音视频文件 (*.mp4 *.mp3 ...)"
    │
    ├─ self._video_files = list(paths)  # 保存到实例变量
    │
    └─ self.output_combo.setCurrentText("output/last_dir")  # 自动设置输出目录


用户点击"选择文件夹"
    │
    ▼
_select_input_folder() (gui.py:633)
    │
    ├─ QFileDialog.getExistingDirectory()
    │
    ├─ _start_scan(folder) (gui.py:671)
    │   │
    │   ├─ 创建 ScanFilesWorker(folder, input_exts)
    │   │   └─ run(): Path(folder).rglob(f"*{ext}")  # 递归扫描所有支持格式
    │   │
    │   ├─ worker.result.connect(self._on_scan_result)  # 扫描完成信号
    │   │
    │   └─ 启动 QThread 后台扫描
    │
    ▼
_on_scan_result(video_files) (gui.py:695)
    │
    ├─ video_files 为空？ → 提示"未找到支持的音视频文件"
    │
    ├─ 弹出 VideoSelectionDialog(video_files)
    │   └─ 树形视图展示文件，支持按类型/后缀/大小/关键字筛选
    │   └─ get_selected_files() → 返回用户勾选的文件列表
    │
    └─ self._video_files = selected_files  # 保存选中文件
```

#### 14.2.3 仅转写（`_on_transcribe`）

```
用户点击"仅转写"按钮
    │
    ▼
_on_transcribe() (gui.py:889)
    │
    ├─ 1. 校验: self._video_files 为空？ → 弹出警告
    │
    ├─ 2. 初始化 UI
    │   ├─ file_list.clear()           # 清空文件列表
    │   ├─ transcript_view.clear()     # 清空转写区
    │   ├─ summary_view.clear()        # 清空摘要区
    │   ├─ log_panel.clear()           # 清空日志面板
    │   ├─ progress_bar.setMaximum(total)  # 设置进度条上限
    │   └─ _set_busy_state(True)       # 禁用所有操作按钮
    │
    ├─ 3. 创建后台线程
    │   │
    │   ├─ thread = QThread()
    │   │
    │   ├─ worker = TranscribeWorker(video_files, output_dir, settings)
    │   │   │
    │   │   └─ run() (gui_workers.py:110):
    │   │       ├─ cfg = _load_tx_config(settings)  # 加载转写配置
    │   │       ├─ transcriber = get_cached_transcriber(...)  # 获取缓存模型
    │   │       ├─ transcriber.load_model()  # 加载模型（含 OOM 降级）
    │   │       │
    │   │       ├─ 创建 TranscriptionService(
    │   │       │       transcriber, video_processor, file_writer,
    │   │       │       language, beam_size, temperature, vad_filter,
    │   │       │       max_chunk_duration, output_formats,
    │   │       │       on_video_done=回调, on_video_error=回调,
    │   │       │       cancel_check=lambda: self._cancelled
    │   │       │   )
    │   │       │
    │   │       └─ service.run(video_files, output_dir)  # 执行批量转写
    │   │
    │   └─ 4. 连接信号
    │       ├─ worker.video_done → self._on_single_video_transcribed
    │       ├─ worker.video_error → self._on_transcribe_error
    │       ├─ worker.progress → self._on_progress
    │       └─ worker.error → self._on_worker_error
    │
    └─ 5. _start_worker(thread, worker)  # 启动线程


_on_single_video_transcribed(video_name, segments_count, output_paths) (gui.py:923)
    │
    ├─ self._tx_success += 1
    ├─ file_list.addItem(video_name)       # 立即添加到文件列表
    ├─ file_list.setCurrentItem(...)       # 自动选中
    ├─ _load_transcript_content(video_name) # 读取转写文件到 transcript_view
    └─ status_bar.showMessage("转写完成: {name} ({count} 段)")


_on_transcribe_error(video_name, error_msg) (gui.py:957)
    │
    ├─ self._tx_fail += 1
    ├─ self._fail_records.append((video_name, "转写", error_msg))
    └─ status_bar.showMessage("转写失败: {name} — {msg}")
```

#### 14.2.4 仅总结（`_on_summarize`）

```
用户点击"仅总结"按钮
    │
    ▼
_on_summarize() (gui.py:965)
    │
    ├─ 1. 判断模式
    │   ├─ 有 video_files → 文件列表总结模式
    │   ├─ 有 completed_names → 历史文件总结模式
    │   ├─ 无文件但 transcript_view 有文本 → _summarize_standalone()
    │   └─ 都没有 → 弹出警告
    │
    ├─ 2. 如果用户编辑了 transcript_view，先保存到 .txt 文件
    │   └─ transcript_path.write_text(standalone_text)
    │
    ├─ 3. 创建后台线程
    │   │
    │   ├─ thread = QThread()
    │   │
    │   ├─ worker = SummarizeWorker(
    │   │       video_names, output_dir, settings,
    │   │       custom_prompt, stream=True/False
    │   │   )
    │   │   │
    │   │   └─ run() (gui_workers.py:219):
    │   │       ├─ 判断 provider 和 nvidia_mode
    │   │       │
    │   │       ├─ 单线程模式 (_run_single_thread):
    │   │       │   ├─ OllamaClient.full_check() 或 provider.check_connection()
    │   │       │   ├─ provider = create_provider(settings)
    │   │       │   ├─ service = SummarizationService(provider, custom_prompt, ...)
    │   │       │   └─ _execute_summarization():
    │   │       │       └─ for video in video_files:
    │   │       │           ├─ file_writer.find_transcript_file(video_name)
    │   │       │           ├─ text = transcript_path.read_text()
    │   │       │           ├─ self.summarize_started.emit(video_name)
    │   │       │           ├─ summary = service.summarize(text, stream=True)
    │   │       │           │   └─ on_stream_token → self.stream_token.emit(token)
    │   │       │           └─ self.video_done.emit(video_name, summary)
    │   │       │
    │   │       └─ 多线程模式 (_run_multi_thread):  # NVIDIA multi
    │   │           ├─ provider.check_connection()
    │   │           ├─ RateLimiter(min_interval=1.5)  # 速率限制
    │   │           ├─ ThreadPoolExecutor(max_workers=thread_count)
    │   │           └─ for each task:
    │   │               ├─ rate_limiter.acquire()  # 等待间隔
    │   │               ├─ provider = create_provider(settings)  # 每线程独立实例
    │   │               ├─ service.summarize(text, stream=False)
    │   │               └─ self.video_done.emit(video_name, summary)
    │   │
    │   └─ 4. 连接信号
    │       ├─ worker.stream_token → self._on_stream_token
    │       ├─ worker.summarize_started → self._on_summarize_started
    │       ├─ worker.video_done → self._on_single_video_summarized
    │       ├─ worker.video_error → self._on_summarize_error
    │       ├─ worker.progress → self._on_progress
    │       └─ worker.error → self._on_worker_error
    │
    └─ 5. _start_worker(thread, worker)


_on_stream_token(token) (gui.py:1064)
    │
    ├─ summary_view.moveCursor(QTextCursor.End)
    ├─ summary_view.insertPlainText(token)    # 逐 token 追加到摘要区
    └─ summary_view.ensureCursorVisible()     # 自动滚动到底部


_on_single_video_summarized(video_name, summary) (gui.py:1075)
    │
    ├─ self._sum_success += 1
    ├─ file_list.addItem(video_name)      # 添加到文件列表（如果不存在）
    ├─ summary_view.setPlainText(summary) # 更新摘要区完整文本
    └─ status_bar.showMessage("总结完成: {name}")
```

#### 14.2.5 转写总结管道（`_on_pipeline`）

```
用户点击"转写总结"按钮
    │
    ▼
_on_pipeline() (gui.py:1097)
    │
    ├─ 1. 校验 + 初始化 UI
    │   ├─ progress_bar.setMaximum(total * 2)  # 进度条: 转写N + 总结N
    │   └─ _set_busy_state(True)
    │
    ├─ 2. 创建后台线程
    │   │
    │   ├─ thread = QThread()
    │   │
    │   ├─ worker = PipelineWorker(
    │   │       video_files, output_dir, settings,
    │   │       custom_prompt, stream=True/False
    │   │   )
    │   │   │
    │   │   └─ run() (gui_workers.py:592):
    │   │       │
    │   │       ├─ 阶段 1: 转写
    │   │       │   ├─ get_cached_transcriber() + load_model()
    │   │       │   ├─ 检查总结服务可用性 (Ollama/NVIDIA)
    │   │       │   ├─ 创建 TranscriptionService(...)
    │   │       │   ├─ results = tx_service.run(video_files, output_dir)
    │   │       │   │   └─ 每完成一个文件:
    │   │       │   │       ├─ on_tx_done → self.transcribe_done.emit(name, count, paths)
    │   │       │   │       └─ self.progress.emit(done_count, total_steps)
    │   │       │   │
    │   │       ├─ 阶段 2: 总结（转写完成后自动开始）
    │   │       │   │
    │   │       │   ├─ NVIDIA multi 模式 → _summarize_results_multi()
    │   │       │   │   ├─ _prepare_text(): segments → merge → clean
    │   │       │   │   │   ├─ segment_merger.merge_segments(result.segments)
    │   │       │   │   │   ├─ segment_merger.format_segments_as_text(merged)
    │   │       │   │   │   └─ text_cleaner.clean(processed_text)
    │   │       │   │   ├─ ThreadPoolExecutor(max_workers=thread_count)
    │   │       │   │   └─ 每完成一个:
    │   │       │   │       ├─ self.summarize_done.emit(name, summary)
    │   │       │   │       └─ self.progress.emit(total + sum_done, total_steps)
    │   │       │   │
    │   │       │   └─ 其他模式 → _summarize_results_serial()
    │   │       │       └─ for result in results:
    │   │       │           ├─ _prepare_text(result, merger, cleaner)
    │   │       │           ├─ self.summarize_started.emit(name)
    │   │       │           ├─ service.summarize(text, stream=True)
    │   │       │           │   └─ on_stream_token → self.stream_token.emit(token)
    │   │       │           ├─ self.summarize_done.emit(name, summary)
    │   │       │           └─ self.progress.emit(total + idx, total_steps)
    │   │       │
    │   │       └─ finally: transcriber 不卸载（缓存复用）
    │   │
    │   └─ 3. 连接信号
    │       ├─ worker.transcribe_done → self._on_single_video_transcribed
    │       ├─ worker.transcribe_error → self._on_transcribe_error
    │       ├─ worker.summarize_started → self._on_summarize_started
    │       ├─ worker.stream_token → self._on_stream_token
    │       ├─ worker.summarize_done → self._on_single_video_summarized
    │       ├─ worker.summarize_error → self._on_summarize_error
    │       ├─ worker.progress → self._on_progress
    │       └─ worker.error → self._on_worker_error
    │
    └─ 4. _start_worker(thread, worker)
```

#### 14.2.6 暂停/继续与取消

```
暂停/继续 (gui.py:872)
    │
    ▼
_on_pause_resume()
    │
    ├─ self._worker.is_paused ?
    │   ├─ True  → self._worker.resume()
    │   │         ├─ TranscribeWorker.resume()
    │   │         │   └─ self._service.resume()  # _pause_event.set()
    │   │         └─ PipelineWorker.resume()
    │   │             └─ self._tx_service.resume()
    │   │
    │   └─ False → self._worker.pause()
    │             ├─ TranscribeWorker.pause()
    │             │   └─ self._service.pause()  # _pause_event.clear()
    │             └─ PipelineWorker.pause()
    │                 └─ self._tx_service.pause()
    │
    └─ pause_btn.setText("暂停" / "继续")


取消（关闭窗口时）(gui.py:1345)
    │
    ▼
closeEvent(event)
    │
    ├─ self._worker.cancel()       # 设置 _cancelled = True
    ├─ self._worker.unpause()      # 解除暂停（防止线程阻塞在 _wait_if_paused）
    ├─ self._worker_thread.quit()
    ├─ self._worker_thread.wait(3000)
    │   └─ 超时 → terminate() + wait(1000)
    │
    ├─ self.log_panel.cleanup()    # 清理日志面板资源
    ├─ OllamaClient.stop_service() # 停止 Ollama 服务进程
    ├─ 卸载所有缓存的转写模型       # 释放 GPU 显存
    │   └─ for transcriber in _transcriber_cache: transcriber.unload_model()
    │
    └─ self._result_viewer.close()  # 关闭结果查看器
```

#### 14.2.7 文件列表切换与内容加载

```
用户点击文件列表中的某一项
    │
    ▼
_on_file_selected(current, _previous) (gui.py:1309)
    │
    ├─ video_name = current.data(Qt.ItemDataRole.UserRole)
    ├─ self._current_video_name = video_name
    │
    └─ QTimer.singleShot(0, lambda: _load_file_content(name, output_dir))
        │                                                              # 延迟到事件循环空闲时执行，避免阻塞 UI
        ▼
    _load_file_content(video_name, output_dir) (gui.py:1321)
        │
        ├─ FileWriter(output_dir).find_transcript_file(video_name)
        │   └─ 依次查找 .txt, .srt, .vtt, .json
        ├─ transcript_path.read_text(encoding="utf-8-sig")
        ├─ self.transcript_view.setPlainText(text)
        │
        ├─ _find_summary_path(output_dir, video_name)
        │   └─ 依次查找 _summary.txt, _summary.md
        ├─ summary_path.read_text(encoding="utf-8")
        ├─ self.summary_view.setPlainText(summary)
        │
        └─ self._search_controller.refresh_if_active()  # 刷新搜索高亮
```

#### 14.2.8 Worker 信号汇总表

| Worker 类 | 信号 | 参数 | 连接的槽函数 | 说明 |
|-----------|------|------|-------------|------|
| **TranscribeWorker** | `video_done` | `(str, int, list)` | `_on_single_video_transcribed` | 单文件转写完成 |
| | `video_error` | `(str, str)` | `_on_transcribe_error` | 单文件转写失败 |
| | `progress` | `(int, int)` | `_on_progress` | 进度更新 |
| | `error` | `(str,)` | `_on_worker_error` | 线程级异常 |
| | `finished` | `()` | `thread.quit` | 线程结束 |
| | `confirm_download` | `()` | `_on_confirm_download` | 下载确认请求 |
| **SummarizeWorker** | `summarize_started` | `(str,)` | `_on_summarize_started` | 开始总结新文件 |
| | `stream_token` | `(str,)` | `_on_stream_token` | 流式 token（逐字显示） |
| | `video_done` | `(str, str)` | `_on_single_video_summarized` | 单文件总结完成 |
| | `video_error` | `(str, str)` | `_on_summarize_error` | 单文件总结失败 |
| | `progress` | `(int, int)` | `_on_progress` | 进度更新 |
| | `error` | `(str,)` | `_on_worker_error` | 线程级异常 |
| | `finished` | `()` | `thread.quit` | 线程结束 |
| **PipelineWorker** | `transcribe_done` | `(str, int, list)` | `_on_single_video_transcribed` | 转写完成 |
| | `transcribe_error` | `(str, str)` | `_on_transcribe_error` | 转写失败 |
| | `summarize_started` | `(str,)` | `_on_summarize_started` | 开始总结 |
| | `stream_token` | `(str,)` | `_on_stream_token` | 流式 token |
| | `summarize_done` | `(str, str)` | `_on_single_video_summarized` | 总结完成 |
| | `summarize_error` | `(str, str)` | `_on_summarize_error` | 总结失败 |
| | `progress` | `(int, int)` | `_on_progress` | 进度更新 |
| | `error` | `(str,)` | `_on_worker_error` | 线程级异常 |
| | `finished` | `()` | `thread.quit` | 线程结束 |
| | `confirm_download` | `()` | `_on_confirm_download` | 下载确认请求 |
| | `phase_changed` | `(str,)` | `_on_phase_changed` | 阶段切换通知 |
| **ScanFilesWorker** | `result` | `(list,)` | `_on_scan_result` | 扫描结果 |
| | `finished` | `()` | `thread.quit` | 线程结束 |
| **CheckWorker** | `result` | `(bool, float, str)` | 配置面板槽函数 | 连接状态 + 延迟 + 详情 |

#### 14.2.9 RateLimiter 速率限制器

多线程并发总结时，为避免触发 API 限流，使用 `RateLimiter` 控制请求间隔：

```python
class RateLimiter:
    """确保两次操作之间的间隔不低于指定秒数"""

    def __init__(self, min_interval: float = 1.5):
        self._lock = threading.Lock()
        self._min_interval = min_interval
        self._last_time = 0.0

    def acquire(self) -> None:
        """获取操作许可，若距上次操作不足最小间隔则阻塞等待"""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_time = time.monotonic()
```

在 `_execute_summarization_multi()` 中，每个线程在发起 API 请求前先调用 `rate_limiter.acquire()`，确保全局请求间隔不低于 1.5 秒。

### 14.3 实时语音转写数据流（VoiceToText）

新增的实时语音转写功能（`src/services/voice_recorder.py`、`src/services/voice_transcription.py`、`src/ui/voice_to_text_widget.py`）实现了麦克风实时采集 + VAD 端点检测 + 异步转写 + 上下文记忆的完整流程。

#### 14.3.1 组件架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        VoiceToTextWidget (UI)                        │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────────┐ │
│  │  录音按钮     │  │  转写结果区   │  │  对话历史列表 + 书签        │ │
│  └──────────────┘  └──────────────┘  └────────────────────────────┘ │
│  信号: start/stop/pause  ←→  VoiceRecorder / VoiceTranscriptionService │
└─────────────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐
│ VoiceRecorder   │  │ VoiceTranscription │  │ VoiceConversationStore │
│ (麦克风 + VAD)   │  │ Service (转写引擎)  │  │ (持久化对话)           │
│                 │  │                 │  │                     │
│ • sounddevice   │  │ • get_cached_   │  │ • voice_conversations.json │
│ • 实时能量检测   │  │   transcriber   │  │ • 按 video_name/时间检索 │
│ • 自动校准噪底   │  │ • VAD 参数      │  │ • 上下文记忆           │
│ • 端点检测       │  │ • 上下文提示词   │  └─────────────────────┘
│ • chunk 提取     │  │ • OpenCC 简体化  │
└─────────────────┘  └─────────────────┘
```

#### 14.3.2 录音 + VAD 数据流

```
用户点击“开始录音”
    │
    ▼
VoiceToTextWidget._on_record_start()
    │
    ├─ 创建 VoiceRecorder(settings)
    │       └─ 从 [voice_to_text] 读取:
    │           audio_sample_rate, audio_channels,
    │           vad_endpoint_detection, vad_energy_threshold,
    │           vad_silence_frames, vad_min_speech_frames
    │
    ├─ recorder.started.connect(...)
    ├─ recorder.speech_ended.connect(self._on_speech_chunk)
    ├─ recorder.volume_changed.connect(self._update_volume_ui)
    ├─ recorder.error_occurred.connect(...)
    │
    └─ recorder.start()  → 启动 threading.Thread(_record_loop)
            │
            ▼
    sounddevice.InputStream(callback=_audio_callback)
            │
            ├─ 每 blocksize(512) 帧触发一次回调
            │
            ├─ _audio_callback(indata, ...):
            │       │
            │       ├─ VAD 启用？
            │       │   ├─ 未校准 → _calibrate_noise_floor()  (收集 30 帧求均值 * 1.2)
            │       │   └─ 已校准 → _detect_speech_end()
            │       │           ├─ rms > noise_floor*2 → 语音开始
            │       │           └─ 连续静音帧 ≥ vad_silence_frames → 语音结束
            │       │                   └─ _extract_speech_chunk() → 保存 voice_vad_*.wav
            │       │                           └─ speech_ended.emit(wav_path)
            │       │
            │       └─ 无论是否 VAD，都把 indata 存入 _frames[] + 计算音量
            │
            ▼
    用户点击“停止”或 VAD 自动结束
            │
            ▼
    recorder.stop() → _running=False → 回调中抛出 CallbackAbort
            │
            └─ (若非 VAD 模式) save_to_wav() → finished.emit(wav_path)
```

#### 14.3.3 转写 + 上下文数据流

```
speech_ended.emit(chunk_path) 或 finished.emit(wav_path)
    │
    ▼
VoiceToTextWidget._on_speech_chunk(wav_path) 或 _on_record_finished()
    │
    ├─ 创建/复用 VoiceTranscriptionService
    │       └─ _get_transcriber():
    │               model_path, device, compute_type, num_workers
    │               → get_cached_transcriber(...)  (复用 Transcriber LRU 缓存)
    │
    ├─ service.text_ready.connect(self._append_transcript)
    ├─ service.error_occurred.connect(...)
    │
    └─ service.transcribe_file(wav_path, previous_text=_last_text)
            │
            ├─ 读取 [voice_to_text] 配置:
            │   language, vad_filter, vad_threshold,
            │   vad_min_speech_duration_ms, vad_speech_pad_ms,
            │   context_max_chars, initial_prompt
            │
            ├─ 构建上下文提示:
            │   _build_context_prompt(_last_text)
            │       └─ 取最后 N 字符 → "上文: {context}"
            │           └─ 拼接到 initial_prompt
            │
            ├─ transcriber.transcribe(
            │       wav_path,
            │       language=...,
            │       vad_filter=...,
            │       vad_parameters={threshold, min_speech_..., speech_pad_ms, ...},
            │       temperature=[0.0,0.2,0.4],
            │       condition_on_previous_text=True,
            │       initial_prompt=带上下文的提示
            │   )
            │
            ├─ 合并 segments → text = " ".join(...)
            │
            ├─ _to_simplified(text)  (OpenCC t2s，如有)
            │
            └─ text_ready.emit(text)
                    │
                    ▼
            VoiceToTextWidget._append_transcript(text)
                    │
                    ├─ 在结果区追加显示（带时间戳）
                    ├─ _last_text = text  (更新上下文）
                    ├─ 保存到 voice/{timestamp}.json
                    │       └─ ConversationItem(
                    │               video_name=当前会话,
                    │               transcript=text,
                    │               timestamp=...
                    │           )
                    │
                    └─ 自动滚动到底部
```

#### 14.3.4 关键方法索引（Voice 模块）

| 组件 | 方法 | 文件:行号 | 说明 |
|------|------|-----------|------|
| VoiceRecorder | `start()` | `voice_recorder.py:172` | 启动录音线程 |
| VoiceRecorder | `_record_loop()` | `voice_recorder.py:187` | sounddevice InputStream 主循环 |
| VoiceRecorder | `_audio_callback()` | `voice_recorder.py:223` | PortAudio 回调，VAD + 音量 |
| VoiceRecorder | `_detect_speech_end()` | `voice_recorder.py:117` | 能量阈值判断语音结束 |
| VoiceRecorder | `_extract_speech_chunk()` | `voice_recorder.py:139` | 提取 VAD 片段并保存 WAV |
| VoiceRecorder | `extract_chunk()` | `voice_recorder.py:278` | 线程安全提取当前缓冲 |
| VoiceTranscriptionService | `transcribe_file()` | `voice_transcription.py:118` | 带上下文的单文件转写 |
| VoiceTranscriptionService | `_build_context_prompt()` | `voice_transcription.py:101` | 截取上文用于 initial_prompt |
| VoiceTranscriptionService | `_build_vad_parameters()` | `voice_transcription.py:79` | 从配置构造 VAD 参数字典 |
| VoiceTranscriptionService | `_to_simplified()` | `voice_transcription.py:19` | OpenCC 繁→简转换 |
| VoiceToTextWidget | `_on_speech_chunk()` | `voice_to_text_widget.py:420` | VAD chunk 到达处理 |
| VoiceToTextWidget | `_append_transcript()` | `voice_to_text_widget.py:480` | 追加转写结果 + 保存对话 |

#### 14.3.5 配置段 `[voice_to_text]`

```ini
[voice_to_text]
audio_sample_rate = 16000
audio_channels = 1
model_path = large-v3
device = auto
compute_type = float16
num_workers = 1
language = zh
vad_filter = True
vad_threshold = 0.5
vad_min_speech_duration_ms = 2000
vad_min_silence_ms = 2000
vad_speech_pad_ms = 400
vad_max_speech_s = 0
vad_endpoint_detection = True          # 是否启用端点检测
vad_energy_threshold = 0.0             # 0 表示自动校准
vad_silence_frames = 30
vad_min_speech_frames = 10
vad_calibration_frames = 30
context_max_chars = 200                # 上下文提示最大字符数
initial_prompt =                       # 自定义初始提示词
```

实时语音转写完全复用现有的 `Transcriber` 缓存和 `TranscriptionConfig` 加载逻辑，与离线转写保持一致的模型管理与 OOM 降级策略。
