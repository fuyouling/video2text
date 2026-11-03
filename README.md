# Video2Text

Audio/video transcription and summarization tool based on faster-whisper, suitable for Windows environment.

## GUI

If you don't like this background image, you can replace it and adjust the transparency as you wish.

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
- [paraformer-large-vad-punc](https://www.modelscope.cn/models/damo/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx) (FunASR transcription speech model, Chinese high accuracy)
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

Normally automatically downloads `faster-whisper-large-v3-turbo-ct2` and `paraformer-large-vad-punc`. If you want to use other whisper models, you can download them from [Systran](https://huggingface.co/Systran)

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

**FunASR Model Description:**
- FunASR Paraformer is an industrial-grade, high-accuracy Chinese speech recognition model open-sourced by Alibaba DAMO Academy, integrating Paraformer speech recognition, FSMN-VAD voice activity detection, and CT-Transformer punctuation restoration.
- Utilizing a non-autoregressive end-to-end architecture, it provides fast inference speed and high Chinese accuracy, especially suitable for long audio, Chinese meetings, and lecture transcriptions.
- Supports hotword (Hotwords) configuration to improve recognition accuracy for domain-specific terminology and proper nouns.
- Can be selected under `Settings > Edit Config > Transcription` (`Faster-Whisper (multilingual)` or `FunASR (Chinese High-Accuracy)`).

> After placing the models, you can use the audio/video to text feature.

**2. Summary Model Installation**

Supports multiple summary services: NVIDIA online models, local Ollama models, Mistral online models, and AMD online models. Choose one as needed. Configuration steps: `Settings > Edit Config > Summary > Radio button selection`

> **Note**: In the "Transcription + Summary" workflow, FunASR / Faster-Whisper is responsible for the first stage of speech-to-text transcription to generate punctuated text with timestamps. Once transcription is complete, the configured summary model (AMD, NVIDIA, Mistral, or Ollama) will generate structured summaries from the transcript.

**2.1 NVIDIA Online (Use online NVIDIA models for summarization)**

You need to first register on [NVIDIA Build](https://build.nvidia.com/) and create an API Key, then add it to the program via `Settings > Manage API Key`

**2.2 Install Ollama (Use local models for summarization)**

Ollama is a local large language model runtime framework used for generating text summaries. If you have limited VRAM, it may be difficult to find a model with good summarization performance. It is recommended to use online models directly.

1. For download and installation, see the [official website](https://docs.ollama.com/). You need to add ollama to the environment variables
2. The ollama service will be automatically started during summarization, and there are start and stop functions in [Configuration]

> After registering for an Ollama account, there are also free online models you can use

**2.3 mistral Online Model**

You need to first register on [mistral](https://v2.auth.mistral.ai/login), then create an API Key, and add it to the program via `Settings > Manage API Key`

**2.4 AMD Online Model (Use online AMD Radeon Cloud models for summarization)**

You need to first register on the [AMD Developer Community](https://developer.amd.com.cn/) and create an API Key (Token Factory), then add it to the program via `Settings > Manage API Key`.
- Default summary model is `Qwen3.8-Flash-Next` (can be customized to other models such as `MiMo-V2.6-Flash` in configuration)
- Supports single-thread streaming output and multi-threaded concurrent chunk summarization
- Built-in "Tools > AMD API Response Test" dialog allows you to quickly test connectivity and apply available models

## Discussion Group
QQ Group: 296875960
