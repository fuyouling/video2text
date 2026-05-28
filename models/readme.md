# models

模型文件存放目录，用于存放语音转写所需的 faster-whisper 模型。

## 目录结构

```
models/
├── large-v3/                    # 默认转写模型
│   ├── model.bin                # 模型权重文件
│   ├── config.json
│   ├── tokenizer.json
│   └── vocabulary.txt
└── (其他模型)/                   # 可自行添加其他 whisper 模型
```

## 默认模型下载

`large-v3` 模型的 `model.bin` 文件体积较大，需手动下载：

```
https://huggingface.co/Systran/faster-whisper-large-v3/resolve/main/model.bin?download=true
```

下载后放入 `models/large-v3/` 目录。国内用户可配置 `config.ini` 中的 `[network]` 段使用镜像加速：

```ini
[network]
hf_mirror_url = https://huggingface.co/Systran/faster-whisper-large-v3/resolve/main
```

## 说明

- 模型路径可在 `config.ini` 的 `[transcription]` 段中通过 `model_path` 参数配置
- 支持所有 [faster-whisper](https://github.com/SYSTRAN/faster-whisper) 兼容模型（如 `base`, `small`, `medium`, `large-v2`, `large-v3` 等）
- 此目录已被 `.gitignore` 忽略，不会提交到版本控制中
