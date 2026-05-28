from zai import ZhipuAiClient

import requests, base64
import os
from dotenv import load_dotenv
# 加载 .env 文件（默认读取当前目录的 .env）
load_dotenv()
client = ZhipuAiClient(api_key= os.environ.get("ZHIPU_API_KEY", ""))  # 请填写您自己的 API Key

response = client.chat.completions.create(
    model="glm-4.7-flash",
    messages=[
        {"role": "user", "content": "作为一名营销专家，请为我的产品创作一个吸引人的口号"},
        {"role": "assistant", "content": "当然，要创作一个吸引人的口号，请告诉我一些关于您产品的信息"},
        {"role": "user", "content": "智谱开放平台"}
    ],
    # messages=[{'role': 'user', 'content': 'hi'}],
    thinking={
        "type": "enabled",    # 启用深度思考模式
    },
    max_tokens=65536,          # 最大输出 tokens
    temperature=1.0           # 控制输出的随机性
)

# 获取完整回复
print(response.choices[0].message)