"""AMD GPU Cloud (Radeon Cloud) 连通性与对话测试脚本。

用法:
    python tests/test_amd.py
"""

import os
import sys
import time
from pathlib import Path

# 确保项目根目录在 sys.path 中，支持直接命令行运行
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.summarization.amd_client import AmdClient
from src.utils.env_loader import ensure_env_loaded, get_api_key


def main():
    print("=" * 60)
    print("AMD GPU Cloud (Radeon Cloud) API 测试")
    print("=" * 60)

    # 1. 加载环境变量与 API Key
    ensure_env_loaded()
    api_key = get_api_key("AMD_API_KEY")
    if not api_key:
        print("[错误] 未检测到 AMD_API_KEY，请在项目根目录 .env 文件中配置：")
        print("       AMD_API_KEY=rc-xxxxxxxx")
        sys.exit(1)

    masked_key = api_key[:6] + "..." + api_key[-4:] if len(api_key) > 10 else "***"
    print(f"[配置] API Key: {masked_key}")

    api_url = "https://developer.amd.com.cn/radeon/api/v1/chat/completions"
    # 支持从命令行指定模型，默认使用 Qwen3.8-Flash-Next
    default_model = "Qwen3.8-Flash-Next"
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        model = sys.argv[1]
    else:
        model = default_model

    print(f"[配置] API 端点: {api_url}")
    print(f"[配置] 测试模型: {model} (可通过命令行指定，例如: python tests/test_amd.py Qwen3.8-Flash-Next)")

    # 2. 初始化客户端
    client = AmdClient(
        api_url=api_url,
        api_key=api_key,
        model=model,
        timeout=60,
    )

    # 3. 测试 API 连通性
    print("\n[步骤 1] 正在检查 API 连通性...")
    t0 = time.monotonic()
    is_connected = client.check_connection()
    latency_ms = (time.monotonic() - t0) * 1000
    if is_connected:
        print(f"[成功] 连接正常，耗时: {latency_ms:.0f} ms")
    else:
        print(f"[警告] 连接检查未返回成功，耗时: {latency_ms:.0f} ms（继续尝试发送消息）")

    # 4. 发送 "hello" 消息并流式接收回复
    user_prompt = "hello"
    print(f"\n[步骤 2] 发送消息: '{user_prompt}'")
    print("-" * 60)
    print(f"[{model} 回复 (流式接收)]: ", end="", flush=True)

    start_time = time.monotonic()
    token_count = 0

    def on_token(token: str):
        nonlocal token_count
        token_count += 1
        print(token, end="", flush=True)

    try:
        reply = client.generate(
            model=model,
            prompt=user_prompt,
            temperature=0.7,
            max_tokens=8192,
            stream=True,
            on_token=on_token,
        )
        elapsed = time.monotonic() - start_time
        print("\n" + "-" * 60)
        print(f"[完成] 回复接收完毕！共计 {len(reply)} 字符 (约 {token_count} chunks)，耗时 {elapsed:.2f} 秒")
    except Exception as e:
        print(f"\n[失败] 模型 {model} 请求异常: {e}")
        if model != default_model:
            print(f"\n[提示] 您可以尝试当前 AMD 云端正常在线的模型: {default_model}")
    finally:
        client.close()

    print("=" * 60)


if __name__ == "__main__":
    main()
