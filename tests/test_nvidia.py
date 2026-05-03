
import requests, base64
import os
from dotenv import load_dotenv

# 加载 .env 文件（默认读取当前目录的 .env）
load_dotenv()


invoke_url = "https://integrate.api.nvidia.com/v1/chat/completions"
stream = False


headers = {
  "Authorization": "Bearer " + os.environ.get("NVIDIA_API_KEY", ""),
  "Accept": "text/event-stream" if stream else "application/json"
}

user_input = 'What is the capital of France?'

payload = {
  "model": "openai/gpt-oss-120b",
  "messages": [{"role":"user","content":user_input}],
  "max_tokens": 100000,
  "temperature": 1.00,
  "top_p": 1.00,
  "frequency_penalty": 0.00,
  "presence_penalty": 0.00,
  "stream": stream
}
# print("Request headers:", headers)
response = requests.post(invoke_url, headers=headers, json=payload)

if stream:
    for line in response.iter_lines():
        if line:
            print(line.decode("utf-8"))
else:
    print(response.json())
