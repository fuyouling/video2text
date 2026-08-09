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

start_time = time.time()

completion = client.chat.completions.create(
  model="openai/gpt-oss-20b",
  messages=[{"content":"hello","role":"user"}],
  temperature=1,
  top_p=1,
  max_tokens=4096,
  stream=True,
  extra_body={"reasoning_effort": "low"}
)

for chunk in completion:
  if not getattr(chunk, "choices", None):
    continue
  reasoning = getattr(chunk.choices[0].delta, "reasoning_content", None)
  if reasoning:
    print(reasoning, end="")
  if chunk.choices and chunk.choices[0].delta.content is not None:
    print(chunk.choices[0].delta.content, end="")

elapsed = time.time() - start_time
print(f"\n\n[耗时] 总计: {elapsed:.2f} 秒")