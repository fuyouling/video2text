import pytest
from faster_whisper import WhisperModel


pytestmark = pytest.mark.skip(reason="requires GPU and model download")


# model_size = "models/large-v3"
model_size = "models/faster-whisper-large-v3-turbo-ct2"

# Run on GPU with FP16
model = WhisperModel(model_size, device="cuda", compute_type="float16")

# or run on GPU with INT8
# model = WhisperModel(model_size, device="cuda", compute_type="int8_float16")
# or run on CPU with INT8
# model = WhisperModel(model_size, device="cpu", compute_type="int8")

# 电商教程场景字符串（精简版：initial_prompt 必须短，否则会挤占 448 token 上下文）
scene_text = "国内电商教学教程，含淘宝、拼多多、抖音、快手、小红书等平台运营、直播带货、短视频带货内容，请精准识别电商专业术语，简体中文输出。"

# 电商专属热词候选（按重要度排序；实际只取能塞进 token 预算的部分）
hotwords_candidates = [
    # 主流电商平台
    "抖店","淘宝","拼多多","小红书","快手电商","淘系",
    # 开店基础术语
    "SKU","一件代发","无货源",
    # 流量与运营术语
    "UV价值","GMV",
    # 短视频/直播电商术语
    "千川","随心推","DOU+","憋单","拉停留","达人带货","精选联盟","挂车视频",
    # 电商行业黑话/专属词汇
    "起店","破零","测款","打爆单品","拉新","促活","复购","私域流量","公域流量","精细化运营","铺货","动销",
]

# 模型文本上下文上限为 model.max_length(=448 token)。使用 hotwords 时，faster-whisper 会把它
# 注入“每一个”窗口，且与 condition_on_previous_text 累积的真实前文各自上限 223 token，
# 二者叠加极易超过 448 → 报 "maximum decoding length must be > 0"。
# 解决：关闭 condition_on_previous_text（每窗前文重置为空），hotwords 单独预算 ≤ 220 token 即安全。
def trim_hotwords(model, candidates, max_total=220):
    """在 token 预算内贪心截取热词，返回空格分隔的字符串（库内部还会再截断到 223）。"""
    acc = []
    for kw in candidates:
        trial = " ".join(acc + [kw])
        if len(model.hf_tokenizer.encode(" " + trial.strip()).ids) > max_total:
            break
        acc.append(kw)
    return " ".join(acc)

hotwords = trim_hotwords(model, hotwords_candidates)
print("热词 token 数约: %d（预算 220，库上限 223，模型上限 448）"
      % len(model.hf_tokenizer.encode(" " + hotwords.strip()).ids))

segments, info = model.transcribe("video/out_16k_mono.wav", 
                                  beam_size=5,
                                  language="zh",
                                  temperature=0.0,
                                #   initial_prompt=scene_text,
                                  hotwords=hotwords,
                                  condition_on_previous_text=False)

print(
    "Detected language '%s' with probability %f"
    % (info.language, info.language_probability)
)

for segment in segments:
    print("[%.2fs -> %.2fs] %s" % (segment.start, segment.end, segment.text))
