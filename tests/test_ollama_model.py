import os
import pytest

pytestmark = pytest.mark.skip(reason="manual ollama model test")


def test_ollama_model():
    from ollama import Client

    client = Client(
        host="https://ollama.com",
        headers={"Authorization": "Bearer " + os.environ.get("OLLAMA_API_KEY", "")},
    )

    messages = [
        {
            "role": "user",
            "content": "Why is the sky blue?",
        },
    ]

    for part in client.chat("kimi-k2.5:cloud", messages=messages, stream=True):
        print(part["message"]["content"], end="", flush=True)
