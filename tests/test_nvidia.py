
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

user_input = """
你是一个严格的中文文本修正器。
修正用户语音转写文本中的错别字和不通顺的语句，
保留用户原来的语气和表达习惯，只修正明显错误的字词。
请以JSON格式输出，格式为：
{"source_text":"用户原文","update_text":"修正后的文本"}
只输出JSON，不要添加任何其他内容。
文本内容:
看一看英雷达API是否能够使用
"""

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
