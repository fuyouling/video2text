# video

测试视频和音频文件存放目录。

## 支持的格式

**视频格式（16种）：** `.mp4` `.avi` `.mov` `.mkv` `.flv` `.wmv` `.webm` `.ts` `.mts` `.m4v` `.3gp` `.mpeg` `.mpg` `.vob` `.ogv` `.rm` `.rmvb`

**音频格式（7种）：** `.mp3` `.wav` `.flac` `.aac` `.ogg` `.m4a` `.wma`

## 使用方式

```bash
# 转写单个文件
python -m src.main transcribe video/sample.mp4 --output-dir output

# GUI 模式选择文件或文件夹
python -m src.ui.gui
```

## 说明

- GUI 模式支持选择文件夹，会自动递归扫描子目录中的媒体文件
- 此目录已被 `.gitignore` 忽略，不会提交到版本控制中
