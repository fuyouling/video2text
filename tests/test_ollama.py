"""测试Ollama连接和模型"""

import requests
import json


def test_ollama():
    """测试Ollama服务"""
    base_url = "http://127.0.0.1:11434"

    print("Testing Ollama service connection...")

    # 测试连接
    try:
        response = requests.get(f"{base_url}/api/tags", timeout=10)
        if response.status_code == 200:
            print("[OK] Ollama service connected successfully")

            # 获取模型列表
            data = response.json()
            models = data.get("models", [])

            if models:
                print(f"\nAvailable models ({len(models)}):")
                for model in models:
                    print(f"  - {model['name']}")
            else:
                print("\n[WARNING] No models found")
                print("Please run: ollama pull qwen2.5:7b-instruct-q4_K_M")
        else:
            print(f"[ERROR] Ollama service connection failed: {response.status_code}")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"[ERROR] Cannot connect to Ollama service: {e}")
        print("\nPlease make sure Ollama service is running:")
        print("  ollama serve")


if __name__ == "__main__":
    test_ollama()
