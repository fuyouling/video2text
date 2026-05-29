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

`large-v3` 模型：

```
https://huggingface.co/Systran/faster-whisper-large-v3/resolve/main
```

下载后放入 `models/large-v3/` 目录。

## 说明

- 模型路径可在 `config.ini` 的 `[transcription]` 段中通过 `model_path` 参数配置
- 支持所有 [faster-whisper](https://github.com/SYSTRAN/faster-whisper) 兼容模型（如 `base`, `small`, `medium`, `large-v2`, `large-v3` 等）
- 此目录已被 `.gitignore` 忽略，不会提交到版本控制中
