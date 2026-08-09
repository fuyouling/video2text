from openai import OpenAI
import os
import time
from dotenv import load_dotenv

# 加载 .env 文件（默认读取当前目录的 .env）
load_dotenv()


client = OpenAI(
  base_url = "https://integrate.api.nvidia.com/v1",
  api_key = os.environ.get("NVIDIA_API_KEY", "")
)

response = client.responses.create(
  model="openai/gpt-oss-20b",
  input="hello",
  max_output_tokens=4096,
  top_p=1,
  temperature=1,
  stream=True
)


reasoning_done = False
for chunk in response:
  if chunk.type == "response.reasoning_text.delta":
    print(chunk.delta, end="")
  elif chunk.type == "response.output_text.delta":
    if not reasoning_done:
      print("\n")
      reasoning_done = True
    print(chunk.delta, end="")