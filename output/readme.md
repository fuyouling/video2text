# output

默认输出目录，用于存放视频/音频转写和总结生成的结果文件。

## 输出文件类型

| 文件 | 说明 | 生成方式 |
|------|------|----------|
| `{name}.txt` | 转写文本（可读格式） | 转写 / 管道 |
| `{name}.srt` | SRT 字幕文件 | 转写 / 管道 |
| `{name}.vtt` | VTT 字幕文件 | 转写 / 管道 |
| `{name}.json` | 转写分段 JSON 数据 | 转写 / 管道 |
| `{name}_summary.txt` | 文本摘要（纯文本） | 总结 / 管道 |
| `{name}_summary.md` | 文本摘要（Markdown） | 总结 / 管道 |
| `{name}_full.json` | 完整管道输出数据 | 管道 |

## 说明

- 输出格式可通过 `config.ini` 的 `[output]` 段配置
- `transcript_format` 控制转写文件格式（支持 `txt,srt,vtt,json` 多选）
- `summary_format` 控制摘要文件格式（`txt` 或 `md`）
- 此目录已被 `.gitignore` 忽略，不会提交到版本控制中
