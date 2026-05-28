# logs

运行日志目录，用于存放 Video2Text 应用程序运行时生成的日志文件。

## 说明

- 日志文件由 Python logging 模块自动生成
- 日志级别可在 `config.ini` 的 `[app]` 段中通过 `log_level` 参数配置（默认 `INFO`）
- 日志记录了转写、总结、预处理等各模块的运行状态和错误信息
- 此目录已被 `.gitignore` 忽略，不会提交到版本控制中
