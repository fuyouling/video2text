# Video2Text

Audio/video transcription and summarization tool based on faster-whisper, suitable for Windows environment.

## GUI

![](https://github.com/user-attachments/assets/444687f6-302c-4b76-be40-9c5a595a0151)

**Program Entry**
```bash
python -m src.ui.gui
```

## Installation

**1. Download and Dependencies**

Download the portable version from the release page, extract it, and double-click to run. The program will automatically download dependencies.

If the download fails, check if the download source exists:

- [faster-whisper-large-v3-turbo-ct2](https://huggingface.co/deepdml/faster-whisper-large-v3-turbo-ct2) (transcription speech model)
- [cuBLAS.and.cuDNN_CUDA12_win_v3.7z](https://github.com/Purfview/whisper-standalone-win/releases/tag/libs) (GPU acceleration dependency libraries)

libs directory description:

Normally automatically downloads the `cuBLAS.and.cuDNN_CUDA12_win_v3.7z` package and automatically extracts it to the `libs` directory, containing CUDA 12 and cuDNN 9 dynamic libraries used for local GPU-accelerated speech recognition inference:

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

models directory structure:

Normally automatically downloads the `faster-whisper-large-v3-turbo-ct2` model. If you want to use other models, you can download them from [Systran](https://huggingface.co/Systran)

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

> After placing the models, you can use the audio/video to text feature.

**2. Summary Model Installation**

Supports two summary services: NVIDIA online models and local Ollama models. Choose one as needed. Configuration steps: `Settings > Edit Configuration > Summary > Radio button selection`

**2.1 NVIDIA Online (Use online NVIDIA models for summarization)**

You need to first register on [NVIDIA Build](https://build.nvidia.com/) and create an API Key (most models are free to use currently). After obtaining the Key, create a new text file named `.env` in the program directory (note: the filename starts with a dot and has no extension). Open it with Notepad and add the following content as needed:

``` ini
# NVIDIA API Key (required when using online NVIDIA models for summarization)
NVIDIA_API_KEY=nvapi-your API key
```

Save the file. The program will automatically read environment variables from this file when starting.

**2.2 Install Ollama (Use local models for summarization)**

Ollama is a local large language model runtime framework used for generating text summaries. If you have 8GB of VRAM, it may be difficult to find a model with good summarization performance. It is recommended to use online models directly.

1. For download and installation, see the [official website](https://docs.ollama.com/). You need to add ollama to the environment variables
2. The ollama service will be automatically started during summarization, and there are start and stop functions in [Configuration]
