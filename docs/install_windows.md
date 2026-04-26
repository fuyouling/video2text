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