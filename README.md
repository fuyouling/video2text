# Video2Text

视频转文本工具 - 使用 faster-whisper 进行语音转写，默认使用 Ollama + Qwen2.5 或 NVIDIA API 进行文本总结，语音转写和文本总结模型可自行更换。

## 功能特性

- 完全免费，无时长限制，可批量转写视频和音频
- 基于 faster-whisper large-v3，高准确率
- 集成 Ollama / NVIDIA 大模型，自动生成摘要
- 图形界面 + 命令行，Windows 绿色版已打包
- 批量转写 + 总结，输出 TXT/SRT/VTT/JSON


## 安装

### 1. 安装依赖
需要依赖 cuBLAS for CUDA 12 和 cuDNN 9 for CUDA 12
- 可以从[这里](https://github.com/Purfview/whisper-standalone-win/releases/tag/libs)下载,把DLL文件解压到libs目录即可.
- 或者直接使用pytorch,安装步骤查看[官网](https://pytorch.org/),使用cuda 12的版本
```bash
pip install -r requirements.txt
```

### 2. 转写模型文件下载

- 模型下载地址huggingface: [faster-whisper-large-v3](https://huggingface.co/Systran/faster-whisper-large-v3)
- 也可以到Windows 安装教程查看网盘下载地址


### 3. 总结模型安装

支持两种总结服务，按需选择其一即可。

#### 3.1 NVIDIA 在线（使用在线 NVIDIA 模型总结）

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

#### 3.2 安装 Ollama（使用本地模型总结）

> 本文以 `qwen2.5:7b-instruct-q4_K_M` 为例进行安装演示，该模型实际总结效果一般，推荐优先使用 NVIDIA 在线模型。
> 或者本地显卡较好,拉取较大的模型也可使用这种本地总结的方式

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


## 许可证

GNU GENERAL PUBLIC LICENSE

## 讨论小组
QQ群: 296875960
