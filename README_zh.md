# Video2Text

基于 faster-whisper 开发得音视频转写和摘要工具,适用于 windows 环境.

## GUI

![](https://github.com/user-attachments/assets/444687f6-302c-4b76-be40-9c5a595a0151)

**入口**
```bash
python -m src.ui.gui
```

## 安装


**1. 安装步骤**

**1.1 部署 video2text**

将 `video2text_portable_windows_*.zip` 解压后双击打开程序会自动下载依赖.如果需要手动下载:
    - [faster-whisper-large-v3-turbo-ct2](https://huggingface.co/deepdml/faster-whisper-large-v3-turbo-ct2)（转写语音模型）
    - [cuBLAS.and.cuDNN_CUDA12_win_v3.7z](https://github.com/Purfview/whisper-standalone-win/releases/tag/libs)（GPU 加速依赖库）

**libs目录结构说明**

`libs` 目录存放程序运行所需的第三方依赖库。包含 CUDA 12 与 cuDNN 9 相关动态库，用于本地 GPU 加速的语音识别推理，需要解压 `cuBLAS.and.cuDNN_CUDA12_win_v3.7z` 到 `libs` 目录：

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

 ****models目录结构说明****：

```text
video2text\models\
└── faster-whisper-large-v3-turbo-ct2\
    ├── config.json                 
    ├── model.bin                   
    ├── preprocessor_config.json    
    ├── README.md                   
    ├── tokenizer.json              
    ├── vocabulary.json             
    └── .gitattributes              
```

> 放好模型后就可以使用视频转文本功能了。

**1.2 总结模型安装**

video2text 支持两种总结服务：NVIDIA 在线模型和本地 Ollama 模型，按需选择其一即可。配置步骤`设置>编辑配置>总结>单选框选择`

**1.2.1 NVIDIA 在线（使用在线 NVIDIA 模型总结）**

需要先在 [NVIDIA Build](https://build.nvidia.com/) 注册账号并创建 API Key（目前大部分模型免费使用）。获取 Key 后在程序目录下新建一个名为 `.env` 的文本文件（注意文件名以点开头，无扩展名）。用记事本打开，按需添加以下内容：

``` ini
# NVIDIA API Key（使用在线 NVIDIA 模型总结时需要）
NVIDIA_API_KEY=nvapi-你的API密钥
```

保存文件。程序启动时会自动读取该文件中的环境变量。API Key 也可以通过系统环境变量设置，效果相同（系统环境变量优先级高于 `.env` 文件）。NVIDIA 提供有很多免费的模型，如果网络访问有问题需要自行解决。

**1.2.2 安装 Ollama（使用本地模型总结）**

Ollama 是一个本地大语言模型运行框架，video2text 使用它来生成文本摘要。

1. 下载和安装请查看[官网](https://docs.ollama.com/),需要将ollama添加到环境变量中
2. 总结时会自动启动ollama服务并且[配置]中有启动和关闭功能


## 讨论小组
QQ群: 296875960
