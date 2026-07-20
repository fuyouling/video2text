**1. 系统要求**

在安装 video2text 这一本地视频转文字工具前，请先确认你的电脑满足以下条件。

**1.1 最低配置与推荐配置**

| 项目 | 最低要求 | 推荐配置 |
|------|---------|---------|
| **操作系统** | Windows 10 64位 | Windows 11 64位 |
| **磁盘空间** | 20 GB 可用空间 | 30 GB 以上（含模型文件） |
| **内存（RAM）** | 8 GB | 16 GB 及以上 |
| **显卡** | 无（CPU模式可用但比较慢） | NVIDIA 显卡（8GB显存以上） + CUDA |

> **注意**：AMD 显卡暂不支持 GPU 加速。

**1.2 需要下载哪些文件**

下载 release 压缩包后，程序首次运行时会自动从 Hugging Face 拉取转写语音模型，依赖包已随压缩包内置。

如需手动准备，可提前下载以下两项：

- [faster-whisper-large-v3-turbo-ct2](https://huggingface.co/deepdml/faster-whisper-large-v3-turbo-ct2)（转写语音模型）
- [cuBLAS.and.cuDNN_CUDA12_win_v3.7z](https://github.com/Purfview/whisper-standalone-win/releases/tag/libs)（GPU 加速依赖库）

若自动拉取失败，下载 release 压缩包后需手动放置以上两项：模型文件夹放于 `models` 目录（见 2.1 第二步），`cuBLAS.and.cuDNN_CUDA12_win_v3.7z` 需解压到 `libs` 目录（见 2.1 第一步）。

**1.3 转写模型选择**

下表为同一批视频在 NVIDIA GeForce RTX 4060 Laptop GPU 上的实测平均转写时间（按每分钟音频折算）：

| 模型 | 平均转写时间 | 模型体积 |
|------|------------|---------|
| `large-v3` | 约 19 秒/分钟音频 | ~3 GB |
| `faster-whisper-large-v3-turbo-ct2` | 约 5 秒/分钟音频 | ~1.5 GB |

turbo 模型速度约为 large-v3 的 4倍，准确率接近比 large-v3 低一些，显存占用更低，适合大批量转写。

**2. 安装步骤**

以下按顺序介绍 video2text 本地视频转文字工具的完整安装流程。

**2.1 部署 video2text 本地视频转文字程序**

**第一步：解压程序包**

将 `video2text_portable_windows_*.zip` 解压到你希望存放程序的位置，例如 `D:\video2text`。该程序为**绿色版**，无需安装，不会写入注册表，解压即用。解压后目录结构如下：

```text
D:\video2text\
├── video2text.exe          ← 主程序
├── video2text.bat          ← 启动脚本（自动设置工作目录）
├── config.ini              ← 配置文件
├── .env                    ← 环境变量配置（存放 API Key，需手动创建）
├── docs                    ← 文档
├── assets\                 ← 图标资源
├── ffmpeg\                 ← 内置 FFmpeg
├── libs\                   ← 依赖库目录
├── 7za.exe                 ← 用于依赖解压
├── models\                 ← 模型目录（需要放入模型文件）
├── output\                 ← 输出目录（可选）
├── logs\                   ← 日志目录
└── README.md               ← 说明文档
```

**libs目录结构**

>自动下载的这步跳过就行

`libs` 目录存放程序运行所需的第三方依赖库（已随绿色版打包，通常无需手动改动）。当前包含 CUDA 12 与 cuDNN 9 相关动态库，用于本地 GPU 加速的语音识别推理，需要解压 `cuBLAS.and.cuDNN_CUDA12_win_v3.7z` 到 `libs` 目录：

```text
D:\video2text\libs\
├── cublas64_12.dll                     ← NVIDIA cuBLAS 12 库
├── cublasLt64_12.dll                   ← NVIDIA cuBLASLt 12 库
├── cudnn64_9.dll                       ← NVIDIA cuDNN 9 主库
├── cudnn_adv64_9.dll                   ← cuDNN 高级推理库
├── cudnn_cnn64_9.dll                   ← cuDNN CNN 库
├── cudnn_engines_precompiled64_9.dll   ← cuDNN 预编译引擎
├── cudnn_engines_runtime_compiled64_9.dll ← cuDNN 运行时编译引擎
├── cudnn_graph64_9.dll                 ← cuDNN 图执行库
├── cudnn_heuristic64_9.dll             ← cuDNN 启发式引擎
├── cudnn_ops64_9.dll                   ← cuDNN 算子库
├── readme_en.md                        ← 依赖说明（英文）
└── readme_zh.md                        ← 依赖说明（中文）
```

> 若你使用官方绿色版压缩包，`libs` 已包含上述所需文件；请勿随意删除或替换，以免程序无法启动。这些库仅在启用 GPU 加速时生效，使用 CPU 推理时不依赖它们。

**第二步：放入语音识别模型**

>自动下载的这步跳过就行

将下载的模型文件夹解压/放置到程序目录下的 `models` 文件夹中。确保模型文件位于 `models\faster-whisper-large-v3-turbo-ct2\` 子目录下，且包含以下核心文件：

```text
D:\video2text\models\
└── faster-whisper-large-v3-turbo-ct2\
    ├── config.json                  ← 模型配置（约 2.3 KB）
    ├── model.bin                    ← 核心模型文件（约 1.6 GB）
    ├── preprocessor_config.json     ← 预处理配置（约 340 B）
    ├── README.md                    ← 模型说明
    ├── tokenizer.json               ← 分词器（约 2.7 MB）
    ├── vocabulary.json              ← 词表（约 1.1 MB）
    └── .gitattributes               ← Git 属性（可忽略）
```

> 放好模型后就可以使用视频转文本功能了。

**2.2 总结模型安装**

video2text 支持两种总结服务：NVIDIA 在线模型和本地 Ollama 模型，按需选择其一即可。配置步骤`设置>编辑配置>总结>单选框选择`

**2.2.1 NVIDIA 在线（使用在线 NVIDIA 模型总结）**

需要先在 [NVIDIA Build](https://build.nvidia.com/) 注册账号并创建 API Key（目前大部分模型免费使用）。获取 Key 后在程序目录下新建一个名为 `.env` 的文本文件（注意文件名以点开头，无扩展名）。用记事本打开，按需添加以下内容：

``` ini
# NVIDIA API Key（使用在线 NVIDIA 模型总结时需要）
NVIDIA_API_KEY=nvapi-你的API密钥
```

保存文件。程序启动时会自动读取该文件中的环境变量。API Key 也可以通过系统环境变量设置，效果相同（系统环境变量优先级高于 `.env` 文件）。NVIDIA 提供有很多免费的模型，如果网络访问有问题需要自行解决。

**2.2.2 安装 Ollama（使用本地模型总结）**

Ollama 是一个本地大语言模型运行框架，video2text 使用它来生成文本摘要。

> 本文以 `qwen2.5:7b-instruct-q4_K_M` 为例进行安装演示，该模型实际总结效果一般，推荐优先使用 NVIDIA 在线模型。

**第一步：运行安装程序**

双击 `OllamaSetup.exe`，按提示完成安装。安装过程无需手动配置，会自动完成。

**第二步：解压预下载模型**

找到下载好的 `models.zip` 文件，将其解压到 `C:\Users\你的用户名\.ollama` 目录下。确保解压后的目录结构如下：

```text
C:\Users\你的用户名\.ollama\
└── models\
    └── blobs\          ← 模型数据文件
    └── manifests\      ← 模型清单文件
```

**第三步：启动 Ollama 服务**

方式一：在开始菜单找到 Ollama 图标并启动。
方式二：按 `Win + R` 打开运行窗口，输入 `cmd`，执行 `ollama serve`。

启动后系统托盘会出现 Ollama 图标，表示服务已就绪。

如果需要使用 Ollama 在线云服务模型（如 deepseek-v3.1:671b-cloud、gpt-oss:120b-cloud），需注册账号并在 `.env` 文件中配置 `OLLAMA_API_KEY`：

```ini
# Ollama API Key（使用带认证的 Ollama 服务时可选配置）
OLLAMA_API_KEY=你的API密钥
```
**2.3 验证安装是否成功**

完成以上所有步骤后，按顺序验证各组件是否正常工作：
1. **启动 video2text**：
   - 双击 `video2text.exe` 或 `video2text.bat` 启动程序。
   - 程序主窗口应正常显示，标题为「Video2Text - 视频转文本工具」。
   - 底部状态栏会显示当前使用的配置文件路径。

2. **快速测试**（可选）：
   - 选择一个短小的视频文件（1-2 分钟即可）。
   - 点击「仅转写」按钮，观察日志面板是否有输出、进度条是否推进。
   - 转写完成后，右侧面板应显示转写文本。
   - 点击「仅总结」按钮，确认能正常生成摘要。