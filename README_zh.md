# Video2Text

基于 faster-whisper 开发得音视频转写和摘要工具,适用于 windows 环境.

## GUI

如果不喜欢这个背景图片可自行更换和修改透明度.

![](https://github.com/user-attachments/assets/444687f6-302c-4b76-be40-9c5a595a0151)

**程序入口**
```bash
python -m src.ui.gui
```

## 安装

**1. 下载和依赖说明**

从release界面下载便携版解压后双击打开程序会自动下载依赖.

如果下载失败可以查看下载源是否存在:

- [faster-whisper-large-v3-turbo-ct2](https://huggingface.co/deepdml/faster-whisper-large-v3-turbo-ct2)（转写语音模型）
- [paraformer-large-vad-punc](https://www.modelscope.cn/models/damo/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx)（FunASR 转写语音模型，中文高精度）
- [cuBLAS.and.cuDNN_CUDA12_win_v3.7z](https://github.com/Purfview/whisper-standalone-win/releases/tag/libs)（GPU 加速依赖库）

libs目录说明:

正常自动下载 `cuBLAS.and.cuDNN_CUDA12_win_v3.7z` 压缩包且自动解压到 `libs` 目录,包含 CUDA 12 与 cuDNN 9 相关动态库，用于本地 GPU 加速的语音识别推理：

```text
video2text\libs\
├── cublas64_12.dll                    
├── cublasLt64_12.dll                  
├── cudnn64_9.dll                      
├── cudnn_adv64_9.dll                  
├── cudnn_cnn64_9.dll                  
├── cudnn_engines_precompiled64_9.dll  
├── cudnn_engines_runtime_compiled64_9.dll
├── cudnn_graph64_9.dll                
├── cudnn_heuristic64_9.dll            
├── cudnn_ops64_9.dll                  
├── readme_en.md                       
└── readme_zh.md                       
```

 models目录结构说明：

 正常自动下载模型`faster-whisper-large-v3-turbo-ct2`与`paraformer-large-vad-punc`,如果想使用其它 whisper 模型可以到[Systran](https://huggingface.co/Systran)进行下载

```text
video2text\models\
├── faster-whisper-large-v3-turbo-ct2\
│   ├── config.json                 
│   ├── model.bin                   
│   ├── preprocessor_config.json    
│   ├── README.md                   
│   ├── tokenizer.json              
│   ├── vocabulary.json             
│   └── .gitattributes              
└── paraformer-large-vad-punc\
    ├── asr\
    │   ├── am.mvn
    │   ├── config.yaml
    │   ├── model_quant.onnx
    │   └── tokens.json
    ├── vad\
    │   ├── config.yaml
    │   └── model_quant.onnx
    └── punc\
        ├── config.yaml
        ├── model_quant.onnx
        └── tokens.json
```

**FunASR 模型说明：**
- FunASR Paraformer 是阿里达摩院开源的工业级中文高精度语音识别模型，内置 Paraformer 语音识别、FSMN-VAD 语音活动检测和 CT-Transformer 标点恢复。
- 采用非自回归（Non-autoregressive）端到端架构，识别速度快，特别适合长音频与中文会议转写。
- 支持热词（Hotwords）配置，可输入专有名词以提升特定词汇的识别准确率。
- 在`设置 > 编辑配置 > 转写`中可通过单选框切换 `Faster-Whisper (通用多语言)` 与 `FunASR (中文高精度)`。

> 放好模型后就可以使用音视频转文本功能了。

**2. 总结模型安装**

支持多种总结服务：NVIDIA 在线模型、本地 Ollama 模型和 AMD 在线模型，按需选择其一即可。配置步骤`设置>编辑配置>总结>单选框选择`

> **说明**：在“转写+总结”全流程中，FunASR / Faster-Whisper 负责第一阶段的音视频语音识别转写，输出带标点和时间戳的文本；转写完成后，再由配置好的总结大模型（AMD、NVIDIA、Ollama）对文本进行摘要总结。

**2.1 NVIDIA 在线（使用在线 NVIDIA 模型总结）**

需要先在 [NVIDIA Build](https://build.nvidia.com/) 注册账号并创建 API Key,再通过`设置>设置 API Key`添加到程序

**2.2 安装 Ollama（使用本地模型总结）**

Ollama 是一个本地大语言模型运行框架，使用它来生成文本摘要,如果显存比较小很难找到总结效果较好的模型,建议直接使用在线模型。

1. 下载和安装请查看[官网](https://docs.ollama.com/),需要将ollama添加到环境变量中
2. 总结时会自动启动ollama服务并且[配置]中有启动和关闭功能

>注册Ollama账号后也有免费的在线模型可以使用

**2.3 AMD 在线模型（使用在线 AMD Radeon Cloud 模型总结）**

需要先在 [AMD 开发者社区](https://developer.amd.com.cn/) 注册账号并创建 API Key（Token Factory），再通过`设置>设置 API Key`添加到程序。
- 默认总结模型为 `Qwen3.8-Flash-Next`（可根据需要修改为其他模型如 `MiMo-V2.6-Flash` 等）
- 支持单线程流式输出（Stream）与多线程并发切片加速总结
- 程序内置“工具 > AMD API 响应测试”，可便捷测试网络延迟并一键获取/应用可用模型

## 讨论小组
QQ群: 296875960
