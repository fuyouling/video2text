# Video2Text

Audio/video transcription and summarization tool based on faster-whisper, designed for Windows environment.

## GUI

![GUI](https://github.com/user-attachments/assets/444687f6-302c-4b76-be40-9c5a595a0151)

**Run**
```bash
python -m src.ui.gui
```

## Installation

**1. Installation Steps**

**1.1 Deploy video2text**

Extract `video2text_portable_windows_*.zip` and open the program. Dependencies will be downloaded automatically. If manual download is needed:
    - [faster-whisper-large-v3-turbo-ct2](https://huggingface.co/deepdml/faster-whisper-large-v3-turbo-ct2) (speech transcription model)
    - [cuBLAS.and.cuDNN_CUDA12_win_v3.7z](https://github.com/Purfview/whisper-standalone-win/releases/tag/libs) (GPU acceleration dependency library)

**libs directory structure**

The `libs` directory stores third-party dependency libraries required for program operation. It contains CUDA 12 and cuDNN 9 related dynamic libraries used for local GPU-accelerated speech recognition inference. Extract `cuBLAS.and.cuDNN_CUDA12_win_v3.7z` to the `libs` directory:

```text
video2text\libs\
├── cublas64_12.dll                     ← NVIDIA cuBLAS 12 library
├── cublasLt64_12.dll                   ← NVIDIA cuBLASLt 12 library
├── cudnn64_9.dll                       ← NVIDIA cuDNN 9 main library
├── cudnn_adv64_9.dll                   ← cuDNN advanced inference library
├── cudnn_cnn64_9.dll                   ← cuDNN CNN library
├── cudnn_engines_precompiled64_9.dll   ← cuDNN precompiled engines
├── cudnn_engines_runtime_compiled64_9.dll ← cuDNN runtime compiled engines
├── cudnn_graph64_9.dll                 ← cuDNN graph execution library
├── cudnn_heuristic64_9.dll             ← cuDNN heuristic engine
├── cudnn_ops64_9.dll                   ← cuDNN operator library
├── readme_en.md                        ← dependency description (English)
└── readme_zh.md                        ← dependency description (Chinese)
```

****models directory structure****:

```text
video2text\models\
└── faster-whisper-large-v3-turbo-ct2\
    ├── config.json                  ← model configuration (~2.3 KB)
    ├── model.bin                    ← core model file (~1.6 GB)
    ├── preprocessor_config.json     ← preprocessing configuration (~340 B)
    ├── README.md                    ← model description
    ├── tokenizer.json               ← tokenizer (~2.7 MB)
    ├── vocabulary.json              ← vocabulary (~1.1 MB)
    └── .gitattributes               ← Git attributes (can be ignored)
```

> After placing the models, you can use the video-to-text feature.

**1.2 Summary model installation**

video2text supports two summary services: NVIDIA online models and local Ollama models. Choose one as needed. Configuration steps: `Settings > Edit Configuration > Summary > Radio button selection`

**1.2.1 NVIDIA Online (Use online NVIDIA models for summarization)**

You need to first register on [NVIDIA Build](https://build.nvidia.com/) and create an API Key (most models are free to use currently). After obtaining the Key, create a new text file named `.env` in the program directory (note: the filename starts with a dot and has no extension). Open it with Notepad and add the following content as needed:

``` ini
# NVIDIA API Key (required when using online NVIDIA models for summarization)
NVIDIA_API_KEY=nvapi-your API key
```

Save the file. The program will automatically read environment variables from this file when starting. API Key can also be set via system environment variables with the same effect (system environment variables take precedence over `.env` file). NVIDIA offers many free models; if you have network access issues, you need to resolve them yourself.

**1.2.2 Install Ollama (Use local models for summarization)**

Ollama is a local large language model runtime framework. video2text uses it to generate text summaries.

1. For download and installation, see [official website](https://docs.ollama.com/), you need to add ollama to environment variables
2. The ollama service will be automatically started during summarization, and there are start/stop functions in [Configuration]

